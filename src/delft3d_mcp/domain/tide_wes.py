"""Input generators and native harmonic analysis for Delft3D-WES, TIDE, and RTC."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

# Standard tidal angular frequencies (degrees per hour)
TIDAL_SPEEDS_DEG_HR: dict[str, float] = {
    "M2": 28.9841042,
    "S2": 30.0000000,
    "N2": 28.4397295,
    "K2": 30.0821373,
    "K1": 15.0410686,
    "O1": 13.9430356,
    "P1": 14.9589314,
    "Q1": 13.3986609,
    "M4": 57.9682084,
    "MS4": 58.9841042,
}


def build_wes_input(params: dict[str, Any]) -> str:
    """Generate WES cyclone track / spiderweb configuration file."""
    method = params.get("method", "Holland")
    track_file = params.get("track_file", "cyclone.trk")
    output_file = params.get("output_file", "cyclone.spw")
    radius_km = float(params.get("radius_km", 300.0))
    n_radial = int(params.get("n_radial", 36))
    n_angular = int(params.get("n_angular", 36))
    lines = [
        "[General]",
        "  FileVersion = 1.00",
        f"  Method      = {method}",
        f"  TrackFile   = {track_file}",
        f"  Spiderweb   = {output_file}",
        "[Grid]",
        f"  RadiusKm    = {radius_km:.2f}",
        f"  NRadial     = {n_radial}",
        f"  NAngular    = {n_angular}",
    ]
    return "\n".join(lines) + "\n"


def build_tide_analysis_input(
    series_file: str,
    constituents: list[str],
    output_file: str,
) -> str:
    """Generate input specification for tide_analysis."""
    lines = [
        f"INPUT  = {series_file}",
        f"OUTPUT = {output_file}",
        f"CONSTITUENTS = {' '.join(constituents)}",
    ]
    return "\n".join(lines) + "\n"


def harmonic_fit_and_predict(
    times_hours: list[float],
    values: list[float],
    constituents: list[str] | None = None,
    predict_times_hours: list[float] | None = None,
) -> dict[str, Any]:
    """Least-squares tidal harmonic decomposition and optional forward prediction."""
    names = [c.upper() for c in (constituents or ["M2", "S2", "K1", "O1"])]
    valid_names = [n for n in names if n in TIDAL_SPEEDS_DEG_HR]
    if not valid_names:
        raise ValueError("No valid tidal constituents supplied.")

    t = np.asarray(times_hours, dtype=float)
    y = np.asarray(values, dtype=float)
    cols: list[np.ndarray[Any, Any]] = [np.ones_like(t)]
    for name in valid_names:
        omega = math.radians(TIDAL_SPEEDS_DEG_HR[name])
        cols.append(np.cos(omega * t))
        cols.append(np.sin(omega * t))
    design = np.column_stack(cols)
    coef, _, _, _ = np.linalg.lstsq(design, y, rcond=None)

    z0 = float(coef[0])
    parsed: list[dict[str, float | str]] = []
    for idx, name in enumerate(valid_names):
        a = float(coef[1 + 2 * idx])
        b = float(coef[2 + 2 * idx])
        amp = math.hypot(a, b)
        phase_deg = math.degrees(math.atan2(b, a)) % 360.0
        parsed.append(
            {
                "constituent": name,
                "speed_deg_hr": TIDAL_SPEEDS_DEG_HR[name],
                "amplitude": amp,
                "phase_deg": phase_deg,
            }
        )

    out: dict[str, Any] = {"z0": z0, "constituents": parsed}
    if predict_times_hours is not None:
        tp = np.asarray(predict_times_hours, dtype=float)
        yp = np.full_like(tp, z0)
        for item in parsed:
            omega = math.radians(float(item["speed_deg_hr"]))
            amp = float(item["amplitude"])
            ph = math.radians(float(item["phase_deg"]))
            yp += amp * np.cos(omega * tp - ph)
        out["predicted"] = yp.tolist()
    return out


def build_rtc_config(params: dict[str, Any]) -> str:
    """Generate XML runtime configuration for Delft3D-RTC / FBCTools."""
    mode = str(params.get("mode", "Feedback"))
    dt_sec = float(params.get("dt_sec", 60.0))
    rules_file = str(params.get("rules_file", "rtcToolsConfig.xml"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<rtcRuntimeConfig>\n"
        f"  <mode>{mode}</mode>\n"
        f"  <timeStepSeconds>{dt_sec:.2f}</timeStepSeconds>\n"
        f"  <rulesConfig>{rules_file}</rulesConfig>\n"
        "</rtcRuntimeConfig>\n"
    )
