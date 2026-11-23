"""End-to-end integration test executing real Delft3D 4.07.02 d_hydro.exe on a synthetic 5x5 basin."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from delft3d_mcp import _core
from delft3d_mcp.config import Delft3DPaths
from delft3d_mcp.domain.boundaries import format_bct_file, format_bnd_file, format_obs_file
from delft3d_mcp.domain.mdf import MdfDocument
from delft3d_mcp.runner.job_manager import JobManager
from delft3d_mcp.runner.validator import validate_flow_case


@pytest.mark.requires_delft3d
def test_e2e_micro_flow_simulation_and_nefis(tmp_path: Path) -> None:
    paths = Delft3DPaths.discover()
    if not paths.d_hydro.exists():
        pytest.skip("Delft3D 4.07.02 d_hydro.exe not found on this machine")

    work_dir = tmp_path / "micro_sim"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create 5x5 grid (node count 5x5 -> Delft3D MNKmax = 6x6x1) & enclosure & depth (6x6 padded)
    grd = work_dir / "micro.grd"
    enc = work_dir / "micro.enc"
    dep = work_dir / "micro.dep"
    _core.create_rect_grd(str(grd), 0.0, 0.0, 100.0, 100.0, 5, 5, 0.0, "Cartesian")
    _core.write_enc(str(enc), [(1, 1), (6, 1), (6, 6), (1, 6), (1, 1)])
    dep_6x6: list[float] = []
    for n in range(6):
        for m in range(6):
            dep_6x6.append(-999.0 if (m == 5 or n == 5) else 5.0)
    _core.write_dep_file(str(dep), dep_6x6, 6, 6)

    # 2. Boundary & observation station
    bnd = work_dir / "micro.bnd"
    bct = work_dir / "micro.bct"
    obs = work_dir / "micro.obs"
    bnd.write_text(
        format_bnd_file([{"name": "OpenWest", "btype": "Z", "forcing": "T", "m1": 1, "n1": 2, "m2": 1, "n2": 5}]),
        encoding="utf-8",
    )
    bct.write_text(
        format_bct_file(
            [{"location": "OpenWest", "records": [[0.0, 0.0, 0.0], [10.0, 0.1, 0.1]]}],
            reference_time=20260101,
        ),
        encoding="utf-8",
    )
    obs.write_text(format_obs_file([{"name": "CenterObs", "m": 3, "n": 3}]), encoding="utf-8")

    # 3. Minimal valid MDF
    mdf = work_dir / "micro.mdf"
    doc = MdfDocument()
    doc.set_many(
        {
            "Ident": "Delft3D-FLOW  .03.55.00",
            "Runtxt": "Micro E2E test",
            "Filcco": "micro.grd",
            "Filgrd": "micro.enc",
            "MNKmax": [6, 6, 1],
            "Thick": [100.0],
            "Anglat": 0.0,
            "Grdang": 0.0,
            "Fildep": "micro.dep",
            "Itdate": "2026-01-01",
            "Tunit": "M",
            "Tstart": 0.0,
            "Tstop": 5.0,
            "Dt": 0.5,
            "Zeta0": 0.0,
            "Filbnd": "micro.bnd",
            "FilbcT": "micro.bct",
            "Filsta": "micro.obs",
            "Roumet": "C",
            "Ccofu": 65.0,
            "Ccofv": 65.0,
            "Vicouv": 1.0,
            "Dicouv": 1.0,
            "Flmap": [0.0, 1.0, 5.0],
            "Flhis": [0.0, 1.0, 5.0],
            "Flpp": [0.0, 0.0, 0.0],
            "Flrst": 0.0,
        }
    )
    mdf.write_text(doc.to_text(), encoding="utf-8")

    val = validate_flow_case(mdf, core_mod=_core)
    assert val["valid"], f"Validation failed: {val}"

    jm = JobManager(paths, registry_path=tmp_path / "jobs.json")
    job = jm.start_simulation(mode="flow_single", work_dir=work_dir, input_file=mdf)

    deadline = time.time() + 45.0
    status = jm.get_status(job.job_id)
    while status["status"] == "running" and time.time() < deadline:
        time.sleep(0.5)
        status = jm.get_status(job.job_id)

    assert status["status"] == "completed", (
        f"Job did not complete cleanly: {status}\nLog: {Path(job.log_path).read_text(errors='replace')}"
    )

    tri_diag = work_dir / "tri-diag.micro"
    assert tri_diag.exists()
    diag_summary = _core.tail_diag_log(str(tri_diag), 20)
    assert diag_summary["error_count"] == 0, f"tri-diag errors: {diag_summary['errors']}"

    trim_dat = work_dir / "trim-micro.dat"
    trih_dat = work_dir / "trih-micro.dat"
    assert trim_dat.exists() and trih_dat.exists()

    groups_his = _core.nefis_list_groups(str(trih_dat), None)
    assert "his-series" in [g["name"] for g in groups_his]

    zwl = _core.nefis_read_element(str(trih_dat), "his-series", "ZWL", None, None, None)
    assert len(zwl["values"]) >= 2

    groups_map = _core.nefis_list_groups(str(trim_dat), None)
    assert "map-series" in [g["name"] for g in groups_map]
    s1 = _core.nefis_read_element(str(trim_dat), "map-series", "S1", None, 0, 1)
    assert len(s1["values"]) == 36
    assert all(v == v for v in s1["values"]), "Step 0 (vd=0) must not contain NaN values"

    from delft3d_mcp.post.extract import export_nefis_to_netcdf

    nc_out = work_dir / "micro_trim.nc"
    nc_res = export_nefis_to_netcdf(trim_dat, nc_out, _core, groups=["map-series"])
    assert nc_out.exists() and nc_res["exported_count"] > 0
