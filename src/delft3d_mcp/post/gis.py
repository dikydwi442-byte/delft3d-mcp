"""GIS export helpers (GeoJSON grid, sim2ugrid, maptonetcdf, Delft3D2Shape)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
        coords: list[list[float]] = []
        for m in range(m_max):
            idx = n * m_max + m
            if abs(x[idx] - miss) > 1e-6 or abs(y[idx] - miss) > 1e-6:
                coords.append([x[idx], y[idx]])
        if len(coords) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "properties": {"axis": "eta", "index": n + 1},
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )
    for m in range(0, m_max, step):
        coords = []
        for n in range(n_max):
            idx = n * m_max + m
            if abs(x[idx] - miss) > 1e-6 or abs(y[idx] - miss) > 1e-6:
                coords.append([x[idx], y[idx]])
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
