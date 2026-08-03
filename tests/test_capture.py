from pathlib import Path

from sunknee.capture import DayCapture, Reading, watts_multiplier


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
