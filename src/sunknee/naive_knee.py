"""Placeholder threshold-based knee detector and a rolling peak tracker.

This is NOT the real knee-detection algorithm (see DESIGN.md, "Algorithm
Details", and the stub in sunknee.knee for the intended direct/diffuse
decomposition + linear-extrapolation approach). It exists purely so
captured data has *something* to look at -- both as native HA sensors
(published by apps/sunknee_app.py) and in local matplotlib plots
(sunknee.diagnostics) -- while the real estimator is still unbuilt.

Stdlib-only: imported by the AppDaemon app, so no numpy/pandas here.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

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


class RollingPeakTracker:
    """Tracks a monotonic "today's peak", robust to lone spikes.

    A percentile taken over *all* of today's readings-so-far falls once
    the afternoon's (lower) readings grow past the outlier fraction --
    the percentile rank then lands in the afternoon tail instead of at
    the actual midday peak, so the reported "peak" just tracks the
    current, declining power level. That's not what "peak" should mean.

    Instead, take the percentile over a trailing window only (rejects a
    lone spike the same way DESIGN.md's envelope method does, just
    locally) and keep the running max of that smoothed value. Being a
    max over time, it can only hold or increase, never fall below where
    it's already been.
    """

    def __init__(self, window: int = 40, percentile: float = 95.0):
        self.window = window
        self.percentile = percentile
        self._recent: deque[float] = deque(maxlen=window)
        self.peak_watts = 0.0
        self.peak_at: str | None = None
        self.smoothed_series: list[tuple[str, float]] = []

    def update(self, reading: Reading) -> None:
        self._recent.append(reading.watts)
        smoothed = _percentile(list(self._recent), self.percentile)
        self.smoothed_series.append((reading.timestamp, smoothed))
        if smoothed >= self.peak_watts:
            self.peak_watts = smoothed
            self.peak_at = reading.timestamp

    def replay(self, readings: list[Reading]) -> None:
        """Rebuild state from stored readings -- e.g. after an app
        restart partway through the day."""
        for r in readings:
            self.update(r)


def _det3(m: list[list[float]]) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def _fit_quadratic(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    """Least-squares fit of y = a*x^2 + b*x + c via the closed-form normal
    equations, solved with Cramer's rule -- stdlib-only, no numpy. Returns
    None if the system is singular (e.g. all x identical)."""
    s0, s1, s2, s3, s4 = len(xs), 0.0, 0.0, 0.0, 0.0
    t0, t1, t2 = 0.0, 0.0, 0.0
    for x, y in zip(xs, ys):
        s1 += x
        s2 += x**2
        s3 += x**3
        s4 += x**4
        t0 += y
        t1 += x * y
        t2 += x**2 * y

    a_matrix = [[s4, s3, s2], [s3, s2, s1], [s2, s1, s0]]
    rhs = [t2, t1, t0]

    d = _det3(a_matrix)
    if d == 0:
        return None

    coeffs = []
    for col in range(3):
        m = [row[:] for row in a_matrix]
        for row in range(3):
            m[row][col] = rhs[row]
        coeffs.append(_det3(m) / d)
    return coeffs[0], coeffs[1], coeffs[2]


def fit_peak(
    smoothed_series: list[tuple[str, float]], min_points: int = 30
) -> dict | None:
    """Cross-check RollingPeakTracker's running peak by fitting a downward
    parabola to the day's smoothed curve and reading off its vertex.

    A parabola is a crude stand-in for the real bell-ish shape (not
    DESIGN.md's direct/diffuse model) -- good enough as a same-day sanity
    check against the running-max approach, not a replacement for it.

    Returns None until there's enough data to be worth it: fewer than
    min_points readings, a fit that isn't concave-down yet (still just
    rising -- no hump visible), or a vertex that falls outside the
    observed time range (the fit would be extrapolating, unreliable).
    """
    if len(smoothed_series) < min_points:
        return None

    timestamps = [t for t, _ in smoothed_series]
    watts = [w for _, w in smoothed_series]

    base = datetime.fromisoformat(timestamps[0])
    xs = [(datetime.fromisoformat(t) - base).total_seconds() / 60.0 for t in timestamps]

    fit = _fit_quadratic(xs, watts)
    if fit is None:
        return None
    a, b, c = fit
    if a >= 0:
        return None  # not concave-down -- no hump yet

    vertex_x = -b / (2 * a)
    if not (min(xs) <= vertex_x <= max(xs)):
        return None  # vertex outside observed data -- extrapolating, unreliable

    vertex_watts = a * vertex_x**2 + b * vertex_x + c
    vertex_at = (base + timedelta(minutes=vertex_x)).isoformat()

    return {"fit_peak_watts": vertex_watts, "fit_peak_at": vertex_at}
