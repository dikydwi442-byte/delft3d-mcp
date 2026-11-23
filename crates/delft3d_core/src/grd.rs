use rayon::prelude::*;
use std::fs::File;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::Path;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum GrdError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Invalid GRD format: {0}")]
    Parse(String),
}

#[derive(Debug, Clone)]
pub struct GridData {
    pub coordinate_system: String,
    pub missing_value: f64,
    pub m_max: usize,
    pub n_max: usize,
    /// Flattened row-major (`n` in `0..n_max`, `m` in `0..m_max`): `index = n * m_max + m`
    pub x: Vec<f64>,
    pub y: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct GridQuality {
    pub m_max: usize,
    pub n_max: usize,
    pub active_cells: usize,
    pub max_abs_cos_ortho: f64,
    pub mean_abs_cos_ortho: f64,
    pub bad_ortho_count: usize,
    pub max_smoothness_m: f64,
    pub max_smoothness_n: f64,
    pub max_aspect_ratio: f64,
    pub min_dx: f64,
    pub max_dx: f64,
    pub min_dy: f64,
    pub max_dy: f64,
}

impl GridData {
    #[must_use]
    pub fn is_missing(&self, idx: usize) -> bool {
        let xv = self.x[idx];
        let yv = self.y[idx];
        !xv.is_finite()
            || !yv.is_finite()
            || ((xv - self.missing_value).abs() < 1e-9 && (yv - self.missing_value).abs() < 1e-9)
    }

    pub fn from_file(path: &Path) -> Result<Self, GrdError> {
        let file = File::open(path)?;
        let reader = BufReader::new(file);

        let mut coordinate_system = String::from("Cartesian");
        let mut missing_value = 0.0_f64;
        let mut m_max = 0_usize;
        let mut n_max = 0_usize;
        let mut dims_parsed = false;

        let mut raw_x: Vec<f64> = Vec::new();
        let mut raw_y: Vec<f64> = Vec::new();
        let mut reading_y = false;
        let mut current_row_vals: Vec<f64> = Vec::new();
        let mut current_row_idx: Option<usize> = None;

        for line_res in reader.lines() {
            let line = line_res?;
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('*') || trimmed.starts_with('#') {
                continue;
            }
            if !dims_parsed {
                if let Some(rest) = trimmed.strip_prefix("Coordinate System") {
                    if let Some((_, val)) = rest.split_once('=') {
                        coordinate_system = val.trim().to_string();
                    }
                    continue;
                }
                if let Some(rest) = trimmed.strip_prefix("Missing Value") {
                    if let Some((_, val)) = rest.split_once('=') {
                        missing_value = val.trim().parse::<f64>().map_err(|_| {
                            GrdError::Parse(format!("Invalid Missing Value: {val}"))
                        })?;
                    }
                    continue;
                }
                let parts: Vec<&str> = trimmed.split_whitespace().collect();
                if parts.len() >= 2 {
                    if let (Ok(m), Ok(n)) = (parts[0].parse::<usize>(), parts[1].parse::<usize>()) {
                        if m == 0 || n == 0 {
                            return Err(GrdError::Parse(format!(
                                "Invalid grid dimensions: {m} x {n}"
                            )));
                        }
                        m_max = m;
                        n_max = n;
                        dims_parsed = true;
                        raw_x.resize(m_max * n_max, missing_value);
                        raw_y.resize(m_max * n_max, missing_value);
                        continue;
                    }
                }
                continue;
            }

            // Skip origin line like "0 0 0" between dimensions and first ETA=
            if current_row_idx.is_none() && !trimmed.starts_with("ETA=") {
                continue;
            }

            if let Some(after_eta) = trimmed.strip_prefix("ETA=") {
                if let Some(prev_n) = current_row_idx {
                    Self::commit_eta_row(
                        prev_n,
                        m_max,
                        n_max,
                        &current_row_vals,
                        if reading_y { &mut raw_y } else { &mut raw_x },
                    )?;
                    if prev_n == n_max {
                        reading_y = true;
                    }
                }
                let mut iter = after_eta.split_whitespace();
                let n_str = iter
                    .next()
                    .ok_or_else(|| GrdError::Parse("Missing ETA row index".to_string()))?;
                let n_idx = n_str
                    .parse::<usize>()
                    .map_err(|_| GrdError::Parse(format!("Invalid ETA index: {n_str}")))?;
                if n_idx == 1 && current_row_idx == Some(n_max) {
                    reading_y = true;
                }
                current_row_idx = Some(n_idx);
                current_row_vals.clear();
                for tok in iter {
                    current_row_vals.push(
                        tok.parse::<f64>()
                            .map_err(|_| GrdError::Parse(format!("Invalid float: {tok}")))?,
                    );
                }
            } else {
                for tok in trimmed.split_whitespace() {
                    current_row_vals.push(
                        tok.parse::<f64>()
                            .map_err(|_| GrdError::Parse(format!("Invalid float: {tok}")))?,
                    );
                }
            }
        }

        if let Some(last_n) = current_row_idx {
            Self::commit_eta_row(
                last_n,
                m_max,
                n_max,
                &current_row_vals,
                if reading_y { &mut raw_y } else { &mut raw_x },
            )?;
        }

        if !dims_parsed || raw_x.len() != m_max * n_max || raw_y.len() != m_max * n_max {
            return Err(GrdError::Parse(
                "Incomplete GRD file: missing dimensions or coordinates".to_string(),
            ));
        }

        Ok(Self {
            coordinate_system,
            missing_value,
            m_max,
            n_max,
            x: raw_x,
            y: raw_y,
        })
    }

    fn commit_eta_row(
        n_1based: usize,
        m_max: usize,
        n_max: usize,
        vals: &[f64],
        target: &mut [f64],
    ) -> Result<(), GrdError> {
        if n_1based == 0 || n_1based > n_max {
            return Err(GrdError::Parse(format!(
                "ETA index {n_1based} out of bounds 1..={n_max}"
            )));
        }
        if vals.len() != m_max {
            return Err(GrdError::Parse(format!(
                "ETA={n_1based} has {} values, expected {m_max}",
                vals.len()
            )));
        }
        let offset = (n_1based - 1) * m_max;
        target[offset..offset + m_max].copy_from_slice(vals);
        Ok(())
    }

    pub fn to_file(&self, path: &Path) -> Result<(), GrdError> {
        let file = File::create(path)?;
        let mut w = BufWriter::new(file);
        writeln!(w, "* Delft3D RGFGRID file generated by delft3d-mcp")?;
        writeln!(w, "Coordinate System = {}", self.coordinate_system)?;
        writeln!(w, "Missing Value = {:>20.12E}", self.missing_value)?;
        writeln!(w, "{:>8} {:>8}", self.m_max, self.n_max)?;
        writeln!(w, "       0       0       0")?;

        for arr in [&self.x, &self.y] {
            for n in 0..self.n_max {
                write!(w, " ETA={:>5}", n + 1)?;
                let row = &arr[n * self.m_max..(n + 1) * self.m_max];
                for (m, val) in row.iter().enumerate() {
                    if m > 0 && m % 5 == 0 {
                        writeln!(w)?;
                        write!(w, "          ")?;
                    }
                    write!(w, " {val:>23.16E}")?;
                }
                writeln!(w)?;
            }
        }
        w.flush()?;
        Ok(())
    }

    #[must_use]
    #[allow(clippy::too_many_arguments)]
    pub fn create_rectangular(
        x0: f64,
        y0: f64,
        dx: f64,
        dy: f64,
        m_max: usize,
        n_max: usize,
        angle_deg: f64,
        coordinate_system: &str,
    ) -> Self {
        let theta = angle_deg.to_radians();
        let cos_t = theta.cos();
        let sin_t = theta.sin();
        let total = m_max * n_max;
        let mut x = vec![0.0; total];
        let mut y = vec![0.0; total];

        for n in 0..n_max {
            let yn = (n as f64) * dy;
            for m in 0..m_max {
                let xm = (m as f64) * dx;
                let idx = n * m_max + m;
                x[idx] = x0 + xm * cos_t - yn * sin_t;
                y[idx] = y0 + xm * sin_t + yn * cos_t;
            }
        }

        Self {
            coordinate_system: coordinate_system.to_string(),
            missing_value: -999.999,
            m_max,
            n_max,
            x,
            y,
        }
    }

    #[must_use]
    pub fn refine(&self, factor_m: usize, factor_n: usize) -> Self {
        let fm = factor_m.max(1);
        let fn_ = factor_n.max(1);
        let new_m = (self.m_max - 1) * fm + 1;
        let new_n = (self.n_max - 1) * fn_ + 1;
        let mut new_x = vec![self.missing_value; new_m * new_n];
        let mut new_y = vec![self.missing_value; new_m * new_n];

        for n in 0..self.n_max - 1 {
            for m in 0..self.m_max - 1 {
                let i00 = n * self.m_max + m;
                let i10 = n * self.m_max + (m + 1);
                let i01 = (n + 1) * self.m_max + m;
                let i11 = (n + 1) * self.m_max + (m + 1);
                if self.is_missing(i00)
                    || self.is_missing(i10)
                    || self.is_missing(i01)
                    || self.is_missing(i11)
                {
                    continue;
                }
                for dn in 0..=fn_ {
                    let v = (dn as f64) / (fn_ as f64);
                    for dm in 0..=fm {
                        let u = (dm as f64) / (fm as f64);
                        let w00 = (1.0 - u) * (1.0 - v);
                        let w10 = u * (1.0 - v);
                        let w01 = (1.0 - u) * v;
                        let w11 = u * v;
                        let dst = (n * fn_ + dn) * new_m + (m * fm + dm);
                        new_x[dst] = w00 * self.x[i00]
                            + w10 * self.x[i10]
                            + w01 * self.x[i01]
                            + w11 * self.x[i11];
                        new_y[dst] = w00 * self.y[i00]
                            + w10 * self.y[i10]
                            + w01 * self.y[i01]
                            + w11 * self.y[i11];
                    }
                }
            }
        }

        Self {
            coordinate_system: self.coordinate_system.clone(),
            missing_value: self.missing_value,
            m_max: new_m,
            n_max: new_n,
            x: new_x,
            y: new_y,
        }
    }

    #[must_use]
    pub fn quality_metrics(&self) -> GridQuality {
        let cell_stats: Vec<(f64, f64, f64, f64, f64)> = (0..self.n_max.saturating_sub(1))
            .into_par_iter()
            .flat_map_iter(|n| {
                (0..self.m_max.saturating_sub(1)).filter_map(move |m| {
                    let i00 = n * self.m_max + m;
                    let i10 = n * self.m_max + (m + 1);
                    let i01 = (n + 1) * self.m_max + m;
                    let i11 = (n + 1) * self.m_max + (m + 1);
                    if self.is_missing(i00)
                        || self.is_missing(i10)
                        || self.is_missing(i01)
                        || self.is_missing(i11)
                    {
                        return None;
                    }
                    let ax = self.x[i10] - self.x[i00];
                    let ay = self.y[i10] - self.y[i00];
                    let bx = self.x[i01] - self.x[i00];
                    let by = self.y[i01] - self.y[i00];
                    let len_a = (ax * ax + ay * ay).sqrt();
                    let len_b = (bx * bx + by * by).sqrt();
                    if len_a < 1e-12 || len_b < 1e-12 {
                        return None;
                    }
                    let cos_ortho = ((ax * bx + ay * by) / (len_a * len_b))
                        .abs()
                        .clamp(0.0, 1.0);
                    let aspect = (len_a / len_b).max(len_b / len_a);
                    Some((cos_ortho, len_a, len_b, aspect, 1.0))
                })
            })
            .collect();

        let active_cells = cell_stats.len();
        let mut max_cos = 0.0_f64;
        let mut sum_cos = 0.0_f64;
        let mut bad_ortho = 0_usize;
        let mut max_aspect = 0.0_f64;
        let mut min_dx = f64::INFINITY;
        let mut max_dx = 0.0_f64;
        let mut min_dy = f64::INFINITY;
        let mut max_dy = 0.0_f64;

        for (c, dx, dy, asp, _) in &cell_stats {
            if *c > max_cos {
                max_cos = *c;
            }
            sum_cos += *c;
            if *c > 0.04 {
                bad_ortho += 1;
            }
            if *asp > max_aspect {
                max_aspect = *asp;
            }
            min_dx = min_dx.min(*dx);
            max_dx = max_dx.max(*dx);
            min_dy = min_dy.min(*dy);
            max_dy = max_dy.max(*dy);
        }

        // M-direction and N-direction smoothness
        let mut max_sm_m = 1.0_f64;
        let mut max_sm_n = 1.0_f64;
        for n in 0..self.n_max {
            for m in 0..self.m_max.saturating_sub(2) {
                let i0 = n * self.m_max + m;
                let i1 = i0 + 1;
                let i2 = i0 + 2;
                if self.is_missing(i0) || self.is_missing(i1) || self.is_missing(i2) {
                    continue;
                }
                let d1 =
                    ((self.x[i1] - self.x[i0]).powi(2) + (self.y[i1] - self.y[i0]).powi(2)).sqrt();
                let d2 =
                    ((self.x[i2] - self.x[i1]).powi(2) + (self.y[i2] - self.y[i1]).powi(2)).sqrt();
                if d1 > 1e-12 && d2 > 1e-12 {
                    max_sm_m = max_sm_m.max((d1 / d2).max(d2 / d1));
                }
            }
        }
        for n in 0..self.n_max.saturating_sub(2) {
            for m in 0..self.m_max {
                let i0 = n * self.m_max + m;
                let i1 = (n + 1) * self.m_max + m;
                let i2 = (n + 2) * self.m_max + m;
                if self.is_missing(i0) || self.is_missing(i1) || self.is_missing(i2) {
                    continue;
                }
                let d1 =
                    ((self.x[i1] - self.x[i0]).powi(2) + (self.y[i1] - self.y[i0]).powi(2)).sqrt();
                let d2 =
                    ((self.x[i2] - self.x[i1]).powi(2) + (self.y[i2] - self.y[i1]).powi(2)).sqrt();
                if d1 > 1e-12 && d2 > 1e-12 {
                    max_sm_n = max_sm_n.max((d1 / d2).max(d2 / d1));
                }
            }
        }

        GridQuality {
            m_max: self.m_max,
            n_max: self.n_max,
            active_cells,
            max_abs_cos_ortho: max_cos,
            mean_abs_cos_ortho: if active_cells > 0 {
                sum_cos / (active_cells as f64)
            } else {
                0.0
            },
            bad_ortho_count: bad_ortho,
            max_smoothness_m: max_sm_m,
            max_smoothness_n: max_sm_n,
            max_aspect_ratio: max_aspect,
            min_dx: if min_dx.is_finite() { min_dx } else { 0.0 },
            max_dx,
            min_dy: if min_dy.is_finite() { min_dy } else { 0.0 },
            max_dy,
        }
    }

    /// Generate a curvilinear grid via Transfinite Interpolation (Coons patch) from 4 boundary curves.
    /// `south` and `north` progress from M=0 to M=m_max-1; `west` and `east` progress from N=0 to N=n_max-1.
    pub fn create_curvilinear_tfi(
        south: &[[f64; 2]],
        north: &[[f64; 2]],
        west: &[[f64; 2]],
        east: &[[f64; 2]],
        m_max: usize,
        n_max: usize,
        coordinate_system: &str,
    ) -> Result<Self, GrdError> {
        if m_max < 2 || n_max < 2 {
            return Err(GrdError::Parse("m_max and n_max must be >= 2".to_string()));
        }
        let south_c = resample_polyline(south, m_max)?;
        let north_c = resample_polyline(north, m_max)?;
        let west_c = resample_polyline(west, n_max)?;
        let east_c = resample_polyline(east, n_max)?;

        let p00 = south_c[0];
        let p10 = south_c[m_max - 1];
        let p01 = north_c[0];
        let p11 = north_c[m_max - 1];

        let mut x_out = vec![0.0_f64; m_max * n_max];
        let mut y_out = vec![0.0_f64; m_max * n_max];

        for n_idx in 0..n_max {
            let eta = (n_idx as f64) / ((n_max - 1) as f64);
            for m_idx in 0..m_max {
                let xi = (m_idx as f64) / ((m_max - 1) as f64);
                let idx = n_idx * m_max + m_idx;

                let px = (1.0 - eta) * south_c[m_idx][0]
                    + eta * north_c[m_idx][0]
                    + (1.0 - xi) * west_c[n_idx][0]
                    + xi * east_c[n_idx][0]
                    - ((1.0 - xi) * (1.0 - eta) * p00[0]
                        + xi * (1.0 - eta) * p10[0]
                        + (1.0 - xi) * eta * p01[0]
                        + xi * eta * p11[0]);
                let py = (1.0 - eta) * south_c[m_idx][1]
                    + eta * north_c[m_idx][1]
                    + (1.0 - xi) * west_c[n_idx][1]
                    + xi * east_c[n_idx][1]
                    - ((1.0 - xi) * (1.0 - eta) * p00[1]
                        + xi * (1.0 - eta) * p10[1]
                        + (1.0 - xi) * eta * p01[1]
                        + xi * eta * p11[1]);
                x_out[idx] = px;
                y_out[idx] = py;
            }
        }

        Ok(Self {
            coordinate_system: coordinate_system.to_string(),
            missing_value: -999.999,
            m_max,
            n_max,
            x: x_out,
            y: y_out,
        })
    }

    /// Generate a curvilinear grid between two opposing splines (e.g. river banks or coast/offshore).
    pub fn create_from_two_splines(
        south_spline: &[[f64; 2]],
        north_spline: &[[f64; 2]],
        m_max: usize,
        n_max: usize,
        coordinate_system: &str,
    ) -> Result<Self, GrdError> {
        let s = resample_polyline(south_spline, m_max)?;
        let n_curve = resample_polyline(north_spline, m_max)?;
        let west = vec![s[0], n_curve[0]];
        let east = vec![s[m_max - 1], n_curve[m_max - 1]];
        Self::create_curvilinear_tfi(&s, &n_curve, &west, &east, m_max, n_max, coordinate_system)
    }

    /// Winslow elliptic grid orthogonalizer & smoother on active internal grid nodes.
    pub fn orthogonalize_elliptic(&mut self, iterations: usize, omega: f64) {
        let relax = omega.clamp(0.1, 1.5);
        let mut next_x = self.x.clone();
        let mut next_y = self.y.clone();

        for _ in 0..iterations.max(1) {
            for n in 1..self.n_max.saturating_sub(1) {
                for m in 1..self.m_max.saturating_sub(1) {
                    let c = n * self.m_max + m;
                    let e = c + 1;
                    let w = c - 1;
                    let nn = (n + 1) * self.m_max + m;
                    let ss = (n - 1) * self.m_max + m;
                    let ne = nn + 1;
                    let nw = nn - 1;
                    let se = ss + 1;
                    let sw = ss - 1;

                    if [c, e, w, nn, ss, ne, nw, se, sw]
                        .iter()
                        .any(|&i| self.is_missing(i))
                    {
                        continue;
                    }

                    let x_xi = 0.5 * (self.x[e] - self.x[w]);
                    let y_xi = 0.5 * (self.y[e] - self.y[w]);
                    let x_eta = 0.5 * (self.x[nn] - self.x[ss]);
                    let y_eta = 0.5 * (self.y[nn] - self.y[ss]);

                    let alpha = x_eta * x_eta + y_eta * y_eta;
                    let beta = x_xi * x_eta + y_xi * y_eta;
                    let gamma = x_xi * x_xi + y_xi * y_xi;
                    let denom = 2.0 * (alpha + gamma);
                    if denom < 1e-18 {
                        continue;
                    }

                    let target_x = (alpha * (self.x[e] + self.x[w])
                        + gamma * (self.x[nn] + self.x[ss])
                        - 0.5 * beta * (self.x[ne] - self.x[nw] - self.x[se] + self.x[sw]))
                        / denom;
                    let target_y = (alpha * (self.y[e] + self.y[w])
                        + gamma * (self.y[nn] + self.y[ss])
                        - 0.5 * beta * (self.y[ne] - self.y[nw] - self.y[se] + self.y[sw]))
                        / denom;

                    next_x[c] = self.x[c] + relax * (target_x - self.x[c]);
                    next_y[c] = self.y[c] + relax * (target_y - self.y[c]);
                }
            }
            self.x.copy_from_slice(&next_x);
            self.y.copy_from_slice(&next_y);
        }
    }
}

fn resample_polyline(pts: &[[f64; 2]], count: usize) -> Result<Vec<[f64; 2]>, GrdError> {
    if pts.len() < 2 {
        return Err(GrdError::Parse(
            "Boundary spline needs at least 2 points".to_string(),
        ));
    }
    if count < 2 {
        return Err(GrdError::Parse(
            "Target sample count must be >= 2".to_string(),
        ));
    }
    let mut cum = Vec::with_capacity(pts.len());
    cum.push(0.0_f64);
    for i in 1..pts.len() {
        let ds = ((pts[i][0] - pts[i - 1][0]).powi(2) + (pts[i][1] - pts[i - 1][1]).powi(2)).sqrt();
        cum.push(cum[i - 1] + ds);
    }
    let total = *cum.last().unwrap_or(&0.0);
    if total < 1e-12 {
        return Err(GrdError::Parse(
            "Degenerate zero-length boundary spline".to_string(),
        ));
    }

    let mut out = Vec::with_capacity(count);
    let mut seg = 0_usize;
    for k in 0..count {
        let s_target = total * (k as f64) / ((count - 1) as f64);
        while seg + 1 < cum.len() - 1 && cum[seg + 1] < s_target {
            seg += 1;
        }
        let seg_len = cum[seg + 1] - cum[seg];
        let t = if seg_len > 1e-12 {
            ((s_target - cum[seg]) / seg_len).clamp(0.0, 1.0)
        } else {
            0.0
        };
        out.push([
            pts[seg][0] + t * (pts[seg + 1][0] - pts[seg][0]),
            pts[seg][1] + t * (pts[seg + 1][1] - pts[seg][1]),
        ]);
    }
    Ok(out)
}

pub fn read_enclosure(path: &Path) -> Result<Vec<(usize, usize)>, GrdError> {
    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut pts = Vec::new();
    for line_res in reader.lines() {
        let line = line_res?;
        let t = line.trim();
        if t.is_empty() || t.starts_with('*') || t.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = t.split_whitespace().collect();
        if parts.len() >= 2 {
            if let (Ok(m), Ok(n)) = (parts[0].parse::<usize>(), parts[1].parse::<usize>()) {
                pts.push((m, n));
            }
        }
    }
    Ok(pts)
}

pub fn write_enclosure(path: &Path, points: &[(usize, usize)]) -> Result<(), GrdError> {
    let file = File::create(path)?;
    let mut w = BufWriter::new(file);
    for (m, n) in points {
        writeln!(w, "{m:>6}{n:>6}")?;
    }
    w.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rectangular_grid_and_quality() {
        let g = GridData::create_rectangular(0.0, 0.0, 100.0, 100.0, 6, 6, 0.0, "Cartesian");
        let q = g.quality_metrics();
        assert_eq!(q.active_cells, 25);
        assert!(q.max_abs_cos_ortho < 1e-12);
        assert!((q.max_aspect_ratio - 1.0).abs() < 1e-12);
    }

    #[test]
    fn test_grid_roundtrip_and_refine() {
        let g = GridData::create_rectangular(10.0, 20.0, 50.0, 25.0, 4, 3, 15.0, "Cartesian");
        let tmp = std::env::temp_dir().join("delft3d_mcp_test_grid.grd");
        g.to_file(&tmp).unwrap();
        let g2 = GridData::from_file(&tmp).unwrap();
        assert_eq!(g2.m_max, 4);
        assert_eq!(g2.n_max, 3);
        assert!((g2.x[0] - 10.0).abs() < 1e-9);
        let refined = g2.refine(2, 2);
        assert_eq!(refined.m_max, 7);
        assert_eq!(refined.n_max, 5);
        let _ = std::fs::remove_file(tmp);
    }

    #[test]
    fn test_curvilinear_tfi_and_elliptic_ortho() {
        let s = [[0.0, 0.0], [50.0, 10.0], [100.0, 0.0]];
        let n = [[0.0, 100.0], [50.0, 110.0], [100.0, 100.0]];
        let mut g = GridData::create_from_two_splines(&s, &n, 9, 9, "Cartesian").unwrap();
        let before = g.quality_metrics();
        g.orthogonalize_elliptic(40, 0.8);
        let after = g.quality_metrics();
        assert_eq!(after.active_cells, 64);
        assert!(after.mean_abs_cos_ortho <= before.mean_abs_cos_ortho + 1e-6);
    }
}
