"""Fake histories for the null test: same candles, the pattern under test removed.

- ``shuffle``: bars in random order (research.shuffled_bars): keeps each bar's shape and the
  overall drift, destroys every pattern in time, volatility clustering included.
- ``signflip``: each bar mirrored around the drift with probability 1/2: keeps timestamps,
  bar sizes, volatility clustering and the drift, destroys only which way the bars went.
A rule that trades direction has to beat both. Order flow (volume, buy volume) stays with its
bar; a mirrored bar also mirrors its share of market buys around the average share, so price
and flow still agree bar by bar and the usual buy/sell balance is kept like the drift.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

from anon.models import Bar
from anon.research import shuffled_bars

KINDS = ("shuffle", "signflip")


def signflip_bars(bars: Sequence[Bar], rng: random.Random) -> list[Bar]:
    prev = bars[0].open
    vectors = []
    for b in bars:
        vectors.append((math.log(b.open / prev), math.log(b.high / prev), math.log(b.low / prev), math.log(b.close / prev)))
        prev = b.close
    drift = statistics.fmean(v[3] for v in vectors)
    shares = [b.buy_volume / b.volume for b in bars if b.volume > 0]
    buy_drift = statistics.fmean(shares) if shares else 0.5
    out: list[Bar] = []
    price = bars[0].open
    for b, (a, h, lo, c) in zip(bars, vectors, strict=True):
        buy = b.buy_volume
        if rng.random() < 0.5:
            a, h, lo, c = -a, -lo, -h, 2 * drift - c
            h, lo = max(h, a, c), min(lo, a, c)
            if b.volume > 0:  # mirrored flow too: the other side's market orders drove it (around the usual share)
                buy = b.volume * min(1.0, max(0.0, 2 * buy_drift - b.buy_volume / b.volume))
        out.append(Bar(b.time, price * math.exp(a), price * math.exp(h), price * math.exp(lo), price * math.exp(c),
                       b.volume, buy))
        price *= math.exp(c)
    return out


def fake_history(kind: str, bars: Sequence[Bar], seed: int) -> list[Bar]:
    rng = random.Random(seed)
    if kind == "shuffle":
        return shuffled_bars(bars, rng)
    if kind == "signflip":
        return signflip_bars(bars, rng)
    raise ValueError(f"unknown null kind {kind}")
