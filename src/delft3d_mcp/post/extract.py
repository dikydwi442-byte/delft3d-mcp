"""NEFIS extraction, CF NetCDF4 & GeoJSON export, and calibration skill metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


def list_nefis_groups(dat_path: Path, core_mod: Any, def_path: Path | None = None) -> list[dict[str, Any]]:
    return list(core_mod.nefis_list_groups(str(dat_path), str(def_path) if def_path else None))


def extract_nefis_slice(
    dat_path: Path,
    group_name: str,
    element_name: str,
    core_mod: Any,
    vd_start: int | None = None,
    vd_end: int | None = None,
    def_path: Path | None = None,
) -> dict[str, Any]:
    ds = core_mod.nefis_read_element(
        str(dat_path),
        group_name,
        element_name,
        str(def_path) if def_path else None,
        vd_start,
        vd_end,
    )
    vals = ds["values"]
    arr = np.asarray(vals)
    summary: dict[str, Any] = {
        "group": ds["group"],
        "element": ds["element"],
        "units": ds["units"],
        "description": ds["description"],
        "shape": ds["shape"],
        "count": int(arr.size),
    }
    if np.issubdtype(arr.dtype, np.number) and arr.size > 0:
        finite = arr[np.isfinite(arr) & (np.abs(arr - (-999.0)) > 1e-4)]
        if finite.size > 0:
            summary["min"] = float(np.min(finite))
            summary["max"] = float(np.max(finite))
            summary["mean"] = float(np.mean(finite))
        summary["values_preview"] = arr.ravel()[:200].tolist()
    else:
        summary["values_preview"] = vals[:100]
    return summary


def export_nefis_to_netcdf(
    dat_path: Path,
    out_nc_path: Path,
    core_mod: Any,
    groups: list[str] | None = None,
    def_path: Path | None = None,
) -> dict[str, Any]:
    """Export selected (or all numeric) NEFIS groups/elements directly to CF-friendly NetCDF4."""
    import netCDF4

    all_groups = list_nefis_groups(dat_path, core_mod, def_path=def_path)
    target_names = set(groups) if groups else {g["name"] for g in all_groups}

    out_nc_path.parent.mkdir(parents=True, exist_ok=True)
    exported_vars: list[str] = []
    with netCDF4.Dataset(str(out_nc_path), "w", format="NETCDF4") as ds:
        ds.title = f"Exported from Delft3D NEFIS: {dat_path.name}"
        ds.source = "delft3d-mcp Rust NEFIS reader"

        for g in all_groups:
            gname = g["name"]
            if gname not in target_names:
                continue
            grp_nc = ds.createGroup(gname.replace("-", "_"))
            for elem in g["elements"]:
                try:
                    res = core_mod.nefis_read_element(
                        str(dat_path), gname, elem, str(def_path) if def_path else None, None, None
                    )
                except Exception:
                    continue
                arr = np.asarray(res["values"])
                if not np.issubdtype(arr.dtype, np.number):
                    continue
                shape = tuple(int(d) for d in res["shape"])
                arr = arr.reshape(shape) if int(np.prod(shape)) == arr.size else arr.ravel()
                dims: list[str] = []
                for idx, dsize in enumerate(arr.shape):
                    dname = f"{elem}_d{idx}_{dsize}"
                    if dname not in grp_nc.dimensions:
                        grp_nc.createDimension(dname, dsize)
                    dims.append(dname)
                v = grp_nc.createVariable(elem, "f8" if np.issubdtype(arr.dtype, np.floating) else "i8", tuple(dims))
                v.units = str(res.get("units", ""))
                v.long_name = str(res.get("description", ""))
                v[:] = arr
                exported_vars.append(f"{gname}/{elem}")

    return {"nc_path": str(out_nc_path), "exported_count": len(exported_vars), "variables": exported_vars}


def export_grid_geojson(grd_path: Path, out_geojson: Path, core_mod: Any, stride: int = 1) -> dict[str, Any]:
    """Export Delft3D grid lines/cells to GeoJSON without external GIS dependencies."""
    g = core_mod.read_grd(str(grd_path))
    m_max, n_max = int(g["m_max"]), int(g["n_max"])
    x = g["x"]
    y = g["y"]
    miss = float(g["missing_value"])
    step = max(1, int(stride))

    features: list[dict[str, Any]] = []
    for n in range(0, n_max, step):
        coords = [
            [x[n * m_max + m], y[n * m_max + m]]
            for m in range(m_max)
            if abs(x[n * m_max + m] - miss) > 1e-6 or abs(y[n * m_max + m] - miss) > 1e-6
        ]
        if len(coords) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"axis": "eta", "index": n + 1},
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )
    for m in range(0, m_max, step):
        coords = [
            [x[n * m_max + m], y[n * m_max + m]]
            for n in range(n_max)
            if abs(x[n * m_max + m] - miss) > 1e-6 or abs(y[n * m_max + m] - miss) > 1e-6
        ]
        if len(coords) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"axis": "xi", "index": m + 1},
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )

    fc = {"type": "FeatureCollection", "features": features}
    out_geojson.parent.mkdir(parents=True, exist_ok=True)
    out_geojson.write_text(json.dumps(fc), encoding="utf-8")
    return {"geojson_path": str(out_geojson), "feature_count": len(features)}


def compute_skill_metrics(observed: list[float], simulated: list[float]) -> dict[str, float]:
    """Compute hydrodynamic model calibration metrics (RMSE, MAE, Bias, Pearson R, NSE, Willmott d)."""
    obs = np.asarray(observed, dtype=float)
    sim = np.asarray(simulated, dtype=float)
    if obs.shape != sim.shape or obs.size == 0:
        raise ValueError("observed and simulated arrays must be non-empty and equal length.")
    mask = np.isfinite(obs) & np.isfinite(sim)
    obs = obs[mask]
    sim = sim[mask]
    diff = sim - obs
    rmse = float(np.sqrt(np.mean(diff**2)))
    mae = float(np.mean(np.abs(diff)))
    bias = float(np.mean(diff))
    obs_mean = float(np.mean(obs))
    denom_nse = float(np.sum((obs - obs_mean) ** 2))
    nse = float(1.0 - np.sum(diff**2) / denom_nse) if denom_nse > 0 else float("nan")
    denom_d = float(np.sum((np.abs(sim - obs_mean) + np.abs(obs - obs_mean)) ** 2))
    willmott_d = float(1.0 - np.sum(diff**2) / denom_d) if denom_d > 0 else float("nan")
    corr = float(np.corrcoef(obs, sim)[0, 1]) if obs.size > 1 and np.std(obs) > 0 and np.std(sim) > 0 else float("nan")
    return {
        "n": float(obs.size),
        "rmse": rmse,
        "mae": mae,
        "bias": bias,
        "pearson_r": corr,
        "nse": nse,
        "willmott_d": willmott_d,
    }
