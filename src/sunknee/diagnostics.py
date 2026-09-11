"""Local-only diagnostics: plot a captured day's PV power curve with the
naive placeholder knee marker.

Requires the `diagnostics` dependency group (matplotlib) -- not
installed on the AppDaemon/HA side, and not imported by apps/sunknee_app.py.
Run via `uv run sunknee-plot <capture.json>`.

matplotlib is imported lazily inside plot_day(), not at module level:
AppDaemon's own dependency scanner tries to import every .py file it
finds recursively under the apps directory (since the whole repo is
cloned in-place there, not just the deployable subset -- see DESIGN.md's
symlink-import-path note), including this file, even though
sunknee_app.py never imports it. A module-level import would make that
scan fail with ModuleNotFoundError on the Pi, where matplotlib isn't
installed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sunknee.capture import DayCapture, plausible_readings, solcast_watts_series
from sunknee.naive_knee import RollingPeakTracker, fit_peak, naive_knee_indices


def plot_day(
    capture: DayCapture,
    out_path: Path,
    threshold_w: float = 10.0,
    peak_window: int = 40,
    peak_percentile: float = 95.0,
    fit_min_points: int = 30,
    fit_tau: float = 0.9,
    fit_kappa: float | None = None,
    fit_active_fraction: float = 0.1,
    max_plausible_watts: float | None = None,
) -> None:
    import matplotlib.pyplot as plt

    times = [datetime.fromisoformat(r.timestamp) for r in capture.readings]
    watts = [r.watts for r in capture.readings]

    # Raw trace above plots every reading, glitches included, so an
    # implausible spike stays visible even once it's excluded from
    # everything derived below (see sunknee.capture.plausible_readings).
    readings = plausible_readings(capture.readings, max_plausible_watts)

    # Same windowed-percentile smoothing apps/sunknee_app.py uses live
    # (RollingPeakTracker), just recomputed locally from the raw capture
    # -- "the filtered output", spikes rejected without needing anything
    # extra persisted from the Pi side.
    tracker = RollingPeakTracker(window=peak_window, percentile=peak_percentile)
    tracker.replay(readings)
    smoothed_times = [datetime.fromisoformat(t) for t, _ in tracker.smoothed_series]
    smoothed_watts = [w for _, w in tracker.smoothed_series]

    fig, ax = plt.subplots(figsize=(10, 5))

    # Predbat's own Solcast-derived forecast (P10-P90 shaded, P50 line),
    # if a daily snapshot was captured -- drawn first so it sits behind
    # the real curve. Needs a reference timezone to resolve Predbat's
    # UTC period_start values against this capture's local date (see
    # solcast_watts_series) -- taken from the first raw reading, since
    # there's nothing else in a capture to derive it from.
    if capture.solcast_forecast and times:
        local_tz = times[0].tzinfo
        solcast = solcast_watts_series(capture.solcast_forecast, capture.date, local_tz)
        if solcast:
            s_times = [datetime.fromisoformat(t) for t, _, _, _ in solcast]
            s_p10 = [p10 for _, p10, _, _ in solcast]
            s_p50 = [p50 for _, _, p50, _ in solcast]
            s_p90 = [p90 for _, _, _, p90 in solcast]
            ax.fill_between(s_times, s_p10, s_p90, color="tab:purple", alpha=0.15, label="Solcast P10-P90")
            ax.plot(s_times, s_p50, color="tab:purple", linestyle="-.", linewidth=1.2, label="Solcast P50")

    ax.plot(times, watts, label=capture.entity_id, color="tab:orange", alpha=0.5, linewidth=0.8)
    ax.plot(
        smoothed_times, smoothed_watts,
        label=f"filtered (p{peak_percentile:g}, window={peak_window})",
        color="tab:blue", linewidth=1.5,
    )

    morning_i, evening_i = naive_knee_indices(readings, threshold_w)
    if morning_i is not None:
        # Indices are into `readings` (filtered), not `times`/`watts`
        # (raw, unfiltered) -- resolve the timestamp from `readings`
        # itself rather than reusing the raw arrays' indices.
        ax.axvline(datetime.fromisoformat(readings[morning_i].timestamp), color="tab:green", linestyle="--", label="knee (naive)")
        ax.axvline(datetime.fromisoformat(readings[evening_i].timestamp), color="tab:green", linestyle="--")

    # Parabola cross-check, same one apps/sunknee_app.py publishes as
    # sensor.sunknee_fit_peak_power_today -- drawn as a full curve here,
    # not just its vertex. Fit tracks the upper envelope (quantile-Huber
    # loss, tau close to 1) rather than balancing errors symmetrically --
    # see fit_peak. Only fits the active window (same threshold_w as the
    # knee markers), not the flat dark stretches either side of it -- a
    # plain parabola still won't match a clipped flat-topped day's shape
    # well (see DESIGN.md) -- that mismatch is itself a useful thing to
    # see, not a plotting bug.
    fit = fit_peak(
        tracker.smoothed_series, min_points=fit_min_points,
        tau=fit_tau, kappa=fit_kappa, threshold_w=threshold_w,
        active_fraction=fit_active_fraction,
    )
    if fit is not None:
        base = datetime.fromisoformat(fit["base"])
        a, b, c = fit["a"], fit["b"], fit["c"]
        n = 200
        xs_curve = [fit["x_min"] + i * (fit["x_max"] - fit["x_min"]) / n for i in range(n + 1)]
        curve_times = [base + timedelta(minutes=x) for x in xs_curve]
        # Clipped to >=0 for display: trimming the fit to the active
        # window (above) stops it extrapolating deep into the night, but
        # doesn't guarantee the curve stays non-negative right at that
        # window's own edges -- a parabola's curvature is constant, real
        # solar power eases up from zero gently near the knee rather
        # than linearly, so a parabola matching the midday peak can
        # still dip slightly below zero right at the boundary even
        # though every point it was fit to is positive. The fit's own
        # coefficients (a, b, c) are left as-is; only the drawn line
        # respects the physical floor the math doesn't know about.
        curve_watts = [max(0.0, a * x**2 + b * x + c) for x in xs_curve]
        ax.plot(curve_times, curve_watts, color="tab:red", linestyle=":", label=f"parabola fit (τ={fit_tau:g})")
        ax.axvline(datetime.fromisoformat(fit["fit_peak_at"]), color="tab:red", linestyle=":", alpha=0.5)

    ax.set_title(f"sunknee capture — {capture.date}")
    ax.set_xlabel("time")
    ax.set_ylabel("watts")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def day_summary(
    capture: DayCapture,
    threshold_w: float = 10.0,
    peak_window: int = 40,
    peak_percentile: float = 95.0,
    fit_min_points: int = 30,
    fit_tau: float = 0.9,
    fit_kappa: float | None = None,
    fit_active_fraction: float = 0.1,
    max_plausible_watts: float | None = None,
) -> dict:
    """Per-day summary parameters -- knee times, filtered peak time/watts,
    parabola fit peak time/watts -- extracted the same way plot_day and
    the live HA sensors do, for plot_summary's day-to-day trend view.
    Any parameter that couldn't be determined for this day (e.g. no
    knee crossing, or not enough points for a fit) comes back as None
    rather than a placeholder value, so callers can skip gaps instead of
    plotting a misleading zero."""
    readings = plausible_readings(capture.readings, max_plausible_watts)

    morning_i, evening_i = naive_knee_indices(readings, threshold_w)
    morning_at = readings[morning_i].timestamp if morning_i is not None else None
    evening_at = readings[evening_i].timestamp if evening_i is not None else None

    tracker = RollingPeakTracker(window=peak_window, percentile=peak_percentile)
    tracker.replay(readings)

    fit = fit_peak(
        tracker.smoothed_series, min_points=fit_min_points,
        tau=fit_tau, kappa=fit_kappa, threshold_w=threshold_w,
        active_fraction=fit_active_fraction,
    )

    return {
        "date": capture.date,
        "morning_knee_at": morning_at,
        "evening_knee_at": evening_at,
        "peak_watts": tracker.peak_watts if tracker.peak_at is not None else None,
        "peak_at": tracker.peak_at,
        "fit_peak_watts": fit["fit_peak_watts"] if fit is not None else None,
        "fit_peak_at": fit["fit_peak_at"] if fit is not None else None,
        # How well the active window agrees with the fitted curve --
        # NOT the same thing as "was today informative" (see fit_peak).
        # Relative (fraction of this day's own peak) so it's comparable
        # across days of very different magnitude.
        "fit_relative_residual": (
            fit["fit_rms_residual"] / fit["fit_peak_watts"]
            if fit is not None and fit["fit_peak_watts"]
            else None
        ),
    }


def _hour_of_day(iso_timestamp: str) -> float:
    dt = datetime.fromisoformat(iso_timestamp)
    return dt.hour + dt.minute / 60 + dt.second / 3600


def peak_ratios(summaries: list[dict]) -> list[float | None]:
    """Each day's filtered peak relative to the best peak seen across
    all of them -- a free (no pvlib/clear-sky model needed) stand-in for
    DESIGN.md's kt clear-sky index: not "was today's absolute output
    high" but "was today close to what this system's actually capable
    of." A day can have a clean, tight fit (see fit_relative_residual)
    while still being useless for pose -- an overcast day is smooth and
    low, not smooth and informative -- and this is what catches that,
    which fit quality alone can't. None for days with no peak_watts."""
    known = [s["peak_watts"] for s in summaries if s["peak_watts"] is not None]
    if not known or max(known) <= 0:
        return [None] * len(summaries)
    best = max(known)
    return [s["peak_watts"] / best if s["peak_watts"] is not None else None for s in summaries]


def plot_summary(data_dir: Path, out_path: Path, **day_summary_kwargs) -> None:
    """Day-to-day trend view across every capture JSON in data_dir: knee/
    peak/fit times (top panel, hour-of-day), peak/fit watts (middle
    panel), and two confidence signals (bottom panel) -- e.g. to watch
    for the seasonal knee-time drift DESIGN.md's algorithm depends on
    separating tilt from azimuth, and to spot which days are actually
    trustworthy. peak_ratio and fit_relative_residual are deliberately
    separate lines, not combined into one score: they measure different
    failure modes (weak signal vs. noisy fit) and conflating them loses
    exactly the distinction that matters -- see peak_ratio's docstring.
    Today's file (if present) is included like any other; its "evening
    knee"/peak-so-far is just wherever capture had gotten to, not a real
    dusk value yet -- expect the most recent point to often look out of
    step until that day is actually complete.
    """
    import matplotlib.pyplot as plt

    paths = sorted(data_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"no capture JSON files found in {data_dir}")
    summaries = [day_summary(DayCapture.load(p), **day_summary_kwargs) for p in paths]
    ratios = peak_ratios(summaries)
    # A plain calendar date, not a datetime -- there's no timezone
    # question for "which day is this" the way there is for the
    # readings' own timestamps.
    dates = [datetime.strptime(s["date"], "%Y-%m-%d").date() for s in summaries]  # noqa: DTZ007

    fig, (ax_time, ax_watts, ax_confidence) = plt.subplots(3, 1, figsize=(10, 11), sharex=True)

    def _time_series(key):
        xs, ys = [], []
        for d, s in zip(dates, summaries):
            if s[key] is not None:
                xs.append(d)
                ys.append(_hour_of_day(s[key]))
        return xs, ys

    for key, label, color in [
        ("morning_knee_at", "morning knee", "tab:green"),
        ("evening_knee_at", "evening knee", "tab:olive"),
        ("peak_at", "filtered peak time", "tab:blue"),
        ("fit_peak_at", "fit peak time", "tab:red"),
    ]:
        xs, ys = _time_series(key)
        ax_time.plot(xs, ys, marker="o", markersize=3, label=label, color=color)

    ax_time.set_ylabel("time of day (hours)")
    ax_time.set_ylim(0, 24)
    ax_time.set_title("sunknee day-to-day summary")
    ax_time.legend()

    def _watts_series(key):
        xs, ys = [], []
        for d, s in zip(dates, summaries):
            if s[key] is not None:
                xs.append(d)
                ys.append(s[key])
        return xs, ys

    for key, label, color in [
        ("peak_watts", "filtered peak", "tab:blue"),
        ("fit_peak_watts", "fit peak", "tab:red"),
    ]:
        xs, ys = _watts_series(key)
        ax_watts.plot(xs, ys, marker="o", markersize=3, label=label, color=color)

    ax_watts.set_ylabel("watts")
    ax_watts.legend()

    def _confidence_series(values):
        xs, ys = [], []
        for d, v in zip(dates, values):
            if v is not None:
                xs.append(d)
                ys.append(v)
        return xs, ys

    xs, ys = _confidence_series(ratios)
    ax_confidence.plot(xs, ys, marker="o", markersize=3, label="peak ratio (vs. best seen)", color="tab:purple")
    xs, ys = _confidence_series([s["fit_relative_residual"] for s in summaries])
    ax_confidence.plot(xs, ys, marker="o", markersize=3, label="fit relative residual", color="tab:brown")

    ax_confidence.set_ylabel("ratio")
    ax_confidence.set_xlabel("date")
    ax_confidence.legend()

    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_json", type=Path, help="Path to a DayCapture JSON export")
    parser.add_argument("-o", "--out", type=Path, default=None, help="Output PNG path (default: alongside input)")
    parser.add_argument("--threshold-w", type=float, default=10.0)
    parser.add_argument("--peak-window", type=int, default=40, help="Trailing-window size for the filtered curve (default: 40)")
    parser.add_argument("--peak-percentile", type=float, default=95.0, help="Percentile for the filtered curve (default: 95)")
    parser.add_argument("--fit-min-points", type=int, default=30, help="Minimum smoothed points before drawing the parabola fit (default: 30)")
    parser.add_argument("--fit-tau", type=float, default=0.9, help="Quantile-Huber tau for the parabola fit; closer to 1.0 hugs the upper envelope harder (default: 0.9)")
    parser.add_argument("--fit-kappa", type=float, default=None, help="Quantile-Huber transition width in watts (default: 5%% of the day's range)")
    parser.add_argument("--fit-active-fraction", type=float, default=0.1, help="Exclude points below this fraction of the day's peak from the parabola fit, not just below --threshold-w (default: 0.1)")
    parser.add_argument("--max-plausible-watts", type=float, default=None, help="Ignore readings above this for peak/knee/fit (raw trace still shows them) -- e.g. a sensor/Modbus glitch reading 2x your inverter's rated capacity (default: no filtering)")
    args = parser.parse_args(argv)

    capture = DayCapture.load(args.capture_json)
    out_path = args.out or args.capture_json.with_suffix(".png")
    plot_day(
        capture, out_path, args.threshold_w,
        peak_window=args.peak_window,
        peak_percentile=args.peak_percentile,
        fit_min_points=args.fit_min_points,
        fit_tau=args.fit_tau,
        fit_kappa=args.fit_kappa,
        fit_active_fraction=args.fit_active_fraction,
        max_plausible_watts=args.max_plausible_watts,
    )
    print(f"wrote {out_path}")
    return 0


def summary_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=plot_summary.__doc__)
    parser.add_argument("data_dir", type=Path, help="Directory of DayCapture JSON exports (e.g. ./data)")
    parser.add_argument("-o", "--out", type=Path, default=None, help="Output PNG path (default: summary.png inside data_dir)")
    parser.add_argument("--threshold-w", type=float, default=10.0)
    parser.add_argument("--peak-window", type=int, default=40)
    parser.add_argument("--peak-percentile", type=float, default=95.0)
    parser.add_argument("--fit-min-points", type=int, default=30)
    parser.add_argument("--fit-tau", type=float, default=0.9)
    parser.add_argument("--fit-kappa", type=float, default=None)
    parser.add_argument("--fit-active-fraction", type=float, default=0.1)
    parser.add_argument("--max-plausible-watts", type=float, default=None, help="Ignore readings above this for peak/knee/fit (default: no filtering)")
    args = parser.parse_args(argv)

    out_path = args.out or (args.data_dir / "summary.png")
    plot_summary(
        args.data_dir, out_path,
        threshold_w=args.threshold_w,
        peak_window=args.peak_window,
        peak_percentile=args.peak_percentile,
        fit_min_points=args.fit_min_points,
        fit_tau=args.fit_tau,
        fit_kappa=args.fit_kappa,
        fit_active_fraction=args.fit_active_fraction,
        max_plausible_watts=args.max_plausible_watts,
    )
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
