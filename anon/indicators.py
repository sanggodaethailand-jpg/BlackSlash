"""Minimal indicators in pure Python (no numpy dependency)."""

from __future__ import annotations

from collections.abc import Sequence

from anon.models import Bar


def ema(values: Sequence[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be > 0")
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def atr(bars: Sequence[Bar], period: int) -> list[float]:
    """Wilder ATR. Values before ``period`` bars are a running mean."""
    trs = true_ranges(bars)
    out: list[float] = []
    for i, tr in enumerate(trs):
        if i < period:
            out.append(sum(trs[: i + 1]) / (i + 1))
        else:
            out.append((out[-1] * (period - 1) + tr) / period)
    return out
