"""Placeholder threshold-based knee detector.

This is NOT the real knee-detection algorithm (see DESIGN.md, "Primary
signal: morning/evening knee timing", and the stub in sunknee.knee for
the intended AOI=90/pvlib-based approach). It exists purely so captured
data has *something* to look at -- both as native HA sensors (published
by apps/sunknee_app.py) and in local matplotlib plots (sunknee.diagnostics)
-- while the real estimator is still unbuilt.

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
