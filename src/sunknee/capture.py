"""Capture data model: a day's worth of PV power readings.

Stdlib-only by design -- this is imported directly by the AppDaemon app
running on the HA/Pi side (see apps/sunknee_app.py), which should not
need numpy/pandas/matplotlib installed in that environment. Local
tooling (sunknee.diagnostics) reads the same JSON export format.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
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
    # A single daily snapshot of Predbat's own Solcast-derived forecast
    # entities (sensor.predbat_pv_today/_tomorrow -- Predbat pulls
    # Solcast directly, no separate HA integration exposes it), read
    # verbatim via AppDaemon's get_state(attribute="all") rather than
    # modelled into a dataclass: it's HA's own state dict (state +
    # attributes, including detailedForecast's half-hourly
    # pv_estimate/10/90 -- kWh per period, not instantaneous kW), and
    # treating it as an opaque blob is more robust to schema changes
    # than re-declaring every field. None for days captured before this
    # existed, or if the entities aren't configured/available.
    solcast_forecast: dict | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "date": self.date,
                "entity_id": self.entity_id,
                "readings": [
                    {"timestamp": r.timestamp, "watts": r.watts}
                    for r in self.readings
                ],
                "solcast_forecast": self.solcast_forecast,
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
            solcast_forecast=data.get("solcast_forecast"),
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


def solcast_watts_series(
    solcast_forecast: dict | None,
    date: str,
    local_tz: tzinfo,
    which: str = "today",
) -> list[tuple[str, float, float, float]]:
    """This day's (period_start_iso, p10_watts, p50_watts, p90_watts)
    from a captured Solcast/Predbat forecast snapshot, or [] if there's
    no snapshot, no `which` entry, or no periods landing on `date`.

    Two conversions happen here, both easy to get wrong silently:
    - Predbat's detailedForecast periods are in UTC (`period_start`,
      e.g. "2026-09-10T23:00:00+0000"), but a capture's own `date` is a
      local calendar date -- near midnight these disagree (a 23:00 UTC
      period can already be tomorrow in BST/CEST). Each period_start is
      converted to `local_tz` before comparing against `date`, not
      string-matched against the raw UTC value.
    - pv_estimate/10/90 are kWh for that half-hour period, not
      instantaneous kW -- converted to average watts for the period
      (kWh / 0.5h * 1000) so they're comparable to a Reading's watts.
    """
    if not solcast_forecast:
        return []
    entry = solcast_forecast.get(which)
    if not entry:
        return []
    detailed = (entry.get("attributes") or {}).get("detailedForecast", [])

    result = []
    for period in detailed:
        period_start = period.get("period_start")
        if not period_start:
            continue
        try:
            local_dt = datetime.fromisoformat(period_start).astimezone(local_tz)
        except ValueError:
            continue
        if local_dt.strftime("%Y-%m-%d") != date:
            continue
        try:
            p50_kwh = float(period["pv_estimate"])
            p10_kwh = float(period["pv_estimate10"])
            p90_kwh = float(period["pv_estimate90"])
        except (KeyError, TypeError, ValueError):
            continue
        result.append((local_dt.isoformat(), p10_kwh * 2000.0, p50_kwh * 2000.0, p90_kwh * 2000.0))
    return result


def completed_day_files(export_dir: Path, today: str) -> list[Path]:
    """Capture JSON files in export_dir safe to delete once downloaded --
    every day except today's, which is excluded unconditionally. Today's
    file is still being actively written (a new reading rewrites it in
    full from the in-memory capture, regardless of whether the file on
    disk was just deleted), so deleting it doesn't even save space, and
    risks losing the rest of the day's data outright if no further
    reading arrives before midnight to trigger a re-save."""
    return sorted(p for p in export_dir.glob("*.json") if p.stem != today)
