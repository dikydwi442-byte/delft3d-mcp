"""Pre-run dry validator for Delft3D FLOW and WAVE cases."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from delft3d_mcp.domain.mdf import MdfDocument, read_mdw


def validate_flow_case(mdf_path: Path, core_mod: Any | None = None) -> dict[str, Any]:
    """Validate MDF file references, grid/bathymetry dimensions, and Courant number."""
    doc = MdfDocument.from_file(mdf_path)
    base_dir = mdf_path.parent
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    refs = doc.referenced_files()
    for key, rel_file in refs.items():
        fpath = (base_dir / rel_file).resolve()
        if not fpath.exists():
            errors.append(f"Missing referenced file {key}='{rel_file}' ({fpath})")

    mnk = doc.get("MNKmax")
    m_mdf = n_mdf = k_mdf = None
    if isinstance(mnk, list) and len(mnk) >= 3:
        m_mdf, n_mdf, k_mdf = int(mnk[0]), int(mnk[1]), int(mnk[2])
        metrics["mnk_max"] = [m_mdf, n_mdf, k_mdf]
    else:
        errors.append("MNKmax is missing or invalid in MDF.")

    dt_val = doc.get("Dt")
    tstart = doc.get("Tstart")
    tstop = doc.get("Tstop")
    tunit = str(doc.get("Tunit", "M")).upper()
    if dt_val is None or float(dt_val) <= 0:
        errors.append("Time step Dt must be > 0.")
    if tstart is not None and tstop is not None and float(tstop) <= float(tstart):
        errors.append(f"Tstop ({tstop}) must be greater than Tstart ({tstart}).")

    # Cross-check grid and bathymetry dimensions + CFL estimate if Rust core is available
    filcco = refs.get("Filcco")
    if core_mod is not None and filcco and (base_dir / filcco).exists():
        try:
            q = core_mod.check_grd_quality(str(base_dir / filcco))
            metrics["grid_quality"] = q
            if (
                m_mdf is not None
                and n_mdf is not None
                and (q["m_max"] + 1 != m_mdf or q["n_max"] + 1 != n_mdf)
            ):
                    warnings.append(
                        f"Grid nodes ({q['m_max']}x{q['n_max']}) vs MNKmax ({m_mdf}x{n_mdf}): "
                        "Delft3D-FLOW expects MNKmax M=m_grid+1, N=n_grid+1."
                    )
            if q["bad_ortho_count"] > 0:
                warnings.append(
                    f"Grid has {q['bad_ortho_count']} cells with |cos(ortho)| > 0.04 "
                    f"(max={q['max_abs_cos_ortho']:.4f})."
                )

            # Check bathymetry count and CFL
            fildep = refs.get("Fildep")
            max_depth = 10.0
            if fildep and (base_dir / fildep).exists():
                dep_vals: list[float] | None = None
                try:
                    if m_mdf is not None and n_mdf is not None:
                        dep_vals = core_mod.read_dep_file(str(base_dir / fildep), m_mdf, n_mdf)
                except Exception:
                    dep_vals = None
                if dep_vals is None:
                    try:
                        dep_vals = core_mod.read_dep_file(
                            str(base_dir / fildep), q["m_max"], q["n_max"]
                        )
                        warnings.append(
                            f"DEP file has grid-node count ({q['m_max']}x{q['n_max']}); "
                            f"Delft3D-FLOW expects MNKmax size ({m_mdf}x{n_mdf}) with -999.0 border padding."
                        )
                    except Exception as dep_exc:
                        errors.append(f"DEP dimension mismatch: {dep_exc}")
                if dep_vals:
                    valid_depths = [abs(v) for v in dep_vals if abs(v - (-999.0)) > 1e-4]
                    if valid_depths:
                        max_depth = max(valid_depths)
                        metrics["max_abs_depth_m"] = max_depth
                        metrics["min_abs_depth_m"] = min(valid_depths)

            if dt_val is not None:
                dt_sec = float(dt_val) * (60.0 if tunit.startswith("M") else 1.0)
                min_ds = min(
                    x for x in (q["min_dx"], q["min_dy"]) if x > 0
                ) if (q["min_dx"] > 0 or q["min_dy"] > 0) else 0.0
                if min_ds > 0:
                    cfl = (dt_sec * math.sqrt(9.81 * max(max_depth, 0.1))) / min_ds
                    metrics["estimated_cfl"] = round(cfl, 3)
                    if cfl > 10.0:
                        warnings.append(
                            f"High estimated Courant number ({cfl:.2f} > 10). Consider reducing Dt."
                        )
        except Exception as exc:
            errors.append(f"Grid/Depth validation failed: {exc}")

    return {
        "valid": len(errors) == 0,
        "mdf_path": str(mdf_path),
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
        "referenced_files": refs,
    }


def validate_wave_case(mdw_path: Path) -> dict[str, Any]:
    """Validate Delft3D-WAVE .mdw references and required sections."""
    sections = read_mdw(mdw_path)
    base_dir = mdw_path.parent
    errors: list[str] = []
    warnings: list[str] = []

    if "General" not in sections:
        errors.append("Missing [General] section in MDW.")
    for sec_name, kv in sections.items():
        if sec_name.startswith("Domain"):
            grd = kv.get("Grid")
            if grd and not (base_dir / grd.strip("#")).exists():
                errors.append(f"Missing WAVE grid file in [{sec_name}]: {grd}")

    return {
        "valid": len(errors) == 0,
        "mdw_path": str(mdw_path),
        "sections": list(sections.keys()),
        "errors": errors,
        "warnings": warnings,
    }
