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


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile, stdlib-only (no numpy)."""
    ordered = sorted(values)
    idx = min(int(pct / 100 * len(ordered)), len(ordered) - 1)
    return ordered[idx]


def summary_stats(readings: list[Reading], peak_percentile: float = 95.0) -> dict:
    """Count and a smoothed peak-so-far for a day's readings -- simple
    liveness/diagnostic stats, not part of the real estimator.

    Real inverter/Modbus readings include short single-sample spikes well
    above the true curve (glitches, not the cloud-edge irradiance
    enhancement DESIGN.md's envelope section discusses). A literal max()
    picks up whichever spike happens to be tallest, so peak_watts uses a
    percentile instead -- same "percentile rejects overshoot outliers"
    logic as the envelope method, just applied within a single day rather
    than across a rolling multi-day window. raw_max_watts is kept
    alongside it so the two can be compared.
    """
    if not readings:
        return {"count": 0, "peak_watts": 0.0, "peak_at": None, "raw_max_watts": 0.0}
    watts = [r.watts for r in readings]
    peak_watts = _percentile(watts, peak_percentile)
    peak_reading = min(readings, key=lambda r: abs(r.watts - peak_watts))
    return {
        "count": len(readings),
        "peak_watts": peak_watts,
        "peak_at": peak_reading.timestamp,
        "raw_max_watts": max(watts),
    }
