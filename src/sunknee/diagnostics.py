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
from datetime import datetime
from pathlib import Path

from sunknee.capture import DayCapture
from sunknee.naive_knee import naive_knee_indices


def plot_day(capture: DayCapture, out_path: Path, threshold_w: float = 10.0) -> None:
    import matplotlib.pyplot as plt

    times = [datetime.fromisoformat(r.timestamp) for r in capture.readings]
    watts = [r.watts for r in capture.readings]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(times, watts, label=capture.entity_id, color="tab:orange")

    morning_i, evening_i = naive_knee_indices(capture.readings, threshold_w)
    if morning_i is not None:
        ax.axvline(times[morning_i], color="tab:blue", linestyle="--", label="knee (naive)")
        ax.axvline(times[evening_i], color="tab:blue", linestyle="--")

    ax.set_title(f"sunknee capture — {capture.date}")
    ax.set_xlabel("time")
    ax.set_ylabel("watts")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_json", type=Path, help="Path to a DayCapture JSON export")
    parser.add_argument("-o", "--out", type=Path, default=None, help="Output PNG path (default: alongside input)")
    parser.add_argument("--threshold-w", type=float, default=10.0)
    args = parser.parse_args(argv)

    capture = DayCapture.load(args.capture_json)
    out_path = args.out or args.capture_json.with_suffix(".png")
    plot_day(capture, out_path, args.threshold_w)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
