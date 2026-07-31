# sunknee

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

## License

TBD.
