"""Input generators for Delft3D-WES (cyclone wind) and Delft3D-TIDE."""

from __future__ import annotations

from typing import Any


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
