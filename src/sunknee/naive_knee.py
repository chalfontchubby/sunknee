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


def _fit_quadratic(
    xs: list[float], ys: list[float], weights: list[float] | None = None
) -> tuple[float, float, float] | None:
    """Weighted least-squares fit of y = a*x^2 + b*x + c via the
    closed-form normal equations, solved with Cramer's rule --
    stdlib-only, no numpy. weights defaults to all 1.0 (plain OLS).
    Returns None if the system is singular (e.g. all x identical)."""
    if weights is None:
        weights = [1.0] * len(xs)
    s0, s1, s2, s3, s4 = 0.0, 0.0, 0.0, 0.0, 0.0
    t0, t1, t2 = 0.0, 0.0, 0.0
    for x, y, w in zip(xs, ys, weights):
        s0 += w
        s1 += w * x
        s2 += w * x**2
        s3 += w * x**3
        s4 += w * x**4
        t0 += w * y
        t1 += w * x * y
        t2 += w * x**2 * y

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


def _quantile_huber_weight(residual: float, tau: float, kappa: float) -> float:
    """IRLS weight approximating quantile-Huber loss's gradient as
    weight*residual: quadratic (constant weight) within +/-kappa of
    zero, decaying as 1/|residual| beyond it (the asymptotically linear,
    robust part of Huber loss). tau > 0.5 penalizes positive residuals
    (data above the fit) less than negative ones (data below it) --
    exactly backwards from what "clouds only ever push power down"
    calls for, so callers wanting an upper-envelope fit should pass
    tau close to 1.0, not close to 0 (see fit_peak)."""
    side = tau if residual >= 0 else (1.0 - tau)
    return side / max(kappa, abs(residual))


def _fit_quadratic_quantile_huber(
    xs: list[float],
    ys: list[float],
    tau: float = 0.9,
    kappa: float | None = None,
    iterations: int = 15,
) -> tuple[float, float, float] | None:
    """Quadratic fit that tracks the upper envelope of the data rather
    than balancing errors symmetrically like plain least squares does.

    See DESIGN.md, "Fitting against asymmetric noise": cloud dropouts
    are frequent and can be large, but there's no comparable mechanism
    for generation to land meaningfully *above* the true curve, so a
    symmetric loss gets dragged down toward the dropouts. Solved via
    iteratively reweighted least squares (IRLS) -- refit, recompute
    per-point weights from the residuals, repeat -- rather than a
    closed-form solution, since quantile-Huber loss doesn't have one.

    kappa (the Huber quadratic/linear transition width, in the same
    units as ys) defaults to 5% of the data's own spread if not given,
    so it scales with the array's actual output rather than needing a
    fixed constant tuned per deployment.
    """
    if kappa is None:
        spread = max(ys) - min(ys)
        kappa = max(1.0, spread * 0.05)

    coeffs = _fit_quadratic(xs, ys)
    if coeffs is None:
        return None

    for _ in range(iterations):
        a, b, c = coeffs
        weights = [
            _quantile_huber_weight(y - (a * x**2 + b * x + c), tau, kappa)
            for x, y in zip(xs, ys)
        ]
        updated = _fit_quadratic(xs, ys, weights)
        if updated is None:
            break
        coeffs = updated

    return coeffs


def fit_peak(
    smoothed_series: list[tuple[str, float]],
    min_points: int = 30,
    tau: float = 0.9,
    kappa: float | None = None,
    threshold_w: float = 10.0,
    active_fraction: float = 0.1,
) -> dict | None:
    """Cross-check RollingPeakTracker's running peak by fitting a downward
    parabola to the day's smoothed curve and reading off its vertex.

    A parabola is a crude stand-in for the real bell-ish shape (not
    DESIGN.md's direct/diffuse model) -- good enough as a same-day sanity
    check against the running-max approach, not a replacement for it.
    The fit itself tracks the upper envelope of the data (quantile-Huber
    loss, tau=0.9 by default) rather than balancing errors symmetrically
    -- plain least-squares gets pulled down toward cloud dropouts, which
    are frequent and can be large, when there's no comparable mechanism
    pushing generation *above* the true curve. See DESIGN.md's "Fitting
    against asymmetric noise" and _fit_quadratic_quantile_huber.

    Only fits against the active window: watts > max(threshold_w,
    active_fraction * this day's peak). threshold_w alone (same absolute
    floor naive_knee_indices uses for "is this dark or daylight") isn't
    enough on its own -- the shallow "easing in" region just past the
    knee doesn't look parabolic either (real solar power rises roughly
    linearly there, not with a parabola's constant curvature), and
    including it is what let the fit extrapolate to physically
    impossible negative watts trying to reconcile a curved middle with a
    near-flat edge. active_fraction excludes that region too, fitting
    only the part of the day that's actually plausibly bell-shaped --
    cheaper than modelling the shallow region properly (e.g. as a
    separate ambient/diffuse term, see DESIGN.md's signal-decomposition
    section) and reuses the same closed-form solver rather than needing
    a nonlinear one.

    Known gap, deliberately not handled here: on a heavily overcast day,
    "peak" is itself just diffuse noise, not a real direct-beam bell --
    active_fraction still picks *some* window relative to that tiny
    peak and can produce a fit that looks structurally valid (passes
    every check below) while being meaningless. No clear-sky gating is
    implemented in this diagnostic; that's the real Kalman filter's `kt`
    pre-gate's job later (see DESIGN.md, "State update over time") --
    skip low-signal days outright rather than try to make this fit smart
    about it.

    Returns None until there's enough data to be worth it: fewer than
    min_points *active* readings, a fit that isn't concave-down yet
    (still just rising -- no hump visible), or a vertex that falls
    outside the active time range (the fit would be extrapolating,
    unreliable).
    """
    if not smoothed_series:
        return None
    peak_watts = max(w for _, w in smoothed_series)
    active_threshold = max(threshold_w, active_fraction * peak_watts)
    active = [(t, w) for t, w in smoothed_series if w > active_threshold]
    if len(active) < min_points:
        return None

    timestamps = [t for t, _ in active]
    watts = [w for _, w in active]

    base = datetime.fromisoformat(timestamps[0])
    xs = [(datetime.fromisoformat(t) - base).total_seconds() / 60.0 for t in timestamps]

    fit = _fit_quadratic_quantile_huber(xs, watts, tau=tau, kappa=kappa)
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

    return {
        "fit_peak_watts": vertex_watts,
        "fit_peak_at": vertex_at,
        # Coefficients + reference point, so a caller (e.g.
        # sunknee.diagnostics) can redraw the whole fitted curve, not
        # just its vertex: watts(t) = a*x^2 + b*x + c, x = minutes
        # since `base`.
        "a": a,
        "b": b,
        "c": c,
        "base": timestamps[0],
        "x_min": min(xs),
        "x_max": max(xs),
    }
