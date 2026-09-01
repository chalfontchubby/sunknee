"""Capture data model: a day's worth of PV power readings.

Stdlib-only by design -- this is imported directly by the AppDaemon app
running on the HA/Pi side (see apps/sunknee_app.py), which should not
need numpy/pandas/matplotlib installed in that environment. Local
tooling (sunknee.diagnostics) reads the same JSON export format.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

WATTS_PER_UNIT = {"W": 1.0, "kW": 1_000.0, "MW": 1_000_000.0}


def watts_multiplier(unit: str | None) -> float:
    """Conversion factor to real watts for a HA power sensor's
    unit_of_measurement. Falls back to 1.0 (assume already watts) for
    unrecognised/missing units -- callers should log a warning in that
    case, since silently assuming watts for an unknown unit could be
    wrong."""
    return WATTS_PER_UNIT.get(unit, 1.0)


@dataclass
class Reading:
    timestamp: str  # ISO 8601, as received from HA
    watts: float  # always real watts -- see watts_multiplier for HA-unit conversion


@dataclass
class DayCapture:
    date: str  # YYYY-MM-DD
    entity_id: str
    readings: list[Reading] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "date": self.date,
                "entity_id": self.entity_id,
                "readings": [
                    {"timestamp": r.timestamp, "watts": r.watts}
                    for r in self.readings
                ],
            },
            indent=2,
        )

    def save(self, path: Path) -> None:
        path.write_text(self.to_json())

    @classmethod
    def from_json(cls, text: str) -> DayCapture:
        data = json.loads(text)
        return cls(
            date=data["date"],
            entity_id=data["entity_id"],
            readings=[Reading(**r) for r in data["readings"]],
        )

    @classmethod
    def load(cls, path: Path) -> DayCapture:
        return cls.from_json(Path(path).read_text())


def plausible_readings(
    readings: list[Reading], max_watts: float | None
) -> list[Reading]:
    """Readings with watts <= max_watts -- a cheap sanity filter against
    sensor/Modbus glitches, applied at every point derived signals
    (RollingPeakTracker, naive_knee_indices, fit_peak, the HA sensors)
    get computed from. Returns readings unchanged if max_watts is None.

    Real over-nameplate output happens (cloud-edge irradiance
    enhancement -- see DESIGN.md) but is modest, a few percent, not a
    multiple -- a reading at 2x a known inverter's rated capacity,
    especially late in the day when output should be declining rather
    than doubling, is far more likely a data-quality artifact than
    genuine generation. Deliberately doesn't touch the stored capture
    itself (readings are still appended/saved raw) -- only what
    downstream analysis sees, so the anomaly stays visible in the raw
    record for later debugging instead of silently vanishing.
    """
    if max_watts is None:
        return readings
    return [r for r in readings if r.watts <= max_watts]


def completed_day_files(export_dir: Path, today: str) -> list[Path]:
    """Capture JSON files in export_dir safe to delete once downloaded --
    every day except today's, which is excluded unconditionally. Today's
    file is still being actively written (a new reading rewrites it in
    full from the in-memory capture, regardless of whether the file on
    disk was just deleted), so deleting it doesn't even save space, and
    risks losing the rest of the day's data outright if no further
    reading arrives before midnight to trigger a re-save."""
    return sorted(p for p in export_dir.glob("*.json") if p.stem != today)
