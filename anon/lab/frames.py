"""Higher timeframes built from closed H1 bars, exposed only once they have closed."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from anon.models import Bar


def resample(bars: Sequence[Bar], hours: int) -> tuple[list[Bar], list[int]]:
    """Buckets of ``hours`` aligned to UTC (24 = calendar day, 4 = 00/04/08...).

    Returns (htf_bars, last_closed) where ``last_closed[i]`` is the index of the newest
    bucket that had fully closed at the close of H1 bar ``i`` (-1 if none yet). A bucket
    counts as closed when its final hour has closed or a later bucket has started
    (missing hours); a bucket still forming at the end of the data is returned but never
    exposed through ``last_closed``."""
    if hours <= 0 or 24 % hours:
        raise ValueError("hours must divide 24")
    span = hours * 3600
    htf: list[Bar] = []
    ends: list[int] = []
    last_closed: list[int] = []
    for b in bars:
        key = int(b.time.timestamp()) // span
        if not ends or ends[-1] != (key + 1) * span:
            htf.append(Bar(datetime.fromtimestamp(key * span, tz=UTC), b.open, b.high, b.low, b.close,
                           b.volume, b.buy_volume))
            ends.append((key + 1) * span)
        else:
            prev = htf[-1]
            htf[-1] = Bar(prev.time, prev.open, max(prev.high, b.high), min(prev.low, b.low), b.close,
                          prev.volume + b.volume, prev.buy_volume + b.buy_volume)
        own = len(htf) - 1
        last_closed.append(own if b.close_time.timestamp() >= ends[own] else own - 1)
    return htf, last_closed
