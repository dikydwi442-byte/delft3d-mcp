"""NEFIS extraction via Rust core with optional NetCDF/MATLAB cross-validation."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from delft3d_mcp.config import Delft3DPaths


def list_nefis_groups(dat_path: Path, core_mod: Any, def_path: Path | None = None) -> list[dict[str, Any]]:
    return list(core_mod.nefis_list_groups(str(dat_path), str(def_path) if def_path else None))


def extract_nefis_slice(
    dat_path: Path,
    group_name: str,
    element_name: str,
    core_mod: Any,
    vd_start: int | None = None,
    vd_end: int | None = None,
    def_path: Path | None = None,
) -> dict[str, Any]:
    ds = core_mod.nefis_read_element(
        str(dat_path),
        group_name,
        element_name,
        str(def_path) if def_path else None,
        vd_start,
        vd_end,
    )
    vals = ds["values"]
    arr = np.asarray(vals)
    summary: dict[str, Any] = {
        "group": ds["group"],
        "element": ds["element"],
        "units": ds["units"],
        "description": ds["description"],
        "shape": ds["shape"],
        "count": int(arr.size),
    }
    if np.issubdtype(arr.dtype, np.number) and arr.size > 0:
        finite = arr[np.isfinite(arr) & (np.abs(arr - (-999.0)) > 1e-4)]
        if finite.size > 0:
            summary["min"] = float(np.min(finite))
            summary["max"] = float(np.max(finite))
            summary["mean"] = float(np.mean(finite))
        summary["values_preview"] = arr.ravel()[:200].tolist()
    else:
        summary["values_preview"] = vals[:100]
    return summary


def matlab_vs_let_bridge(
    paths: Delft3DPaths,
    dat_path: Path,
    group_name: str,
    element_name: str,
    timeout_sec: int = 60,
) -> dict[str, Any]:
    """Optional cross-check using installed MATLAB + delft3d_matlab vs_use/vs_let."""
    matlab_exe = shutil.which("matlab")
    if not matlab_exe:
        raise FileNotFoundError("MATLAB executable not found in PATH.")

    dm_dir = str(paths.delft3d_matlab_dir).replace("'", "''")
    dfile = str(dat_path).replace("'", "''")
    grp = group_name.replace("'", "''")
    elm = element_name.replace("'", "''")
    cmd_str = (
        f"addpath('{dm_dir}'); "
        f"nfs = vs_use('{dfile}', 'quiet'); "
        f"val = vs_let(nfs, '{grp}', '{elm}', 'quiet'); "
        f"fprintf('SIZE=%s\\nMIN=%.10g\\nMAX=%.10g\\n', mat2str(size(val)), min(val(:)), max(val(:)));"
    )
    proc = subprocess.run(
        [matlab_exe, "-batch", cmd_str],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        shell=False,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
