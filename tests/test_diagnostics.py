from sunknee.capture import DayCapture, Reading
from sunknee.diagnostics import _hour_of_day, day_summary, peak_ratios


def test_hour_of_day():
    assert _hour_of_day("2026-08-10T06:30:00+00:00") == 6.5
    assert _hour_of_day("2026-08-10T00:00:00+00:00") == 0.0
    assert _hour_of_day("2026-08-10T23:59:00+00:00") == 23.0 + 59 / 60


def _clear_day_capture():
    """Ramp up, plateau, decline -- enough points for both a naive knee
    crossing and a parabola fit."""
    readings = []
    for i in range(40):
        readings.append(Reading(timestamp=f"2026-08-10T{6 + i // 20:02d}:{(i % 20) * 3:02d}:00+00:00", watts=float(i * 50)))
    for i in range(60):
        readings.append(Reading(timestamp=f"2026-08-10T{10 + i // 20:02d}:{(i % 20) * 3:02d}:00+00:00", watts=2000.0))
    for i in range(60):
        readings.append(Reading(timestamp=f"2026-08-10T{13 + i // 20:02d}:{(i % 20) * 3:02d}:00+00:00", watts=max(0.0, 2000.0 - i * 40)))
    return DayCapture(date="2026-08-10", entity_id="sensor.pv_power", readings=readings)


def test_day_summary_finds_knee_peak_and_fit():
    capture = _clear_day_capture()

    summary = day_summary(capture, threshold_w=10.0, fit_min_points=30)

    assert summary["date"] == "2026-08-10"
    assert summary["morning_knee_at"] is not None
    assert summary["evening_knee_at"] is not None
    assert summary["peak_watts"] == 2000.0
    assert summary["peak_at"] is not None
    assert summary["fit_peak_watts"] is not None
    assert summary["fit_peak_at"] is not None
    assert summary["fit_relative_residual"] is not None
    assert summary["fit_relative_residual"] >= 0.0


def test_day_summary_none_for_empty_day():
    capture = DayCapture(date="2026-08-10", entity_id="sensor.pv_power", readings=[])

    summary = day_summary(capture)

    assert summary["morning_knee_at"] is None
    assert summary["evening_knee_at"] is None
    assert summary["peak_at"] is None
    assert summary["peak_watts"] is None
    assert summary["fit_peak_at"] is None
    assert summary["fit_peak_watts"] is None
    assert summary["fit_relative_residual"] is None


def test_peak_ratios_relative_to_best_seen():
    summaries = [
        {"peak_watts": 2000.0},
        {"peak_watts": 500.0},  # an overcast day, low relative to the site's best
        {"peak_watts": None},  # no data that day
        {"peak_watts": 1800.0},
    ]

    ratios = peak_ratios(summaries)

    assert ratios == [1.0, 0.25, None, 0.9]


def test_peak_ratios_all_none_when_no_data():
    assert peak_ratios([{"peak_watts": None}, {"peak_watts": None}]) == [None, None]
