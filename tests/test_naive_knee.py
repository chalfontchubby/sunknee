from sunknee.capture import Reading
from sunknee.naive_knee import naive_knee_indices


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
