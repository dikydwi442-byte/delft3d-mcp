"""Isolated environment builder and config_d_hydro.xml generator for Delft3D 4.07.02."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from delft3d_mcp.config import Delft3DPaths


def build_kernel_env(paths: Delft3DPaths, extra_paths: list[Path] | None = None) -> dict[str, str]:
    """Construct clean subprocess environment with Delft3D bin/lib directories prepended."""
    env = os.environ.copy()
    prepends: list[str] = [
        str(paths.kernels_bin),
        str(paths.kernels_share),
        str(paths.kernels_lib),
        str(paths.guis_x64 / "util" / "bin"),
        str(paths.guis_x64 / "quickplot" / "bin"),
        str(paths.guis_x64 / "plugins" / "bin"),
        str(paths.guis_x64 / "plugins" / "bin" / "exe"),
        str(paths.guis_x64 / "tide" / "bin"),
        str(paths.guis_x64 / "lib"),
    ]
    if extra_paths:
        for ep in extra_paths:
            prepends.insert(0, str(ep))
    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(prepends + ([existing_path] if existing_path else []))
    env["D3D_HOME"] = str(paths.guis_x64.parent)
    env["ARCH"] = "x64"
    return env


def generate_config_d_hydro_xml(
    mdf_name: str | None = None,
    ddb_name: str | None = None,
    single_precision: bool = False,
    enable_delft_online: bool = False,
) -> str:
    """Generate config_d_hydro.xml matching Deltares create_config_xml.tcl schema."""
    if bool(mdf_name) == bool(ddb_name):
        raise ValueError("Specify exactly one of mdf_name or ddb_name.")
    lib = "flow2d3d_sp" if single_precision else "flow2d3d"
    now_str = datetime.now().strftime("%d %B %Y, %H:%M:%S")
    if mdf_name:
        target_tag = f"        <mdfFile>{escape(mdf_name)}</mdfFile>"
        url_file = f"{Path(mdf_name).stem}.url"
    else:
        assert ddb_name is not None
        target_tag = f"        <ddbFile>{escape(ddb_name)}</ddbFile>"
        url_file = f"{Path(ddb_name).stem}.url"

    dol_block = ""
    if enable_delft_online:
        dol_block = f"""    <delftOnline>
        <enabled>true</enabled>
        <urlFile>{escape(url_file)}</urlFile>
        <waitOnStart>false</waitOnStart>
        <clientControl>true</clientControl>
        <clientWrite>false</clientWrite>
    </delftOnline>"""

    return (
        '<?xml version="1.0" encoding="iso-8859-1"?>\n'
        '<deltaresHydro xmlns="http://schemas.deltares.nl/deltaresHydro" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://schemas.deltares.nl/deltaresHydro '
        'http://content.oss.deltares.nl/schemas/d_hydro-1.00.xsd">\n'
        "    <documentation>\n"
        "        File created by    : delft3d-mcp\n"
        f"        File creation date : {now_str}\n"
        "        File version       : 1.00\n"
        "    </documentation>\n"
        "    <control>\n"
        "        <sequence>\n"
        "            <start>myNameFlow</start>\n"
        "        </sequence>\n"
        "    </control>\n"
        '    <flow2D3D name="myNameFlow">\n'
        f"        <library>{lib}</library>\n"
        f"{target_tag}\n"
        "    </flow2D3D>\n"
        f"{dol_block}\n"
        "</deltaresHydro>\n"
    )
