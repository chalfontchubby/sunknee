# PV calibration analysis

Offline experiments comparing Predbat's Solcast forecast (as captured each night by SunKnee) with
measured generation. They were written for batpred issues #6204 (calibrating the PV10/PV90 band)
and #6205 (separate optimistic-solar and optimistic-load weights). Exploratory scripts, not part of
the package.

Run from the repo root with the project Python:

```bash
uv run python analysis/calib.py data /tmp/slots.json        # build per-half-hour forecast/actual table
uv run python analysis/calib_eval.py /tmp/slots.json        # rolling out-of-sample comparison of calibration schemes
uv run python analysis/bandscale.py /tmp/slots.json 0.1     # per-band exceedance scaling, P90 >= 0.1 kWh/slot (200 W)
```

`calib.py` writes `{"days": {date: {"fc": {slot: {...}}, "act": {slot: kWh}}}}`. Slots are UTC
half-hours (0-47) and every value is kWh per slot. In the forecast, `pv_estimate` is the raw
provider P50, `pv_estimateCL` is Predbat's calibrated P50 (not a clear-sky curve), and
`pv_estimate10` / `pv_estimate90` are Predbat's calibrated band.

Up to 2026-09-28 the 00:05 capture often ran before Predbat rolled `pv_today` over, so the day's
forecast is in the snapshot's `tomorrow` entry. `calib.py` picks whichever entry covers the date.
