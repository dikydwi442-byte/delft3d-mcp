"""Round-trip tests for Delft3D domain file parsers and generators."""

from __future__ import annotations

from pathlib import Path

from delft3d_mcp.domain.boundaries import (
    format_bct_file,
    format_bnd_file,
    format_crs_file,
    format_obs_file,
    format_wnd_file,
)
from delft3d_mcp.domain.mdf import (
    MdfDocument,
    format_ddb,
    format_dry_points,
    format_thin_dams,
    read_mdw,
    write_mdw,
)
from delft3d_mcp.domain.tide_wes import build_tide_analysis_input, build_wes_input
from delft3d_mcp.domain.waq_part import build_part_inp, update_waq_inp


def test_mdf_lossless_roundtrip() -> None:
    sample = (
        "* Test MDF comment\n"
        "Ident  = #Delft3D-FLOW 4.07#\n"
        "Filcco = #basin.grd#\n"
        "MNKmax = 6  6  1\n"
        "Thick  = 1.0000000e+002\n"
        "Dt     = 1.0\n"
    )
    doc = MdfDocument.from_text(sample)
    assert doc.get("MNKmax") == [6, 6, 1]
    assert doc.get("Filcco") == "basin.grd"

    doc.set_many({"Dt": "0.5", "Tstop": 60.0, "Waveol": True})
    out = doc.to_text()
    assert "* Test MDF comment" in out
    assert "Dt     = 0.5" in out
    assert "#0.5#" not in out
    assert "Tstop  = 60" in out
    assert "Waveol = #Y#" in out


def test_boundaries_and_wave_waq_part_formats(tmp_path: Path) -> None:
    bnd = format_bnd_file([{"name": "West", "btype": "Z", "forcing": "T", "m1": 1, "n1": 2, "m2": 1, "n2": 5}])
    assert "West" in bnd and "Z T" in bnd

    bct = format_bct_file(
        [{"location": "West", "records": [[0.0, 0.5, 0.5], [60.0, 1.0, 1.0]]}],
        reference_time=20260101,
    )
    assert "records-in-table     2" in bct
    assert "20260101" in bct

    assert "Obs1" in format_obs_file([{"name": "Obs1", "m": 3, "n": 3}])
    assert "Crs1" in format_crs_file([{"name": "Crs1", "m1": 2, "n1": 2, "m2": 2, "n2": 5}])
    assert "10.0000" in format_wnd_file([{"time_min": 0.0, "speed_ms": 10.0, "direction_deg": 180.0}])
    assert "3     4" in format_dry_points([(3, 4)])
    assert "U" in format_thin_dams([{"m1": 2, "n1": 2, "m2": 2, "n2": 4, "direction": "U"}])
    ddb_txt = format_ddb(
        [{"dom_a": "dom1", "m1a": 1, "n1a": 1, "m2a": 1, "n2a": 5, "dom_b": "dom2", "m1b": 5, "n1b": 1, "m2b": 7, "n2b": 5}]
    )
    assert "dom1.grd" in ddb_txt and "    7     5" in ddb_txt

    mdw_path = tmp_path / "wave.mdw"
    mdw_txt = write_mdw({"General": {"Version": "4.00"}, "Domain": {"Grid": "basin.grd"}})
    mdw_path.write_text(mdw_txt, encoding="utf-8")
    parsed_mdw = read_mdw(mdw_path)
    assert parsed_mdw["Domain"]["Grid"] == "basin.grd"

    waq = update_waq_inp("#1\n'Test'\n#2\n60\n", {2: "120\n"})
    assert "120" in waq
    assert "Tracer" in build_part_inp({"num_particles": 500})
    assert "Holland" in build_wes_input({"method": "Holland"})
    assert "M2 S2" in build_tide_analysis_input("wl.txt", ["M2", "S2"], "out.txt")


def test_bcc_bch_dis_harmonic_and_skill() -> None:
    import math

    from delft3d_mcp.domain.boundaries import format_bcc_file, format_bch_file, format_src_dis_files
    from delft3d_mcp.domain.tide_wes import build_rtc_config, harmonic_fit_and_predict
    from delft3d_mcp.post.extract import compute_skill_metrics

    assert "Salinity" in format_bcc_file(
        [{"location": "West", "param_a": "Salinity end A", "records": [[0.0, 32.0, 32.0]]}]
    )
    bch = format_bch_file([28.9841042], [[1.2]], [[1.2]], [[45.0]], [[45.0]])
    assert "28.984104" in bch
    src_t, dis_t = format_src_dis_files([{"name": "Outfall1", "m": 3, "n": 4, "records": [[0.0, 5.0, 5.0]]}])
    assert "Outfall1" in src_t and "5.000000E+00" in dis_t

    times = [float(h) for h in range(48)]
    vals = [1.5 * math.cos(math.radians(28.9841042 * h - 30.0)) + 0.5 for h in times]
    fit = harmonic_fit_and_predict(times, vals, constituents=["M2"], predict_times_hours=[48.0, 49.0])
    assert abs(fit["z0"] - 0.5) < 1e-3
    assert abs(fit["constituents"][0]["amplitude"] - 1.5) < 1e-3
    assert len(fit["predicted"]) == 2
    assert "Feedback" in build_rtc_config({"mode": "Feedback"})

    skill = compute_skill_metrics([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert skill["rmse"] == 0.0 and skill["nse"] == 1.0
