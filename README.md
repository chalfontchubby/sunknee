# SunKnee

Determine solar panel pose from power output.

An AppDaemon module for Home Assistant that infers actual panel tilt and
azimuth from historical PV generation data alone — no pyranometer, no
external irradiance sensor. Tracks the estimate over time and flags
drift (panel movement, new shading, snow load, etc). Publishes a
locally-modelled generation forecast as an HA sensor that [Predbat](https://github.com/springfall2008/batpred)
can consume alongside existing forecast sources.

## Why

Forecast services like Solcast rely on the tilt/azimuth you tell them,
and their own irradiance model on top of that. This project doesn't
require either to be right — it estimates panel geometry from the
generation curve itself, using:

- **Morning/evening "knee" timing** — the point the sun crosses the
  plane of the array is pure geometry, independent of cloud cover, and
  more robust to partly-cloudy days than fitting a full curve shape.
- **Percentile-envelope baseline curves** — reconstructing a clear-sky-like
  reference from many imperfect days rather than requiring a genuinely
  clear one.

Full design notes, prior art, and open implementation decisions are in
[`DESIGN.md`](./DESIGN.md).

## Status

Early design stage — no code yet. Not a Solcast replacement or a
Solcast-correction tool (no API exists for writing back into their
model); this runs alongside existing forecast sources for comparison
and drift detection.

## Requirements (planned)

- Home Assistant with AppDaemon (same host Predbat runs on is fine)
- `pvlib` (installable via AppDaemon's `python_packages`)
- A PV generation sensor in HA (any inverter integration)
- A few weeks to months of history for the tilt/azimuth estimate to
  converge — seasonal spread meaningfully improves accuracy

## Repo layout

- `src/sunknee/` — the importable package. Currently: a stdlib-only
  `capture` data model and `naive_knee` placeholder detector (both safe
  to run on the AppDaemon/HA side), matplotlib-based `diagnostics`
  (local-only, `diagnostics` dependency group), and unimplemented stubs
  (`knee`, `envelope`, `estimator`) for the real algorithm from
  [`DESIGN.md`](./DESIGN.md).
- `apps/` — what actually gets deployed to AppDaemon: `apps.yaml` +
  `sunknee_app.py` (the `Hass` app) + a symlinked copy of
  `src/sunknee` so the app can `import sunknee` without needing the
  package pip-installed on the Pi.

## Local development

```
uv sync --group dev --group diagnostics
uv run pytest
```

Once you have a capture export (see below), plot it:

```
uv run sunknee-plot path/to/2026-07-31.json
```

This currently marks the naive threshold-based knee, not the real
geometric estimate (not built yet) — just enough to sanity-check
captured data.

## Deploying to AppDaemon

1. On the Pi, clone/pull this repo somewhere AppDaemon can reach (or
   symlink AppDaemon's `apps_dir` at this repo's `apps/` directory).
2. Edit `apps/apps.yaml` and set `pv_power_entity` to your Sigenergy PV
   power sensor's real entity_id.
3. AppDaemon hot-reloads on file changes. Check HA for
   `sensor.sunknee_status` (should read `running` — the hello-world
   liveness check) and, once there's daylight data,
   `sensor.sunknee_knee_morning` / `sensor.sunknee_knee_evening`.
4. Capture JSON lands in `export_dir` (default
   `/conf/apps/sunknee/data/YYYY-MM-DD.json`) inside the AppDaemon
   container, one file per day, updated on every sensor reading. Pull
   these files back here (scp/Samba/whatever you use) to run
   `sunknee-plot` against real data.

## License

TBD.
