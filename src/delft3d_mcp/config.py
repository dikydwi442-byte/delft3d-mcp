"""Configuration, Delft3D 4.07.02 path discovery, and Workspace Sandbox Guard."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_D3D_ROOT = Path(r"C:\Program Files\Deltares\Delft3D 4.07.02")
STATE_DIR = Path.home() / ".delft3d_mcp"


class SecurityError(PermissionError):
    """Raised when an operation attempts to escape registered workspace boundaries."""


@dataclass(slots=True)
class Delft3DPaths:
    """Resolved binary and resource paths for local Delft3D 4.07.02 installation."""

    root: Path
    kernels_bin: Path
    kernels_lib: Path
    kernels_share: Path
    guis_x64: Path
    d_hydro: Path
    mpiexec: Path
    wave_exe: Path
    delwaq_exe: Path
    delpar_exe: Path
    agrhyd_exe: Path
    ddcouple_exe: Path
    mormerge_exe: Path
    nesthd1_exe: Path
    nesthd2_exe: Path
    maptonetcdf_exe: Path
    mfe_app_exe: Path
    d3d_menu_exe: Path
    flow_gui_exe: Path
    wave_gui_exe: Path
    waq_gui_exe: Path
    part_gui_exe: Path
    d3d_qp_exe: Path
    vs_exe: Path
    wes_exe: Path
    tide_analysis_exe: Path
    tide_predict_exe: Path
    delft3d2shape_exe: Path
    delwaq2raster_exe: Path
    sim2ugrid_exe: Path
    delft3d_matlab_dir: Path
    manuals_dir: Path

    @classmethod
    def discover(cls, root_override: str | Path | None = None) -> Delft3DPaths:
        env_root = os.environ.get("DELFT3D_ROOT")
        root = Path(root_override or env_root or DEFAULT_D3D_ROOT).resolve()
        kbin = root / "kernels" / "x64" / "bin"
        klib = root / "kernels" / "x64" / "lib"
        kshare = root / "kernels" / "x64" / "share"
        guis = root / "guis" / "x64"

        return cls(
            root=root,
            kernels_bin=kbin,
            kernels_lib=klib,
            kernels_share=kshare,
            guis_x64=guis,
            d_hydro=kbin / "d_hydro.exe",
            mpiexec=kbin / "mpiexec.exe",
            wave_exe=kbin / "wave.exe",
            delwaq_exe=kbin / "delwaq.exe",
            delpar_exe=kbin / "delpar.exe",
            agrhyd_exe=kbin / "agrhyd.exe",
            ddcouple_exe=kbin / "ddcouple.exe",
            mormerge_exe=kbin / "mormerge.exe",
            nesthd1_exe=kbin / "nesthd1.exe",
            nesthd2_exe=kbin / "nesthd2.exe",
            maptonetcdf_exe=kbin / "maptonetcdf.exe",
            mfe_app_exe=guis / "plugins" / "bin" / "mfe_app.exe",
            d3d_menu_exe=guis / "menu" / "bin" / "d3d_menu.exe",
            flow_gui_exe=guis / "dflow2d3d" / "bin" / "flow_gui.exe",
            wave_gui_exe=guis / "dwaves" / "bin" / "wave_gui.exe",
            waq_gui_exe=guis / "dwaq" / "bin" / "waq_gui.exe",
            part_gui_exe=guis / "dpart" / "bin" / "part_gui.exe",
            d3d_qp_exe=guis / "quickplot" / "bin" / "d3d_qp.exe",
            vs_exe=guis / "util" / "bin" / "vs.exe",
            wes_exe=guis / "util" / "bin" / "wes.exe",
            tide_analysis_exe=guis / "tide" / "bin" / "tide_analysis.exe",
            tide_predict_exe=guis / "tide" / "bin" / "tide_predict.exe",
            delft3d2shape_exe=guis / "plugins" / "bin" / "exe" / "Delft3D2Shape.exe",
            delwaq2raster_exe=guis / "quickplot" / "bin" / "delwaq2raster.exe",
            sim2ugrid_exe=guis / "quickplot" / "bin" / "sim2ugrid.exe",
            delft3d_matlab_dir=guis / "delft3d_matlab",
            manuals_dir=root / "manuals",
        )


@dataclass
class WorkspaceRegistry:
    """Manages registered sandboxed workspaces for safe read/write/run operations."""

    state_file: Path = field(default_factory=lambda: STATE_DIR / "workspaces.json")
    workspaces: dict[str, str] = field(default_factory=dict)
    active_name: str | None = None

    def __post_init__(self) -> None:
        self.load()

    def load(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.workspaces = {
                    str(k): str(Path(v).resolve())
                    for k, v in data.get("workspaces", {}).items()
                }
                self.active_name = data.get("active_name")
            except (json.JSONDecodeError, OSError):
                self.workspaces = {}
                self.active_name = None

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_name": self.active_name,
            "workspaces": self.workspaces,
        }
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def register(self, path: str | Path, name: str | None = None, create: bool = True) -> Path:
        target = Path(path).expanduser()
        if create:
            target.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
        if not resolved.is_dir():
            raise SecurityError(f"Workspace path is not an existing directory: {resolved}")

        # Prevent registering system / Delft3D installation directories as writable workspaces
        d3d_root = DEFAULT_D3D_ROOT.resolve()
        windows_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).resolve()
        prog_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")).resolve()
        for forbidden in (d3d_root, windows_root, prog_files):
            if resolved == forbidden or resolved.is_relative_to(d3d_root):
                raise SecurityError(f"Refusing to register protected system directory: {resolved}")

        ws_name = name or resolved.name
        self.workspaces[ws_name] = str(resolved)
        self.active_name = ws_name
        self.save()
        return resolved

    def get_active_root(self) -> Path | None:
        if self.active_name and self.active_name in self.workspaces:
            return Path(self.workspaces[self.active_name]).resolve()
        return None

    def resolve_safe_path(
        self,
        raw_path: str | Path,
        workspace: str | None = None,
        must_exist: bool = False,
    ) -> tuple[Path, Path]:
        """Resolve path and enforce strict containment inside a registered workspace."""
        ws_root: Path | None = None
        if workspace:
            if workspace not in self.workspaces:
                raise SecurityError(f"Workspace '{workspace}' is not registered.")
            ws_root = Path(self.workspaces[workspace]).resolve()
        else:
            candidate = Path(raw_path).expanduser()
            if candidate.is_absolute():
                resolved_cand = candidate.resolve()
                for root_str in self.workspaces.values():
                    root = Path(root_str).resolve()
                    if resolved_cand == root or resolved_cand.is_relative_to(root):
                        ws_root = root
                        break
            if ws_root is None:
                ws_root = self.get_active_root()

        if ws_root is None:
            raise SecurityError(
                "No registered workspace matches this path. Call project_init_workspace first."
            )

        p = Path(raw_path).expanduser()
        resolved = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
        if resolved != ws_root and not resolved.is_relative_to(ws_root):
            raise SecurityError(
                f"Path '{resolved}' escapes sandboxed workspace '{ws_root}'."
            )
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"File not found inside workspace: {resolved}")
        return resolved, ws_root
