"""Parsers, process table readers, and editors for Delft3D-WAQ (.inp) and Delft3D-PART (.inp/.par)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_waq_sections(text: str) -> dict[int, list[str]]:
    """Split a Delft3D-WAQ .inp file into blocks indexed by #1..#10 section markers."""
    sections: dict[int, list[str]] = {0: []}
    cur = 0
    marker_re = re.compile(r"^#(\d+)\b")
    for line in text.splitlines():
        m = marker_re.match(line.strip())
        if m:
            cur = int(m.group(1))
            sections.setdefault(cur, [])
        sections[cur].append(line)
    return sections


def update_waq_inp(text: str, section_updates: dict[int, str]) -> str:
    """Replace specific numbered sections (#1..#10) in a WAQ .inp file."""
    sections = parse_waq_sections(text)
    for sec_id, new_body in section_updates.items():
        body_lines = new_body.splitlines()
        if not body_lines or not body_lines[0].strip().startswith(f"#{sec_id}"):
            body_lines.insert(0, f"#{sec_id}")
        sections[int(sec_id)] = body_lines

    ordered: list[str] = []
    for k in sorted(sections.keys()):
        ordered.extend(sections[k])
    return "\n".join(ordered).rstrip() + "\n"


def list_waq_processes_from_install(d3d_guis_x64: Path, query: str = "", limit: int = 50) -> list[dict[str, str]]:
    """Inspect Delft3D-WAQ process definition tables from guis/x64/dwaq/default."""
    proctabl = d3d_guis_x64 / "dwaq" / "default" / "proctabl.d3d"
    if not proctabl.exists():
        return []
    q = query.lower().strip()
    results: list[dict[str, str]] = []
    for line in proctabl.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        if q and q not in s.lower():
            continue
        cols = s.split(None, 2)
        results.append(
            {
                "id": cols[0] if len(cols) > 0 else s,
                "group": cols[1] if len(cols) > 1 else "",
                "detail": cols[2] if len(cols) > 2 else "",
            }
        )
        if len(results) >= limit:
            break
    return results


def build_part_inp(params: dict[str, Any]) -> str:
    """Generate or update a clean Delft3D-PART .inp configuration block."""
    title = params.get("title", "Delft3D-PART run via delft3d-mcp")
    hyd_file = params.get("hyd_file", "com-run.hyd")
    num_particles = int(params.get("num_particles", 1000))
    t_start = int(params.get("t_start_sec", 0))
    t_stop = int(params.get("t_stop_sec", 86400))
    dt = int(params.get("dt_sec", 300))
    substances = params.get("substances", ["Tracer"])
    lines = [
        f"'{title}'",
        f"'{hyd_file}'",
        "; number of particles, start, stop, dt",
        f"{num_particles}  {t_start}  {t_stop}  {dt}",
        f"{len(substances)} ; number of substances",
    ]
    for s in substances:
        lines.append(f"'{s}'")
    return "\n".join(lines) + "\n"
