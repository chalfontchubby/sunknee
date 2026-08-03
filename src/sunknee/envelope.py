"""Percentile-envelope baseline curve (secondary signal).

Not yet implemented -- see DESIGN.md, "Secondary signal: envelope/
percentile baseline curve" (Lonij et al., SCSF). Fitting the clear-sky
reference faces the same asymmetric dropout/overshoot noise as knee
estimation (sunknee.knee) -- meant to share that module's quantile-Huber
regression routine rather than a second bespoke implementation.
"""
from __future__ import annotations


def rolling_percentile_envelope(history, percentile: float = 90.0, window_days: int = 14):
    raise NotImplementedError("see DESIGN.md, envelope/percentile baseline curve")
