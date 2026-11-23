"""Win32 window lifecycle, module launcher, and mss screenshot capture for Delft3D GUIs."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import mss
import mss.tools
import psutil

from delft3d_mcp.config import Delft3DPaths


def build_gui_env(paths: Delft3DPaths, module: str) -> dict[str, str]:
    """Replicate d3d_menu.bat / rgfgrid.cmd / quickin.cmd environment variables safely."""
    env = os.environ.copy()
    d3d_home_m = str(paths.guis_x64)
    env["D3D_HOME"] = str(paths.guis_x64.parent)
    env["ARCH"] = "x64"
    env["WL_PLUGINS_HOME"] = str(paths.guis_x64 / "plugins")

    if module in {"rgfgrid", "quickin", "dido"}:
        env["QT_PLUGIN_PATH"] = str(paths.guis_x64 / "plugins" / "bin")
        extra = [str(paths.guis_x64 / "plugins" / "bin")]
    elif module == "quickplot":
        env["QT_PLUGIN_PATH"] = str(paths.guis_x64 / "quickplot" / "bin")
        extra = [str(paths.guis_x64 / "quickplot" / "bin")]
    else:
        env["QT_PLUGIN_PATH"] = str(paths.guis_x64 / "menu" / "bin")
        extra = [str(paths.guis_x64 / "lib"), str(paths.guis_x64 / "menu" / "bin")]

    env["PATH"] = os.pathsep.join([*extra, d3d_home_m, env.get("PATH", "")])
    return env


def launch_gui_module(
    paths: Delft3DPaths,
    module: str,
    work_dir: Path,
    target_file: Path | None = None,
) -> dict[str, Any]:
    """Launch a Delft3D desktop GUI module cleanly without blocking stdio."""
    mod = module.lower().strip()
    if mod == "menu":
        cmd = [str(paths.d3d_menu_exe)]
    elif mod in {"rgfgrid", "quickin", "dido"}:
        cmd = [str(paths.mfe_app_exe), f"{mod}.dll", mod]
        if target_file:
            cmd.append(str(target_file))
    elif mod == "flow_gui":
        cmd = [str(paths.flow_gui_exe)]
    elif mod == "wave_gui":
        cmd = [str(paths.wave_gui_exe)]
    elif mod == "waq_gui":
        cmd = [str(paths.waq_gui_exe)]
    elif mod == "part_gui":
        cmd = [str(paths.part_gui_exe)]
    elif mod == "quickplot":
        cmd = [str(paths.d3d_qp_exe)]
        if target_file:
            cmd.append(str(target_file))
    elif mod == "vsi":
        cmd = [str(paths.guis_x64 / "util" / "bin" / "vsi.exe")]
    else:
        raise ValueError(f"Unknown Delft3D GUI module: {module}")

    env = build_gui_env(paths, mod)
    proc = subprocess.Popen(
        cmd,
        cwd=str(work_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    time.sleep(0.8)
    return {
        "module": mod,
        "pid": proc.pid,
        "command": cmd,
        "work_dir": str(work_dir),
        "running": psutil.pid_exists(proc.pid),
    }


def list_delft3d_windows() -> list[dict[str, Any]]:
    """Enumerate visible top-level Delft3D-related windows on Windows."""
    if sys.platform != "win32":
        return []
    import win32gui
    import win32process

    target_exes = {
        "d3d_menu.exe",
        "mfe_app.exe",
        "flow_gui.exe",
        "wave_gui.exe",
        "waq_gui.exe",
        "part_gui.exe",
        "d3d_qp.exe",
        "vsi.exe",
        "tide.exe",
    }
    found: list[dict[str, Any]] = []

    def _cb(hwnd: int, _: Any) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe_name = ""
        with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
            exe_name = psutil.Process(pid).name().lower()
        if exe_name in target_exes or any(
            kw in title.lower() for kw in ("delft3d", "rgfgrid", "quickin", "dido", "quickplot")
        ):
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            found.append(
                {
                    "hwnd": hwnd,
                    "pid": pid,
                    "exe": exe_name,
                    "title": title,
                    "rect": {
                        "left": left,
                        "top": top,
                        "width": max(0, right - left),
                        "height": max(0, bottom - top),
                    },
                }
            )

    win32gui.EnumWindows(_cb, None)
    return found


def capture_window_screenshot(
    out_png: Path,
    hwnd: int | None = None,
    title_contains: str | None = None,
) -> dict[str, Any]:
    """Focus target Delft3D window and capture PNG via Win32 PrintWindow with mss fallback."""
    if sys.platform != "win32":
        raise RuntimeError("Window capture is only supported on Windows.")
    import ctypes

    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    windows = list_delft3d_windows()
    target: dict[str, Any] | None = None
    if hwnd is not None:
        target = next((w for w in windows if w["hwnd"] == hwnd), None)
    elif title_contains:
        needle = title_contains.lower()
        target = next((w for w in windows if needle in w["title"].lower()), None)
    elif windows:
        target = windows[0]

    if target is None:
        raise RuntimeError("No matching visible Delft3D window found to capture.")

    thwnd = int(target["hwnd"])
    if win32gui.IsIconic(thwnd):
        win32gui.ShowWindow(thwnd, win32con.SW_RESTORE)
    with contextlib.suppress(Exception):
        win32gui.SetForegroundWindow(thwnd)
    time.sleep(0.25)

    left, top, right, bottom = win32gui.GetWindowRect(thwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)
    region = {
        "left": max(0, left),
        "top": max(0, top),
        "width": width,
        "height": height,
    }
    out_png.parent.mkdir(parents=True, exist_ok=True)

    captured = False
    hwnd_dc = win32gui.GetWindowDC(thwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bmp)
        # PW_RENDERFULLCONTENT = 2 works reliably for Qt5/OpenGL windows and occluded windows
        res = ctypes.windll.user32.PrintWindow(thwnd, save_dc.GetSafeHdc(), 2)
        if res == 1:
            bmpinfo = bmp.GetInfo()
            bmpstr = bmp.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )
            img.save(out_png)
            captured = True
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(thwnd, hwnd_dc)

    if not captured:
        with mss.mss() as sct:
            shot = sct.grab(region)
            mss.tools.to_png(shot.rgb, shot.size, output=str(out_png))

    return {
        "png_path": str(out_png),
        "hwnd": thwnd,
        "title": target["title"],
        "region": region,
        "method": "PrintWindow" if captured else "mss",
    }
