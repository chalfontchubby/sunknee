from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from sunknee.capture import Reading
from sunknee.naive_knee import RollingPeakTracker, fit_peak, naive_knee_indices


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


def test_fit_peak_none_below_min_points():
    x_values = list(range(20))
    series, _ = _parabola_series(x_values)

    assert fit_peak(series, min_points=30) is None


def test_fit_peak_none_during_monotonic_rise():
    # A straight-line rise fits a=0 exactly -- not concave-down, so no
    # hump has been seen yet.
    base = datetime(2026, 7, 31, 6, 0, 0, tzinfo=UTC)
    series = [
        ((base + timedelta(minutes=x)).isoformat(), 2.0 * x) for x in range(50)
    ]

    assert fit_peak(series, min_points=30) is None


def test_fit_peak_none_when_vertex_outside_observed_range():
    # Only the rising half of the same parabola -- the true vertex (100)
    # is real, but well past the last observed point, so it'd be an
    # extrapolation rather than a genuine cross-check.
    x_values = list(range(0, 90, 2))
    series, _ = _parabola_series(x_values, vertex_x=100.0, vertex_watts=2000.0)

    assert fit_peak(series, min_points=30) is None
