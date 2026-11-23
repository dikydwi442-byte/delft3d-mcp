"""Tests for compiled Rust PyO3 extension (delft3d_mcp._core)."""

from __future__ import annotations

from pathlib import Path

from delft3d_mcp import _core


def test_grid_create_inspect_refine_quality(tmp_path: Path) -> None:
    grd = tmp_path / "basin.grd"
    refined = tmp_path / "basin_fine.grd"
    _core.create_rect_grd(str(grd), 0.0, 0.0, 100.0, 100.0, 6, 6, 0.0, "Cartesian")
    g = _core.read_grd(str(grd))
    assert g["m_max"] == 6
    assert g["n_max"] == 6
    assert len(g["x"]) == 36

    q = _core.check_grd_quality(str(grd))
    assert q["active_cells"] == 25
    assert q["max_abs_cos_ortho"] < 1e-9
    assert q["bad_ortho_count"] == 0

    nm, nn = _core.refine_grd(str(grd), str(refined), 2, 2)
    assert (nm, nn) == (11, 11)


def test_enclosure_and_bathy_interpolation(tmp_path: Path) -> None:
    enc = tmp_path / "basin.enc"
    _core.write_enc(str(enc), [(1, 1), (6, 1), (6, 6), (1, 6), (1, 1)])
    pts = _core.read_enc(str(enc))
    assert len(pts) == 5

    grd = tmp_path / "basin.grd"
    _core.create_rect_grd(str(grd), 0.0, 0.0, 10.0, 10.0, 5, 5, 0.0, "Cartesian")
    xyz = tmp_path / "samples.xyz"
    xyz.write_text("0 0 2.0\n40 0 6.0\n0 40 4.0\n40 40 10.0\n", encoding="utf-8")

    dep = tmp_path / "basin.dep"
    cnt = _core.interpolate_xyz(str(grd), str(xyz), str(dep), "idw", 2.0, None, 4)
    assert cnt == 25

    vals = _core.read_dep_file(str(dep), 5, 5)
    assert abs(vals[0] - 2.0) < 1e-6
    assert abs(vals[24] - 10.0) < 1e-6

    dep_smooth = tmp_path / "basin_smooth.dep"
    _core.smooth_dep_file(str(dep), str(dep_smooth), 5, 5, 3, False, -999.0)
    vals2 = _core.read_dep_file(str(dep_smooth), 5, 5)
    assert len(vals2) == 25


def test_tail_diag_log(tmp_path: Path) -> None:
    diag = tmp_path / "tri-diag.test"
    diag.write_text(
        "*** MESSAGE Init\n*** WARNING Small depth\nSimulation time = 10.0 min\n*** ERROR Blowup\n",
        encoding="utf-8",
    )
    summary = _core.tail_diag_log(str(diag), 10)
    assert summary["warning_count"] == 1
    assert summary["error_count"] == 1
    assert "Simulation time" in summary["last_progress_line"]


def test_tekal_curvilinear_tfi_and_orthogonalize(tmp_path: Path) -> None:
    spl = tmp_path / "river.spl"
    _core.write_tekal_file(
        str(spl),
        [
            ("BANK_S", [[0.0, 0.0], [50.0, 15.0], [100.0, 0.0]]),
            ("BANK_N", [[0.0, 80.0], [50.0, 95.0], [100.0, 80.0]]),
        ],
    )
    feats = _core.read_tekal_file(str(spl))
    assert len(feats) == 2

    grd = tmp_path / "curv.grd"
    res = _core.create_curvilinear_grd(
        str(grd),
        feats[0]["points"],
        feats[1]["points"],
        11,
        9,
        None,
        None,
        "Cartesian",
        25,
    )
    assert res["m_max"] == 11
    assert res["n_max"] == 9
    assert res["active_cells"] == 80

    ortho_grd = tmp_path / "curv_ortho.grd"
    imp = _core.orthogonalize_grd(str(grd), str(ortho_grd), 40, 0.8)
    assert imp["after_mean_abs_cos"] <= imp["before_mean_abs_cos"] + 1e-6

    trans_grd = tmp_path / "curv_shift.grd"
    _core.transform_grd(str(ortho_grd), str(trans_grd), 500.0, 200.0, 1.0, 1.0, 0.0)
    masked_cnt = _core.mask_grd_block(str(trans_grd), str(trans_grd), 1, 2, 1, 2, True)
    assert masked_cnt == 4

    xyz = tmp_path / "avg.xyz"
    xyz.write_text("0 0 4.0\n1 1 6.0\n", encoding="utf-8")
    _core.create_rect_grd(str(grd), 0.0, 0.0, 10.0, 10.0, 3, 3, 0.0, "Cartesian")
    dep_avg = tmp_path / "avg.dep"
    assert _core.interpolate_xyz(str(grd), str(xyz), str(dep_avg), "cell_average", 2.0, 5.0, 4) == 9
    avg_vals = _core.read_dep_file(str(dep_avg), 3, 3)
    assert abs(avg_vals[0] - 5.0) < 1e-6
