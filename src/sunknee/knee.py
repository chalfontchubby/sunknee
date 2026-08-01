"""Morning/evening 'knee' timing: geometric AOI=90 crossing prediction and
observed-knee detection from generation data.

Not yet implemented -- see DESIGN.md, "Primary signal: morning/evening
knee timing". `sunknee.naive_knee.naive_knee_indices` is an unrelated
placeholder threshold detector used only to visualise captured data
until this is built.
"""
from __future__ import annotations

from datetime import date as date_type


def expected_knee_times(
    tilt: float, azimuth: float, latitude: float, longitude: float,
    day: date_type, tz: str,
):
    """AOI=90 crossing times for a candidate (tilt, azimuth), via pvlib.solarposition."""
    raise NotImplementedError("see DESIGN.md, primary signal: knee timing")


def detect_observed_knees(readings):
    """Fitted knee detection accounting for horizon shading and inverter
    low-light MPPT offset (see DESIGN.md confounders)."""
    raise NotImplementedError("see DESIGN.md, knee-timing confounders")
