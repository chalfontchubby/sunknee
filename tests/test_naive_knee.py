from sunknee.capture import Reading
from sunknee.naive_knee import naive_knee_indices, summary_stats


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


def test_summary_stats_percentile_rejects_a_lone_spike():
    # A near-flat curve (99 readings at 200W) plus one single-sample
    # spike (500W) -- a literal max() would report the spike as "today's
    # peak"; the percentile should reject it and report the real curve
    # value instead, while still surfacing the spike via raw_max_watts.
    readings = [Reading(timestamp=f"t{i}", watts=200.0) for i in range(99)]
    readings.append(Reading(timestamp="spike", watts=500.0))

    stats = summary_stats(readings, peak_percentile=95.0)

    assert stats["count"] == 100
    assert stats["peak_watts"] == 200.0
    assert stats["peak_at"] == "t0"
    assert stats["raw_max_watts"] == 500.0


def test_summary_stats_empty():
    assert summary_stats([]) == {
        "count": 0,
        "peak_watts": 0.0,
        "peak_at": None,
        "raw_max_watts": 0.0,
    }
