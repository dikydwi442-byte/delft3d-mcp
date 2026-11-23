"""Tests for QUICKPLOT batch helpers, FLOW-padded DEP snapshots, and GUI guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from delft3d_mcp import _core
from delft3d_mcp.config import Delft3DPaths
from delft3d_mcp.gui.qt5_adapter import execute_qt5_action
from delft3d_mcp.gui.win32_bridge import launch_gui_module
from delft3d_mcp.post.quickplot import parse_qplog_outputs, render_grid_and_bathy_png


def test_render_grid_and_bathy_supports_flow_padded_dep(tmp_path: Path) -> None:
    grd = tmp_path / "bay.grd"
    dep = tmp_path / "bay.dep"
    out_png = tmp_path / "bay.png"

    _core.create_rect_grd(str(grd), 0.0, 0.0, 50.0, 50.0, 4, 3, 0.0, "Cartesian")
    # Write MNKmax-padded (m+1, n+1) = (5, 4) = 20 values instead of raw 4*3 = 12.
    _core.write_dep_file(str(dep), [-10.0] * 20, 5, 4)

    res = render_grid_and_bathy_png(grd, out_png, _core, dep_path=dep, title="Padded Bathy")
    assert res["m_max"] == 4
    assert res["n_max"] == 3
    assert res["dep_padded"] is True
    assert out_png.exists() and out_png.stat().st_size > 0


def test_parse_qplog_outputs_extracts_printfigure_paths() -> None:
    script = (
        "openfile 'D:\\data\\trim-case.dat'\n"
        "quickview\n"
        "printfigure 'D:\\out\\fig1.png' 'PNG file' 2 300 1\n"
        "closefigure\n"
        "printfigure 'D:\\out\\fig2.png' 'PNG file' 2 300 1\n"
        "close\n"
    )
    paths = parse_qplog_outputs(script)
    assert [str(p) for p in paths] == [r"D:\out\fig1.png", r"D:\out\fig2.png"]


def test_launch_quickplot_rejects_qplog_cli_target(tmp_path: Path) -> None:
    qplog = tmp_path / "batch.qplog"
    qplog.write_text("close\n", encoding="utf-8")
    paths = Delft3DPaths.discover()

    with pytest.raises(ValueError, match="post_run_qplog"):
        launch_gui_module(paths, "quickplot", tmp_path, target_file=qplog)


def test_qt5_unsupported_action_lists_supported_actions() -> None:
    with pytest.raises(ValueError, match="send_keys"):
        execute_qt5_action(hwnd=0, action="nonexistent_action")
