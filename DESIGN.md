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

## Architecture

### Environment
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
- Confirmed setup: Home Assistant OS with Supervisor, AppDaemon running
  as the AppDaemon add-on (not a bare pip install). Its apps directory
  is the add-on's own config folder, on the host at
  `/addon_configs/<hash>_appdaemon/apps/` — distinct from HA core's
  `/config`, and not the same as the generic AppDaemon docs' `/conf`
  path.
- Deployment gotcha: AppDaemon's directory walk (which decides what to
  add to the Python import path) does not follow symlinks. A symlinked
  app directory gets its `apps.yaml` discovered but the `.py` module
  fails to import (`ModuleNotFoundError`) with no more specific error.
  Deploy by cloning the repo directly into a real directory under the
  apps folder, not by symlinking one in from elsewhere.
- AppDaemon merges every `apps.yaml` it finds recursively under the
  apps directory, so sunknee lives in its own subdirectory without
  touching Predbat's existing config.
- Site latitude/longitude aren't captured anywhere yet -- `knee.py`'s
  stub already takes them as parameters, but nothing supplies them.
  Rather than duplicating them into `apps.yaml` by hand, HA's built-in
  `zone.home` entity already carries `latitude`/`longitude` attributes
  (it has to, for `sun.sun` and weather integrations to work) --
  `self.get_state("zone.home", attribute="latitude")` should give the
  real value directly, no separate config or derivation needed.
- More precise cross-check numbers, for whenever the estimator
  converges enough to compare against: Solcast is configured at
  −160.25° in its own signed (0=N, wraps past ±180°) convention, which
  unwraps to 199.75° true bearing. An independent measurement (Suunto
  baseplate compass, multiple sighting points along the house with a
  rod for alignment, corrected for the site's +1°01′ magnetic
  declination) puts it at ~196.0° true -- a ~3.7° gap between the two,
  itself resting on the assumption that the house's walls are square
  and parallel to the roof/panel plane.

### Output
- Publish a forecast sensor via AppDaemon's `set_state` for tomorrow's
  expected generation, derived from the converged tilt/azimuth plus
  whatever cloud/weather input is available.
- Predbat's `pv_forecast` config accepts arbitrary sensor entities, so
  this can run alongside Solcast for comparison rather than requiring a
  cutover.
- Drift alert: notify if converged (tilt, azimuth) departs from the
  configured 36°/~202° by more than a few degrees.

### Implementation status
Diagnostics and integration were built before the algorithm, deliberately
— the goal is to see real generation data (raw curve, detected knees,
fitted curves) before committing to the estimator's internals.

- `src/sunknee/capture.py`: stdlib-only day-capture data model (JSON),
  used both by the AppDaemon app and local tooling.
- `apps/sunknee_app.py`: deployed AppDaemon app. Publishes a
  `sensor.sunknee_status` liveness sensor, listens to the Sigenergy PV
  power sensor, writes/updates a per-day JSON capture file, and
  publishes naive knee-time sensors (`sensor.sunknee_knee_morning` /
  `_evening`) so the raw signal can be charted natively in HA without
  anything extra installed there.
- `src/sunknee/naive_knee.py`: placeholder threshold-crossing knee
  detector powering those HA sensors — explicitly not the real
  algorithm above (no direct/diffuse decomposition, no linear
  extrapolation), just enough to sanity-check that data is flowing.
- `src/sunknee/diagnostics.py`: local-only matplotlib CLI
  (`uv run sunknee-plot capture.json`) that plots a captured day's raw
  curve with the naive knee markers.
- `src/sunknee/knee.py`, `envelope.py`, `estimator.py`: unimplemented
  stubs for the real algorithm described above — not started yet.

### Open decisions for implementation
- Storage: SQLite vs. flat JSON for the rolling data store (avoid
  depending on HA recorder for anything beyond ~2 weeks). The capture
  export format above (per-day JSON) is the debug/diagnostics facility,
  not necessarily this decision — the estimator's own rolling state
  store is still open.
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

## Algorithm Details

### Signal decomposition: direct vs. diffuse
Total generation = direct (beam) + diffuse (sky dome) component. These
have distinct shapes near the morning/evening boundary, and this
distinction is the key to reliable knee detection:

- **Direct** has a genuinely sharp cutoff at the true geometric knee
  (AOI=90°, sun crosses the plane of the array) — a hard boundary, no
  gradual decay.
- **Diffuse** doesn't disappear when direct does — it's a view-factor
  integral over the visible sky dome, so it decays gradually and
  contributes a small tail *past* the true geometric knee.

This means naive "power crosses a noise-floor threshold" detection is
biased late: the diffuse tail smears the observable transition past the
true direct-cutoff you actually want for pose estimation.

### Knee estimation: linear extrapolation of the direct component
Rather than detecting the transition directly, fit and extrapolate:

1. Take a rough changepoint estimate first (slope-based, e.g. where the
   derivative shifts from steep to shallow) to bound a candidate window.
2. Fit a straight line to the steep, direct-dominated segment of the
   curve just before that changepoint.
3. Extrapolate the line to its zero-crossing — that x-intercept is the
   knee estimate.

This works because near AOI=90°, cos(AOI) is locally linear in angle
(derivative of cosine is maximal there), and solar angular motion is
smooth over a short window, so direct-component power is close to
linear in time just before the true knee. The zero-crossing is
determined by the line's slope and position, not its height — so
day-to-day DNI magnitude noise (haze, thin cloud) mostly changes the
line's steepness and only weakly perturbs where it crosses zero. This
gives meaningfully lower variance per-day than waiting for power to
visibly hit a threshold.

Practical notes:
- Iterate: rough changepoint bounds the fit window, linear fit run
  within it, then re-extrapolate — self-correcting rather than circular.
- Needs a handful of samples with non-trivial direct signal in the
  window, not a clean view of the knee itself.
- Sensor resolution matters here: worth checking empirically whether
  the Sigenergy sensor's interval (and any inverter-side MPPT smoothing)
  gives enough points in the linear segment to fit well, or whether the
  window needs widening (at the cost of more diffuse contamination) to
  get enough data.

### Separating shading signal from pose signal
Shading can only push an *observed* knee inward relative to the true
unobstructed geometric knee (later mornings, earlier evenings) — never
outward. This gives a clean way to distinguish "this knee is telling me
about panel pose" from "this knee is telling me about an obstruction":

- Track knee times per day using the extrapolation method above.
- Take the extremum (earliest morning, latest evening) across a rolling
  window, using a percentile (e.g. 90–95th) rather than literal min/max
  to reject noise the same way the amplitude-envelope approach rejects
  overshoot outliers.
- If the extremum converges and continues to track seasonal movement as
  geometry predicts → treat as a valid pose (tilt/azimuth) observation.
- If the extremum plateaus and stops moving despite months of new data
  → that side is permanently obstructed at that boundary. Stop using it
  for pose fitting; treat the pinned value itself as a measurement for
  the horizon-profile stretch goal instead (see below) — it's not
  wasted, it's literally one point on the site's skyline (elevation, at
  the azimuth implied by that time of year).
- Tilt/azimuth remains identifiable from a single unobstructed knee's
  seasonal drift alone if the other side is permanently shaded — slower
  convergence than having both sides, but not blocked.

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

### Stretch goal: horizon profile from power data
A horizon profile (azimuth/elevation pairs describing the site's
skyline) is a standard input already consumed by `pvlib`, PVGIS, and
PVsyst — normally captured by fisheye photo or terrain data. This
project can derive the same artifact from power data alone:

- Bin observed sun positions by azimuth, using the converged
  tilt/azimuth model.
- For each azimuth bin, find the minimum elevation at which generation
  reliably switches on across many days/seasons.
- Assemble into a standard `.HOR`-style profile, feed back into
  `pvlib`'s existing horizon-shading DNI adjustment.
- Resolution is naturally strongest near the horizon (where the sun
  spends time at low elevation across many days/azimuths) and weak at
  high elevation — a reasonable match, since horizon obstructions rarely
  extend far up anyway.
- Requires the core tilt/azimuth estimate to have converged first, since
  azimuth-binning depends on it. Build after the core estimator is
  working, not alongside it.

### Note on upstream contribution
The linear-extrapolation knee method and the power-derived horizon
profile are novel enough that they *could* theoretically interest `pvanalytics` maintainers, but contributing to a scientific-computing library requires a level of domain confidence and review-readiness this project isn't aiming for — solar engineering isn't the core expertise here, this is a personal tool built for one system. Not a planned direction; noted only so it's not forgotten if the method ever proves unusually solid.


## Prior art / references
- Lonij, V. et al. — fleet-based tilt/orientation estimation via 80th
  percentile time-of-day method.
- Meyers, B. et al., "Statistical Clear Sky Fitting Algorithm,"
  arXiv:1907.08279 — model-agnostic clear-sky signal extraction from
  historical PV power data alone.
- Data-driven curve-matching method for tilt/azimuth inference from PV
  generation + off-site irradiance (ScienceDirect, ~4.5°/4.3° MAE).
- `pvlib` / `pvanalytics` — solar position, clear-sky models
  (Ineichen/Haurwitz), existing clear-sky detection utilities, and
  built-in horizon-shading DNI adjustment from az/el profiles.


