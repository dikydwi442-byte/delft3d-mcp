"""Tests for config_d_hydro.xml generation and pre-run validator."""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

from delft3d_mcp import _core
from delft3d_mcp.domain.mdf import MdfDocument
from delft3d_mcp.runner.env import generate_config_d_hydro_xml
from delft3d_mcp.runner.validator import validate_flow_case


def test_config_d_hydro_xml_generation() -> None:
    xml_txt = generate_config_d_hydro_xml(mdf_name="case01.mdf", single_precision=False)
    root = ElementTree.fromstring(xml_txt)
    assert root.tag.endswith("deltaresHydro")
    assert "case01.mdf" in xml_txt
    assert "<library>flow2d3d</library>" in xml_txt


def test_flow_validator_checks_refs_and_cfl(tmp_path: Path) -> None:
    grd = tmp_path / "grid.grd"
    dep = tmp_path / "grid.dep"
    mdf = tmp_path / "case.mdf"

    _core.create_rect_grd(str(grd), 0.0, 0.0, 100.0, 100.0, 5, 5, 0.0, "Cartesian")
    _core.write_dep_file(str(dep), [10.0] * 25, 5, 5)

    doc = MdfDocument()
    doc.set_many(
        {
            "Filcco": "grid.grd",
            "Fildep": "grid.dep",
            "MNKmax": [6, 6, 1],
            "Itdate": "2026-01-01",
            "Tunit": "M",
            "Tstart": 0.0,
            "Tstop": 10.0,
            "Dt": 0.5,
        }
    )
    mdf.write_text(doc.to_text(), encoding="utf-8")

    res = validate_flow_case(mdf, core_mod=_core)
    assert res["valid"] is True
    assert res["metrics"]["estimated_cfl"] > 0
