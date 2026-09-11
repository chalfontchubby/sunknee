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
- Originally assumed Predbat and sunknee would share one AppDaemon
  container -- turned out wrong once actually deployed. They're
  separate HA add-ons (`6adb4f0d_predbat`, `a0d7b954_appdaemon`), each
  with its own config, confirmed directly from the filesystem. Whether
  Predbat's add-on still uses AppDaemon internally at all is a separate,
  softer claim -- Predbat reportedly moved away from that architecture
  at some point (unverified here, per recollection rather than direct
  inspection) -- but it doesn't matter either way: nothing in sunknee's
  config or deployment touches Predbat's, and nothing about Predbat's
  own setup (its dashboard being reachable, say) is evidence about
  sunknee's AppDaemon instance specifically. `pvlib` installs via
  AppDaemon's `python_packages` config, no new infra needed.
- PV generation sensor comes from the Sigenergy Modbus HA integration,
  and reports in **kW**, not W (`sensor.sigen_inverter_pv_power`, max
  ~5.4 in real captured data). sunknee now reads the entity's
  `unit_of_measurement` at startup and converts to real watts at
  ingestion (`sunknee.capture.watts_multiplier`) -- before this fix,
  `knee_threshold_w: 10` never triggered against kW-scale values (max
  5.4 < 10), so `sensor.sunknee_knee_morning`/`_evening` had silently
  never populated since deployment, and the peak-power sensors were
  numerically fine but tagged `unit_of_measurement: "W"` while actually
  holding kW-scale numbers (1000x mislabeled). Capture files from before
  this fix (2026-08-01 to 2026-08-03) are in the old, unconverted scale.
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
  touching whatever else is already defined there (in this case, an
  earlier `hello.py` test app in the same `a0d7b954_appdaemon` add-on --
  not Predbat, which is a separate add-on entirely, see above).
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
- AppDaemon's HTTP component is enabled -- confirmed directly in
  `a0d7b954_appdaemon/appdaemon.yaml` itself (`http:`, `admin:`, `api:`
  all declared there), not inferred from Predbat (see above: separate
  add-on, tells you nothing about this one). `register_route` works for
  serving arbitrary responses with custom headers -- used for the
  capture-download route (see Implementation status) instead of needing
  SSH/Samba to retrieve data for local analysis. `http:` (like `admin:`,
  `api:`, `hadashboard:`) must be a **top-level** key, a sibling of
  `appdaemon:` -- not nested inside it. Nesting it there doesn't crash
  anything (AppDaemon logs `Extra config field 'http'. This will be
  ignored` and falls back to the default port anyway), but the
  configured `url:` silently has no effect either.
- Sharp edge in `register_route` callbacks specifically: AppDaemon's
  sync-friendly API methods (`get_now()`, etc.) are `@sync_decorator`-
  wrapped so plain sync app code can call them as if they were regular
  synchronous methods. That wrapper checks which thread it's running
  on: from a worker thread (any plain `def` callback -- `initialize()`,
  `_on_power_change()`) it blocks and returns the real result via
  `run_coroutine_threadsafe`; from the *main* thread -- which is where
  `register_route`'s `async def` callbacks actually run, dispatched
  directly on AppDaemon's own HTTP event loop -- it instead does
  `asyncio.create_task(coro)` and hands back the **un-awaited Task
  object**, not the result. Calling `self.get_now()` the same way from
  inside `_download_capture` silently returned a Task instead of a
  datetime (`AttributeError: '_asyncio.Task' object has no attribute
  'strftime'`), and because AppDaemon's own `get_web_response` error
  helper doesn't set the actual HTTP status code on its response
  (embeds it in the HTML body text only), the client saw a misleading
  plain `200 OK` instead of anything flagging a server error. Fix: from
  an async route callback, `await self.get_now()` explicitly rather
  than relying on the sync wrapper's implicit behaviour -- a Task is
  itself awaitable, so this works cleanly once done deliberately.
- The AppDaemon add-on's port being correctly published to the host
  (Settings -> Add-ons -> AppDaemon -> Network, confirmed 5050 -> 5050)
  wasn't the actual blocker it looked like -- `homeassistant.local`
  resolves to both an IPv4 and an IPv6 link-local address, and the
  IPv6 one connects (TCP handshake succeeds) but then resets with zero
  response bytes for *any* path, not just the download route (verified
  by hitting AppDaemon's own built-in `/aui/index.html` and getting the
  identical failure). Forcing IPv4 (`curl -4`, or targeting the LAN IP
  directly) works cleanly. Root cause not chased further (probably
  router/mDNS advertising an IPv6 address Docker's port-publishing can't
  actually route to) -- `sunknee.pull` forces IPv4 by resolving the host
  itself rather than leaving it to default dual-stack ordering. One
  layer deeper than expected, though: passing `family=AF_INET` into
  `socket.getaddrinfo()` itself fails outright for `.local` mDNS names
  on macOS (`ping`, which doesn't restrict family, works fine) --
  `resolve_ipv4()` queries unrestricted and filters the results in
  Python instead of restricting the query.
- Deployment gotcha #2: cloning the whole repo in-place (per the
  symlink fix above) means AppDaemon's dependency scanner tries to
  import *every* `.py` file it finds recursively under the apps
  directory -- not just the one `sunknee_app.py` references -- including
  `tests/*.py` (needs `pytest`, not installed on the Pi). Logs a
  `ModuleNotFoundError` for each on every file change, though it doesn't
  actually stop `sunknee_app.py` itself from working, since that only
  imports `sunknee.capture`/`sunknee.naive_knee`. Fix: add `tests` to
  `exclude_dirs` in `appdaemon.yaml`'s top-level `appdaemon:` section.
  `src/sunknee/diagnostics.py` hit the same scanner for its matplotlib
  import; fixed on the code side instead by moving that import inside
  `plot_day()` rather than the module top level, since matplotlib is
  needed by one file within a directory (`src/sunknee/`) we do need
  scanned -- `exclude_dirs` can't select individual files.
  **Forward-looking caveat**: `exclude_dirs` matches on bare directory
  *name* only, anywhere in the scanned tree, with no per-app scoping --
  confirmed by reading AppDaemon's own `recursive_get_files()` (matches
  `item.name in exclude`, not a path). A non-issue today (nothing else
  shares `a0d7b954_appdaemon`'s apps directory), but if sunknee is ever
  installed alongside other custom AppDaemon apps -- someone else's, or
  a future HACS distribution -- and one of them has its own `tests`
  directory that needs scanning, this setting would silently exclude
  that too. The real fix would be a deploy step that copies only the
  files actually needed (`apps/apps.yaml`, `apps/sunknee_app.py`,
  `src/sunknee/*.py`) into the apps directory rather than cloning the
  whole repo in-place, so `tests/`, docs, and `pyproject.toml` never
  enter the scanned tree at all -- more moving parts than `git pull`,
  not worth building for a single deployment, but worth revisiting
  before any broader distribution.

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
  used both by the AppDaemon app and local tooling. Also carries an
  optional daily snapshot of Predbat's own Solcast-derived forecast
  entities (`solcast_forecast` on `DayCapture` -- Predbat pulls Solcast
  directly, no separate HA integration for it here) stored as a raw,
  unmodelled blob, plus `solcast_watts_series` to extract a given day's
  half-hourly P10/50/90 as watts (converted from Predbat's kWh-per-
  period figures) with correct UTC-vs-local-date handling (Predbat's
  `period_start` is UTC; a 23:00 UTC period is already tomorrow in
  BST/CEST -- matched against the local calendar date, not string-
  compared against the raw UTC value). Motivation is two-fold: sanity-
  checking Solcast's own accuracy against real captured generation
  (directly relevant to the ~30-35% underestimate noted in Context/why
  above), and -- separately, not the same thing -- exploring whether
  Solcast's day-ahead P10/50/90 spread correlates with actual intra-day
  spikiness (cloud-edge bursts, broken-cloud variability), which would
  make it a *leading indicator* Predbat could use to size export/battery
  buffers ahead of time. It is **not** a direct measurement of
  spikiness itself: Solcast's spread is inter-scenario/day-ahead
  forecast uncertainty at ~30min resolution, not sub-period variability
  -- that's a separate, still-open analysis on sunknee's own raw-vs-
  smoothed capture data, not something Solcast's forecast can give
  directly regardless of resolution.
- `apps/sunknee_app.py`: deployed AppDaemon app. Publishes a
  `sensor.sunknee_status` liveness sensor, listens to the Sigenergy PV
  power sensor, writes/updates a per-day JSON capture file, publishes
  naive knee-time sensors (`sensor.sunknee_knee_morning` / `_evening`,
  monotonic rolling peak, and a same-day parabola-fit cross-check) so
  the raw signal can be charted natively in HA without anything extra
  installed there, registers a `/app/sunknee_download` route that
  zips the capture files with a download header -- pulls data to a
  laptop for local analysis without SSH/Samba -- and once daily
  (`solcast_capture_time`, default 00:05) snapshots
  `sensor.predbat_pv_today`/`_tomorrow` into that day's capture file. A
  single daily read is a deliberate, accepted limitation: Solcast/
  Predbat's forecast can update intraday, this only ever sees whatever
  it looked like at capture time.
- `src/sunknee/naive_knee.py`: placeholder threshold-crossing knee
  detector powering those HA sensors — explicitly not the real
  algorithm above (no direct/diffuse decomposition, no linear
  extrapolation), just enough to sanity-check that data is flowing. Its
  parabola cross-check fit does use a real prototype of the asymmetric
  quantile-Huber loss the real algorithm calls for, though (IRLS,
  `_fit_quadratic_quantile_huber`) -- not the plain least-squares it
  started as.
  **Known bug, deliberately not fixed yet (2026-08-26 example)**:
  `RollingPeakTracker`'s trailing window is sized in reading *count*
  (40 readings), not wall-clock time. A ~1.5hr gap in incoming readings
  (HA/the integration apparently just not emitting state-changes while
  the sensor sat near 0 -- confirmed via the raw capture, next reading
  18:47:34 to 20:16:26) left the window mostly full of stale ~800W
  entries from just before the gap; only one entry gets evicted per new
  reading, so `smoothed_series` reported a flat, wrong ~826W across the
  *entire* gap (confirmed directly -- 826.0 at 18:47:34, still 826.0 at
  20:16:26) instead of decaying toward zero. Doesn't corrupt the naive
  knee (computed from raw readings, correct at 18:47:29) or that day's
  peak-so-far (826 < the real midday peak), but does feed the parabola
  fit's active window (826W clears the threshold) and visibly misleads
  the plot. Real fix: window sized in minutes, not reading count -- a
  gap then naturally empties it rather than stranding stale entries,
  and removes an existing wart (`peak_window`'s config comment already
  admits readings-vs-time is something the user has to mentally
  convert). Deferred: changes `RollingPeakTracker`'s constructor
  semantics (breaking), touches `apps.yaml`/CLI config, and every
  existing test using placeholder (non-ISO) timestamps for convenience
  would need real ones for time-based eviction to work -- a bigger
  lift than most fixes so far, parked rather than rushed.
- `src/sunknee/diagnostics.py`: local-only matplotlib CLI
  (`uv run sunknee-plot capture.json`) that plots a captured day's raw
  curve, the same windowed-percentile filtered curve
  `RollingPeakTracker` computes live (recomputed locally from the raw
  capture, nothing extra needed from the Pi), the naive knee markers,
  the parabola cross-check drawn as a full curve rather than just its
  vertex, and -- if that day's capture has a `solcast_forecast`
  snapshot -- Predbat's Solcast-derived P10-P90 band (shaded) and P50
  (dash-dot line) for a direct visual comparison against real
  generation. Also `uv run sunknee-summary ./data`
  (`sunknee.diagnostics.plot_summary`): a day-to-day trend view across
  every captured day in a directory -- knee/peak/fit times and watts
  plotted against date, for watching seasonal knee-time drift build up
  over the weeks/months before the real estimator exists to quantify it
  properly, plus a third panel with two deliberately-separate confidence
  signals: `peak_ratio` (today's filtered peak relative to the best seen
  across the whole directory -- a pvlib-free stand-in for the real `kt`
  clear-sky index) and `fit_relative_residual` (how well the active
  window agrees with its own fitted curve). Kept apart rather than
  combined into one score because they catch different failure modes --
  an overcast day can have a deceptively tight-looking fit (smooth and
  low, not smooth and informative), which fit quality alone can't
  distinguish from a genuinely strong, clean day. **Planned upgrade, not
  built yet**: comparing against a single global best-seen value is
  crude -- the true achievable clear-sky peak has its own smooth annual
  envelope (roughly sinusoidal outside the tropics, tracking solar
  declination; flatter or bimodal near the equator depending on tilt),
  so a September clear day should be *expected* to peak lower than a
  June one even under identical conditions. Fitting that envelope and
  comparing against it, rather than a static max, would be a better
  reference -- but it also depends on tilt/azimuth to a degree (the
  annual peak curve's shape shifts with pose), tolerable for a
  diagnostic confidence signal in a way it wouldn't be if it fed the
  actual pose estimate. Needs real seasonal calendar spread to fit
  meaningfully (ideally across a solstice) -- with only ~1 month of
  same-season data so far, fitting any seasonal curve now would mean
  fitting noise, a worse reference than the current crude one, not a
  better one. Revisit once there's enough span, or once the real
  estimator's own `kt`/clear-sky machinery exists to inform it directly.
  `sunknee-pull`
  generates both plots automatically (`sunknee-plot` per day,
  `sunknee-summary` once across all of them) since it calls the same
  underlying functions.
- `src/sunknee/knee.py`, `envelope.py`, `estimator.py`: unimplemented
  stubs for the real algorithm described above — not started yet.
- Storage on the Pi is unbounded for now: real captured data runs
  ~450KB-800KB/day of raw JSON (measured from the first 3 days), which
  adds up over months but isn't urgent. `/app/sunknee_download?delete=true`
  (`sunknee-pull --and-clear`) opts into deleting completed days after
  zipping them -- never touches today's file (still being actively
  written; deleting it wouldn't even save space, since the next reading
  rewrites it in full from the in-memory capture regardless, and risks
  losing the rest of the day if nothing triggers a re-save before
  midnight). The delete happens server-side as part of the same request
  that serves the zip, before the client has actually received the
  bytes -- a deliberate simplicity/safety trade-off (one round-trip, but
  a dropped connection mid-transfer means the source is gone despite an
  incomplete download), opt-in rather than default for that reason.
  Gzipping each day's file at rest (not just the download zip) would
  recover a lot of that footprint and isn't hard, but not worth building
  until it's actually a problem.

### Open decisions for implementation
- Storage: SQLite vs. flat JSON for the rolling data store (avoid
  depending on HA recorder for anything beyond ~2 weeks). The capture
  export format above (per-day JSON) is the debug/diagnostics facility,
  not necessarily this decision — the estimator's own rolling state
  store is still open. Worth keeping in mind these are different
  lifetimes: full per-reading raw capture is only needed during this
  diagnostics-before-algorithm phase, to have real data to develop
  against locally. Once the real estimator exists and processes each
  day's data once (extracting a knee-time or envelope observation, then
  updating the Kalman state), the Pi shouldn't need to retain raw
  per-day captures indefinitely at all -- only the estimator's own
  compact state (fit parameters, drift history) needs to persist
  long-term. The delete-after-download option above is a stopgap for
  now, not the intended steady-state shape of the system.
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

### Knee estimation: fit against the exact geometric model
(Supersedes an earlier linear-extrapolation approach: fit a straight
line to the steep, direct-dominated segment before the knee and
extrapolate to its zero-crossing. That's only valid when the sun
clears the horizon steeply enough that curvature is negligible next to
slope — and that assumption degrades continuously with latitude and
season, not as a hard cutoff. The sunrise/sunset crossing angle is
roughly `(90° − latitude)` at the equinox and shallows further toward
the local summer solstice, so the approximation is measurably worse
exactly at the solstice (checked explicitly for ~56°N/Edinburgh:
curvature there is real, not negligible). That's a real problem because
solstice data is precisely what separates tilt from azimuth — seasonal
spread is the whole point (see Primary signal above) — so the linear
method's error was worst on the observations most worth trusting.
Rejected fix: a locally-fit parabola or sinusoid chosen by
latitude/season — requires picking the right local approximation order
per deployment rather than one method correct everywhere, and still
breaks down at extreme cases (a hypothetical polar panel: the knee can
be tangential — curve touches zero and turns back without a
transversal sign change — locally quadratic there, not sinusoidal, so
even "parabola vs. sinusoid" isn't a clean choice across cases).

Resolution: don't locally approximate the curve shape at all. Fit the
observed power directly against the **exact `pvlib`-predicted curve**
for a candidate (tilt, azimuth) — compute true solar position and the
resulting cos(AOI)(t) via `pvlib.solarposition`, and regress observed
power against that exact shape. This sidesteps the
linear-vs-quadratic-vs-sinusoidal question entirely: the same method is
valid at the equinox, at the solstice, and (for free, though out of
scope to actively target) at extreme latitudes.

**Fitting against asymmetric noise**: cloud interference isn't
symmetric around the clear-sky curve — dropouts (attenuation) are
frequent and can be large; overshoot (cloud-edge enhancement, inverter
clipping) is occasional and small. Ordinary least squares weights both
directions equally and gets pulled down toward the dropouts. Use
**quantile regression** instead (pinball loss: `τ·r` for `r>0`,
`(τ−1)·r` for `r<0`), with τ set high (~0.7–0.9, tunable) to penalize
dropouts far more than overshoot, pulling the fit toward the upper
envelope rather than averaging through the gaps. Specifically the
smoothed variant, **quantile Huber loss** (quadratic within a small
window κ around zero) — stays differentiable everywhere, unlike plain
pinball loss's kink at zero, so gradient-based optimizers behave
sensibly. Not a novel choice: it's the same mechanism the SCSF
follow-on decomposition work (cited below) uses for its clear-sky
component, τ hand-tuned around 0.65.

**Unification**: the envelope/baseline-curve fit (below) faces the
identical asymmetric-noise problem, so both should share one
quantile-Huber regression routine applied to different target curves,
rather than two bespoke implementations.

Practical notes:
- Needs a handful of samples with non-trivial direct signal, not a
  clean view of the knee itself — the fit is against the whole
  candidate curve shape, not just a local window around the crossing.
- Sensor resolution matters here: worth checking empirically whether
  the Sigenergy sensor's interval (and any inverter-side MPPT smoothing)
  gives enough points to fit well.

### Separating shading signal from pose signal
Shading can only push an *observed* knee inward relative to the true
unobstructed geometric knee (later mornings, earlier evenings) — never
outward. This gives a clean way to distinguish "this knee is telling me
about panel pose" from "this knee is telling me about an obstruction":

- Track knee times per day using the geometric-model fit above.
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
  outliers (cloud-edge irradiance enhancement) — a real but midday-only
  phenomenon, doesn't touch the knee-based fit since that's a dawn/dusk,
  low-power regime.
- **Confirmed from real data**: this site clips. Filtered peak sits at a
  flat ~2.7-3.0kW on clear days (2026-08-31 example), matching a 7×400W
  array's ~2.8kW DC nameplate almost exactly, and the tops of clear-day
  curves are visibly flat rather than smoothly peaked. Worth being
  precise about the distinction from the overshoot bullet above:
  clipping is a *sustained plateau* lasting a real fraction of midday on
  clear days, not a brief outlier -- the envelope method should
  represent it as the genuine observed ceiling (percentile naturally
  does this correctly, since it's common enough within the window to
  not get rejected), not treat it as noise to reject the way a rare
  cloud-edge spike is. Confirms the parabola cross-check
  (`sunknee.naive_knee.fit_peak`) is a poor *shape* for clipped days
  specifically: plotted as a full curve (`sunknee-plot`/`sunknee-pull`
  now overlay it, not just its vertex), a parabola can't represent a
  flat top at all, whatever loss function fits it. Originally also
  undershot the real plateau by ~700-1000W with plain least-squares
  (symmetric loss pulled down by cloud dropouts -- see "Fitting against
  asymmetric noise" and "Multiple signals, not multiple competing curve
  shapes" below); switching the fit itself to quantile-Huber (tau=0.9,
  tracking the upper envelope) fixed that part -- now hugs the top of
  the plateau closely (2026-08-10 example) rather than cutting through
  the middle -- while the flat-top-vs-parabola shape mismatch remains,
  as expected, since only the real geometric model fixes that. Concrete
  evidence for why the real algorithm needs to fit against the exact
  geometric model rather than a generic polynomial, same reasoning as
  the knee-estimation section above.
- **A parabola can go negative; the sun can't.** Originally fit against
  the *whole* day's smoothed series, including the flat near-zero
  stretches before dawn and after dusk -- a parabola has no way to be
  curved in the middle and flat at the edges at once, and since nothing
  constrains it to stay >=0, it extrapolated to physically impossible
  negative watts trying to reconcile the two (visibly, in the plotted
  curve). Fixed by trimming `fit_peak`'s input to the active window
  (watts > threshold_w, same threshold naive_knee_indices uses) before
  fitting at all, not just clipping the output after the fact. That
  removed the worst of it (no more diving to large negative values deep
  into the night) but not quite all of it: even fit only to positive
  active-window data, the fitted curve can still dip slightly negative
  right at that window's own edges, because a parabola's curvature is
  constant while real solar power eases up from zero gently near the
  knee rather than linearly -- a parabola wide enough to match the
  midday peak doesn't have enough freedom left to also match that gentle
  edge behaviour. `sunknee.diagnostics` clips the *drawn* curve to >=0
  as a pragmatic finish (the fit's own coefficients are left honest,
  unclipped); fully resolving it needs the real geometric shape, not a
  patch on top of the wrong one -- same conclusion as the flat-top
  clipping mismatch above, from a different direction.
- **Circularity concern, raised and addressed differently than first
  discussed.** Trimming `fit_peak`'s input using the same threshold that
  also defines the naive knee markers means the two aren't fully
  independent measurements -- if they roughly agree, part of that
  agreement is manufactured by sharing a boundary, not discovered by the
  fit. Properly fixing that means the knee times becoming genuine *fit
  outputs* instead of an externally-imposed boundary -- e.g. fitting
  `k + max(0, A·sin(π·(t−t1)/(t2−t1)))` (ambient + a clipped direct
  term, matching the direct/diffuse decomposition above almost exactly)
  against the whole day with no trimming at all, t1/t2 falling out of
  the fit. That needs real nonlinear optimization (t1/t2 sit inside the
  sine's argument; unlike the quadratic case there's no closed form) --
  judged not worth building for a diagnostic cross-check when it's this
  close to just being the real algorithm, so parked rather than built.
  Cheaper interim fix taken instead: raise the active-window threshold
  to `max(threshold_w, active_fraction * today's peak)` (10% by
  default) rather than just `threshold_w` alone -- excludes the shallow
  "easing in" region near the knees too (never looked parabolic
  either -- real power rises roughly linearly there, a parabola can't),
  which was also why the fit looked implausibly wide. Doesn't remove the
  circularity, just narrows the window it's threshold-gated over, but
  costs nothing beyond a second, data-relative threshold and visibly
  fixed the width complaint. Known gap left deliberately unhandled: on a
  heavily overcast day "peak" is itself just diffuse noise, and
  active_fraction relative to a noisy peak can produce a fit that looks
  structurally valid while being meaningless -- no clear-sky gating
  here, that's the real filter's `kt` pre-gate's job (see "State update
  over time" below): skip low-signal days outright rather than make this
  diagnostic fit smart about it.
- Prior art: Lonij et al. used 80th percentile of time-matched
  historical output for tilt/orientation estimation across a PV fleet;
  the "Statistical Clear Sky Fitting" (SCSF) method (Meyers et al.,
  arXiv:1907.08279) generalises this — model-agnostic, resilient to
  shading, no irradiance sensor required.
- Faces the same asymmetric-noise problem as knee estimation above
  (dropouts frequent/large, overshoot occasional/small) — fitting the
  clear-sky-like reference should reuse the same quantile-Huber
  regression routine rather than a second bespoke implementation.

### State update over time
Recursive least squares or a Kalman filter, state = [tilt, azimuth],
updated once per usable day (either a knee-time observation or an
envelope-fit observation). Converges over weeks, damps single-day noise
(one dirty panel, a bird incident, a single bad fit).

Use an **adaptive (heteroscedastic) filter**: rather than a fixed
measurement-noise assumption, set the measurement noise covariance R
per day, so a bad day's observation naturally gets a small Kalman gain
(barely moves the state) without a separate hand-built confidence score
or ad hoc accept/reject gate bolted on top. Two inputs to R, meant to
combine rather than choose between:

1. **Cheap pre-gate: clear-sky index (kt).** `kt` = measured power ÷
   modelled clear-sky power for that time/date (`pvlib`'s clear-sky
   model — already needed for the geometric fit above), averaged over
   the midday window. Below some threshold (persistent kt ~0.3–0.4, the
   London-October case), skip the day outright — R effectively
   infinite, don't run the regression at all. Cheaper than always
   fitting and hoping the covariance saves you, and avoids the
   regression occasionally producing a spurious confident-looking fit
   to what's actually just diffuse-dominated noise. Foreshadowed
   already in the diagnostic tooling: `sunknee.diagnostics.peak_ratios`
   (a day's peak relative to the best seen, no pvlib needed) is a cheap
   stand-in for the same idea, kept deliberately separate from fit
   residual in the summary plot for exactly this reason -- a real `kt`
   gate is the properly physically-grounded version once `pvlib`'s
   clear-sky model is actually in play.
2. **Fit covariance for days that pass the gate.** The quantile-Huber
   regression's parameter covariance at its optimum (from the Hessian
   at convergence, or a quick bootstrap) directly gives the day's
   observation uncertainty — a shallow, noisy fit through weak signal
   produces wide covariance; a clean fit through a proper direct-
   component curve produces tight covariance. This is the actual
   statistical uncertainty of the observation, not a heuristic score,
   and maps directly onto R with no extra invented metric needed.

Net effect: clear summer days pull the tilt/azimuth estimate hard;
persistently overcast days (London in October) either get skipped by
the kt gate or contribute almost nothing via a wide covariance — both
doing the "don't influence the pose estimate" job, arrived at from the
filter's own machinery rather than a bolted-on rule.

**Filter vs. smoother.** A plain Kalman filter is causal by
construction -- it only ever uses data up to and including today, which
is exactly why confidence calibration is weak early on: it hasn't seen
enough days yet to know what "good" looks like for this site (kt and
peak-ratio-style references both need a body of history to be
meaningful). The standard fix is a Kalman *smoother* (forward-backward,
e.g. Rauch-Tung-Striebel): run the causal forward pass as normal, then
a backward pass that revisits every earlier estimate using the *full*
dataset, not just what was available at the time -- well-trodden
estimation theory, not something to invent from scratch. Already
present informally in the diagnostic tooling:
`sunknee.diagnostics.peak_ratios` compares each day against the best
peak seen across the *whole* directory, not just days-so-far -- already
non-causal/batch, not a streaming online comparison.

No real-time constraint exists here (nothing needs a same-day answer
that can never be revised), so the full RTS-smoother machinery probably
isn't necessary to get the same benefit: periodically re-run the whole
estimation over the *entire* accumulated history in batch, rather than
maintaining one irrevocably-forward-only running state -- every day
judged using everything eventually known (the second half of the year
informing the first), without implementing forward-backward Kalman
machinery specifically. A live incremental filter could still run
alongside that for a responsive day-to-day published forecast, with the
periodic batch re-fit recalibrating it and its confidence baselines --
a refinement worth having eventually, not a requirement to start with.

**Multiple signals, not multiple competing curve shapes.** Knee timing
and envelope-fit as separate observations, each with genuine per-day
confidence feeding one filter, is the design. Deliberately not the same
thing as fitting several candidate curve *shapes* (parabola, sinusoid,
...) to the same data and trusting whichever fits best or reports the
tightest covariance: fit covariance only captures noise sensitivity, not
model misspecification, and a structurally wrong model can report
deceptively tight covariance right alongside a systematically biased
answer. Concrete proof already in hand, even after fixing the parabola
cross-check's loss function to track the upper envelope (see the
envelope section above): it now fits the plateau's *height* well, but
still can't represent a flat top at all -- no amount of reweighting
fixes a shape that's structurally wrong, only picking the right shape
does. That's exactly why the knee-estimation method fits against the
one physically-correct `pvlib` geometric curve instead of picking from
a menu of generic shapes.

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
The exact-geometric-model knee-fitting method and the power-derived
horizon profile are novel enough that they *could* theoretically interest `pvanalytics` maintainers, but contributing to a scientific-computing library requires a level of domain confidence and review-readiness this project isn't aiming for — solar engineering isn't the core expertise here, this is a personal tool built for one system. Not a planned direction; noted only so it's not forgotten if the method ever proves unusually solid.


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


