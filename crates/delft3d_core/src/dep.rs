use crate::grd::GridData;
use rayon::prelude::*;
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::Path;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum DepError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Invalid DEP/XYZ format: {0}")]
    Parse(String),
}

pub const DEFAULT_MISSING_DEP: f64 = -999.0;

pub fn read_dep(path: &Path, m_max: usize, n_max: usize) -> Result<Vec<f64>, DepError> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut vals = Vec::with_capacity(m_max * n_max);
    for line_res in reader.lines() {
        let line = line_res?;
        let t = line.trim();
        if t.is_empty() || t.starts_with('*') || t.starts_with('#') {
            continue;
        }
        for tok in t.split_whitespace() {
            vals.push(
                tok.parse::<f64>()
                    .map_err(|_| DepError::Parse(format!("Invalid depth value: {tok}")))?,
            );
        }
    }
    let expected = m_max * n_max;
    if vals.len() != expected {
        return Err(DepError::Parse(format!(
            "DEP count mismatch: got {}, expected {} ({m_max}x{n_max})",
            vals.len(),
            expected
        )));
    }
    Ok(vals)
}

pub fn write_dep(path: &Path, vals: &[f64], m_max: usize, n_max: usize) -> Result<(), DepError> {
    if vals.len() != m_max * n_max {
        return Err(DepError::Parse(format!(
            "DEP write mismatch: len {} != {}x{}",
            vals.len(),
            m_max,
            n_max
        )));
    }
    let file = File::create(path)?;
    let mut w = BufWriter::new(file);
    for n in 0..n_max {
        let row = &vals[n * m_max..(n + 1) * m_max];
        for (m, v) in row.iter().enumerate() {
            if m > 0 && m % 12 == 0 {
                writeln!(w)?;
            }
            write!(w, " {v:>13.7E}")?;
        }
        writeln!(w)?;
    }
    w.flush()?;
    Ok(())
}

pub fn read_xyz(path: &Path) -> Result<Vec<[f64; 3]>, DepError> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut pts = Vec::new();
    for line_res in reader.lines() {
        let line = line_res?;
        let t = line.trim();
        if t.is_empty() || t.starts_with('*') || t.starts_with('#') {
            continue;
        }
        let cleaned = t.replace([',', ';'], " ");
        let cols: Vec<&str> = cleaned.split_whitespace().collect();
        if cols.len() >= 3 {
            if let (Ok(x), Ok(y), Ok(z)) = (
                cols[0].parse::<f64>(),
                cols[1].parse::<f64>(),
                cols[2].parse::<f64>(),
            ) {
                if x.is_finite() && y.is_finite() && z.is_finite() {
                    pts.push([x, y, z]);
                }
            }
        }
    }
    if pts.is_empty() {
        return Err(DepError::Parse("No valid XYZ samples found".to_string()));
    }
    Ok(pts)
}

/// Parallel IDW, nearest, or cell-averaging interpolation from XYZ samples onto `Delft3D` grid nodes.
#[must_use]
pub fn interpolate_xyz_to_grid(
    grid: &GridData,
    samples: &[[f64; 3]],
    method: &str,
    power: f64,
    search_radius: Option<f64>,
    max_neighbors: usize,
) -> Vec<f64> {
    let total = grid.m_max * grid.n_max;
    let r2_limit = search_radius.map(|r| r * r);
    let k_limit = max_neighbors.max(1);
    let use_nearest = method.eq_ignore_ascii_case("nearest");
    let use_cell_avg = method.eq_ignore_ascii_case("cell_average");

    (0..total)
        .into_par_iter()
        .map(|idx| {
            if grid.is_missing(idx) {
                return DEFAULT_MISSING_DEP;
            }
            let gx = grid.x[idx];
            let gy = grid.y[idx];

            if use_cell_avg {
                let n = idx / grid.m_max;
                let m = idx % grid.m_max;
                let mut local_r = 0.0_f64;
                let mut local_cnt = 0_usize;
                for (dn, dm) in [(-1_isize, 0_isize), (1, 0), (0, -1), (0, 1)] {
                    let nn = n.cast_signed() + dn;
                    let mm = m.cast_signed() + dm;
                    if nn >= 0
                        && nn < grid.n_max.cast_signed()
                        && mm >= 0
                        && mm < grid.m_max.cast_signed()
                    {
                        let nidx = nn.cast_unsigned() * grid.m_max + mm.cast_unsigned();
                        if !grid.is_missing(nidx) {
                            let dist =
                                ((grid.x[nidx] - gx).powi(2) + (grid.y[nidx] - gy).powi(2)).sqrt();
                            local_r += 0.5 * dist;
                            local_cnt += 1;
                        }
                    }
                }
                let eff_r2 = r2_limit.unwrap_or_else(|| {
                    let r = if local_cnt > 0 {
                        local_r / (local_cnt as f64)
                    } else {
                        10.0
                    };
                    r * r
                });
                let mut sum_z = 0.0_f64;
                let mut cnt_z = 0_usize;
                for &[sx, sy, sz] in samples {
                    let dx = gx - sx;
                    let dy = gy - sy;
                    if dx * dx + dy * dy <= eff_r2 {
                        sum_z += sz;
                        cnt_z += 1;
                    }
                }
                return if cnt_z > 0 {
                    sum_z / (cnt_z as f64)
                } else {
                    DEFAULT_MISSING_DEP
                };
            }

            if use_nearest {
                let mut best_d2 = f64::INFINITY;
                let mut best_z = DEFAULT_MISSING_DEP;
                for &[sx, sy, sz] in samples {
                    let dx = gx - sx;
                    let dy = gy - sy;
                    let d2 = dx * dx + dy * dy;
                    if let Some(lim) = r2_limit {
                        if d2 > lim {
                            continue;
                        }
                    }
                    if d2 < best_d2 {
                        best_d2 = d2;
                        best_z = sz;
                    }
                }
                return best_z;
            }

            // IDW with bounded top-K selection (O(K) memory per grid node)
            let mut candidates: Vec<(f64, f64)> = Vec::with_capacity(k_limit);
            let mut worst_idx = 0_usize;
            let mut worst_d2 = -1.0_f64;

            for &[sx, sy, sz] in samples {
                let dx = gx - sx;
                let dy = gy - sy;
                let d2 = dx * dx + dy * dy;
                if d2 < 1e-18 {
                    return sz;
                }
                if let Some(lim) = r2_limit {
                    if d2 > lim {
                        continue;
                    }
                }
                if candidates.len() < k_limit {
                    if d2 > worst_d2 {
                        worst_d2 = d2;
                        worst_idx = candidates.len();
                    }
                    candidates.push((d2, sz));
                } else if d2 < worst_d2 {
                    candidates[worst_idx] = (d2, sz);
                    worst_idx = 0;
                    worst_d2 = candidates[0].0;
                    for (i, &(cd2, _)) in candidates.iter().enumerate().skip(1) {
                        if cd2 > worst_d2 {
                            worst_d2 = cd2;
                            worst_idx = i;
                        }
                    }
                }
            }
            if candidates.is_empty() {
                return DEFAULT_MISSING_DEP;
            }
            let half_p = power * 0.5;
            let mut w_sum = 0.0_f64;
            let mut wz_sum = 0.0_f64;
            for (d2, sz) in candidates {
                let w = 1.0 / d2.powf(half_p);
                w_sum += w;
                wz_sum += w * sz;
            }
            if w_sum > 0.0 {
                wz_sum / w_sum
            } else {
                DEFAULT_MISSING_DEP
            }
        })
        .collect()
}

/// Fill missing (-999.0) or smooth active depth nodes using iterative 4-neighbor Laplace relaxation.
#[must_use]
pub fn smooth_or_fill_dep(
    vals: &[f64],
    m_max: usize,
    n_max: usize,
    iterations: usize,
    fill_only: bool,
    missing_val: f64,
) -> Vec<f64> {
    let mut curr = vals.to_vec();
    let mut next = curr.clone();
    let is_miss = |v: f64| !v.is_finite() || (v - missing_val).abs() < 1e-5;

    for _ in 0..iterations.max(1) {
        for n in 0..n_max {
            for m in 0..m_max {
                let idx = n * m_max + m;
                if fill_only && !is_miss(curr[idx]) {
                    next[idx] = curr[idx];
                    continue;
                }
                let mut sum = 0.0_f64;
                let mut cnt = 0_usize;
                for (dn, dm) in [(-1_isize, 0_isize), (1, 0), (0, -1), (0, 1)] {
                    let nn = n.cast_signed() + dn;
                    let mm = m.cast_signed() + dm;
                    if nn >= 0 && nn < n_max.cast_signed() && mm >= 0 && mm < m_max.cast_signed() {
                        let nb = curr[nn.cast_unsigned() * m_max + mm.cast_unsigned()];
                        if !is_miss(nb) {
                            sum += nb;
                            cnt += 1;
                        }
                    }
                }
                if cnt > 0 {
                    next[idx] = if is_miss(curr[idx]) {
                        sum / (cnt as f64)
                    } else {
                        0.5 * curr[idx] + 0.5 * (sum / (cnt as f64))
                    };
                } else {
                    next[idx] = curr[idx];
                }
            }
        }
        curr.copy_from_slice(&next);
    }
    curr
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_idw_and_dep_roundtrip() {
        let g = GridData::create_rectangular(0.0, 0.0, 10.0, 10.0, 4, 4, 0.0, "Cartesian");
        let samples = vec![
            [0.0, 0.0, 5.0],
            [30.0, 0.0, 15.0],
            [0.0, 30.0, 10.0],
            [30.0, 30.0, 20.0],
        ];
        let dep = interpolate_xyz_to_grid(&g, &samples, "idw", 2.0, None, 4);
        assert!((dep[0] - 5.0).abs() < 1e-9);
        assert!((dep[15] - 20.0).abs() < 1e-9);

        let tmp = std::env::temp_dir().join("delft3d_mcp_test_dep.dep");
        write_dep(&tmp, &dep, 4, 4).unwrap();
        let dep2 = read_dep(&tmp, 4, 4).unwrap();
        assert_eq!(dep2.len(), 16);
        let _ = std::fs::remove_file(tmp);
    }
}
