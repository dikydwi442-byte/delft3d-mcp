"""Type stubs for compiled Rust PyO3 extension delft3d_mcp._core."""

from __future__ import annotations

from typing import Any

def read_grd(path: str) -> dict[str, Any]: ...
def write_grd(
    path: str,
    m_max: int,
    n_max: int,
    x: list[float],
    y: list[float],
    coordinate_system: str,
    missing_value: float,
) -> None: ...
def create_rect_grd(
    path: str,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    m_max: int,
    n_max: int,
    angle_deg: float,
    coordinate_system: str,
) -> None: ...
def refine_grd(src_path: str, dst_path: str, factor_m: int, factor_n: int) -> tuple[int, int]: ...
def create_curvilinear_grd(
    out_grd_path: str,
    south: list[list[float]],
    north: list[list[float]],
    m_max: int,
    n_max: int,
    west: list[list[float]] | None = None,
    east: list[list[float]] | None = None,
    coordinate_system: str = "Cartesian",
    ortho_iterations: int = 30,
) -> dict[str, Any]: ...
def orthogonalize_grd(
    src_grd_path: str,
    dst_grd_path: str,
    iterations: int = 50,
    omega: float = 0.8,
) -> dict[str, Any]: ...
def read_tekal_file(path: str) -> list[dict[str, Any]]: ...
def write_tekal_file(path: str, features: list[tuple[str, list[list[float]]]]) -> None: ...
def transform_grd(
    src_grd_path: str,
    dst_grd_path: str,
    dx: float = 0.0,
    dy: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    rotate_deg: float = 0.0,
) -> None: ...
def mask_grd_block(
    src_grd_path: str,
    dst_grd_path: str,
    m1: int,
    m2: int,
    n1: int,
    n2: int,
    interior: bool = True,
) -> int: ...
def read_xyz_file(path: str) -> list[list[float]]: ...
def check_grd_quality(path: str) -> dict[str, Any]: ...
def read_enc(path: str) -> list[tuple[int, int]]: ...
def write_enc(path: str, points: list[tuple[int, int]]) -> None: ...
def read_dep_file(path: str, m_max: int, n_max: int) -> list[float]: ...
def write_dep_file(path: str, values: list[float], m_max: int, n_max: int) -> None: ...
def interpolate_xyz(
    grd_path: str,
    xyz_path: str,
    out_dep_path: str,
    method: str = "idw",
    power: float = 2.0,
    search_radius: float | None = None,
    max_neighbors: int = 8,
) -> int: ...
def smooth_dep_file(
    dep_path: str,
    out_dep_path: str,
    m_max: int,
    n_max: int,
    iterations: int = 5,
    fill_only: bool = True,
    missing_val: float = -999.0,
) -> None: ...
def nefis_list_groups(dat_path: str, def_path: str | None = None) -> list[dict[str, Any]]: ...
def nefis_read_element(
    dat_path: str,
    group_name: str,
    element_name: str,
    def_path: str | None = None,
    vd_start: int | None = None,
    vd_end: int | None = None,
) -> dict[str, Any]: ...
def tail_diag_log(path: str, tail_n: int = 50, max_scan_bytes: int = 4_194_304) -> dict[str, Any]: ...
