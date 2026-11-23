"""Fast headless plotting and Delft3D-QUICKPLOT (.qplog) batch export."""

from __future__ import annotations

import contextlib
import ctypes
import re
import sys
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from delft3d_mcp.config import Delft3DPaths
from delft3d_mcp.gui.qt5_adapter import _click_coords, _find_named_control
from delft3d_mcp.gui.win32_bridge import (
    bring_window_to_front,
    launch_gui_module,
    list_delft3d_windows,
)

_PRINTFIGURE_RE = re.compile(r"^\s*printfigure\s+'([^']+)'", re.IGNORECASE | re.MULTILINE)


def read_dep_matrix(
    core_mod: Any, dep_path: Path, gm: int, gn: int
) -> tuple[np.ndarray[Any, Any], bool]:
    """Read a .dep file supporting either MNKmax-padded (gm+1, gn+1) or raw (gm, gn) shape."""
    try:
        arr = np.asarray(
            core_mod.read_dep_file(str(dep_path), gm + 1, gn + 1), dtype=float
        ).reshape((gn + 1, gm + 1))[:gn, :gm]
        return arr, True
    except Exception:
        arr = np.asarray(core_mod.read_dep_file(str(dep_path), gm, gn), dtype=float).reshape(
            (gn, gm)
        )
        return arr, False


def parse_qplog_outputs(qplog_text: str) -> list[Path]:
    """Extract output file paths from `printfigure '<path>'` lines in a .qplog macro."""
    return [Path(m.group(1)) for m in _PRINTFIGURE_RE.finditer(qplog_text)]


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
    dep_padded: bool | None = None
    if dep_path and dep_path.exists():
        dep, dep_padded = read_dep_matrix(core_mod, dep_path, m_max, n_max)
        dep_masked = np.ma.masked_where(mask | (np.abs(dep - (-999.0)) < 1e-4), dep)
        pcm = ax.pcolormesh(x_masked, y_masked, dep_masked, shading="nearest", cmap="viridis_r")
        fig.colorbar(pcm, ax=ax, label="Depth [m]")

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
    return {"png_path": str(out_png), "m_max": m_max, "n_max": n_max, "dep_padded": dep_padded}


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


def _get_dpi_scale(hwnd: int) -> float:
    with contextlib.suppress(Exception):
        dpi = int(ctypes.windll.user32.GetDpiForWindow(hwnd))
        if dpi > 0:
            return dpi / 96.0
    return 1.0


def _find_qp_file_dialog(qp_pid: int) -> int | None:
    for w in list_delft3d_windows():
        if w["pid"] == qp_pid and w.get("class_name") == "#32770":
            title_l = str(w["title"]).lower()
            if "open" in title_l or "log" in title_l:
                return int(w["hwnd"])
    return None


def run_quickplot_batch(
    paths: Delft3DPaths,
    qplog_path: Path,
    work_dir: Path,
    expected_outputs: list[Path] | None = None,
    timeout_sec: float = 90.0,
) -> dict[str, Any]:
    """Execute a .qplog batch macro in Delft3D-QUICKPLOT and wait for outputs cleanly."""
    if sys.platform != "win32":
        raise RuntimeError("Delft3D-QUICKPLOT macro execution requires Windows.")
    if not paths.d3d_qp_exe.exists():
        raise FileNotFoundError(f"Delft3D-QUICKPLOT executable not found: {paths.d3d_qp_exe}")

    import psutil
    import win32gui
    from pywinauto import Application

    qplog_text = qplog_path.read_text(encoding="utf-8", errors="replace")
    targets = expected_outputs if expected_outputs is not None else parse_qplog_outputs(qplog_text)
    for out_p in targets:
        out_p.parent.mkdir(parents=True, exist_ok=True)
    prev_mtimes = {p: p.stat().st_mtime_ns for p in targets if p.exists()}

    existing_hwnds = {int(w["hwnd"]) for w in list_delft3d_windows()}
    t0 = time.monotonic()
    launch_gui_module(paths, "quickplot", work_dir)

    qp_win: dict[str, Any] | None = None
    deadline = t0 + min(35.0, timeout_sec)
    while time.monotonic() < deadline:
        candidates = [
            w
            for w in list_delft3d_windows()
            if int(w["hwnd"]) not in existing_hwnds and "delft3d-quickplot" in str(w["title"]).lower()
        ]
        if candidates:
            qp_win = candidates[0]
            break
        time.sleep(0.4)
    if qp_win is None:
        raise TimeoutError("Delft3D-QUICKPLOT window did not appear after launch.")

    qp_hwnd = int(qp_win["hwnd"])
    qp_pid = int(qp_win["pid"])
    app = Application(backend="uia").connect(handle=qp_hwnd)
    main_win = app.window(handle=qp_hwnd)

    # Trigger Macro -> Run Log File via Alt+M R, falling back to DPI-scaled toolbar click.
    dlg_hwnd: int | None = None
    bring_window_to_front(qp_hwnd)
    with contextlib.suppress(Exception):
        main_win.set_focus()
        main_win.type_keys("%mr", pause=0.15)
    for _ in range(10):
        dlg_hwnd = _find_qp_file_dialog(qp_pid)
        if dlg_hwnd is not None:
            break
        time.sleep(0.2)

    if dlg_hwnd is None:
        bring_window_to_front(qp_hwnd)
        scale = _get_dpi_scale(qp_hwnd)
        left, top, _, _ = win32gui.GetWindowRect(qp_hwnd)
        toolbar_x = left + round(234 * scale)
        toolbar_y = top + round(65 * scale)
        _click_coords(qp_hwnd, toolbar_x, toolbar_y)
        for _ in range(12):
            dlg_hwnd = _find_qp_file_dialog(qp_pid)
            if dlg_hwnd is not None:
                break
            time.sleep(0.25)

    if dlg_hwnd is None:
        raise RuntimeError(
            "Could not open QUICKPLOT 'Run Log File' dialog automatically; inspect windows with gui_list_windows."
        )

    dlg_win = app.window(handle=dlg_hwnd)
    bring_window_to_front(dlg_hwnd)
    edit = dlg_win.child_window(title="File name:", control_type="Edit")
    edit.set_edit_text(str(qplog_path))
    time.sleep(0.1)
    try:
        open_btn = _find_named_control(dlg_win, "Open")
        wrapper = open_btn.wrapper_object()
        if hasattr(wrapper, "invoke"):
            wrapper.invoke()
        else:
            open_btn.click_input()
    except Exception:
        dlg_win.type_keys("{ENTER}")

    exec_ns = time.time_ns() - 2_000_000_000
    ends_with_close = any(ln.strip().lower() == "close" for ln in qplog_text.splitlines())
    wait_deadline = t0 + timeout_sec
    while time.monotonic() < wait_deadline:
        all_ready = bool(targets) and all(
            p.exists()
            and p.stat().st_size > 0
            and (
                p.stat().st_mtime_ns > prev_mtimes[p]
                if p in prev_mtimes
                else p.stat().st_mtime_ns >= exec_ns
            )
            for p in targets
        )
        proc_alive = psutil.pid_exists(qp_pid)
        if all_ready and (not ends_with_close or not proc_alive):
            break
        if not targets and ends_with_close and not proc_alive:
            break
        time.sleep(0.4)
    else:
        missing = [str(p) for p in targets if not p.exists()]
        raise TimeoutError(
            f"QUICKPLOT macro timed out after {timeout_sec:.1f}s. Missing outputs: {missing}"
        )

    return {
        "qplog_path": str(qplog_path),
        "elapsed_sec": round(time.monotonic() - t0, 2),
        "outputs": [
            {"path": str(p), "bytes": p.stat().st_size}
            for p in targets
            if p.exists()
        ],
        "quickplot_closed": not psutil.pid_exists(qp_pid),
    }
