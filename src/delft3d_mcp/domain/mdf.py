"""Lossless parser, writer, and schema helpers for Delft3D-FLOW (.mdf, .ddb, .mor, .sed, .dry, .thd)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_KV_RE = re.compile(r"^([A-Za-z0-9_]+)\s*=\s*(.*)$")


def _format_mdf_value(val: Any) -> str:
    if isinstance(val, bool):
        return "#Y#" if val else "#N#"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return f"{val:.7g}"
    if isinstance(val, (list, tuple)):
        if all(isinstance(x, (int, float)) for x in val):
            return "  ".join(f"{x:.7g}" if isinstance(x, float) else str(x) for x in val)
        return "  ".join(str(x) for x in val)
    s = str(val).strip()
    if s.startswith("#") and s.endswith("#") and len(s) >= 2:
        return s
    return f"#{s}#"


def _parse_mdf_scalar(raw: str) -> Any:
    s = raw.strip()
    if s.startswith("#") and s.endswith("#") and len(s) >= 2:
        return s[1:-1]
    parts = s.split()
    if len(parts) > 1:
        parsed: list[Any] = []
        for p in parts:
            try:
                if "." in p or "e" in p.lower():
                    parsed.append(float(p))
                else:
                    parsed.append(int(p))
            except ValueError:
                return s
        return parsed
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        return s


@dataclass
class MdfDocument:
    """Lossless representation of a Delft3D-FLOW .mdf file."""

    lines: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    key_order: list[str] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> MdfDocument:
        raw_lines = text.splitlines()
        params: dict[str, Any] = {}
        key_order: list[str] = []
        current_key: str | None = None
        acc_values: list[str] = []

        def flush_current() -> None:
            nonlocal current_key, acc_values
            if current_key is None:
                return
            joined = " ".join(v for v in acc_values if v.strip())
            if len(acc_values) > 1:
                # Multi-line numeric/list values
                toks = joined.split()
                vals: list[Any] = []
                for t in toks:
                    if t.startswith("#") and t.endswith("#"):
                        vals.append(t[1:-1])
                    else:
                        try:
                            vals.append(float(t) if ("." in t or "e" in t.lower()) else int(t))
                        except ValueError:
                            vals.append(t)
                params[current_key] = vals
            else:
                params[current_key] = _parse_mdf_scalar(joined)
            current_key = None
            acc_values = []

        for line in raw_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("*"):
                continue
            m = _KV_RE.match(stripped)
            if m:
                flush_current()
                key = m.group(1)
                val_part = m.group(2).strip()
                current_key = key
                if key not in key_order:
                    key_order.append(key)
                acc_values = [val_part]
            elif current_key is not None:
                acc_values.append(stripped)

        flush_current()
        return cls(lines=raw_lines, params=params, key_order=key_order)

    @classmethod
    def from_file(cls, path: Path) -> MdfDocument:
        return cls.from_text(path.read_text(encoding="utf-8", errors="replace"))

    def get(self, key: str, default: Any = None) -> Any:
        for k, v in self.params.items():
            if k.lower() == key.lower():
                return v
        return default

    def set_many(self, updates: dict[str, Any]) -> None:
        """Update or append MDF keywords while preserving existing comments and structure."""
        lower_map = {k.lower(): k for k in self.params}
        canon_updates: dict[str, Any] = {}
        for k, v in updates.items():
            actual_key = lower_map.get(k.lower(), k)
            self.params[actual_key] = v
            if actual_key not in self.key_order:
                self.key_order.append(actual_key)
            canon_updates[actual_key.lower()] = (actual_key, v)

        new_lines: list[str] = []
        skip_continuation = False
        seen_lower: set[str] = set()

        for line in self.lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("*"):
                skip_continuation = False
                new_lines.append(line)
                continue

            m = _KV_RE.match(stripped)
            if m:
                skip_continuation = False
                k_name = m.group(1)
                k_low = k_name.lower()
                if k_low in canon_updates:
                    actual_key, new_val = canon_updates[k_low]
                    seen_lower.add(k_low)
                    skip_continuation = True
                    if isinstance(new_val, list) and actual_key.lower() in {"thick", "t"}:
                        new_lines.append(f"{actual_key:<7}= {_format_mdf_value(new_val[0])}")
                        for extra in new_val[1:]:
                            new_lines.append(f"         {_format_mdf_value(extra)}")
                    else:
                        new_lines.append(f"{actual_key:<7}= {_format_mdf_value(new_val)}")
                else:
                    new_lines.append(line)
            else:
                if not skip_continuation:
                    new_lines.append(line)

        for k_low, (actual_key, new_val) in canon_updates.items():
            if k_low not in seen_lower:
                new_lines.append(f"{actual_key:<7}= {_format_mdf_value(new_val)}")

        self.lines = new_lines

    def to_text(self) -> str:
        if not self.lines:
            out: list[str] = []
            for k in self.key_order:
                out.append(f"{k:<7}= {_format_mdf_value(self.params[k])}")
            return "\n".join(out) + "\n"
        return "\n".join(self.lines).rstrip() + "\n"

    def referenced_files(self) -> dict[str, str]:
        """Extract all file-referencing keywords present in the MDF."""
        file_keys = (
            "Filcco", "Filgrd", "Fildep", "Fildry", "Filtd", "Filbnd", "FilbcT",
            "FilbcC", "FilbcH", "FilbcQ", "Filwnd", "Filwu", "Filwv", "Filwp",
            "Filsta", "Filcrs", "Filmor", "Filsed", "Filbar", "Filgat", "Filcdw",
            "Filic", "Filrgh", "Filrgs", "Fil2dw", "Filana", "Filfls", "Fillwl",
            "Filppl",
        )
        refs: dict[str, str] = {}
        for k in file_keys:
            val = self.get(k)
            if isinstance(val, str) and val.strip():
                refs[k] = val.strip()
        return refs


def parse_ini_blocks(text: str) -> dict[str, dict[str, str]]:
    """Parse Deltares INI-like files (.mor, .sed, .mdw)."""
    sections: dict[str, dict[str, str]] = {}
    current = "_global"
    sections[current] = {}
    for raw in text.splitlines():
        line = raw.split("*")[0].split("#")[0].strip() if raw.strip().startswith(("*", "#")) else raw.strip()
        if not line or line.startswith("*") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            sections[current][k.strip()] = v.strip()
    return sections


def dump_ini_blocks(sections: dict[str, dict[str, str]], header_comment: str = "") -> str:
    lines: list[str] = []
    if header_comment:
        lines.append(f"* {header_comment}")
    for sec, kv in sections.items():
        if sec != "_global":
            lines.append(f"[{sec}]")
        for k, v in kv.items():
            lines.append(f"  {k:<24} = {v}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_dry_points(points: list[tuple[int, int]]) -> str:
    """Format .dry file (m n pairs)."""
    return "\n".join(f"{m:>6}{n:>6}" for m, n in points) + ("\n" if points else "")


def format_thin_dams(dams: list[dict[str, Any]]) -> str:
    """Format .thd file (m1 n1 m2 n2 UV)."""
    lines: list[str] = []
    for d in dams:
        uv = str(d.get("direction", "U")).upper()[:1]
        lines.append(f"{int(d['m1']):>6}{int(d['n1']):>6}{int(d['m2']):>6}{int(d['n2']):>6}  {uv}")
    return "\n".join(lines) + ("\n" if lines else "")


def format_ddb(links: list[dict[str, Any]]) -> str:
    """Format .ddb Domain Decomposition file."""
    lines: list[str] = []
    for lk in links:
        lines.append(
            f"{lk['dom_a']}.grd  {int(lk['m1a']):>5} {int(lk['n1a']):>5} {int(lk['m2a']):>5} {int(lk['n2a']):>5}  "
            f"{lk['dom_b']}.grd  {int(lk['m1b']):>5} {int(lk['n1b']):>5} {int(lk['n2b']):>5} {int(lk['n2b']):>5}"
        )
    return "\n".join(lines) + ("\n" if lines else "")
