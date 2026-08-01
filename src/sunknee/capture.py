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


@dataclass
class Reading:
    timestamp: str  # ISO 8601, as received from HA
    watts: float


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
