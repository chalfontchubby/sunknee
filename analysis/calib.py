# ruff: noqa: UP031, SIM115, B023  (exploratory analysis scripts: %-format output, one-shot file reads, immediately-called lambdas)
"""Offline PV calibration experiments on SunKnee data (raw Solcast P50 vs 5 s generation)."""

import glob
import json
import sys
from datetime import UTC, datetime

DATA = sys.argv[1]
GLITCH_W = 3300.0
days = {}
for f in sorted(glob.glob(DATA + "/*.json")):
    d = json.load(open(f))
    sf = d.get("solcast_forecast")
    if not sf:
        continue
    date = d["date"]
    # The 00:05 capture can run before Predbat rolls pv_today over (every file up to 2026-09-28):
    # "today" then still holds yesterday's forecast and this date's is in "tomorrow". Take
    # whichever snapshot has more periods on this date (same rule as
    # capture.solcast_watts_series_for_date).
    best = {}
    for which in ("today", "tomorrow"):
        cand = {}
        for e in ((sf.get(which) or {}).get("attributes") or {}).get(
            "detailedForecast"
        ) or []:
            t = datetime.strptime(e["period_start"], "%Y-%m-%dT%H:%M:%S%z").astimezone(
                UTC
            )
            if t.strftime("%Y-%m-%d") != date:
                continue
            slot = t.hour * 2 + t.minute // 30
            # Values are kW averages for the half hour; energy = kW * 0.5
            cand[slot] = {
                k: e.get(k, 0.0) * 0.5
                for k in (
                    "pv_estimate",
                    "pv_estimate10",
                    "pv_estimate90",
                    "pv_estimateCL",
                )
            }
        if len(cand) > len(best):
            best = cand
    fc = best
    # Actual energy per UTC half-hour from 5 s samples (sample-and-hold, gaps > 60 s ignored, glitches dropped)
    act = {}
    rd = d["readings"]
    prev_t = prev_w = None
    for r in rd:
        t = datetime.fromisoformat(r["timestamp"]).astimezone(UTC)
        w = r["watts"]
        if w is None or w > GLITCH_W:
            continue
        if prev_t is not None:
            dt = (t - prev_t).total_seconds()
            if 0 < dt <= 60 and prev_t.strftime("%Y-%m-%d") == date:
                slot = prev_t.hour * 2 + prev_t.minute // 30
                act[slot] = act.get(slot, 0.0) + prev_w * dt / 3600.0 / 1000.0
        prev_t, prev_w = t, w
    if sum(act.values()) < 0.05:
        continue
    days[date] = (fc, act)

dates = sorted(days)
print("days", len(dates), dates[0], dates[-1])


def tot(x, key=None):
    return sum((v[key] if key else v) for v in x.values())


# 1. Per-day ratio and forecast "clearness": raw P50 total / rolling max raw P50 total (+-10 days)
print(
    "\n== Per day: actual / raw P50, and forecast clearness (P50 vs +-10 day max forecast)"
)
day_rows = []
for i, dt in enumerate(dates):
    fc, act = days[dt]
    p50 = tot(fc, "pv_estimate")
    a = tot(act)
    near = [tot(days[x][0], "pv_estimate") for x in dates[max(0, i - 10) : i + 11]]
    clear = p50 / max(near) if max(near) > 0 else 0
    day_rows.append((dt, p50, a, a / p50 if p50 > 0.05 else None, clear))
bins = [(0, 0.4), (0.4, 0.7), (0.7, 0.9), (0.9, 1.01)]
for lo, hi in bins:
    rows = [r for r in day_rows if lo <= r[4] < hi and r[3] is not None]
    if rows:
        print(
            "forecast clearness %.1f-%.1f: %2d days, ratio of sums %.2f, per-day ratios %s"
            % (
                lo,
                hi,
                len(rows),
                sum(r[2] for r in rows) / sum(r[1] for r in rows),
                " ".join("%.2f" % r[3] for r in rows),
            )
        )

# Slot-level: bin by slot forecast clearness (P50 slot / max P50 same slot over +-10 days)
print("\n== Per half-hour slot (09:00-15:00 UTC), binned by forecast clearness")
slot_rows = []
for i, dt in enumerate(dates):
    fc, act = days[dt]
    for s in range(18, 30):
        p = fc.get(s, {}).get("pv_estimate", 0)
        env = max(
            days[x][0].get(s, {}).get("pv_estimate", 0)
            for x in dates[max(0, i - 10) : i + 11]
        )
        if env > 0.05:
            slot_rows.append((dt, s, p, act.get(s, 0.0), p / env))
for lo, hi in [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)]:
    rows = [r for r in slot_rows if lo <= r[4] < hi]
    if rows:
        rs = sorted(r[3] / r[2] for r in rows if r[2] > 0.02)
        q = lambda f: rs[min(len(rs) - 1, int(f * len(rs)))]
        print(
            "clearness %.2f-%.2f: %3d slots, ratio of sums %.2f, slot ratio p10 %.2f p50 %.2f p90 %.2f"
            % (
                lo,
                hi,
                len(rows),
                sum(r[3] for r in rows) / sum(r[2] for r in rows),
                q(0.1),
                q(0.5),
                q(0.9),
            )
        )

json.dump(
    {"days": {d: {"fc": days[d][0], "act": days[d][1]} for d in dates}},
    open(sys.argv[2], "w"),
    indent=1,
)
