from pathlib import Path

from sunknee.capture import DayCapture, Reading


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
