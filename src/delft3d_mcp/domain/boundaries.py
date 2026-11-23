"""Parsers and generators for Delft3D-FLOW boundaries, observation points, cross-sections, and wind."""

from __future__ import annotations

from typing import Any


def format_bnd_file(boundaries: list[dict[str, Any]]) -> str:
    """Format Delft3D-FLOW .bnd open boundary file.

    Each boundary dict expects:
      name, btype (Z/C/Q/R/N/T), forcing (H/T/Q), m1, n1, m2, n2,
      alpha (float, default 0.0), profile (uniform/linear/logarithmic/3d-profile, optional)
    """
    lines: list[str] = []
    for b in boundaries:
        name = str(b["name"])[:20]
        btype = str(b.get("btype", "Z")).upper()[:1]
        forcing = str(b.get("forcing", "T")).upper()[:1]
        m1, n1 = int(b["m1"]), int(b["n1"])
        m2, n2 = int(b["m2"]), int(b["n2"])
        alpha = float(b.get("alpha", 0.0))
        profile = str(b.get("profile", "uniform"))
        base = f"{name:<20} {btype} {forcing} {m1:>5} {n1:>5} {m2:>5} {n2:>5} {alpha:>10.4f}"
        if forcing in {"T", "Q"} and btype in {"C", "Q", "R", "T"}:
            base += f" {profile:<15}"
        lines.append(base)
    return "\n".join(lines) + ("\n" if lines else "")


def format_bct_file(
    tables: list[dict[str, Any]],
    reference_time: int = 20260101,
    time_unit: str = "minutes",
) -> str:
    """Format Delft3D-FLOW .bct time-series boundary condition file.

    Each table dict expects:
      location (str, must match .bnd name),
      contents (str, e.g. 'Uniform' or 'Linear'),
      interpolation (str, default 'linear'),
      param_a (str, e.g. 'water elevation (z)  end A'),
      param_b (str, e.g. 'water elevation (z)  end B'),
      unit (str, e.g. '[m]' or '[m3/s]'),
      records: list[[time, val_a, val_b]]
    """
    blocks: list[str] = []
    for idx, tbl in enumerate(tables, start=1):
        recs = tbl.get("records", [])
        loc = str(tbl["location"])[:20]
        contents = tbl.get("contents", "Uniform")
        interp = tbl.get("interpolation", "linear")
        unit = tbl.get("unit", "[m]")
        pa = tbl.get("param_a", "water elevation (z)  end A")
        pb = tbl.get("param_b", "water elevation (z)  end B")

        hdr = [
            f"table-name           'Boundary Section : {idx}'",
            f"contents             '{contents}'",
            f"location             '{loc:<20}'",
            "time-function        'non-equidistant'",
            f"reference-time       {int(reference_time)}",
            f"time-unit            '{time_unit}'",
            f"interpolation        '{interp}'",
            f"parameter            'time                '                     unit '[{time_unit[:3]}]'",
            f"parameter            '{pa:<20}'               unit '{unit}'",
            f"parameter            '{pb:<20}'               unit '{unit}'",
            f"records-in-table     {len(recs)}",
        ]
        for row in recs:
            t_val = float(row[0])
            a_val = float(row[1])
            b_val = float(row[2]) if len(row) > 2 else a_val
            hdr.append(f" {t_val:>15.6E}  {a_val:>15.6E}  {b_val:>15.6E}")
        blocks.append("\n".join(hdr))
    return "\n".join(blocks) + "\n"


def format_obs_file(stations: list[dict[str, Any]]) -> str:
    """Format .obs observation points file."""
    lines = [
        f"{str(s['name'])[:20]:<21} {int(s['m']):>5} {int(s['n']):>5}"
        for s in stations
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def format_crs_file(sections: list[dict[str, Any]]) -> str:
    """Format .crs cross-section file."""
    lines = [
        f"{str(c['name'])[:20]:<21} {int(c['m1']):>5} {int(c['n1']):>5} {int(c['m2']):>5} {int(c['n2']):>5}"
        for c in sections
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def format_wnd_file(records: list[dict[str, float]]) -> str:
    """Format uniform wind .wnd file (time_min, speed_ms, direction_deg)."""
    lines = [
        f" {float(r['time_min']):>12.4f}  {float(r['speed_ms']):>10.4f}  {float(r['direction_deg']):>10.4f}"
        for r in records
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def format_bcc_file(
    tables: list[dict[str, Any]],
    reference_time: int = 20260101,
    time_unit: str = "minutes",
) -> str:
    """Format Delft3D-FLOW .bcc constituent/transport boundary time-series file."""
    return format_bct_file(tables, reference_time=reference_time, time_unit=time_unit)


def format_bch_file(
    frequencies_deg_per_hr: list[float],
    amplitudes_end_a: list[list[float]],
    amplitudes_end_b: list[list[float]],
    phases_end_a: list[list[float]],
    phases_end_b: list[list[float]],
    z0_per_boundary: list[float] | None = None,
) -> str:
    """Format Delft3D-FLOW .bch harmonic boundary file."""
    lines: list[str] = ["  ".join(f"{f:>12.6f}" for f in frequencies_deg_per_hr), ""]
    z0s = z0_per_boundary or [0.0] * len(amplitudes_end_a)
    lines.append("  ".join(f"{z:>12.6f}" for z in z0s))
    lines.append("")
    for block in (amplitudes_end_a, amplitudes_end_b, phases_end_a, phases_end_b):
        for row in block:
            lines.append("  ".join(f"{v:>12.6f}" for v in row))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_src_dis_files(
    discharges: list[dict[str, Any]],
    reference_time: int = 20260101,
    time_unit: str = "minutes",
) -> tuple[str, str]:
    """Generate linked .src (location) and .dis (time-series) files for Delft3D-FLOW."""
    src_lines: list[str] = []
    dis_tables: list[dict[str, Any]] = []
    for d in discharges:
        name = str(d["name"])[:20]
        m, n, k = int(d["m"]), int(d["n"]), int(d.get("k", 1))
        interp = str(d.get("interpolation", "N")).upper()[:1]
        src_lines.append(f"{name:<20} {interp} {m:>5} {n:>5} {k:>5}")
        dis_tables.append(
            {
                "location": name,
                "contents": "Uniform",
                "param_a": "discharge           ",
                "param_b": "discharge (end B)   ",
                "unit": "[m3/s]",
                "records": d.get("records", [[0.0, 0.0, 0.0]]),
            }
        )
    src_txt = "\n".join(src_lines) + ("\n" if src_lines else "")
    dis_txt = format_bct_file(dis_tables, reference_time=reference_time, time_unit=time_unit)
    return src_txt, dis_txt
