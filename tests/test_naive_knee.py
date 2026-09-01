from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from sunknee.capture import Reading
from sunknee.naive_knee import (
    RollingPeakTracker,
    _fit_quadratic,
    _fit_quadratic_quantile_huber,
    _quantile_huber_weight,
    fit_peak,
    naive_knee_indices,
)


def test_finds_first_and_last_reading_above_threshold():
    readings = [
        Reading(timestamp="t0", watts=0.0),
        Reading(timestamp="t1", watts=5.0),
        Reading(timestamp="t2", watts=500.0),
        Reading(timestamp="t3", watts=200.0),
        Reading(timestamp="t4", watts=5.0),
        Reading(timestamp="t5", watts=0.0),
    ]

    morning_i, evening_i = naive_knee_indices(readings, threshold_w=10.0)

    assert (morning_i, evening_i) == (2, 3)


def test_no_readings_above_threshold_returns_none():
    readings = [Reading(timestamp="t0", watts=0.0), Reading(timestamp="t1", watts=1.0)]

    assert naive_knee_indices(readings, threshold_w=10.0) == (None, None)


def _ramp_plateau_decline():
    """Synthetic day: ramp 0->2000W, hold at 2000W, then decline back to 0."""
    ramp = [Reading(timestamp=f"ramp{i}", watts=float(i * 50)) for i in range(40)]
    plateau = [Reading(timestamp=f"plat{i}", watts=2000.0) for i in range(60)]
    decline = [
        Reading(timestamp=f"decl{i}", watts=max(0.0, 2000.0 - i * 40)) for i in range(60)
    ]
    return ramp + plateau + decline


def test_rolling_peak_stays_at_plateau_despite_afternoon_decline():
    # This is the bug that prompted RollingPeakTracker: a percentile over
    # *all* of today's readings falls once the declining tail is large
    # enough, so the reported "peak" ends up tracking the current falling
    # power instead of the actual midday high. The running-max-of-a-
    # trailing-window approach should hold at the plateau instead.
    tracker = RollingPeakTracker(window=40, percentile=95.0)
    peaks_over_time = []

    for reading in _ramp_plateau_decline():
        tracker.update(reading)
        peaks_over_time.append(tracker.peak_watts)

    assert tracker.peak_watts == 2000.0
    # Monotonic: the tracked peak never decreases as the decline plays out.
    assert all(b >= a for a, b in pairwise(peaks_over_time))


def test_rolling_peak_rejects_a_lone_spike():
    tracker = RollingPeakTracker(window=40, percentile=95.0)
    tracker.replay(_ramp_plateau_decline())
    assert tracker.peak_watts == 2000.0

    tracker.update(Reading(timestamp="spike", watts=9000.0))

    assert tracker.peak_watts == 2000.0


def test_replay_matches_incremental_updates():
    readings = _ramp_plateau_decline()

    incremental = RollingPeakTracker(window=40, percentile=95.0)
    for reading in readings:
        incremental.update(reading)

    replayed = RollingPeakTracker(window=40, percentile=95.0)
    replayed.replay(readings)

    assert incremental.peak_watts == replayed.peak_watts
    assert incremental.peak_at == replayed.peak_at


def test_rolling_peak_empty():
    tracker = RollingPeakTracker()
    assert tracker.peak_watts == 0.0
    assert tracker.peak_at is None


def _parabola_series(x_values, vertex_x=100.0, vertex_watts=2000.0, width=50.0):
    """Exact downward parabola y = -(x-vertex_x)^2/width + vertex_watts,
    as a smoothed_series of (timestamp, watts), x in minutes from an
    arbitrary base time."""
    base = datetime(2026, 7, 31, 6, 0, 0, tzinfo=UTC)
    series = []
    for x in x_values:
        watts = -((x - vertex_x) ** 2) / width + vertex_watts
        series.append(((base + timedelta(minutes=x)).isoformat(), watts))
    return series, base


def test_fit_peak_recovers_exact_parabola_vertex():
    x_values = list(range(0, 201, 2))
    series, base = _parabola_series(x_values, vertex_x=100.0, vertex_watts=2000.0)

    fit = fit_peak(series, min_points=30)

    assert fit is not None
    assert fit["fit_peak_watts"] == pytest.approx(2000.0, abs=1e-6)
    expected_at = (base + timedelta(minutes=100.0)).isoformat()
    assert fit["fit_peak_at"] == expected_at
    # Exact data, no noise -- the fit should agree with it almost exactly.
    assert fit["fit_rms_residual"] == pytest.approx(0.0, abs=1e-6)


def test_fit_peak_rms_residual_reflects_actual_scatter():
    x_values = list(range(0, 201, 2))
    series, _ = _parabola_series(x_values, vertex_x=100.0, vertex_watts=2000.0)

    # Alternate residuals above/below the true curve by a known amount --
    # a "clean but scattered" day, not a shape mismatch. tau=0.5 (plain
    # symmetric loss) so the fit sits at the midpoint and the RMS
    # residual comes out exactly at the noise amplitude -- tau=0.9 (the
    # default) deliberately doesn't split symmetric noise symmetrically,
    # since it's pulled toward the upper points on purpose.
    noisy = [
        (t, w + (50.0 if i % 2 == 0 else -50.0))
        for i, (t, w) in enumerate(series)
    ]

    clean_fit = fit_peak(series, min_points=30, tau=0.5)
    noisy_fit = fit_peak(noisy, min_points=30, tau=0.5)

    assert clean_fit is not None
    assert noisy_fit is not None
    assert noisy_fit["fit_rms_residual"] > clean_fit["fit_rms_residual"]
    assert noisy_fit["fit_rms_residual"] == pytest.approx(50.0, rel=0.1)


def test_fit_peak_none_below_min_points():
    x_values = list(range(20))
    series, _ = _parabola_series(x_values)

    assert fit_peak(series, min_points=30) is None


def test_fit_peak_excludes_flat_dark_stretches_either_side():
    # Real days look like: flat near-zero night, curved daylight bell,
    # flat near-zero night again. A parabola fit to the *whole* thing --
    # including the flat tails -- has no way to be both curved in the
    # middle and flat at the edges, and since nothing keeps a parabola
    # >=0 it extrapolates to physically impossible negative watts trying
    # to reconcile the two. Only the active (> threshold_w) window
    # should reach the fit at all.
    daylight_x = list(range(0, 201, 2))
    daylight_series, base = _parabola_series(daylight_x, vertex_x=100.0, vertex_watts=2000.0)
    night_before = [((base + timedelta(minutes=x)).isoformat(), 0.0) for x in range(-120, 0, 5)]
    night_after = [((base + timedelta(minutes=x)).isoformat(), 0.0) for x in range(202, 322, 5)]
    full_day = night_before + daylight_series + night_after

    fit = fit_peak(full_day, min_points=30, threshold_w=10.0)

    assert fit is not None
    assert fit["fit_peak_watts"] == pytest.approx(2000.0, abs=1.0)
    # x_min/x_max reflect the trimmed active window, not the padded
    # full-day range -- the fit was never asked to explain the flat
    # tails in the first place.
    assert fit["x_min"] == pytest.approx(0.0)
    assert fit["x_max"] == pytest.approx(200.0)


def test_fit_peak_none_during_monotonic_rise():
    # A straight-line rise fits a=0 exactly -- not concave-down, so no
    # hump has been seen yet.
    base = datetime(2026, 7, 31, 6, 0, 0, tzinfo=UTC)
    series = [
        ((base + timedelta(minutes=x)).isoformat(), 2.0 * x) for x in range(50)
    ]

    assert fit_peak(series, min_points=30) is None


def test_quantile_huber_weight_favours_upper_envelope_when_tau_high():
    # tau close to 1: a point below the fit (cloud dropout) barely
    # matters; a point above it (approaching the true curve) matters a
    # lot. Same magnitude residual, opposite sign, well outside kappa so
    # we're in the pure 1/|r| regime.
    below = _quantile_huber_weight(residual=-500.0, tau=0.9, kappa=10.0)
    above = _quantile_huber_weight(residual=500.0, tau=0.9, kappa=10.0)

    assert above > below
    assert above == pytest.approx(0.9 / 500.0)
    assert below == pytest.approx(0.1 / 500.0)


def test_quantile_huber_weight_symmetric_at_tau_half():
    below = _quantile_huber_weight(residual=-500.0, tau=0.5, kappa=10.0)
    above = _quantile_huber_weight(residual=500.0, tau=0.5, kappa=10.0)

    assert below == pytest.approx(above)


def test_quantile_huber_fit_resists_downward_notches_better_than_ols():
    # A clean parabola (true vertex 2000W) with a handful of points
    # knocked down hard -- cloud dropouts, no upward outliers, exactly
    # the asymmetric noise structure DESIGN.md describes. Plain OLS
    # should get pulled down by the notches; the quantile-Huber fit
    # (tau=0.9, tracking the upper envelope) should resist that pull.
    x_values = list(range(0, 201, 2))
    series, base = _parabola_series(x_values, vertex_x=100.0, vertex_watts=2000.0)

    notched = list(series)
    for i in (10, 25, 40, 55, 70):
        ts, watts = notched[i]
        notched[i] = (ts, watts * 0.3)  # a cloud-dropout-style notch

    xs = [(datetime.fromisoformat(t) - base).total_seconds() / 60.0 for t, _ in notched]
    ys = [w for _, w in notched]

    def vertex_watts(coeffs):
        a, b, c = coeffs
        vx = -b / (2 * a)
        return a * vx**2 + b * vx + c

    ols_vertex = vertex_watts(_fit_quadratic(xs, ys))
    huber_vertex = vertex_watts(_fit_quadratic_quantile_huber(xs, ys, tau=0.9))

    # True peak is 2000W. OLS undershoots, dragged down by the notches;
    # quantile-Huber lands substantially closer to the truth.
    assert ols_vertex < huber_vertex
    assert huber_vertex == pytest.approx(2000.0, abs=50.0)
    assert ols_vertex < 1950.0


def test_fit_peak_active_fraction_narrows_window_beyond_threshold_w():
    # A shallow (wide) parabola so its "easing in" region -- above
    # threshold_w but still well below 10% of peak -- actually falls
    # within the observed range. active_fraction should exclude that
    # region too, not just the flat-dark stretches threshold_w alone
    # catches, giving a visibly narrower active window.
    x_values = list(range(-1000, 1201, 20))
    series, _ = _parabola_series(x_values, vertex_x=100.0, vertex_watts=2000.0, width=500.0)

    narrow = fit_peak(series, min_points=30, threshold_w=10.0, active_fraction=0.1)
    wide = fit_peak(series, min_points=30, threshold_w=10.0, active_fraction=0.0)

    assert narrow is not None
    assert wide is not None
    # x_min is always 0.0 by construction (each fit's x-axis is relative
    # to its own first active point) -- compare the window's actual
    # duration and absolute start instead.
    assert (narrow["x_max"] - narrow["x_min"]) < (wide["x_max"] - wide["x_min"])
    assert narrow["base"] > wide["base"]


def test_fit_peak_none_when_vertex_outside_observed_range():
    # Only the rising half of the same parabola -- the true vertex (100)
    # is real, but well past the last observed point, so it'd be an
    # extrapolation rather than a genuine cross-check.
    x_values = list(range(0, 90, 2))
    series, _ = _parabola_series(x_values, vertex_x=100.0, vertex_watts=2000.0)

    assert fit_peak(series, min_points=30) is None
