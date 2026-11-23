use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::Path;

#[derive(Debug, Clone)]
pub struct DiagSummary {
    pub total_bytes: u64,
    pub tail_lines: Vec<String>,
    pub warning_count: usize,
    pub error_count: usize,
    pub warnings: Vec<String>,
    pub errors: Vec<String>,
    pub last_progress_line: Option<String>,
}

/// Fast scanner for tri-diag / log / mon files without loading multi-hundred-MB logs into memory.
pub fn scan_diag_log(
    path: &Path,
    tail_n: usize,
    max_scan_bytes: u64,
) -> Result<DiagSummary, std::io::Error> {
    let mut file = File::open(path)?;
    let total_bytes = file.metadata()?.len();

    // Scan from offset if file exceeds max_scan_bytes
    if total_bytes > max_scan_bytes {
        file.seek(SeekFrom::Start(total_bytes - max_scan_bytes))?;
    }
    let reader = BufReader::new(file);

    let mut ring: Vec<String> = Vec::with_capacity(tail_n.max(1));
    let mut warning_count = 0_usize;
    let mut error_count = 0_usize;
    let mut warnings = Vec::new();
    let mut errors = Vec::new();
    let mut last_progress_line: Option<String> = None;

    for line_res in reader.lines() {
        let Ok(line) = line_res else { continue };
        let upper = line.to_ascii_uppercase();
        if upper.contains("*** ERROR") || upper.contains("FATAL") || upper.contains("NAN ") {
            error_count += 1;
            if errors.len() < 25 {
                errors.push(line.trim().to_string());
            }
        } else if upper.contains("*** WARNING") || upper.contains("WARNING:") {
            warning_count += 1;
            if warnings.len() < 25 {
                warnings.push(line.trim().to_string());
            }
        }
        if upper.contains("TIME =")
            || upper.contains("TSTEP")
            || upper.contains("% COMPLETED")
            || upper.contains("SIMULATION TIME")
        {
            last_progress_line = Some(line.trim().to_string());
        }

        if tail_n > 0 {
            if ring.len() == tail_n {
                ring.remove(0);
            }
            ring.push(line);
        }
    }

    Ok(DiagSummary {
        total_bytes,
        tail_lines: ring,
        warning_count,
        error_count,
        warnings,
        errors,
        last_progress_line,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_scan_diag_log() {
        let tmp = std::env::temp_dir().join("delft3d_mcp_test_tridiag.log");
        {
            let mut f = File::create(&tmp).unwrap();
            writeln!(f, "*** MESSAGE Start simulation").unwrap();
            writeln!(f, "*** WARNING Courant number > 1").unwrap();
            writeln!(f, "Simulation time = 120.0 min").unwrap();
            writeln!(f, "*** ERROR Negative water depth").unwrap();
        }
        let s = scan_diag_log(&tmp, 2, 1024 * 1024).unwrap();
        assert_eq!(s.warning_count, 1);
        assert_eq!(s.error_count, 1);
        assert_eq!(s.tail_lines.len(), 2);
        assert!(s.last_progress_line.is_some());
        let _ = std::fs::remove_file(tmp);
    }
}
