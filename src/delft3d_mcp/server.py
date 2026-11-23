"""FastMCP server exposing 42 modular tools, resources, and prompts for Delft3D 4.07.02."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from fastmcp import FastMCP

from delft3d_mcp.backup import atomic_write_text, create_backup, list_backups, restore_backup
from delft3d_mcp.config import Delft3DPaths, WorkspaceRegistry
from delft3d_mcp.domain.boundaries import (
    format_bcc_file,
    format_bch_file,
    format_bct_file,
    format_bnd_file,
    format_crs_file,
    format_obs_file,
    format_src_dis_files,
    format_wnd_file,
)
from delft3d_mcp.domain.mdf import (
    MdfDocument,
    dump_ini_blocks,
    format_ddb,
    format_dry_points,
    format_thin_dams,
    parse_ini_blocks,
    read_mdw,
    write_mdw,
)
from delft3d_mcp.domain.tide_wes import (
    build_rtc_config,
    build_tide_analysis_input,
    build_wes_input,
    harmonic_fit_and_predict,
)
from delft3d_mcp.domain.waq_part import (
    build_part_inp,
    list_waq_processes_from_install,
    update_waq_inp,
)
from delft3d_mcp.gui.qt5_adapter import (
    execute_qt5_action,
    inspect_qt5_controls,
    send_tcltk_close_or_keys,
)
from delft3d_mcp.gui.win32_bridge import (
    capture_window_screenshot,
    launch_gui_module,
    list_delft3d_windows,
)
from delft3d_mcp.post.extract import (
    compute_skill_metrics,
    export_grid_geojson,
    export_nefis_to_netcdf,
    extract_nefis_slice,
    list_nefis_groups,
)
from delft3d_mcp.post.quickplot import render_grid_and_bathy_png, render_timeseries_png
from delft3d_mcp.runner.job_manager import JobManager
from delft3d_mcp.runner.validator import validate_flow_case, validate_wave_case

logger = logging.getLogger("delft3d_mcp.server")

mcp = FastMCP("delft3d-mcp")


def _get_core() -> Any:
    from delft3d_mcp import _core  # compiled Rust PyO3 extension

    return _core


class AppContext:
    """Lazy-initialized singleton runtime state for Delft3D MCP."""

    def __init__(self) -> None:
        self.paths = Delft3DPaths.discover()
        self.registry = WorkspaceRegistry()
        self.jobs = JobManager(self.paths)


_CTX: AppContext | None = None


def ctx() -> AppContext:
    global _CTX
    if _CTX is None:
        _CTX = AppContext()
    return _CTX


def _opt_safe_path(raw: str | None, workspace: str | None = None, must_exist: bool = True) -> Path | None:
    if not raw:
        return None
    return ctx().registry.resolve_safe_path(raw, workspace=workspace, must_exist=must_exist)[0]


def _read_dep_matrix(core: Any, dep_p: Path, gm: int, gn: int) -> tuple[np.ndarray[Any, Any], bool]:
    """Read .dep file supporting either MNKmax-padded (gm+1, gn+1) or raw grid (gm, gn) shape."""
    try:
        arr = np.asarray(core.read_dep_file(str(dep_p), gm + 1, gn + 1), dtype=float).reshape((gn + 1, gm + 1))[:gn, :gm]
        return arr, True
    except Exception:
        arr = np.asarray(core.read_dep_file(str(dep_p), gm, gn), dtype=float).reshape((gn, gm))
        return arr, False


# ==============================================================================
# 1. PROJECT & SAFETY (5 tools)
# ==============================================================================


@mcp.tool()
def project_init_workspace(path: str, name: str | None = None, create: bool = True) -> dict[str, Any]:
    """Register and activate a sandboxed Delft3D project workspace directory."""
    resolved = ctx().registry.register(path, name=name, create=create)
    return {
        "active_workspace": ctx().registry.active_name,
        "root": str(resolved),
        "all_workspaces": ctx().registry.workspaces,
    }


@mcp.tool()
def project_inspect(workspace: str | None = None) -> dict[str, Any]:
    """Inspect active workspace models (.mdf, .mdw, .grd, .dep, .inp, .dat) and Delft3D install status."""
    c = ctx()
    ws_root = (
        Path(c.registry.workspaces[workspace]).resolve()
        if workspace and workspace in c.registry.workspaces
        else c.registry.get_active_root()
    )
    summary: dict[str, Any] = {
        "delft3d_root": str(c.paths.root),
        "delft3d_version": c.paths.version_label,
        "delft3d_layout": c.paths.layout_kind,
        "delft3d_installed": c.paths.d_hydro.exists(),
        "active_workspace": c.registry.active_name,
        "workspace_root": str(ws_root) if ws_root else None,
    }
    if ws_root and ws_root.exists():
        exts = [".mdf", ".mdw", ".grd", ".dep", ".enc", ".ddb", ".inp", ".par", ".dat"]
        found: dict[str, list[str]] = {e: [] for e in exts}
        for p in ws_root.rglob("*"):
            if ".d3d_mcp_backups" in p.parts or ".d3d_mcp_jobs" in p.parts:
                continue
            if p.is_file() and p.suffix.lower() in found:
                found[p.suffix.lower()].append(str(p.relative_to(ws_root)))
        summary["model_files"] = {k: v for k, v in found.items() if v}
    return summary


@mcp.tool()
def project_list_files(pattern: str = "*", workspace: str | None = None) -> dict[str, Any]:
    """List files matching a glob pattern inside the registered workspace."""
    c = ctx()
    ws_root, _ = c.registry.resolve_safe_path(".", workspace=workspace, must_exist=True)
    matches = [
        str(p.relative_to(ws_root))
        for p in ws_root.glob(pattern)
        if ".d3d_mcp_backups" not in p.parts
    ]
    return {"workspace_root": str(ws_root), "count": len(matches), "files": sorted(matches)[:500]}


@mcp.tool()
def project_backup(file_path: str, label: str = "manual", workspace: str | None = None) -> dict[str, Any]:
    """Create a manual backup snapshot of a file or list available backups if file_path='*'."""
    c = ctx()
    if file_path.strip() == "*":
        ws_root, _ = c.registry.resolve_safe_path(".", workspace=workspace, must_exist=True)
        return {"snapshots": list_backups(ws_root)}
    target, ws_root = c.registry.resolve_safe_path(file_path, workspace=workspace, must_exist=True)
    snap = create_backup(target, ws_root, label=label)
    return {"backed_up": str(target), "snapshot": str(snap) if snap else None}


@mcp.tool()
def project_rollback(snapshot_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Restore model files from a previously saved .d3d_mcp_backups snapshot ID."""
    c = ctx()
    ws_root, _ = c.registry.resolve_safe_path(".", workspace=workspace, must_exist=True)
    restored = restore_backup(ws_root, snapshot_id)
    return {"snapshot_id": snapshot_id, "restored_files": restored}


# ==============================================================================
# 2. GRID / RGFGRID (6 tools)
# ==============================================================================


@mcp.tool()
def grid_inspect(grd_path: str, workspace: str | None = None) -> dict[str, Any]:
    """Read a Delft3D .grd file and return dimensions, coordinate system, and bounding box."""
    target, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    core = _get_core()
    g = core.read_grd(str(target))
    miss = float(g["missing_value"])
    x = np.asarray(g["x"], dtype=float)
    y = np.asarray(g["y"], dtype=float)
    valid = (np.abs(x - miss) > 1e-6) | (np.abs(y - miss) > 1e-6)
    return {
        "path": str(target),
        "coordinate_system": g["coordinate_system"],
        "m_max": g["m_max"],
        "n_max": g["n_max"],
        "valid_nodes": int(np.sum(valid)),
        "x_min": float(np.min(x[valid])) if np.any(valid) else None,
        "x_max": float(np.max(x[valid])) if np.any(valid) else None,
        "y_min": float(np.min(y[valid])) if np.any(valid) else None,
        "y_max": float(np.max(y[valid])) if np.any(valid) else None,
    }


@mcp.tool()
def grid_create_rect(
    out_grd_path: str,
    x0: float,
    y0: float,
    dx: float,
    dy: float,
    m_max: int,
    n_max: int,
    angle_deg: float = 0.0,
    coordinate_system: str = "Cartesian",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Generate a new Delft3D RGFGRID .grd file (with automatic backup if overwriting)."""
    target, ws_root = ctx().registry.resolve_safe_path(out_grd_path, workspace=workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    create_backup(target, ws_root, label="grid_create")
    core = _get_core()
    core.create_rect_grd(
        str(target), float(x0), float(y0), float(dx), float(dy), int(m_max), int(n_max), float(angle_deg), coordinate_system
    )
    return {"grd_path": str(target), "m_max": m_max, "n_max": n_max}


@mcp.tool()
def grid_refine(
    src_grd_path: str,
    dst_grd_path: str,
    factor_m: int = 2,
    factor_n: int = 2,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Sub-divide an existing .grd curvilinear mesh by (factor_m, factor_n)."""
    src, _ = ctx().registry.resolve_safe_path(src_grd_path, workspace=workspace, must_exist=True)
    dst, ws_root = ctx().registry.resolve_safe_path(dst_grd_path, workspace=workspace)
    dst.parent.mkdir(parents=True, exist_ok=True)
    create_backup(dst, ws_root, label="grid_refine")
    new_m, new_n = _get_core().refine_grd(str(src), str(dst), int(factor_m), int(factor_n))
    return {"dst_grd_path": str(dst), "new_m_max": new_m, "new_n_max": new_n}


@mcp.tool()
def grid_check_quality(grd_path: str, workspace: str | None = None) -> dict[str, Any]:
    """Compute parallel RGFGRID quality metrics (orthogonality, smoothness M/N, aspect ratio, dx/dy)."""
    target, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    return dict(_get_core().check_grd_quality(str(target)))


@mcp.tool()
def grid_edit_enclosure(
    enc_path: str,
    points: list[list[int]] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Read or write a Delft3D .enc enclosure polygon file (list of [m, n] 1-based vertices)."""
    core = _get_core()
    if points is None:
        target, _ = ctx().registry.resolve_safe_path(enc_path, workspace=workspace, must_exist=True)
        pts = core.read_enc(str(target))
        return {"enc_path": str(target), "points": [list(p) for p in pts]}
    target, ws_root = ctx().registry.resolve_safe_path(enc_path, workspace=workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    create_backup(target, ws_root, label="enc_edit")
    tuples = [(int(p[0]), int(p[1])) for p in points]
    core.write_enc(str(target), tuples)
    return {"enc_path": str(target), "vertex_count": len(tuples)}


@mcp.tool()
def grid_export_geojson(
    grd_path: str,
    out_geojson_path: str,
    stride: int = 1,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Export a .grd mesh to GeoJSON LineStrings for GIS inspection."""
    src, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    dst, ws_root = ctx().registry.resolve_safe_path(out_geojson_path, workspace=workspace)
    create_backup(dst, ws_root, label="geojson_export")
    return export_grid_geojson(src, dst, _get_core(), stride=stride)


@mcp.tool()
def grid_tekal_io(
    tekal_path: str,
    action: str = "read",
    features: list[dict[str, Any]] | None = None,
    geojson_path: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Read, write, or import from GeoJSON into RGFGRID TEKAL files (.ldb, .spl, .pol)."""
    core = _get_core()
    act = action.lower().strip()
    if act == "read":
        target, _ = ctx().registry.resolve_safe_path(tekal_path, workspace=workspace, must_exist=True)
        feats = core.read_tekal_file(str(target))
        return {"tekal_path": str(target), "feature_count": len(feats), "features": feats}

    target, ws_root = ctx().registry.resolve_safe_path(tekal_path, workspace=workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    create_backup(target, ws_root, label="tekal_write")

    items: list[tuple[str, list[list[float]]]] = []
    if act == "import_geojson":
        if not geojson_path:
            raise ValueError("geojson_path is required for action='import_geojson'.")
        gj_p, _ = ctx().registry.resolve_safe_path(geojson_path, workspace=workspace, must_exist=True)
        fc = json.loads(gj_p.read_text(encoding="utf-8"))
        for idx, feat in enumerate(fc.get("features", []), start=1):
            geom = feat.get("geometry") or {}
            gtype = geom.get("type", "")
            coords = geom.get("coordinates") or []
            name = str((feat.get("properties") or {}).get("name") or f"L{idx:03d}")[:16]
            if gtype == "LineString":
                items.append((name, [[float(c[0]), float(c[1])] for c in coords]))
            elif gtype == "Polygon" and coords:
                items.append((name, [[float(c[0]), float(c[1])] for c in coords[0]]))
            elif gtype == "MultiLineString":
                for sub_i, line in enumerate(coords, start=1):
                    items.append((f"{name}_{sub_i}"[:16], [[float(c[0]), float(c[1])] for c in line]))
    elif act == "write":
        if not features:
            raise ValueError("features list is required for action='write'.")
        for idx, f in enumerate(features, start=1):
            items.append((str(f.get("name", f"BL{idx:02d}")), [[float(p[0]), float(p[1])] for p in f["points"]]))
    else:
        raise ValueError("action must be 'read', 'write', or 'import_geojson'.")

    core.write_tekal_file(str(target), items)
    return {"tekal_path": str(target), "feature_count": len(items)}


@mcp.tool()
def grid_create_curvilinear(
    out_grd_path: str,
    south: list[list[float]],
    north: list[list[float]],
    m_max: int,
    n_max: int,
    west: list[list[float]] | None = None,
    east: list[list[float]] | None = None,
    coordinate_system: str = "Cartesian",
    ortho_iterations: int = 30,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Generate a curvilinear Delft3D .grd mesh from 2 opposing splines or 4 boundary curves (TFI + Elliptic)."""
    target, ws_root = ctx().registry.resolve_safe_path(out_grd_path, workspace=workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    create_backup(target, ws_root, label="grid_curvilinear")
    res = _get_core().create_curvilinear_grd(
        str(target),
        [[float(p[0]), float(p[1])] for p in south],
        [[float(p[0]), float(p[1])] for p in north],
        int(m_max),
        int(n_max),
        [[float(p[0]), float(p[1])] for p in west] if west is not None else None,
        [[float(p[0]), float(p[1])] for p in east] if east is not None else None,
        coordinate_system,
        int(ortho_iterations),
    )
    res["grd_path"] = str(target)
    return dict(res)


@mcp.tool()
def grid_orthogonalize(
    src_grd_path: str,
    dst_grd_path: str | None = None,
    iterations: int = 50,
    omega: float = 0.8,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Run Winslow elliptic orthogonalization on an existing .grd mesh and return quality improvement."""
    src, ws_root = ctx().registry.resolve_safe_path(src_grd_path, workspace=workspace, must_exist=True)
    dst, _ = (
        ctx().registry.resolve_safe_path(dst_grd_path, workspace=workspace)
        if dst_grd_path
        else (src, ws_root)
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    create_backup(dst, ws_root, label="grid_ortho")
    res = dict(_get_core().orthogonalize_grd(str(src), str(dst), int(iterations), float(omega)))
    res["dst_grd_path"] = str(dst)
    return res


@mcp.tool()
def grid_transform_or_mask(
    src_grd_path: str,
    dst_grd_path: str | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    rotate_deg: float = 0.0,
    mask_block: list[int] | None = None,
    mask_interior: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Shift/scale/rotate a .grd mesh or mask (delete) nodes inside/outside [m1, m2, n1, n2]."""
    src, ws_root = ctx().registry.resolve_safe_path(src_grd_path, workspace=workspace, must_exist=True)
    dst, _ = (
        ctx().registry.resolve_safe_path(dst_grd_path, workspace=workspace)
        if dst_grd_path
        else (src, ws_root)
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    create_backup(dst, ws_root, label="grid_transform")
    core = _get_core()
    work_src = src
    if dx != 0.0 or dy != 0.0 or scale_x != 1.0 or scale_y != 1.0 or rotate_deg != 0.0:
        core.transform_grd(str(work_src), str(dst), float(dx), float(dy), float(scale_x), float(scale_y), float(rotate_deg))
        work_src = dst
    masked_count = 0
    if mask_block is not None:
        if len(mask_block) != 4:
            raise ValueError("mask_block must be [m1, m2, n1, n2].")
        masked_count = core.mask_grd_block(
            str(work_src), str(dst), int(mask_block[0]), int(mask_block[1]), int(mask_block[2]), int(mask_block[3]), bool(mask_interior)
        )
    elif work_src == src:
        core.transform_grd(str(src), str(dst), 0.0, 0.0, 1.0, 1.0, 0.0)
    return {"dst_grd_path": str(dst), "masked_nodes": masked_count}


# ==============================================================================
# 3. BATHYMETRY / QUICKIN (4 tools)
# ==============================================================================


@mcp.tool()
def bathy_inspect(
    dep_path: str,
    grd_path: str,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Inspect a .dep bathymetry matrix aligned to a .grd file (supports grid or MNKmax-padded size)."""
    dep_p, _ = ctx().registry.resolve_safe_path(dep_path, workspace=workspace, must_exist=True)
    grd_p, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    core = _get_core()
    g = core.read_grd(str(grd_p))
    dm, dn = int(g["m_max"]), int(g["n_max"])
    try:
        vals = np.asarray(core.read_dep_file(str(dep_p), dm + 1, dn + 1), dtype=float)
        dm, dn = dm + 1, dn + 1
    except Exception:
        vals = np.asarray(core.read_dep_file(str(dep_p), dm, dn), dtype=float)
    valid = vals[np.isfinite(vals) & (np.abs(vals - (-999.0)) > 1e-4)]
    return {
        "dep_path": str(dep_p),
        "m_max": dm,
        "n_max": dn,
        "total_nodes": int(vals.size),
        "valid_nodes": int(valid.size),
        "missing_nodes": int(vals.size - valid.size),
        "min_depth": float(np.min(valid)) if valid.size else None,
        "max_depth": float(np.max(valid)) if valid.size else None,
        "mean_depth": float(np.mean(valid)) if valid.size else None,
    }


@mcp.tool()
def bathy_interpolate_xyz(
    grd_path: str,
    xyz_path: str,
    out_dep_path: str,
    method: str = "idw",
    power: float = 2.0,
    search_radius: float | None = None,
    max_neighbors: int = 8,
    flow_pad: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Interpolate XYZ samples onto a Delft3D .grd (method: idw, nearest, cell_average, or tin)."""
    grd_p, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    xyz_p, _ = ctx().registry.resolve_safe_path(xyz_path, workspace=workspace, must_exist=True)
    dep_p, ws_root = ctx().registry.resolve_safe_path(out_dep_path, workspace=workspace)
    dep_p.parent.mkdir(parents=True, exist_ok=True)
    create_backup(dep_p, ws_root, label="bathy_interp")
    core = _get_core()
    g = core.read_grd(str(grd_p))
    gm, gn = int(g["m_max"]), int(g["n_max"])

    if method.lower().strip() == "tin":
        from scipy.interpolate import LinearNDInterpolator

        pts = np.asarray(core.read_xyz_file(str(xyz_p)), dtype=float)
        gx = np.asarray(g["x"], dtype=float)
        gy = np.asarray(g["y"], dtype=float)
        miss = float(g["missing_value"])
        interp = LinearNDInterpolator(pts[:, :2], pts[:, 2], fill_value=-999.0)
        z_out = interp(gx, gy)
        grid_miss = (np.abs(gx - miss) < 1e-6) & (np.abs(gy - miss) < 1e-6)
        z_out[grid_miss] = -999.0
        core.write_dep_file(str(dep_p), z_out.tolist(), gm, gn)
        count = int(z_out.size)
    else:
        count = core.interpolate_xyz(
            str(grd_p),
            str(xyz_p),
            str(dep_p),
            method,
            float(power),
            float(search_radius) if search_radius is not None else None,
            int(max_neighbors),
        )

    if flow_pad:
        raw = np.asarray(core.read_dep_file(str(dep_p), gm, gn), dtype=float).reshape((gn, gm))
        padded = np.full((gn + 1, gm + 1), -999.0, dtype=float)
        padded[:gn, :gm] = raw
        core.write_dep_file(str(dep_p), padded.ravel().tolist(), gm + 1, gn + 1)
        count = padded.size
    return {"out_dep_path": str(dep_p), "node_count": count, "method": method, "flow_pad": flow_pad}


@mcp.tool()
def bathy_operations(
    action: str,
    dep_path: str,
    grd_path: str,
    second_dep_path: str | None = None,
    out_path: str | None = None,
    combine_op: str = "avg",
    z_ref: float = 0.0,
    tekal_path: str | None = None,
    z_values: list[float] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """QUICKIN Operations parity: 'volumes_areas', 'combine' (two .dep files), or 'samples_from_tekal'."""
    act = action.lower().strip()
    core = _get_core()
    grd_p, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    g = core.read_grd(str(grd_p))
    gm, gn = int(g["m_max"]), int(g["n_max"])

    if act == "samples_from_tekal":
        if not tekal_path:
            raise ValueError("tekal_path is required for samples_from_tekal.")
        tek_p, _ = ctx().registry.resolve_safe_path(tekal_path, workspace=workspace, must_exist=True)
        out_xyz, ws_root = ctx().registry.resolve_safe_path(out_path or "samples_from_poly.xyz", workspace=workspace)
        create_backup(out_xyz, ws_root, label="tekal_to_xyz")
        feats = core.read_tekal_file(str(tek_p))
        lines: list[str] = []
        z0 = float(z_values[0]) if z_values else 0.0
        z1 = float(z_values[-1]) if z_values and len(z_values) > 1 else z0
        for feat in feats:
            pts = feat["points"]
            n_pts = max(1, len(pts) - 1)
            for idx, (px, py) in enumerate(pts):
                z_interp = z0 + (z1 - z0) * (idx / n_pts)
                lines.append(f"{px:.6f}  {py:.6f}  {z_interp:.4f}")
        atomic_write_text(out_xyz, "\n".join(lines) + "\n", ws_root, backup_label="xyz_gen")
        return {"out_xyz_path": str(out_xyz), "sample_count": len(lines)}

    dep_p, ws_root = ctx().registry.resolve_safe_path(dep_path, workspace=workspace, must_exist=True)
    vals1, is_padded = _read_dep_matrix(core, dep_p, gm, gn)

    if act == "volumes_areas":
        gx = np.asarray(g["x"], dtype=float).reshape((gn, gm))
        gy = np.asarray(g["y"], dtype=float).reshape((gn, gm))
        miss = float(g["missing_value"])
        total_area = 0.0
        total_vol = 0.0
        wet_cells = 0
        for n in range(gn - 1):
            for m in range(gm - 1):
                pts_m = [(n, m), (n, m + 1), (n + 1, m + 1), (n + 1, m)]
                if any(abs(gx[r, c] - miss) < 1e-6 and abs(gy[r, c] - miss) < 1e-6 for r, c in pts_m):
                    continue
                d_cell = np.mean([vals1[r, c] for r, c in pts_m])
                if abs(d_cell - (-999.0)) < 1e-4:
                    continue
                x1, y1 = gx[n, m], gy[n, m]
                x2, y2 = gx[n, m + 1], gy[n, m + 1]
                x3, y3 = gx[n + 1, m + 1], gy[n + 1, m + 1]
                x4, y4 = gx[n + 1, m], gy[n + 1, m]
                area = 0.5 * abs((x1 * y2 - x2 * y1) + (x2 * y3 - x3 * y2) + (x3 * y4 - x4 * y3) + (x4 * y1 - x1 * y4))
                water_col = d_cell + float(z_ref)
                total_area += area
                if water_col > 0:
                    wet_cells += 1
                    total_vol += area * water_col
        return {
            "wet_cells": wet_cells,
            "total_area_m2": float(total_area),
            "water_volume_m3": float(total_vol),
            "z_ref": float(z_ref),
        }

    if act == "combine":
        if not second_dep_path:
            raise ValueError("second_dep_path is required for combine.")
        dep2_p, _ = ctx().registry.resolve_safe_path(second_dep_path, workspace=workspace, must_exist=True)
        vals2, _ = _read_dep_matrix(core, dep2_p, gm, gn)
        m1 = np.abs(vals1 - (-999.0)) > 1e-4
        m2 = np.abs(vals2 - (-999.0)) > 1e-4
        both = m1 & m2
        out = vals1.copy()
        cop = combine_op.lower().strip()
        if cop == "add":
            out[both] = vals1[both] + vals2[both]
        elif cop == "sub":
            out[both] = vals1[both] - vals2[both]
        elif cop == "avg":
            out[both] = 0.5 * (vals1[both] + vals2[both])
        elif cop == "min":
            out[both] = np.minimum(vals1[both], vals2[both])
        elif cop == "max":
            out[both] = np.maximum(vals1[both], vals2[both])
        elif cop == "replace_missing":
            fill_mask = (~m1) & m2
            out[fill_mask] = vals2[fill_mask]
        else:
            raise ValueError("combine_op must be add, sub, avg, min, max, or replace_missing.")

        dst_p, _ = ctx().registry.resolve_safe_path(out_path or str(dep_p), workspace=workspace)
        create_backup(dst_p, ws_root, label="bathy_combine")
        if is_padded:
            padded = np.full((gn + 1, gm + 1), -999.0, dtype=float)
            padded[:gn, :gm] = out
            core.write_dep_file(str(dst_p), padded.ravel().tolist(), gm + 1, gn + 1)
        else:
            core.write_dep_file(str(dst_p), out.ravel().tolist(), gm, gn)
        return {"out_dep_path": str(dst_p), "combine_op": cop}

    raise ValueError("action must be 'volumes_areas', 'combine', or 'samples_from_tekal'.")


@mcp.tool()
def bathy_smooth_or_fill(
    dep_path: str,
    grd_path: str,
    out_dep_path: str | None = None,
    iterations: int = 5,
    fill_only: bool = True,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Fill -999.0 missing nodes or apply Laplace bathymetry smoothing on a .dep file."""
    dep_p, ws_root = ctx().registry.resolve_safe_path(dep_path, workspace=workspace, must_exist=True)
    grd_p, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    dst_p, _ = (
        ctx().registry.resolve_safe_path(out_dep_path, workspace=workspace)
        if out_dep_path
        else (dep_p, ws_root)
    )
    create_backup(dst_p, ws_root, label="bathy_smooth")
    core = _get_core()
    g = core.read_grd(str(grd_p))
    core.smooth_dep_file(str(dep_p), str(dst_p), g["m_max"], g["n_max"], int(iterations), bool(fill_only))
    return {"out_dep_path": str(dst_p), "iterations": iterations, "fill_only": fill_only}


@mcp.tool()
def bathy_edit_region(
    dep_path: str,
    grd_path: str,
    m_range: list[int],
    n_range: list[int],
    operation: str,
    value: float,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Modify bathymetry in a 1-based [m1, m2] x [n1, n2] region (operation: set, add, scale)."""
    dep_p, ws_root = ctx().registry.resolve_safe_path(dep_path, workspace=workspace, must_exist=True)
    grd_p, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
    create_backup(dep_p, ws_root, label="bathy_edit")
    core = _get_core()
    g = core.read_grd(str(grd_p))
    m_max, n_max = int(g["m_max"]), int(g["n_max"])
    arr = np.asarray(core.read_dep_file(str(dep_p), m_max, n_max), dtype=float).reshape((n_max, m_max))

    m1, m2 = max(1, m_range[0]) - 1, min(m_max, m_range[1])
    n1, n2 = max(1, n_range[0]) - 1, min(n_max, n_range[1])
    sub = arr[n1:n2, m1:m2]
    op = operation.lower().strip()
    if op == "set":
        arr[n1:n2, m1:m2] = float(value)
    elif op == "add":
        mask = np.abs(sub - (-999.0)) > 1e-4
        sub[mask] += float(value)
    elif op == "scale":
        mask = np.abs(sub - (-999.0)) > 1e-4
        sub[mask] *= float(value)
    else:
        raise ValueError(f"Unsupported operation '{operation}'. Use set, add, or scale.")

    core.write_dep_file(str(dep_p), arr.ravel().tolist(), m_max, n_max)
    return {"dep_path": str(dep_p), "modified_slice": {"m": [m1 + 1, m2], "n": [n1 + 1, n2]}}


# ==============================================================================
# 4. FLOW & MORPHOLOGY (7 tools)
# ==============================================================================


@mcp.tool()
def flow_read_mdf(mdf_path: str, workspace: str | None = None) -> dict[str, Any]:
    """Read a Delft3D-FLOW .mdf file into structured parameters and referenced sub-files."""
    target, _ = ctx().registry.resolve_safe_path(mdf_path, workspace=workspace, must_exist=True)
    doc = MdfDocument.from_file(target)
    return {
        "mdf_path": str(target),
        "params": doc.params,
        "referenced_files": doc.referenced_files(),
    }


@mcp.tool()
def flow_update_mdf(
    mdf_path: str,
    updates: dict[str, Any],
    workspace: str | None = None,
) -> dict[str, Any]:
    """Create or losslessly update keywords in a Delft3D-FLOW .mdf file with auto-backup."""
    target, ws_root = ctx().registry.resolve_safe_path(mdf_path, workspace=workspace)
    doc = MdfDocument.from_file(target) if target.exists() else MdfDocument()
    doc.set_many(updates)
    backup = atomic_write_text(target, doc.to_text(), ws_root, backup_label="mdf_update")
    return {
        "mdf_path": str(target),
        "updated_keys": list(updates.keys()),
        "backup": str(backup) if backup else None,
    }


@mcp.tool()
def flow_set_boundaries(
    bnd_path: str,
    boundaries: list[dict[str, Any]],
    bct_path: str | None = None,
    bct_tables: list[dict[str, Any]] | None = None,
    bcc_path: str | None = None,
    bcc_tables: list[dict[str, Any]] | None = None,
    bch_path: str | None = None,
    bch_params: dict[str, Any] | None = None,
    src_path: str | None = None,
    dis_path: str | None = None,
    discharges: list[dict[str, Any]] | None = None,
    reference_time: int = 20260101,
    mdf_path: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Write .bnd, .bct, .bcc, .bch, and .src/.dis discharge files, optionally linking them to .mdf."""
    bnd_p, ws_root = ctx().registry.resolve_safe_path(bnd_path, workspace=workspace)
    atomic_write_text(bnd_p, format_bnd_file(boundaries), ws_root, backup_label="bnd_write")
    out: dict[str, Any] = {"bnd_path": str(bnd_p), "boundary_count": len(boundaries)}
    mdf_upd: dict[str, Any] = {"Filbnd": bnd_p.name}

    if bct_path and bct_tables is not None:
        bct_p, _ = ctx().registry.resolve_safe_path(bct_path, workspace=workspace)
        atomic_write_text(
            bct_p,
            format_bct_file(bct_tables, reference_time=reference_time),
            ws_root,
            backup_label="bct_write",
        )
        out["bct_path"] = str(bct_p)
        mdf_upd["FilbcT"] = bct_p.name

    if bcc_path and bcc_tables is not None:
        bcc_p, _ = ctx().registry.resolve_safe_path(bcc_path, workspace=workspace)
        atomic_write_text(
            bcc_p,
            format_bcc_file(bcc_tables, reference_time=reference_time),
            ws_root,
            backup_label="bcc_write",
        )
        out["bcc_path"] = str(bcc_p)
        mdf_upd["FilbcC"] = bcc_p.name

    if bch_path and bch_params is not None:
        bch_p, _ = ctx().registry.resolve_safe_path(bch_path, workspace=workspace)
        bch_txt = format_bch_file(
            frequencies_deg_per_hr=bch_params["frequencies_deg_per_hr"],
            amplitudes_end_a=bch_params["amplitudes_end_a"],
            amplitudes_end_b=bch_params["amplitudes_end_b"],
            phases_end_a=bch_params["phases_end_a"],
            phases_end_b=bch_params["phases_end_b"],
            z0_per_boundary=bch_params.get("z0_per_boundary"),
        )
        atomic_write_text(bch_p, bch_txt, ws_root, backup_label="bch_write")
        out["bch_path"] = str(bch_p)
        mdf_upd["FilbcH"] = bch_p.name

    if src_path and dis_path and discharges is not None:
        src_p, _ = ctx().registry.resolve_safe_path(src_path, workspace=workspace)
        dis_p, _ = ctx().registry.resolve_safe_path(dis_path, workspace=workspace)
        src_txt, dis_txt = format_src_dis_files(discharges, reference_time=reference_time)
        atomic_write_text(src_p, src_txt, ws_root, backup_label="src_write")
        atomic_write_text(dis_p, dis_txt, ws_root, backup_label="dis_write")
        out["src_path"] = str(src_p)
        out["dis_path"] = str(dis_p)

    if mdf_path:
        mdf_p, _ = ctx().registry.resolve_safe_path(mdf_path, workspace=workspace, must_exist=True)
        doc = MdfDocument.from_file(mdf_p)
        doc.set_many(mdf_upd)
        atomic_write_text(mdf_p, doc.to_text(), ws_root, backup_label="mdf_link_bnd")
        out["mdf_linked"] = str(mdf_p)
    return out


@mcp.tool()
def flow_set_obs_crs(
    obs_path: str | None = None,
    stations: list[dict[str, Any]] | None = None,
    crs_path: str | None = None,
    cross_sections: list[dict[str, Any]] | None = None,
    wnd_path: str | None = None,
    wind_records: list[dict[str, float]] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Write Delft3D-FLOW observation stations (.obs), cross-sections (.crs), or uniform wind (.wnd)."""
    out: dict[str, Any] = {}
    if obs_path and stations is not None:
        p, ws = ctx().registry.resolve_safe_path(obs_path, workspace=workspace)
        atomic_write_text(p, format_obs_file(stations), ws, backup_label="obs_write")
        out["obs_path"] = str(p)
    if crs_path and cross_sections is not None:
        p, ws = ctx().registry.resolve_safe_path(crs_path, workspace=workspace)
        atomic_write_text(p, format_crs_file(cross_sections), ws, backup_label="crs_write")
        out["crs_path"] = str(p)
    if wnd_path and wind_records is not None:
        p, ws = ctx().registry.resolve_safe_path(wnd_path, workspace=workspace)
        atomic_write_text(p, format_wnd_file(wind_records), ws, backup_label="wnd_write")
        out["wnd_path"] = str(p)
    return out


@mcp.tool()
def flow_set_thin_dams_dry(
    dry_path: str | None = None,
    dry_points: list[list[int]] | None = None,
    thd_path: str | None = None,
    thin_dams: list[dict[str, Any]] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Write Delft3D-FLOW dry points (.dry) or thin dams (.thd)."""
    out: dict[str, Any] = {}
    if dry_path and dry_points is not None:
        p, ws = ctx().registry.resolve_safe_path(dry_path, workspace=workspace)
        pts = [(int(pt[0]), int(pt[1])) for pt in dry_points]
        atomic_write_text(p, format_dry_points(pts), ws, backup_label="dry_write")
        out["dry_path"] = str(p)
    if thd_path and thin_dams is not None:
        p, ws = ctx().registry.resolve_safe_path(thd_path, workspace=workspace)
        atomic_write_text(p, format_thin_dams(thin_dams), ws, backup_label="thd_write")
        out["thd_path"] = str(p)
    return out


@mcp.tool()
def flow_config_morphology(
    mor_path: str | None = None,
    mor_sections: dict[str, dict[str, str]] | None = None,
    sed_path: str | None = None,
    sed_sections: dict[str, dict[str, str]] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Read or update Delft3D morphology (.mor) and sediment (.sed) INI-block files."""
    out: dict[str, Any] = {}
    if mor_path:
        p, ws = ctx().registry.resolve_safe_path(mor_path, workspace=workspace)
        if mor_sections is None:
            out["mor"] = parse_ini_blocks(p.read_text(encoding="utf-8", errors="replace"))
        else:
            atomic_write_text(p, dump_ini_blocks(mor_sections, "Delft3D-MOR"), ws, backup_label="mor_write")
            out["mor_path"] = str(p)
    if sed_path:
        p, ws = ctx().registry.resolve_safe_path(sed_path, workspace=workspace)
        if sed_sections is None:
            out["sed"] = parse_ini_blocks(p.read_text(encoding="utf-8", errors="replace"))
        else:
            atomic_write_text(p, dump_ini_blocks(sed_sections, "Delft3D-SED"), ws, backup_label="sed_write")
            out["sed_path"] = str(p)
    return out


@mcp.tool()
def flow_setup_dd(
    ddb_path: str,
    links: list[dict[str, Any]],
    workspace: str | None = None,
) -> dict[str, Any]:
    """Generate a Domain Decomposition (.ddb) coupling file between multiple FLOW domains."""
    p, ws = ctx().registry.resolve_safe_path(ddb_path, workspace=workspace)
    atomic_write_text(p, format_ddb(links), ws, backup_label="ddb_write")
    return {"ddb_path": str(p), "link_count": len(links)}


# ==============================================================================
# 5. WAVE / WAQ / PART / TIDE-WES (5 tools)
# ==============================================================================


@mcp.tool()
def wave_read_write_mdw(
    mdw_path: str,
    sections: dict[str, dict[str, Any]] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Read or write a Delft3D-WAVE/SWAN .mdw configuration file."""
    if sections is None:
        p, _ = ctx().registry.resolve_safe_path(mdw_path, workspace=workspace, must_exist=True)
        return {"mdw_path": str(p), "sections": read_mdw(p)}
    p, ws = ctx().registry.resolve_safe_path(mdw_path, workspace=workspace)
    content = write_mdw(sections)
    atomic_write_text(p, content, ws, backup_label="mdw_write")
    return {"mdw_path": str(p), "sections": list(sections.keys())}


@mcp.tool()
def waq_edit_inp(
    inp_path: str,
    section_updates: dict[str, str] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Inspect or update numbered sections (#1..#10) of a Delft3D-WAQ .inp file."""
    p, ws = ctx().registry.resolve_safe_path(inp_path, workspace=workspace)
    existing = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
    if section_updates is None:
        from delft3d_mcp.domain.waq_part import parse_waq_sections

        secs = parse_waq_sections(existing)
        return {"inp_path": str(p), "sections": {str(k): len(v) for k, v in secs.items()}}
    parsed_updates = {int(k): v for k, v in section_updates.items()}
    new_text = update_waq_inp(existing, parsed_updates)
    atomic_write_text(p, new_text, ws, backup_label="waq_inp")
    return {"inp_path": str(p), "updated_sections": list(parsed_updates.keys())}


@mcp.tool()
def waq_list_processes(query: str = "", limit: int = 50) -> dict[str, Any]:
    """Search Delft3D-WAQ Processes Library definitions from the local installation."""
    procs = list_waq_processes_from_install(ctx().paths.guis_x64, query=query, limit=limit)
    return {"count": len(procs), "processes": procs}


@mcp.tool()
def part_edit_inp(
    inp_path: str,
    params: dict[str, Any],
    workspace: str | None = None,
) -> dict[str, Any]:
    """Generate or overwrite a Delft3D-PART .inp/.par input file."""
    p, ws = ctx().registry.resolve_safe_path(inp_path, workspace=workspace)
    atomic_write_text(p, build_part_inp(params), ws, backup_label="part_inp")
    return {"inp_path": str(p)}


@mcp.tool()
def tide_wes_generate(
    tool_kind: str,
    out_path: str,
    params: dict[str, Any],
    workspace: str | None = None,
) -> dict[str, Any]:
    """Generate WES/TIDE/RTC configs or run least-squares tidal harmonic fit ('wes', 'tide', 'rtc', 'harmonic_fit')."""
    kind = tool_kind.lower().strip()
    if kind == "harmonic_fit":
        return harmonic_fit_and_predict(
            times_hours=[float(v) for v in params["times_hours"]],
            values=[float(v) for v in params["values"]],
            constituents=params.get("constituents"),
            predict_times_hours=[float(v) for v in params["predict_times_hours"]]
            if params.get("predict_times_hours") is not None
            else None,
        )

    p, ws = ctx().registry.resolve_safe_path(out_path, workspace=workspace)
    if kind == "wes":
        content = build_wes_input(params)
    elif kind == "tide":
        content = build_tide_analysis_input(
            series_file=str(params["series_file"]),
            constituents=list(params.get("constituents", ["M2", "S2", "K1", "O1"])),
            output_file=str(params.get("output_file", "tide_out.txt")),
        )
    elif kind == "rtc":
        content = build_rtc_config(params)
    else:
        raise ValueError("tool_kind must be 'wes', 'tide', 'rtc', or 'harmonic_fit'.")
    atomic_write_text(p, content, ws, backup_label=f"{kind}_gen")
    return {"out_path": str(p), "tool_kind": kind}


# ==============================================================================
# 6. KERNEL RUNNER & JOBS (6 tools)
# ==============================================================================


@mcp.tool()
def sim_validate(
    input_path: str,
    module: str = "flow",
    workspace: str | None = None,
) -> dict[str, Any]:
    """Run dry validation (missing files, M/N dimension match, Courant number CFL) before simulation."""
    p, _ = ctx().registry.resolve_safe_path(input_path, workspace=workspace, must_exist=True)
    mod = module.lower().strip()
    if mod == "flow":
        return validate_flow_case(p, core_mod=_get_core())
    if mod == "wave":
        return validate_wave_case(p)
    raise ValueError("module must be 'flow' or 'wave'.")


@mcp.tool()
def sim_start(
    mode: str,
    input_path: str,
    wave_mdw_path: str | None = None,
    num_procs: int = 1,
    single_precision: bool = False,
    skip_validation: bool = False,
    extra_args: list[str] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Start a non-blocking Delft3D simulation (flow_single, flow_mpi, flow_wave_coupled, flow_dd, wave_standalone, waq, part)."""
    c = ctx()
    inp_p, _ws_root = c.registry.resolve_safe_path(input_path, workspace=workspace, must_exist=True)
    work_dir = inp_p.parent

    if not skip_validation and mode in {"flow_single", "flow_mpi", "flow_wave_coupled"} and inp_p.suffix.lower() == ".mdf":
        val = validate_flow_case(inp_p, core_mod=_get_core())
        if not val["valid"]:
            return {"started": False, "validation_errors": val["errors"], "warnings": val["warnings"]}

    wave_p: Path | None = None
    if wave_mdw_path:
        wave_p, _ = c.registry.resolve_safe_path(wave_mdw_path, workspace=workspace, must_exist=True)

    job = c.jobs.start_simulation(
        mode=mode,
        work_dir=work_dir,
        input_file=inp_p,
        wave_mdw_file=wave_p,
        num_procs=int(num_procs),
        single_precision=bool(single_precision),
        extra_args=extra_args,
    )
    return {"started": True, "job_id": job.job_id, "pids": job.pids, "log_path": job.log_path}


@mcp.tool()
def sim_status(job_id: str | None = None) -> dict[str, Any]:
    """Check status of one simulation job or list all managed jobs."""
    return ctx().jobs.get_status(job_id)


@mcp.tool()
def sim_tail_log(
    log_or_diag_path: str,
    tail_n: int = 50,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Scan a Delft3D tri-diag or log file via Rust tailer for progress, warnings, and errors."""
    p, _ = ctx().registry.resolve_safe_path(log_or_diag_path, workspace=workspace, must_exist=True)
    return dict(_get_core().tail_diag_log(str(p), int(tail_n)))


@mcp.tool()
def sim_stop(job_id: str) -> dict[str, Any]:
    """Terminate a running Delft3D simulation job and its child MPI/WAVE processes."""
    return ctx().jobs.stop_job(job_id)


@mcp.tool()
def sim_run_utility(
    utility: str,
    work_dir_path: str,
    args: list[str],
    timeout_sec: int = 120,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Run a Delft3D CLI utility (agrhyd, ddcouple, mormerge, nesthd1, nesthd2, wes, tide_analysis, tide_predict, maptonetcdf, sim2ugrid, delft3d2shape, delwaq2raster)."""
    wdir, _ = ctx().registry.resolve_safe_path(work_dir_path, workspace=workspace, must_exist=True)
    return ctx().jobs.run_utility_sync(utility, wdir, args, timeout_sec=int(timeout_sec))


# ==============================================================================
# 7. POST-PROCESSING & VISUALIZATION (5 tools)
# ==============================================================================


@mcp.tool()
def post_list_nefis_vars(
    dat_path: str,
    def_path: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """List groups, dimensions, and elements inside a Delft3D NEFIS (.dat/.def) output file."""
    dat_p, _ = ctx().registry.resolve_safe_path(dat_path, workspace=workspace, must_exist=True)
    def_p = _opt_safe_path(def_path, workspace=workspace)
    groups = list_nefis_groups(dat_p, _get_core(), def_path=def_p)
    return {"dat_path": str(dat_p), "group_count": len(groups), "groups": groups}


@mcp.tool()
def post_extract_timeseries(
    dat_path: str,
    group_name: str,
    element_name: str,
    vd_start: int | None = None,
    vd_end: int | None = None,
    def_path: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Extract time-series element data from NEFIS (e.g. trih-* his-series) via Rust NEFIS engine."""
    dat_p, _ = ctx().registry.resolve_safe_path(dat_path, workspace=workspace, must_exist=True)
    def_p = _opt_safe_path(def_path, workspace=workspace)
    return extract_nefis_slice(
        dat_p, group_name, element_name, _get_core(), vd_start=vd_start, vd_end=vd_end, def_path=def_p
    )


@mcp.tool()
def post_extract_map_slice(
    dat_path: str,
    group_name: str,
    element_name: str,
    time_index: int = 0,
    def_path: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Extract a spatial map slice at a given variable-dimension index from NEFIS (e.g. trim-*)."""
    dat_p, _ = ctx().registry.resolve_safe_path(dat_path, workspace=workspace, must_exist=True)
    def_p = _opt_safe_path(def_path, workspace=workspace)
    return extract_nefis_slice(
        dat_p,
        group_name,
        element_name,
        _get_core(),
        vd_start=int(time_index),
        vd_end=int(time_index) + 1,
        def_path=def_p,
    )


@mcp.tool()
def post_convert_netcdf_gis(
    converter: str,
    work_dir_path: str,
    args: list[str],
    workspace: str | None = None,
) -> dict[str, Any]:
    """Convert NEFIS/Delft3D outputs (nefis_to_netcdf, sim2ugrid, maptonetcdf, delwaq2raster, delft3d2shape)."""
    conv = converter.lower().strip()
    if conv == "nefis_to_netcdf":
        if len(args) < 2:
            raise ValueError("nefis_to_netcdf requires args=[dat_path, out_nc_path, ...optional_groups]")
        dat_p, _ = ctx().registry.resolve_safe_path(args[0], workspace=workspace, must_exist=True)
        nc_p, ws_root = ctx().registry.resolve_safe_path(args[1], workspace=workspace)
        create_backup(nc_p, ws_root, label="nefis_nc")
        grp_filter = args[2:] if len(args) > 2 else None
        return export_nefis_to_netcdf(dat_p, nc_p, _get_core(), groups=grp_filter)
    if conv not in {"sim2ugrid", "maptonetcdf", "delwaq2raster", "delft3d2shape"}:
        raise ValueError(
            "converter must be one of: nefis_to_netcdf, sim2ugrid, maptonetcdf, delwaq2raster, delft3d2shape"
        )
    wdir, _ = ctx().registry.resolve_safe_path(work_dir_path, workspace=workspace, must_exist=True)
    return ctx().jobs.run_utility_sync(conv, wdir, args)


@mcp.tool()
def post_validate_skill(
    observed: list[float],
    simulated: list[float],
) -> dict[str, float]:
    """Compute calibration skill metrics (RMSE, MAE, Bias, Pearson R, NSE, Willmott d)."""
    return compute_skill_metrics(observed, simulated)


@mcp.tool()
def post_plot_snapshot(
    plot_kind: str,
    out_png_path: str,
    grd_path: str | None = None,
    dep_path: str | None = None,
    times: list[float] | None = None,
    values: list[float] | None = None,
    title: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Render a headless high-DPI PNG plot ('grid_bathy' or 'timeseries')."""
    out_p, ws_root = ctx().registry.resolve_safe_path(out_png_path, workspace=workspace)
    create_backup(out_p, ws_root, label="plot")
    kind = plot_kind.lower().strip()
    if kind == "grid_bathy":
        if not grd_path:
            raise ValueError("grd_path is required for grid_bathy plot.")
        grd_p, _ = ctx().registry.resolve_safe_path(grd_path, workspace=workspace, must_exist=True)
        dep_p = (
            ctx().registry.resolve_safe_path(dep_path, workspace=workspace, must_exist=True)[0]
            if dep_path
            else None
        )
        return render_grid_and_bathy_png(grd_p, out_p, _get_core(), dep_path=dep_p, title=title)
    if kind == "timeseries":
        if times is None or values is None:
            raise ValueError("times and values are required for timeseries plot.")
        png = render_timeseries_png(times, values, out_p, title=title or "Time Series")
        return {"png_path": png}
    raise ValueError("plot_kind must be 'grid_bathy' or 'timeseries'.")


# ==============================================================================
# 8. SMART WINDOWS GUI BRIDGE (4 tools)
# ==============================================================================


@mcp.tool()
def gui_launch_module(
    module: str,
    work_dir_path: str = ".",
    target_file: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Launch a Delft3D GUI (menu, rgfgrid, quickin, dido, flow_gui, wave_gui, waq_gui, part_gui, quickplot, vsi)."""
    wdir, _ = ctx().registry.resolve_safe_path(work_dir_path, workspace=workspace, must_exist=True)
    tfile = (
        ctx().registry.resolve_safe_path(target_file, workspace=workspace, must_exist=True)[0]
        if target_file
        else None
    )
    return launch_gui_module(ctx().paths, module, wdir, target_file=tfile)


@mcp.tool()
def gui_list_windows(inspect_qt5_hwnd: int | None = None) -> dict[str, Any]:
    """List visible Delft3D windows and optionally inspect UIA controls of a Qt5 HWND."""
    wins = list_delft3d_windows()
    res: dict[str, Any] = {"windows": wins}
    if inspect_qt5_hwnd is not None:
        res["controls"] = inspect_qt5_controls(int(inspect_qt5_hwnd))
    return res


@mcp.tool()
def gui_ui_action(
    hwnd: int,
    backend: str,
    action: str,
    target_name: str | None = None,
    text_value: str | None = None,
    menu_path: str | None = None,
    keys: str | None = None,
    points: list[list[float]] | None = None,
    normalized_coords: bool = True,
    double_click_last: bool = False,
) -> dict[str, Any]:
    """Execute UIA/canvas actions on Qt5 ('qt5': menu_select, click_button, set_text, canvas_click_points, canvas_drag, close) or Tcl/Tk ('tcltk')."""
    b = backend.lower().strip()
    if b == "qt5":
        return execute_qt5_action(
            int(hwnd),
            action=action,
            target_name=target_name,
            text_value=text_value,
            menu_path=menu_path,
            points=points,
            normalized_coords=normalized_coords,
            double_click_last=double_click_last,
            keys=keys,
        )
    if b == "tcltk":
        return send_tcltk_close_or_keys(int(hwnd), action=action, keys=keys)
    raise ValueError("backend must be 'qt5' or 'tcltk'.")


@mcp.tool()
def gui_capture_screenshot(
    out_png_path: str,
    hwnd: int | None = None,
    title_contains: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Capture a screenshot of a Delft3D window into the workspace via mss."""
    out_p, ws_root = ctx().registry.resolve_safe_path(out_png_path, workspace=workspace)
    create_backup(out_p, ws_root, label="screenshot")
    return capture_window_screenshot(out_p, hwnd=hwnd, title_contains=title_contains)


# ==============================================================================
# MCP RESOURCES (4) & PROMPTS (3)
# ==============================================================================


@mcp.resource("delft3d://install/info")
def resource_install_info() -> str:
    p = ctx().paths
    return json.dumps(
        {
            "root": str(p.root),
            "d_hydro_exists": p.d_hydro.exists(),
            "wave_exists": p.wave_exe.exists(),
            "delwaq_exists": p.delwaq_exe.exists(),
            "delpar_exists": p.delpar_exe.exists(),
            "mfe_app_exists": p.mfe_app_exe.exists(),
            "d3d_qp_exists": p.d3d_qp_exe.exists(),
            "active_workspace": ctx().registry.active_name,
        },
        indent=2,
    )


@mcp.resource("delft3d://jobs/all")
def resource_jobs_all() -> str:
    return json.dumps(ctx().jobs.get_status(), indent=2)


@mcp.resource("delft3d://manuals/list")
def resource_manuals_list() -> str:
    mdir = ctx().paths.manuals_dir
    files = sorted(f.name for f in mdir.glob("*.pdf")) if mdir.exists() else []
    return json.dumps({"manuals_dir": str(mdir), "pdfs": files}, indent=2)


@mcp.resource("delft3d://docs/mdf-keywords")
def resource_mdf_keywords() -> str:
    return json.dumps(
        {
            "time": ["Itdate", "Tunit", "Tstart", "Tstop", "Dt"],
            "grid_bathy": ["Filcco", "Filgrd", "MNKmax", "Thick", "Fildep", "Anglat", "Grdang"],
            "boundaries": ["Filbnd", "FilbcT", "FilbcC", "FilbcH", "FilbcQ", "Filwnd"],
            "physics": ["Sub1", "Sub2", "Roumet", "Ccofu", "Ccofv", "Vicouv", "Dicouv", "Zeta0"],
            "morphology": ["Filmor", "Filsed"],
            "output": ["Flmap", "Flhis", "Flpp", "Flrst", "Filsta", "Filcrs", "Filtd", "Fildry"],
        },
        indent=2,
    )


@mcp.prompt()
def setup_new_hydro_model(workspace_path: str, runid: str = "case01") -> str:
    """Guided workflow prompt to build and validate a new Delft3D-FLOW hydrodynamic model."""
    return (
        f"Initialize workspace at '{workspace_path}' via project_init_workspace, "
        f"generate a grid with grid_create_rect, interpolate bathymetry with bathy_interpolate_xyz, "
        f"create '{runid}.mdf' via flow_update_mdf, configure boundaries with flow_set_boundaries, "
        f"validate with sim_validate, then start via sim_start."
    )


@mcp.prompt()
def debug_sim_blowup(mdf_path: str, tri_diag_path: str) -> str:
    """Guided prompt to diagnose instability/blow-up from tri-diag and MDF."""
    return (
        f"Inspect '{tri_diag_path}' using sim_tail_log, read '{mdf_path}' using flow_read_mdf, "
        f"run sim_validate to check Courant number (CFL) and grid quality, and recommend Dt or grid fixes."
    )


@mcp.prompt()
def coupled_flow_wave_pipeline(mdf_path: str, mdw_path: str, num_procs: int = 4) -> str:
    """Guided prompt to run online coupled Delft3D FLOW + WAVE."""
    return (
        f"Validate both '{mdf_path}' and '{mdw_path}' with sim_validate, ensure Waveol=#Y# in MDF, "
        f"and launch via sim_start(mode='flow_wave_coupled', input_path='{mdf_path}', "
        f"wave_mdw_path='{mdw_path}', num_procs={num_procs})."
    )
