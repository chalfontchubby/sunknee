"""Placeholder threshold-based knee detector and simple capture stats.

This is NOT the real knee-detection algorithm (see DESIGN.md, "Algorithm
Details", and the stub in sunknee.knee for the intended direct/diffuse
decomposition + linear-extrapolation approach). It exists purely so
captured data has *something* to look at -- both as native HA sensors
(published by apps/sunknee_app.py) and in local matplotlib plots
(sunknee.diagnostics) -- while the real estimator is still unbuilt.

Stdlib-only: imported by the AppDaemon app, so no numpy/pandas here.
"""
from __future__ import annotations

from sunknee.capture import Reading


def naive_knee_indices(
    readings: list[Reading], threshold_w: float = 10.0
) -> tuple[int | None, int | None]:
    """Index of the first/last reading above threshold_w, or (None, None)
    if the day never crossed it."""
    above = [i for i, r in enumerate(readings) if r.watts > threshold_w]
    if not above:
        return None, None
    return above[0], above[-1]


def summary_stats(readings: list[Reading]) -> dict:
    """Count and peak-so-far for a day's readings -- simple liveness/
    diagnostic stats, not part of the real estimator."""
    if not readings:
        return {"count": 0, "peak_watts": 0.0, "peak_at": None}
    peak = max(readings, key=lambda r: r.watts)
    return {"count": len(readings), "peak_watts": peak.watts, "peak_at": peak.timestamp}
