from pathlib import Path

from sunknee.capture import (
    DayCapture,
    Reading,
    completed_day_files,
    plausible_readings,
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
