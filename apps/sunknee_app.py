"""AppDaemon entry point for sunknee.

Deployed onto the Home Assistant/AppDaemon host (see apps/apps.yaml and
README.md "Deploying" for how this directory gets there). Four jobs,
all ahead of any real estimation algorithm (see DESIGN.md):

1. Liveness: publish sensor.sunknee_status so the app's presence is
   visible in HA -- the "hello world" proof that deployment worked.
2. Capture mode: listen to the configured PV power sensor, persist each
   day's readings to a JSON file sunknee.diagnostics can plot locally,
   and publish naive placeholder knee-time sensors so the raw signal can
   also be charted natively in HA (history graph / Lovelace) without
   needing anything installed here beyond this app.
3. A download route (AppDaemon's register_route, not register_endpoint
   -- the latter force-wraps everything as inline JSON with no header
   control) that zips up the capture files and serves them with a
   Content-Disposition header, so hitting the URL in a browser saves a
   zip straight to Downloads, the same way Predbat's debug-info download
   works.
4. Once daily, snapshot Predbat's own Solcast-derived forecast entities
   (sensor.predbat_pv_today/_tomorrow -- Predbat pulls Solcast directly,
   no separate HA integration needed) into that day's capture file, for
   later comparison against what actually happened. One read a day is a
   deliberate, accepted limitation -- Solcast/Predbat's forecast can
   update intraday, this only ever sees the snapshot at capture time.

Only imports sunknee.capture and sunknee.naive_knee, both stdlib-only --
matplotlib (sunknee.diagnostics) is never loaded on this side. aiohttp
is used for the download route only; it's already bundled with
AppDaemon itself, not a new dependency.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from aiohttp import web
from appdaemon.plugins.hass.hassapi import Hass

from sunknee import __version__
from sunknee.capture import (
    DayCapture,
    Reading,
    completed_day_files,
    plausible_readings,
    watts_multiplier,
)
from sunknee.naive_knee import RollingPeakTracker, fit_peak, naive_knee_indices

STATUS_ENTITY = "sensor.sunknee_status"
KNEE_MORNING_ENTITY = "sensor.sunknee_knee_morning"
KNEE_EVENING_ENTITY = "sensor.sunknee_knee_evening"
READINGS_TODAY_ENTITY = "sensor.sunknee_readings_today"
PEAK_POWER_ENTITY = "sensor.sunknee_peak_power_today"
PEAK_POWER_AT_ENTITY = "sensor.sunknee_peak_power_at_today"
FIT_PEAK_POWER_ENTITY = "sensor.sunknee_fit_peak_power_today"
FIT_PEAK_POWER_AT_ENTITY = "sensor.sunknee_fit_peak_power_at_today"


class SunKnee(Hass):
    def initialize(self):
        self.pv_power_entity = self.args["pv_power_entity"]
        self.export_dir = Path(self.args.get("export_dir", "/config/apps/sunknee/data"))
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.threshold_w = float(self.args.get("knee_threshold_w", 10.0))
        self.peak_percentile = float(self.args.get("peak_percentile", 95.0))
        self.peak_window = int(self.args.get("peak_window", 40))
        self.fit_min_points = int(self.args.get("fit_min_points", 30))
        max_plausible_watts = self.args.get("max_plausible_watts")
        self.max_plausible_watts = (
            float(max_plausible_watts) if max_plausible_watts is not None else None
        )
        self.solcast_today_entity = self.args.get("solcast_today_entity")
        self.solcast_tomorrow_entity = self.args.get("solcast_tomorrow_entity")
        self.solcast_capture_time = self.args.get("solcast_capture_time", "00:05:00")

        source_unit = self.get_state(self.pv_power_entity, attribute="unit_of_measurement")
        self.watts_multiplier = watts_multiplier(source_unit)
        if source_unit not in ("W", "kW", "MW"):
            self.log(
                f"{self.pv_power_entity} has unrecognised unit_of_measurement "
                f"{source_unit!r} -- assuming it's already watts, may be wrong",
                level="WARNING",
            )

        self.capture = self._load_or_start_capture(self._today(self.get_now()))
        self._reset_peak_tracker()

        self.set_state(
            STATUS_ENTITY,
            state="running",
            attributes={
                "friendly_name": "sunknee status",
                "version": __version__,
                "pv_power_entity": self.pv_power_entity,
                "source_unit": source_unit,
                "watts_multiplier": self.watts_multiplier,
            },
            check_existence=False,
        )
        self.log(
            f"sunknee {__version__} started, watching {self.pv_power_entity} "
            f"({source_unit} -> watts multiplier {self.watts_multiplier})"
        )

        self.listen_state(self._on_power_change, self.pv_power_entity)
        self.register_route(self._download_capture, "sunknee_download")
        if self.solcast_today_entity or self.solcast_tomorrow_entity:
            self.run_daily(self._capture_solcast_forecast, self.solcast_capture_time)

    def _today(self, now) -> str:
        """now must already be resolved by the caller -- self.get_now()
        behaves differently depending on the calling thread (see
        DESIGN.md's environment notes): called synchronously from a
        worker thread (initialize()/_on_power_change(), both sync) it
        blocks and returns the real datetime; called the same way from
        an async method already running on the main thread's event loop
        (_download_capture()) it instead hands back an un-awaited
        asyncio.Task. Taking `now` as a parameter forces every call site
        to resolve it the right way for its own context rather than
        this helper silently doing the wrong thing in one of them."""
        return now.strftime("%Y-%m-%d")

    def _capture_path(self, day: str) -> Path:
        return self.export_dir / f"{day}.json"

    def _load_or_start_capture(self, day: str) -> DayCapture:
        path = self._capture_path(day)
        if path.exists():
            return DayCapture.load(path)
        return DayCapture(date=day, entity_id=self.pv_power_entity)

    def _reset_peak_tracker(self):
        """(Re)build the rolling peak tracker for self.capture's day,
        replaying any readings already on disk -- covers both a fresh
        day and an app restart partway through one. Only replays
        plausible readings (see plausible_readings) -- an app restart
        shouldn't let a sensor glitch already on disk back into live
        tracking just because it's being replayed rather than freshly
        received."""
        self.peak_tracker = RollingPeakTracker(
            window=self.peak_window, percentile=self.peak_percentile
        )
        self.peak_tracker.replay(
            plausible_readings(self.capture.readings, self.max_plausible_watts)
        )

    def _ensure_capture_for(self, now) -> None:
        """Roll self.capture over to now's date if it isn't already --
        shared by _on_power_change and _capture_solcast_forecast, since
        both need this and the scheduled forecast job can fire before
        any PV reading has rolled the day over itself (no generation at
        00:05 to trigger it)."""
        today = self._today(now)
        if today != self.capture.date:
            self.capture = self._load_or_start_capture(today)
            self._reset_peak_tracker()

    def _on_power_change(self, entity, attribute, old, new, **kwargs):
        try:
            watts = float(new) * self.watts_multiplier
        except (TypeError, ValueError):
            return  # "unknown"/"unavailable" states etc.

        now = self.get_now()
        self._ensure_capture_for(now)

        reading = Reading(timestamp=now.isoformat(), watts=watts)
        self.capture.readings.append(reading)
        self.capture.save(self._capture_path(self.capture.date))

        # The stored capture always gets the raw reading, glitch or not
        # -- only what feeds derived signals (peak tracking, knee
        # detection, the fit) is protected, so the anomaly stays visible
        # in the raw record rather than silently vanishing.
        if self.max_plausible_watts is None or watts <= self.max_plausible_watts:
            self.peak_tracker.update(reading)
        else:
            self.log(
                f"Ignoring implausible reading {watts:.0f}W from "
                f"{self.pv_power_entity} (> max_plausible_watts="
                f"{self.max_plausible_watts:.0f}W) for peak tracking/knee "
                "detection -- still saved to the raw capture file",
                level="WARNING",
            )

        self._publish_naive_knees()
        self._publish_stats()
        self._publish_fit_peak()

    def _publish_naive_knees(self):
        readings = plausible_readings(self.capture.readings, self.max_plausible_watts)
        morning_i, evening_i = naive_knee_indices(readings, self.threshold_w)
        if morning_i is None:
            return
        self.set_state(
            KNEE_MORNING_ENTITY,
            state=readings[morning_i].timestamp,
            attributes={"friendly_name": "sunknee morning knee (naive)", "device_class": "timestamp"},
            check_existence=False,
        )
        self.set_state(
            KNEE_EVENING_ENTITY,
            state=readings[evening_i].timestamp,
            attributes={"friendly_name": "sunknee evening knee (naive)", "device_class": "timestamp"},
            check_existence=False,
        )

    def _publish_stats(self):
        watts_today = [r.watts for r in self.capture.readings]
        self.set_state(
            READINGS_TODAY_ENTITY,
            state=len(self.capture.readings),
            attributes={
                "friendly_name": "sunknee readings today",
                "state_class": "measurement",
            },
            check_existence=False,
        )
        self.set_state(
            PEAK_POWER_ENTITY,
            state=self.peak_tracker.peak_watts,
            attributes={
                "friendly_name": "sunknee peak power today",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
                "peak_at": self.peak_tracker.peak_at,
                "raw_max_watts": max(watts_today) if watts_today else 0.0,
                "peak_percentile": self.peak_percentile,
                "peak_window": self.peak_window,
            },
            check_existence=False,
        )
        if self.peak_tracker.peak_at is not None:
            self.set_state(
                PEAK_POWER_AT_ENTITY,
                state=self.peak_tracker.peak_at,
                attributes={
                    "friendly_name": "sunknee peak power time today",
                    "device_class": "timestamp",
                },
                check_existence=False,
            )

    async def _download_capture(self, request, kwargs):
        """Zip all captured day-JSON files and serve them with a
        Content-Disposition header -- hitting this URL in a browser
        downloads sunknee_export.zip straight to Downloads, no SSH
        needed. Route callbacks must be async (AppDaemon awaits them
        directly, unlike the sync-friendly listen_state/set_state
        calls elsewhere in this app), but the zipping itself is plain
        sync file I/O -- fine for an occasional, manually-triggered call.

        ?delete=true opts into deleting completed days from the Pi right
        after zipping them, to bound storage growth -- today's file is
        never touched (see completed_day_files). This is a deliberate
        trade-off, not a default: the delete happens as part of building
        this same response, before the client has actually received the
        bytes, so a connection that drops mid-transfer means the source
        files are already gone despite an incomplete download. Only use
        it once you trust the round-trip (sunknee-pull --and-clear).
        """
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(self.export_dir.glob("*.json")):
                zf.write(path, arcname=path.name)

        if request.query.get("delete") == "true":
            now = await self.get_now()
            to_delete = completed_day_files(self.export_dir, self._today(now))
            for path in to_delete:
                path.unlink()
            self.log(f"Deleted {len(to_delete)} completed-day capture file(s) after download")

        return web.Response(
            body=buffer.getvalue(),
            content_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="sunknee_export.zip"'},
        )

    def _publish_fit_peak(self):
        fit = fit_peak(
            self.peak_tracker.smoothed_series,
            min_points=self.fit_min_points,
            threshold_w=self.threshold_w,
        )
        if fit is None:
            return  # not enough data yet for a meaningful fit
        self.set_state(
            FIT_PEAK_POWER_ENTITY,
            state=fit["fit_peak_watts"],
            attributes={
                "friendly_name": "sunknee fitted peak power today",
                "unit_of_measurement": "W",
                "device_class": "power",
                "state_class": "measurement",
            },
            check_existence=False,
        )
        self.set_state(
            FIT_PEAK_POWER_AT_ENTITY,
            state=fit["fit_peak_at"],
            attributes={
                "friendly_name": "sunknee fitted peak power time today",
                "device_class": "timestamp",
            },
            check_existence=False,
        )

    def _read_solcast_entity(self, entity_id: str | None) -> dict | None:
        if not entity_id:
            return None
        state = self.get_state(entity_id, attribute="all", default=None)
        if state is None:
            self.log(f"Solcast entity {entity_id} not found -- skipping", level="WARNING")
        return state

    def _capture_solcast_forecast(self, kwargs):
        """Scheduled once daily (solcast_capture_time, default 00:05) --
        snapshots Predbat's own Solcast-derived forecast entities
        (sensor.predbat_pv_today/_tomorrow) verbatim into today's
        capture file. A deliberate single daily read, not a live
        subscription: Solcast/Predbat's forecast can update intraday,
        this only ever sees whatever it looked like at capture time."""
        now = self.get_now()
        self._ensure_capture_for(now)

        self.capture.solcast_forecast = {
            "captured_at": now.isoformat(),
            "today": self._read_solcast_entity(self.solcast_today_entity),
            "tomorrow": self._read_solcast_entity(self.solcast_tomorrow_entity),
        }
        self.capture.save(self._capture_path(self.capture.date))
        self.log(f"Captured Solcast/Predbat forecast snapshot for {self.capture.date}")
