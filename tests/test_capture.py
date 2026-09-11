from datetime import timedelta, timezone
from pathlib import Path

from sunknee.capture import (
    DayCapture,
    Reading,
    completed_day_files,
    plausible_readings,
    solcast_watts_series,
    watts_multiplier,
)


def test_watts_multiplier_known_units():
    assert watts_multiplier("W") == 1.0
    assert watts_multiplier("kW") == 1_000.0
    assert watts_multiplier("MW") == 1_000_000.0


def test_watts_multiplier_unknown_or_missing_unit_falls_back_to_one():
    assert watts_multiplier("gremlins") == 1.0
    assert watts_multiplier(None) == 1.0


def test_round_trip_json(tmp_path: Path):
    capture = DayCapture(
        date="2026-07-31",
        entity_id="sensor.pv_power",
        readings=[
            Reading(timestamp="2026-07-31T06:00:00+01:00", watts=0.0),
            Reading(timestamp="2026-07-31T12:00:00+01:00", watts=3500.0),
        ],
    )
    path = tmp_path / "2026-07-31.json"
    capture.save(path)

    loaded = DayCapture.load(path)

    assert loaded == capture


def test_round_trip_json_with_solcast_forecast(tmp_path: Path):
    capture = DayCapture(
        date="2026-09-10",
        entity_id="sensor.pv_power",
        readings=[Reading(timestamp="2026-09-10T06:00:00+01:00", watts=0.0)],
        solcast_forecast={
            "captured_at": "2026-09-10T00:05:00+01:00",
            "today": {"state": "12.3", "attributes": {"total": 12.3, "total10": 8.1, "total90": 16.9}},
            "tomorrow": None,
        },
    )
    path = tmp_path / "2026-09-10.json"
    capture.save(path)

    loaded = DayCapture.load(path)

    assert loaded == capture


def test_load_old_capture_without_solcast_forecast_field(tmp_path: Path):
    # Files captured before this field existed have no solcast_forecast
    # key at all -- must still load cleanly, defaulting to None.
    path = tmp_path / "2026-07-31.json"
    path.write_text('{"date": "2026-07-31", "entity_id": "sensor.pv_power", "readings": []}')

    loaded = DayCapture.load(path)

    assert loaded.solcast_forecast is None


def test_completed_day_files_excludes_today(tmp_path: Path):
    for day in ("2026-07-30", "2026-07-31", "2026-08-01"):
        (tmp_path / f"{day}.json").write_text("{}")

    result = completed_day_files(tmp_path, today="2026-08-01")

    assert [p.name for p in result] == ["2026-07-30.json", "2026-07-31.json"]


def test_completed_day_files_empty_dir(tmp_path: Path):
    assert completed_day_files(tmp_path, today="2026-08-01") == []


def test_plausible_readings_filters_above_max_watts():
    readings = [
        Reading(timestamp="t0", watts=1000.0),
        Reading(timestamp="t1", watts=2800.0),
        Reading(timestamp="t2", watts=5752.0),  # e.g. 2026-08-17's glitch
    ]

    result = plausible_readings(readings, max_watts=4000.0)

    assert [r.timestamp for r in result] == ["t0", "t1"]


def test_plausible_readings_none_max_watts_is_a_no_op():
    readings = [Reading(timestamp="t0", watts=1000.0), Reading(timestamp="t1", watts=99999.0)]

    assert plausible_readings(readings, max_watts=None) == readings


BST = timezone(timedelta(hours=1))


def _solcast_forecast(periods):
    return {
        "captured_at": "2026-09-10T00:05:00+01:00",
        "today": {"attributes": {"detailedForecast": periods}},
        "tomorrow": None,
    }


def test_solcast_watts_series_converts_kwh_to_watts():
    forecast = _solcast_forecast(
        [
            {
                "period_start": "2026-09-10T11:00:00+0000",  # noon BST
                "pv_estimate": 1.0,
                "pv_estimate10": 0.5,
                "pv_estimate90": 1.5,
            }
        ]
    )

    result = solcast_watts_series(forecast, date="2026-09-10", local_tz=BST)

    assert len(result) == 1
    _, p10, p50, p90 = result[0]
    # 1.0 kWh over a half-hour period = 2000W average.
    assert (p10, p50, p90) == (1000.0, 2000.0, 3000.0)


def test_solcast_watts_series_handles_utc_local_midnight_boundary():
    # 23:00 UTC is already the next day in BST (+1h) -- a period that's
    # "2026-09-10" in UTC but "2026-09-11" locally must be attributed to
    # the *local* date, not string-matched against the raw UTC value.
    forecast = _solcast_forecast(
        [
            {
                "period_start": "2026-09-10T23:00:00+0000",  # 00:00 BST on the 11th
                "pv_estimate": 0.0,
                "pv_estimate10": 0.0,
                "pv_estimate90": 0.0,
            }
        ]
    )

    assert solcast_watts_series(forecast, date="2026-09-10", local_tz=BST) == []
    result = solcast_watts_series(forecast, date="2026-09-11", local_tz=BST)
    assert len(result) == 1
    assert result[0][0] == "2026-09-11T00:00:00+01:00"


def test_solcast_watts_series_empty_when_no_forecast():
    assert solcast_watts_series(None, date="2026-09-10", local_tz=BST) == []
    assert solcast_watts_series({"today": None}, date="2026-09-10", local_tz=BST) == []
