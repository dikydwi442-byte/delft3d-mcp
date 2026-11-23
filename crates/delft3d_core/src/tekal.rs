use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::Path;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum TekalError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Invalid TEKAL format: {0}")]
    Parse(String),
}

#[derive(Debug, Clone)]
pub struct TekalFeature {
    pub name: String,
    pub points: Vec<[f64; 2]>,
}

pub fn read_tekal(path: &Path) -> Result<Vec<TekalFeature>, TekalError> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut lines = Vec::new();
    for res in reader.lines() {
        let l = res?;
        let t = l.trim().to_string();
        if t.is_empty() || t.starts_with('*') || t.starts_with('#') {
            continue;
        }
        lines.push(t);
    }

    let mut features = Vec::new();
    let mut idx = 0_usize;
    while idx < lines.len() {
        let name = lines[idx].clone();
        idx += 1;
        if idx >= lines.len() {
            break;
        }
        let shape_toks: Vec<&str> = lines[idx].split_whitespace().collect();
        if shape_toks.len() < 2 {
            return Err(TekalError::Parse(format!(
                "Expected '<nrows> <ncols>' after TEKAL block '{name}'"
            )));
        }
        let nrows = shape_toks[0]
            .parse::<usize>()
            .map_err(|_| TekalError::Parse(format!("Invalid row count in '{name}'")))?;
        idx += 1;

        let mut pts = Vec::with_capacity(nrows);
        for r in 0..nrows {
            if idx + r >= lines.len() {
                return Err(TekalError::Parse(format!(
                    "Unexpected EOF in TEKAL block '{name}' at row {r}"
                )));
            }
            let cols: Vec<&str> = lines[idx + r].split_whitespace().collect();
            if cols.len() >= 2 {
                if let (Ok(x), Ok(y)) = (cols[0].parse::<f64>(), cols[1].parse::<f64>()) {
                    pts.push([x, y]);
                }
            }
        }
        idx += nrows;
        features.push(TekalFeature { name, points: pts });
    }
    Ok(features)
}

pub fn write_tekal(path: &Path, features: &[TekalFeature]) -> Result<(), TekalError> {
    let file = File::create(path)?;
    let mut w = BufWriter::new(file);
    for feat in features {
        if feat.points.is_empty() {
            continue;
        }
        let safe_name = if feat.name.trim().is_empty() {
            "BL01"
        } else {
            feat.name.trim()
        };
        writeln!(w, "{safe_name}")?;
        writeln!(w, "{:>8}       2", feat.points.len())?;
        for &[x, y] in &feat.points {
            writeln!(w, " {x:>20.10E}  {y:>20.10E}")?;
        }
    }
    w.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tekal_roundtrip() {
        let tmp = std::env::temp_dir().join("delft3d_mcp_test_tekal.spl");
        let feats = vec![
            TekalFeature {
                name: "SPL01".to_string(),
                points: vec![[0.0, 0.0], [100.0, 10.0], [200.0, 5.0]],
            },
            TekalFeature {
                name: "SPL02".to_string(),
                points: vec![[0.0, 50.0], [100.0, 60.0], [200.0, 55.0]],
            },
        ];
        write_tekal(&tmp, &feats).unwrap();
        let loaded = read_tekal(&tmp).unwrap();
        assert_eq!(loaded.len(), 2);
        assert_eq!(loaded[0].points.len(), 3);
        assert!((loaded[1].points[1][1] - 60.0).abs() < 1e-9);
        let _ = std::fs::remove_file(tmp);
    }
}
