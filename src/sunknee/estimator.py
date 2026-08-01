"""Recursive state estimator for (tilt, azimuth), updated once per usable
day from knee and/or envelope observations.

Not yet implemented -- see DESIGN.md, "State update over time" (RLS/Kalman
filter; open decision on one combined estimator vs. two independent ones).
"""
from __future__ import annotations


class TiltAzimuthEstimator:
    def __init__(self, initial_tilt: float, initial_azimuth: float):
        raise NotImplementedError("see DESIGN.md, state update over time")
