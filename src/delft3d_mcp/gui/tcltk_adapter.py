"""Safe lifecycle adapter for Tcl/Tk Delft3D GUIs (flow_gui, wave_gui, waq_gui, part_gui)."""

from __future__ import annotations

import sys
from typing import Any


def send_tcltk_close_or_keys(hwnd: int, action: str, keys: str | None = None) -> dict[str, Any]:
    """Limited safe operations on Tcl/Tk windows (focus, close, keyboard shortcut)."""
    if sys.platform != "win32":
        raise RuntimeError("Tcl/Tk window control requires Windows.")
    from pywinauto import Application

    app = Application(backend="win32").connect(handle=hwnd)
    win = app.window(handle=hwnd)
    act = action.lower().strip()
    if act == "focus":
        win.set_focus()
        return {"status": "ok", "action": "focus", "hwnd": hwnd}
    if act == "close":
        win.close()
        return {"status": "ok", "action": "close", "hwnd": hwnd}
    if act == "send_keys":
        if not keys:
            raise ValueError("keys is required for send_keys.")
        win.set_focus()
        win.type_keys(keys, with_spaces=True)
        return {"status": "ok", "action": "send_keys", "keys": keys}
    raise ValueError(
        f"Unsupported Tcl/Tk action '{action}'. Use domain file tools for parameter edits."
    )
