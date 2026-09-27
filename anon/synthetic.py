"""Synthetic H1 paths for tests and drills. They exercise the mechanics only —
nothing measured on them says anything about real-market edge."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime

from anon.models import H1, Bar


def waypoint_bars(
    waypoints: Sequence[float],
    bars_per_leg: int = 6,
    noise: float = 40.0,
    seed: int = 1,
    start: datetime = datetime(2026, 9, 1, tzinfo=UTC),
) -> list[Bar]:
    """Closes walk linearly between waypoints; each bar gets a small random wick."""
    rng = random.Random(seed)
    closes: list[float] = [waypoints[0]]
    for a, b in zip(waypoints, waypoints[1:], strict=False):
        for k in range(1, bars_per_leg + 1):
            closes.append(a + (b - a) * k / bars_per_leg)
    bars: list[Bar] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) + rng.uniform(0, noise)
        lo = min(o, c) - rng.uniform(0, noise)
        bars.append(Bar(start + i * H1, o, hi, lo, c))
        prev = c
    return bars
