"""Qt5 UI Automation & calibrated canvas adapter for d3d_menu, mfe_app (RGFGRID/QUICKIN/DIDO), and d3d_qp."""

from __future__ import annotations

import sys
import time
from typing import Any


def inspect_qt5_controls(hwnd: int, max_depth: int = 3) -> list[dict[str, Any]]:
    """Inspect UIA controls inside a Qt5 Delft3D window."""
    if sys.platform != "win32":
        return []
    from pywinauto import Application

    app = Application(backend="uia").connect(handle=hwnd)
    win = app.window(handle=hwnd)
    items: list[dict[str, Any]] = []

    def _walk(ctrl: Any, depth: int) -> None:
        if depth > max_depth or len(items) >= 200:
            return
        try:
            info = ctrl.element_info
            rect = info.rectangle
            items.append(
                {
                    "depth": depth,
                    "control_type": info.control_type,
                    "name": info.name,
                    "auto_id": info.automation_id,
                    "rect": [rect.left, rect.top, rect.right, rect.bottom] if rect else None,
                }
            )
            for ch in ctrl.children():
                _walk(ch, depth + 1)
        except Exception:
            return

    _walk(win, 0)
    return items


def _resolve_canvas_bounds(hwnd: int) -> tuple[int, int, int, int]:
    """Return screen bounding box (left, top, width, height) of the OpenGL/central canvas area."""
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    c_left = left + 8
    c_top = top + 88
    c_right = right - 8
    c_bottom = bottom - 28
    return c_left, c_top, max(10, c_right - c_left), max(10, c_bottom - c_top)


def _find_named_control(win: Any, target_name: str) -> Any:
    """Locate Qt5 UIA control across Button, CheckBox (toolbar actions), RadioButton, and MenuItem."""
    for ctype in ("Button", "CheckBox", "RadioButton", "MenuItem"):
        ctrl = win.child_window(title=target_name, control_type=ctype)
        if ctrl.exists(timeout=0.3):
            return ctrl
    return win.child_window(title=target_name)


def _click_coords(hwnd: int, px: int, py: int, button: str = "left", double: bool = False) -> str:
    """Click screen coordinates with Win32 PostMessage fallback if SetCursorPos is blocked."""
    import win32api
    import win32con
    import win32gui
    from pywinauto import mouse

    try:
        if double:
            mouse.double_click(button=button, coords=(px, py))
        else:
            mouse.click(button=button, coords=(px, py))
    except Exception:
        cx, cy = win32gui.ScreenToClient(hwnd, (px, py))
        lparam = win32api.MAKELONG(cx, cy)
        down_msg = win32con.WM_RBUTTONDOWN if button == "right" else win32con.WM_LBUTTONDOWN
        up_msg = win32con.WM_RBUTTONUP if button == "right" else win32con.WM_LBUTTONUP
        wparam = win32con.MK_RBUTTON if button == "right" else win32con.MK_LBUTTON
        win32gui.PostMessage(hwnd, down_msg, wparam, lparam)
        win32gui.PostMessage(hwnd, up_msg, 0, lparam)
        if double:
            dbl_msg = win32con.WM_RBUTTONDBLCLK if button == "right" else win32con.WM_LBUTTONDBLCLK
            win32gui.PostMessage(hwnd, dbl_msg, wparam, lparam)
            win32gui.PostMessage(hwnd, up_msg, 0, lparam)
        return "win32_postmessage"
    return "mouse_input"


def execute_qt5_action(
    hwnd: int,
    action: str,
    target_name: str | None = None,
    text_value: str | None = None,
    menu_path: str | None = None,
    points: list[list[float]] | None = None,
    normalized_coords: bool = True,
    double_click_last: bool = False,
    button: str = "left",
    keys: str | None = None,
) -> dict[str, Any]:
    """Execute deterministic UIA, toolbar, dialog, statusbar, or calibrated canvas actions on Qt5."""
    if sys.platform != "win32":
        raise RuntimeError("Qt5 UIA actions require Windows.")
    from pywinauto import Application, mouse

    app = Application(backend="uia").connect(handle=hwnd)
    win = app.window(handle=hwnd)
    win.set_focus()

    act = action.lower().strip()
    if act == "menu_select":
        if not menu_path:
            raise ValueError("menu_path is required for menu_select (e.g. 'Operations->Create Rectangular Grid...').")
        win.menu_select(menu_path)
        return {"status": "ok", "action": act, "menu_path": menu_path}

    if act == "click_button":
        if not target_name:
            raise ValueError("target_name is required for click_button.")
        ctrl = _find_named_control(win, target_name)
        method_used = "uia"
        try:
            wrapper = ctrl.wrapper_object()
            if hasattr(wrapper, "toggle"):
                wrapper.toggle()
                method_used = "uia_toggle"
            elif hasattr(wrapper, "invoke"):
                wrapper.invoke()
                method_used = "uia_invoke"
            else:
                ctrl.click()
                method_used = "wm_click"
        except Exception:
            ctrl.click_input()
            method_used = "click_input"
        return {"status": "ok", "action": act, "target": target_name, "method": method_used}

    if act == "click_screen_point":
        if not points or len(points[0]) < 2:
            raise ValueError("points=[[x, y]] in screen pixels is required for click_screen_point.")
        px, py = int(points[0][0]), int(points[0][1])
        used = _click_coords(hwnd, px, py, button=button)
        return {"status": "ok", "action": act, "screen_coords": [px, py], "method": used}

    if act == "set_text":
        if not target_name or text_value is None:
            raise ValueError("target_name and text_value are required for set_text.")
        edit = win.child_window(title=target_name, control_type="Edit")
        edit.set_edit_text(text_value)
        return {"status": "ok", "action": act, "target": target_name}

    if act == "send_keys":
        if not keys:
            raise ValueError("keys is required for send_keys.")
        win.type_keys(keys, with_spaces=True)
        return {"status": "ok", "action": act, "keys": keys}

    if act == "read_status_bar":
        sb = win.child_window(control_type="StatusBar")
        texts = [ch.window_text() for ch in sb.children()] if sb.exists(timeout=0.5) else []
        return {"status": "ok", "action": act, "status_texts": texts}

    if act == "canvas_click_points":
        if not points:
            raise ValueError("points (list of [x, y]) is required for canvas_click_points.")
        c_left, c_top, c_w, c_h = _resolve_canvas_bounds(hwnd)
        clicked: list[tuple[int, int]] = []
        used_methods: list[str] = []
        for idx, pt in enumerate(points):
            px = int(c_left + pt[0] * c_w) if normalized_coords else int(c_left + pt[0])
            py = int(c_top + pt[1] * c_h) if normalized_coords else int(c_top + pt[1])
            m_used = _click_coords(
                hwnd, px, py, button=button, double=(double_click_last and idx == len(points) - 1)
            )
            clicked.append((px, py))
            used_methods.append(m_used)
            time.sleep(0.08)
        return {"status": "ok", "action": act, "clicked_screen_coords": clicked, "methods": used_methods}

    if act == "canvas_drag":
        if not points or len(points) < 2:
            raise ValueError("points with at least 2 [x, y] items required for canvas_drag.")
        c_left, c_top, c_w, c_h = _resolve_canvas_bounds(hwnd)
        sx = int(c_left + points[0][0] * c_w) if normalized_coords else int(c_left + points[0][0])
        sy = int(c_top + points[0][1] * c_h) if normalized_coords else int(c_top + points[0][1])
        ex = int(c_left + points[-1][0] * c_w) if normalized_coords else int(c_left + points[-1][0])
        ey = int(c_top + points[-1][1] * c_h) if normalized_coords else int(c_top + points[-1][1])
        mouse.press(button=button, coords=(sx, sy))
        time.sleep(0.08)
        mouse.move(coords=(ex, ey))
        time.sleep(0.08)
        mouse.release(button=button, coords=(ex, ey))
        return {"status": "ok", "action": act, "from": [sx, sy], "to": [ex, ey]}

    if act == "close":
        win.close()
        return {"status": "ok", "action": "close", "hwnd": hwnd}

    raise ValueError(f"Unsupported Qt5 UIA action: {action}")


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
