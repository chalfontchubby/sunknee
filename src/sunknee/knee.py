"""Morning/evening 'knee' timing: geometric AOI=90 crossing prediction and
observed-knee detection from generation data.

Not yet implemented -- see DESIGN.md, "Algorithm Details": "Signal
decomposition: direct vs. diffuse", "Knee estimation: fit against the
exact geometric model", and "Separating shading signal from pose
signal". `sunknee.naive_knee.naive_knee_indices` is an unrelated
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
    raise NotImplementedError("see DESIGN.md, algorithm details: signal decomposition")


def detect_observed_knees(readings):
    """Regress observed power against the exact pvlib-predicted AOI
    curve for a candidate (tilt, azimuth), using quantile-Huber loss for
    the asymmetric dropout/overshoot noise -- not a local linear/
    parabolic approximation, and not a simple threshold crossing (see
    naive_knee for that placeholder). See DESIGN.md's knee-estimation
    method for why the local-approximation approach was superseded."""
    raise NotImplementedError("see DESIGN.md, algorithm details: knee estimation")
