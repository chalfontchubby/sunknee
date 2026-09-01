#!/usr/bin/env -S uv run python
"""Local-only: pull the capture-download zip from the deployed sunknee
app, unpack it into a local data directory, plot every day found there,
and plot a day-to-day summary trend across all of them
(sunknee.diagnostics.plot_summary).

Requires the `diagnostics` dependency group (matplotlib, via
sunknee.diagnostics.plot_day) -- not run on the AppDaemon/HA side.
Run via `uv run sunknee-pull`, or directly as `./src/sunknee/pull.py`
from within the repo (the shebang's `uv run` picks up this project's
environment based on the working directory, not the script's own path).

Forces IPv4 by resolving the host itself rather than leaving it to
default dual-stack ordering: see DESIGN.md's environment notes -- the
AppDaemon host's mDNS name resolves to both an IPv4 and an IPv6
link-local address, and the IPv6 one connects but then resets with zero
response bytes for any path. `curl`/browsers hit the same issue.
"""
from __future__ import annotations

import argparse
import io
import socket
import sys
import urllib.request
import zipfile
from pathlib import Path

from sunknee.capture import DayCapture
from sunknee.diagnostics import plot_day, plot_summary

DEFAULT_HOST = "homeassistant.local"
DEFAULT_PORT = 5050
DEFAULT_DATA_DIR = Path("data")


def resolve_ipv4(host: str) -> str:
    """First IPv4 address for host, bypassing IPv6 entirely.

    Queries unrestricted (no family filter) and filters the results in
    Python, rather than passing family=AF_INET into getaddrinfo()
    itself: on macOS, forcing AF_INET at the C level fails outright for
    .local mDNS names (a resolver quirk -- `ping` works because it
    doesn't restrict the family either) even when an IPv4 address
    genuinely exists among the unrestricted results.
    """
    infos = socket.getaddrinfo(host, None)
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            return sockaddr[0]
    raise OSError(f"No IPv4 address found for {host!r}")


def download_capture_zip(
    host: str, port: int, *, delete: bool = False, timeout: float = 30.0
) -> bytes:
    ip = resolve_ipv4(host)
    url = f"http://{ip}:{port}/app/sunknee_download"
    if delete:
        url += "?delete=true"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"AppDaemon host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"AppDaemon HTTP port (default: {DEFAULT_PORT})")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help=f"Local directory to unpack into (default: {DEFAULT_DATA_DIR})")
    parser.add_argument("--no-plot", action="store_true", help="Skip per-day plotting after pulling")
    parser.add_argument("--no-summary", action="store_true", help="Skip the day-to-day summary plot after pulling")
    parser.add_argument(
        "--and-clear",
        action="store_true",
        help=(
            "Also delete completed-day capture files on the Pi after "
            "zipping them (today's file is never touched). The delete "
            "happens server-side as part of this same request, before "
            "you've actually received the bytes -- only use this once "
            "you trust the round-trip; a dropped connection mid-transfer "
            "means the source files are gone despite an incomplete "
            "download."
        ),
    )
    args = parser.parse_args(argv)

    print(f"pulling from {args.host}:{args.port} ...")
    data = download_capture_zip(args.host, args.port, delete=args.and_clear)

    args.data_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(args.data_dir)
    print(f"unpacked into {args.data_dir}/")

    if not args.no_plot:
        for path in sorted(args.data_dir.glob("*.json")):
            out_path = path.with_suffix(".png")
            capture = DayCapture.load(path)
            plot_day(capture, out_path)
            print(f"  plotted {out_path}")

    if not args.no_summary:
        summary_path = args.data_dir / "summary.png"
        plot_summary(args.data_dir, summary_path)
        print(f"  plotted {summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
