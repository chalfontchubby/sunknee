"""AppDaemon entry point for sunknee.

Deployed onto the Home Assistant/AppDaemon host (see apps/apps.yaml and
README.md "Deploying" for how this directory gets there). Two jobs, both
ahead of any real estimation algorithm (see DESIGN.md):

1. Liveness: publish sensor.sunknee_status so the app's presence is
   visible in HA -- the "hello world" proof that deployment worked.
2. Capture mode: listen to the configured PV power sensor, persist each
   day's readings to a JSON file sunknee.diagnostics can plot locally,
   and publish naive placeholder knee-time sensors so the raw signal can
   also be charted natively in HA (history graph / Lovelace) without
   needing anything installed here beyond this app.

Only imports sunknee.capture and sunknee.naive_knee, both stdlib-only --
matplotlib (sunknee.diagnostics) is never loaded on this side.
"""
from __future__ import annotations

from pathlib import Path

from appdaemon.plugins.hass.hassapi import Hass

from sunknee import __version__
from sunknee.capture import DayCapture, Reading
from sunknee.naive_knee import naive_knee_indices, summary_stats

STATUS_ENTITY = "sensor.sunknee_status"
KNEE_MORNING_ENTITY = "sensor.sunknee_knee_morning"
KNEE_EVENING_ENTITY = "sensor.sunknee_knee_evening"
READINGS_TODAY_ENTITY = "sensor.sunknee_readings_today"
PEAK_POWER_ENTITY = "sensor.sunknee_peak_power_today"


class SunKnee(Hass):
    def initialize(self):
        self.pv_power_entity = self.args["pv_power_entity"]
        self.export_dir = Path(self.args.get("export_dir", "/config/apps/sunknee/data"))
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.threshold_w = float(self.args.get("knee_threshold_w", 10.0))
        self.peak_percentile = float(self.args.get("peak_percentile", 95.0))

        self.capture = self._load_or_start_capture(self._today())

        self.set_state(
            STATUS_ENTITY,
            state="running",
            attributes={
                "friendly_name": "sunknee status",
                "version": __version__,
                "pv_power_entity": self.pv_power_entity,
            },
            check_existence=False,
        )
        self.log(f"sunknee {__version__} started, watching {self.pv_power_entity}")

        self.listen_state(self._on_power_change, self.pv_power_entity)

    def _today(self) -> str:
        return self.get_now().strftime("%Y-%m-%d")

    def _capture_path(self, day: str) -> Path:
        return self.export_dir / f"{day}.json"

    def _load_or_start_capture(self, day: str) -> DayCapture:
        path = self._capture_path(day)
        if path.exists():
            return DayCapture.load(path)
        return DayCapture(date=day, entity_id=self.pv_power_entity)

    def _on_power_change(self, entity, attribute, old, new, **kwargs):
        try:
            watts = float(new)
        except (TypeError, ValueError):
            return  # "unknown"/"unavailable" states etc.

        today = self._today()
        if today != self.capture.date:
            self.capture = self._load_or_start_capture(today)

        self.capture.readings.append(
            Reading(timestamp=self.get_now().isoformat(), watts=watts)
        )
        self.capture.save(self._capture_path(self.capture.date))

        self._publish_naive_knees()
        self._publish_stats()

    def _publish_naive_knees(self):
        morning_i, evening_i = naive_knee_indices(self.capture.readings, self.threshold_w)
        if morning_i is None:
            return
        self.set_state(
            KNEE_MORNING_ENTITY,
            state=self.capture.readings[morning_i].timestamp,
            attributes={"friendly_name": "sunknee morning knee (naive)", "device_class": "timestamp"},
            check_existence=False,
        )
        self.set_state(
            KNEE_EVENING_ENTITY,
            state=self.capture.readings[evening_i].timestamp,
            attributes={"friendly_name": "sunknee evening knee (naive)", "device_class": "timestamp"},
            check_existence=False,
        )

    def _publish_stats(self):
        stats = summary_stats(self.capture.readings, self.peak_percentile)
        self.set_state(
            READINGS_TODAY_ENTITY,
            state=stats["count"],
            attributes={
                "friendly_name": "sunknee readings today",
                "state_class": "measurement",
            },
            check_existence=False,
        )
        self.set_state(
            PEAK_POWER_ENTITY,
            state=stats["peak_watts"],
            attributes={
                "friendly_name": "sunknee peak power today",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
                "peak_at": stats["peak_at"],
                "raw_max_watts": stats["raw_max_watts"],
                "peak_percentile": self.peak_percentile,
            },
            check_existence=False,
        )
