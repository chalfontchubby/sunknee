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

Early stage: capture mode and a hello-world AppDaemon deployment are
working end to end; the actual tilt/azimuth estimation algorithm isn't
built yet (see `src/sunknee/knee.py`, `envelope.py`, `estimator.py`).
Not a Solcast replacement or a Solcast-correction tool (no API exists
for writing back into their model); this runs alongside existing
forecast sources for comparison and drift detection.

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

1. Clone this repo **directly into a real directory under AppDaemon's
   apps directory** — e.g. for the HA OS AppDaemon add-on:
   ```
   git clone <this repo's URL> /addon_configs/<slug>_appdaemon/apps/sunknee
   ```
   Don't symlink it in from elsewhere: AppDaemon's directory walk (which
   decides what to add to the Python import path) doesn't follow
   symlinks, so a symlinked app directory gets its `apps.yaml` picked up
   but `sunknee_app.py` fails to import (`ModuleNotFoundError:
   No module named 'sunknee_app'`). The one symlink inside the repo
   itself (`apps/sunknee` → `../src/sunknee`) is fine, since that's
   resolved by Python's own import machinery once the containing
   directory is already on `sys.path`, not by AppDaemon's walk.
2. Add `tests` to `exclude_dirs` in `appdaemon.yaml`'s top-level
   `appdaemon:` section. Cloning the whole repo in-place means
   AppDaemon's dependency scanner tries to import every `.py` file it
   finds recursively, including `tests/*.py` (needs `pytest`, not
   installed on the Pi) — logs a `ModuleNotFoundError` on every file
   change otherwise. Harmless to `sunknee_app.py` itself either way
   (it never imports the test files), but noisy.
3. Edit `apps/apps.yaml` and set `pv_power_entity` to your Sigenergy PV
   power sensor's real entity_id.
4. AppDaemon hot-reloads on file changes. Check HA for
   `sensor.sunknee_status` (should read `running` — the hello-world
   liveness check) and, once there's daylight data,
   `sensor.sunknee_knee_morning` / `sensor.sunknee_knee_evening`. If it
   doesn't show up, check the add-on's own log (Settings → Add-ons →
   AppDaemon → Log tab, or `ha addons logs <slug>_appdaemon`) — that's
   separate from Settings → System → Logs, which only covers HA core.
5. Capture JSON lands in `export_dir` (default
   `/config/apps/sunknee/data/YYYY-MM-DD.json`) inside the AppDaemon
   container, one file per day, updated on every sensor reading.
6. To pull that data down for local analysis without SSH: visit
   `http://<appdaemon-host>:<appdaemon-http-port>/app/sunknee_download`
   in a browser (or `curl -O -J <that URL>`) — it zips every captured
   day and serves it with a download header, straight to your
   Downloads folder, the same way Predbat's debug-info download works.
   The port is whatever `http:` is configured to in your `appdaemon.yaml`
   (Predbat's own dashboard already proves this is enabled). Unzip
   locally and run `sunknee-plot` against whichever day you want.
7. Updates: `cd /addon_configs/<slug>_appdaemon/apps/sunknee && git pull`.

## License

TBD.
