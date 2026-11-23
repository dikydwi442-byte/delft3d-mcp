"""Fast headless plotting and QUICKPLOT/MATLAB figure export."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def render_grid_and_bathy_png(
    grd_path: Path,
    out_png: Path,
    core_mod: Any,
    dep_path: Path | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Render a grid mesh (and optional bathymetry shading) to PNG headlessly."""
    g = core_mod.read_grd(str(grd_path))
    m_max, n_max = int(g["m_max"]), int(g["n_max"])
    x = np.asarray(g["x"], dtype=float).reshape((n_max, m_max))
    y = np.asarray(g["y"], dtype=float).reshape((n_max, m_max))
    miss = float(g["missing_value"])
    mask = (np.abs(x - miss) < 1e-6) & (np.abs(y - miss) < 1e-6)
    x_masked = np.ma.masked_where(mask, x)
    y_masked = np.ma.masked_where(mask, y)

    fig, ax = plt.subplots(figsize=(9, 7), dpi=150)
    if dep_path and dep_path.exists():
        dep = np.asarray(core_mod.read_dep_file(str(dep_path), m_max, n_max), dtype=float).reshape((n_max, m_max))
        dep_masked = np.ma.masked_where(mask | (np.abs(dep - (-999.0)) < 1e-4), dep)
        pcm = ax.pcolormesh(x_masked, y_masked, dep_masked, shading="nearest", cmap="viridis_r")
        fig.colorbar(pcm, ax=ax, label="Depth [m]")

    # Draw grid lines
    for n in range(n_max):
        ax.plot(x_masked[n, :], y_masked[n, :], color="black", linewidth=0.35, alpha=0.45)
    for m in range(m_max):
        ax.plot(x_masked[:, m], y_masked[:, m], color="black", linewidth=0.35, alpha=0.45)

    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title or grd_path.name)
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)
    return {"png_path": str(out_png), "m_max": m_max, "n_max": n_max}


def render_timeseries_png(
    times: list[float],
    values: list[float],
    out_png: Path,
    ylabel: str = "Value",
    title: str = "Delft3D Time Series",
) -> str:
    fig, ax = plt.subplots(figsize=(9, 4), dpi=150)
    ax.plot(times, values, linewidth=1.4)
    ax.grid(True, alpha=0.3)
    ax.set_xlabel("Time")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)
    return str(out_png)
