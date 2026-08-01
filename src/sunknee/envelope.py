"""Percentile-envelope baseline curve (secondary signal).

Not yet implemented -- see DESIGN.md, "Secondary signal: envelope/
percentile baseline curve" (Lonij et al., SCSF).
"""
from __future__ import annotations


def rolling_percentile_envelope(history, percentile: float = 90.0, window_days: int = 14):
    raise NotImplementedError("see DESIGN.md, envelope/percentile baseline curve")
