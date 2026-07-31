# PV Orientation Self-Calibration for Home Assistant / Predbat

## Goal
An AppDaemon module that infers actual panel tilt/azimuth from historical
generation data (no pyranometer, no external irradiance sensor), tracks
drift over time, and publishes a locally-modelled generation forecast as
an HA sensor that Predbat can consume alongside (not necessarily instead
of) Solcast.

## Context / why
- Existing setup: Solcast forecast feeds Predbat's `pv_forecast` config.
  Observed a persistent ~30-35% underestimate on clear days, already
  diagnosed as Solcast model bias rather than a tilt/azimuth
  misconfiguration (panels are correctly set as 36° tilt, SSW/~202°
  azimuth in Solcast's site config).
- Consequence: this project won't fix that specific bias directly. Its
  value is (a) an independent, locally-derived forecast to compare
  against Solcast, and (b) a drift/fault detector — if the converged
  tilt/azimuth estimate wanders from the configured value, something
  physically changed (panel movement, new shading, snow, etc).
- Feeding corrections back into Solcast is explicitly a nice-to-have,
  not a requirement — there's no API for it; the closest existing lever
  is `ha-solcast-solar`'s per-hour dampening factors, which is a local
  multiplier on their output, not a model update.

## Environment
- AppDaemon already running (hosts Predbat) on a Pi — this is a second
  AppDaemon app in the same container. `pvlib` installs via AppDaemon's
  `python_packages` config, no new infra needed.
- PV generation sensor comes from the Sigenergy Modbus HA integration.
- HA recorder purges detailed history after `purge_keep_days` (default
  10 days) — **increase this now** so full-resolution data accumulates
  going forward; don't rely on it for backfill beyond a couple of weeks.
- Long-term statistics (hourly agg) are kept indefinitely by default,
  queryable via `statistics_during_period` — usable as a coarse
  bootstrap for older history, at reduced (hourly) resolution.
- Sigen Cloud (the installer/web portal behind mySigen, not necessarily
  the phone app) reportedly offers 5-minute interval historical data
  with CSV download — a better one-off backfill source than HA's own
  stats if accessible. There's also a Sigenergy Cloud OpenAPI (an
  existing third-party HA integration polls it for live sensors,
  5-min-per-endpoint rate limit) — worth checking whether it exposes a
  bulk historical query endpoint too, vs. only live polling.

## Algorithm design

### Primary signal: morning/evening "knee" timing
The knee (generation on/off transition) occurs when the sun crosses the
plane of the array (angle of incidence → 90°) — a pure geometry event,
independent of irradiance magnitude. This makes it far more robust to
broken cloud than full-curve fitting: doesn't need a clean bell curve,
just enough light near dawn/dusk to detect the transition.

- Compute expected AOI=90° crossing times via `pvlib.solarposition` for
  candidate (tilt, azimuth).
- Fit against observed knee times across many days, ideally spanning
  seasons: azimuth mostly governs morning/evening asymmetry relative to
  solar noon; tilt mostly governs how that asymmetry changes with solar
  declination through the year. Seasonal spread separates the two
  parameters much better than any single day's curve shape.

Known confounders to model explicitly, not ignore:
- **Horizon shading** (trees, structures) shifts observed knee times
  independent of panel geometry, and asymmetrically (e.g. only mornings
  late → shading to the east, not an azimuth error). Model as a
  separate, slowly-varying bias term.
- **Inverter low-light MPPT behaviour** (startup threshold/hysteresis)
  adds a systematic, fairly stable per-inverter offset to observed knee
  vs. true geometric knee. Fittable, not just noise — don't assume
  knee = geometry exactly.

### Secondary signal: envelope/percentile baseline curve
For days that aren't fully clear (most of them), reconstruct a
clear-sky-like reference curve statistically rather than requiring a
single clean day:
- Take the Nth percentile (e.g. 80–95th, not max) of output at each
  time-of-day over a rolling multi-week window.
- Percentile rather than max specifically to reject midday overshoot
  outliers (cloud-edge irradiance enhancement, inverter clipping at
  rated power) — a real but midday-only phenomenon, doesn't touch the
  knee-based fit since that's a dawn/dusk, low-power regime.
- Prior art: Lonij et al. used 80th percentile of time-matched
  historical output for tilt/orientation estimation across a PV fleet;
  the "Statistical Clear Sky Fitting" (SCSF) method (Meyers et al.,
  arXiv:1907.08279) generalises this — model-agnostic, resilient to
  shading, no irradiance sensor required.

### State update over time
Recursive least squares or a Kalman filter, state = [tilt, azimuth],
updated once per usable day (either a knee-time observation or an
envelope-fit observation). Converges over weeks, damps single-day noise
(one dirty panel, a bird incident, a single bad fit).

## Output
- Publish a forecast sensor via AppDaemon's `set_state` for tomorrow's
  expected generation, derived from the converged tilt/azimuth plus
  whatever cloud/weather input is available.
- Predbat's `pv_forecast` config accepts arbitrary sensor entities, so
  this can run alongside Solcast for comparison rather than requiring a
  cutover.
- Drift alert: notify if converged (tilt, azimuth) departs from the
  configured 36°/~202° by more than a few degrees.

## Prior art / references
- Lonij, V. et al. — fleet-based tilt/orientation estimation via 80th
  percentile time-of-day method.
- Meyers, B. et al., "Statistical Clear Sky Fitting Algorithm,"
  arXiv:1907.08279 — model-agnostic clear-sky signal extraction from
  historical PV power data alone.
- Data-driven curve-matching method for tilt/azimuth inference from PV
  generation + off-site irradiance (ScienceDirect, ~4.5°/4.3° MAE).
- `pvlib` / `pvanalytics` — solar position, clear-sky models
  (Ineichen/Haurwitz), existing clear-sky detection utilities.

## Open decisions for implementation
- Storage: SQLite vs. flat JSON for the rolling data store (avoid
  depending on HA recorder for anything beyond ~2 weeks).
- Whether to implement knee-detection and envelope-fitting as two
  independent estimators feeding one Kalman update, or a single combined
  cost function.
- Backfill source priority: HA recorder (recent, fine-grained) → HA
  long-term stats (older, coarse) → Sigen Cloud CSV export (potentially
  fine-grained and older, manual one-off) → Sigenergy OpenAPI historical
  endpoint if one exists (not yet confirmed).
- Not in scope initially: writing back to Solcast (no API for it
  currently; local dampening-factor tuning is the closest existing
  lever if wanted later).
