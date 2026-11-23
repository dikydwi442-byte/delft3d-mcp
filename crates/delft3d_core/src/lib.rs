pub mod dep;
pub mod grd;
pub mod nefis;
pub mod tailer;
pub mod tekal;

use crate::dep::{interpolate_xyz_to_grid, read_dep, read_xyz, smooth_or_fill_dep, write_dep};
use crate::grd::{read_enclosure, write_enclosure, GridData};
use crate::nefis::{NefisFile, NefisValues};
use crate::tailer::scan_diag_log;
use crate::tekal::{read_tekal, write_tekal, TekalFeature};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::path::Path;

fn to_py_err<E: std::fmt::Display>(err: E) -> PyErr {
    PyRuntimeError::new_err(err.to_string())
}

#[pyfunction]
fn read_grd(py: Python<'_>, path: &str) -> PyResult<PyObject> {
    let g = GridData::from_file(Path::new(path)).map_err(to_py_err)?;
    let d = PyDict::new(py);
    d.set_item("coordinate_system", g.coordinate_system)?;
    d.set_item("missing_value", g.missing_value)?;
    d.set_item("m_max", g.m_max)?;
    d.set_item("n_max", g.n_max)?;
    d.set_item("x", g.x)?;
    d.set_item("y", g.y)?;
    Ok(d.into())
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn write_grd(
    path: &str,
    m_max: usize,
    n_max: usize,
    x: Vec<f64>,
    y: Vec<f64>,
    coordinate_system: &str,
    missing_value: f64,
) -> PyResult<()> {
    if x.len() != m_max * n_max || y.len() != m_max * n_max {
        return Err(PyValueError::new_err(
            "x/y length does not match m_max * n_max",
        ));
    }
    let g = GridData {
        coordinate_system: coordinate_system.to_string(),
        missing_value,
        m_max,
        n_max,
        x,
        y,
    };
    g.to_file(Path::new(path)).map_err(to_py_err)
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn create_rect_grd(
    path: &str,
    x0: f64,
    y0: f64,
    dx: f64,
    dy: f64,
    m_max: usize,
    n_max: usize,
    angle_deg: f64,
    coordinate_system: &str,
) -> PyResult<()> {
    let g =
        GridData::create_rectangular(x0, y0, dx, dy, m_max, n_max, angle_deg, coordinate_system);
    g.to_file(Path::new(path)).map_err(to_py_err)
}

#[pyfunction]
fn refine_grd(
    src_path: &str,
    dst_path: &str,
    factor_m: usize,
    factor_n: usize,
) -> PyResult<(usize, usize)> {
    let g = GridData::from_file(Path::new(src_path)).map_err(to_py_err)?;
    let refined = g.refine(factor_m, factor_n);
    refined.to_file(Path::new(dst_path)).map_err(to_py_err)?;
    Ok((refined.m_max, refined.n_max))
}

#[pyfunction]
#[pyo3(signature = (
    out_grd_path,
    south,
    north,
    m_max,
    n_max,
    west=None,
    east=None,
    coordinate_system="Cartesian",
    ortho_iterations=30
))]
#[allow(clippy::too_many_arguments)]
fn create_curvilinear_grd(
    py: Python<'_>,
    out_grd_path: &str,
    south: Vec<[f64; 2]>,
    north: Vec<[f64; 2]>,
    m_max: usize,
    n_max: usize,
    west: Option<Vec<[f64; 2]>>,
    east: Option<Vec<[f64; 2]>>,
    coordinate_system: &str,
    ortho_iterations: usize,
) -> PyResult<PyObject> {
    let mut g = match (west, east) {
        (Some(w), Some(e)) => GridData::create_curvilinear_tfi(
            &south,
            &north,
            &w,
            &e,
            m_max,
            n_max,
            coordinate_system,
        ),
        _ => GridData::create_from_two_splines(&south, &north, m_max, n_max, coordinate_system),
    }
    .map_err(to_py_err)?;
    if ortho_iterations > 0 {
        g.orthogonalize_elliptic(ortho_iterations, 0.8);
    }
    g.to_file(Path::new(out_grd_path)).map_err(to_py_err)?;
    let q = g.quality_metrics();
    let d = PyDict::new(py);
    d.set_item("m_max", q.m_max)?;
    d.set_item("n_max", q.n_max)?;
    d.set_item("active_cells", q.active_cells)?;
    d.set_item("max_abs_cos_ortho", q.max_abs_cos_ortho)?;
    d.set_item("mean_abs_cos_ortho", q.mean_abs_cos_ortho)?;
    Ok(d.into())
}

#[pyfunction]
#[pyo3(signature = (src_grd_path, dst_grd_path, iterations=50, omega=0.8))]
fn orthogonalize_grd(
    py: Python<'_>,
    src_grd_path: &str,
    dst_grd_path: &str,
    iterations: usize,
    omega: f64,
) -> PyResult<PyObject> {
    let mut g = GridData::from_file(Path::new(src_grd_path)).map_err(to_py_err)?;
    let before = g.quality_metrics();
    g.orthogonalize_elliptic(iterations, omega);
    g.to_file(Path::new(dst_grd_path)).map_err(to_py_err)?;
    let after = g.quality_metrics();
    let d = PyDict::new(py);
    d.set_item("before_max_abs_cos", before.max_abs_cos_ortho)?;
    d.set_item("after_max_abs_cos", after.max_abs_cos_ortho)?;
    d.set_item("before_mean_abs_cos", before.mean_abs_cos_ortho)?;
    d.set_item("after_mean_abs_cos", after.mean_abs_cos_ortho)?;
    Ok(d.into())
}

#[pyfunction]
fn read_tekal_file(py: Python<'_>, path: &str) -> PyResult<Vec<PyObject>> {
    let feats = read_tekal(Path::new(path)).map_err(to_py_err)?;
    let mut out = Vec::with_capacity(feats.len());
    for f in feats {
        let d = PyDict::new(py);
        d.set_item("name", f.name)?;
        d.set_item("points", f.points)?;
        out.push(d.into());
    }
    Ok(out)
}

#[pyfunction]
fn write_tekal_file(path: &str, features: Vec<(String, Vec<[f64; 2]>)>) -> PyResult<()> {
    let items: Vec<TekalFeature> = features
        .into_iter()
        .map(|(name, points)| TekalFeature { name, points })
        .collect();
    write_tekal(Path::new(path), &items).map_err(to_py_err)
}

#[pyfunction]
#[pyo3(signature = (src_grd_path, dst_grd_path, dx=0.0, dy=0.0, scale_x=1.0, scale_y=1.0, rotate_deg=0.0))]
fn transform_grd(
    src_grd_path: &str,
    dst_grd_path: &str,
    dx: f64,
    dy: f64,
    scale_x: f64,
    scale_y: f64,
    rotate_deg: f64,
) -> PyResult<()> {
    let mut g = GridData::from_file(Path::new(src_grd_path)).map_err(to_py_err)?;
    g.transform(dx, dy, scale_x, scale_y, rotate_deg);
    g.to_file(Path::new(dst_grd_path)).map_err(to_py_err)
}

#[pyfunction]
#[pyo3(signature = (src_grd_path, dst_grd_path, m1, m2, n1, n2, interior=true))]
#[allow(clippy::too_many_arguments)]
fn mask_grd_block(
    src_grd_path: &str,
    dst_grd_path: &str,
    m1: usize,
    m2: usize,
    n1: usize,
    n2: usize,
    interior: bool,
) -> PyResult<usize> {
    let mut g = GridData::from_file(Path::new(src_grd_path)).map_err(to_py_err)?;
    let cnt = g.mask_block(m1, m2, n1, n2, interior);
    g.to_file(Path::new(dst_grd_path)).map_err(to_py_err)?;
    Ok(cnt)
}

#[pyfunction]
fn read_xyz_file(path: &str) -> PyResult<Vec<[f64; 3]>> {
    read_xyz(Path::new(path)).map_err(to_py_err)
}

#[pyfunction]
fn check_grd_quality(py: Python<'_>, path: &str) -> PyResult<PyObject> {
    let g = GridData::from_file(Path::new(path)).map_err(to_py_err)?;
    let q = g.quality_metrics();
    let d = PyDict::new(py);
    d.set_item("m_max", q.m_max)?;
    d.set_item("n_max", q.n_max)?;
    d.set_item("active_cells", q.active_cells)?;
    d.set_item("max_abs_cos_ortho", q.max_abs_cos_ortho)?;
    d.set_item("mean_abs_cos_ortho", q.mean_abs_cos_ortho)?;
    d.set_item("bad_ortho_count", q.bad_ortho_count)?;
    d.set_item("max_smoothness_m", q.max_smoothness_m)?;
    d.set_item("max_smoothness_n", q.max_smoothness_n)?;
    d.set_item("max_aspect_ratio", q.max_aspect_ratio)?;
    d.set_item("min_dx", q.min_dx)?;
    d.set_item("max_dx", q.max_dx)?;
    d.set_item("min_dy", q.min_dy)?;
    d.set_item("max_dy", q.max_dy)?;
    Ok(d.into())
}

#[pyfunction]
fn read_enc(path: &str) -> PyResult<Vec<(usize, usize)>> {
    read_enclosure(Path::new(path)).map_err(to_py_err)
}

#[pyfunction]
fn write_enc(path: &str, points: Vec<(usize, usize)>) -> PyResult<()> {
    write_enclosure(Path::new(path), &points).map_err(to_py_err)
}

#[pyfunction]
fn read_dep_file(path: &str, m_max: usize, n_max: usize) -> PyResult<Vec<f64>> {
    read_dep(Path::new(path), m_max, n_max).map_err(to_py_err)
}

#[pyfunction]
fn write_dep_file(path: &str, values: Vec<f64>, m_max: usize, n_max: usize) -> PyResult<()> {
    write_dep(Path::new(path), &values, m_max, n_max).map_err(to_py_err)
}

#[pyfunction]
#[pyo3(signature = (grd_path, xyz_path, out_dep_path, method="idw", power=2.0, search_radius=None, max_neighbors=8))]
fn interpolate_xyz(
    grd_path: &str,
    xyz_path: &str,
    out_dep_path: &str,
    method: &str,
    power: f64,
    search_radius: Option<f64>,
    max_neighbors: usize,
) -> PyResult<usize> {
    let g = GridData::from_file(Path::new(grd_path)).map_err(to_py_err)?;
    let pts = read_xyz(Path::new(xyz_path)).map_err(to_py_err)?;
    let dep = interpolate_xyz_to_grid(&g, &pts, method, power, search_radius, max_neighbors);
    write_dep(Path::new(out_dep_path), &dep, g.m_max, g.n_max).map_err(to_py_err)?;
    Ok(dep.len())
}

#[pyfunction]
#[pyo3(signature = (dep_path, out_dep_path, m_max, n_max, iterations=5, fill_only=true, missing_val=-999.0))]
fn smooth_dep_file(
    dep_path: &str,
    out_dep_path: &str,
    m_max: usize,
    n_max: usize,
    iterations: usize,
    fill_only: bool,
    missing_val: f64,
) -> PyResult<()> {
    let vals = read_dep(Path::new(dep_path), m_max, n_max).map_err(to_py_err)?;
    let out = smooth_or_fill_dep(&vals, m_max, n_max, iterations, fill_only, missing_val);
    write_dep(Path::new(out_dep_path), &out, m_max, n_max).map_err(to_py_err)
}

#[pyfunction]
#[pyo3(signature = (dat_path, def_path=None))]
fn nefis_list_groups(
    py: Python<'_>,
    dat_path: &str,
    def_path: Option<&str>,
) -> PyResult<Vec<PyObject>> {
    let nf = NefisFile::open(Path::new(dat_path), def_path.map(Path::new)).map_err(to_py_err)?;
    let mut res = Vec::new();
    for g in nf.list_groups() {
        let d = PyDict::new(py);
        d.set_item("name", g.name)?;
        d.set_item("def_name", g.def_name)?;
        d.set_item("cel_name", g.cel_name)?;
        d.set_item("var_dim", g.var_dim)?;
        d.set_item("dims", g.dims)?;
        d.set_item("elements", g.elements)?;
        res.push(d.into());
    }
    Ok(res)
}

#[pyfunction]
#[pyo3(signature = (dat_path, group_name, element_name, def_path=None, vd_start=None, vd_end=None))]
fn nefis_read_element(
    py: Python<'_>,
    dat_path: &str,
    group_name: &str,
    element_name: &str,
    def_path: Option<&str>,
    vd_start: Option<usize>,
    vd_end: Option<usize>,
) -> PyResult<PyObject> {
    let nf = NefisFile::open(Path::new(dat_path), def_path.map(Path::new)).map_err(to_py_err)?;
    let slice = match (vd_start, vd_end) {
        (Some(s), Some(e)) => Some((s, e)),
        (Some(s), None) => Some((s, s + 1)),
        _ => None,
    };
    let ds = nf
        .read_element(group_name, element_name, slice)
        .map_err(to_py_err)?;
    let d = PyDict::new(py);
    d.set_item("group", ds.group)?;
    d.set_item("element", ds.element)?;
    d.set_item("units", ds.units)?;
    d.set_item("description", ds.description)?;
    d.set_item("shape", ds.shape)?;
    match ds.values {
        NefisValues::Float64(v) => d.set_item("values", v)?,
        NefisValues::Int64(v) => d.set_item("values", v)?,
        NefisValues::Strings(v) => d.set_item("values", v)?,
    }
    Ok(d.into())
}

#[pyfunction]
#[pyo3(signature = (path, tail_n=50, max_scan_bytes=4_194_304))]
fn tail_diag_log(
    py: Python<'_>,
    path: &str,
    tail_n: usize,
    max_scan_bytes: u64,
) -> PyResult<PyObject> {
    let s = scan_diag_log(Path::new(path), tail_n, max_scan_bytes).map_err(to_py_err)?;
    let d = PyDict::new(py);
    d.set_item("total_bytes", s.total_bytes)?;
    d.set_item("tail_lines", s.tail_lines)?;
    d.set_item("warning_count", s.warning_count)?;
    d.set_item("error_count", s.error_count)?;
    d.set_item("warnings", s.warnings)?;
    d.set_item("errors", s.errors)?;
    d.set_item("last_progress_line", s.last_progress_line)?;
    Ok(d.into())
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(read_grd, m)?)?;
    m.add_function(wrap_pyfunction!(write_grd, m)?)?;
    m.add_function(wrap_pyfunction!(create_rect_grd, m)?)?;
    m.add_function(wrap_pyfunction!(refine_grd, m)?)?;
    m.add_function(wrap_pyfunction!(create_curvilinear_grd, m)?)?;
    m.add_function(wrap_pyfunction!(orthogonalize_grd, m)?)?;
    m.add_function(wrap_pyfunction!(read_tekal_file, m)?)?;
    m.add_function(wrap_pyfunction!(write_tekal_file, m)?)?;
    m.add_function(wrap_pyfunction!(transform_grd, m)?)?;
    m.add_function(wrap_pyfunction!(mask_grd_block, m)?)?;
    m.add_function(wrap_pyfunction!(read_xyz_file, m)?)?;
    m.add_function(wrap_pyfunction!(check_grd_quality, m)?)?;
    m.add_function(wrap_pyfunction!(read_enc, m)?)?;
    m.add_function(wrap_pyfunction!(write_enc, m)?)?;
    m.add_function(wrap_pyfunction!(read_dep_file, m)?)?;
    m.add_function(wrap_pyfunction!(write_dep_file, m)?)?;
    m.add_function(wrap_pyfunction!(interpolate_xyz, m)?)?;
    m.add_function(wrap_pyfunction!(smooth_dep_file, m)?)?;
    m.add_function(wrap_pyfunction!(nefis_list_groups, m)?)?;
    m.add_function(wrap_pyfunction!(nefis_read_element, m)?)?;
    m.add_function(wrap_pyfunction!(tail_diag_log, m)?)?;
    Ok(())
}
