"""Fake histories for the null test: same candles, the pattern under test removed.

- ``shuffle``: bars in random order (research.shuffled_bars): keeps each bar's shape and the
  overall drift, destroys every pattern in time, volatility clustering included.
- ``signflip``: each bar mirrored around the drift with probability 1/2: keeps timestamps,
  bar sizes, volatility clustering and the drift, destroys only which way the bars went.
A rule that trades direction has to beat both.
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
    out: list[Bar] = []
    price = bars[0].open
    for b, (a, h, lo, c) in zip(bars, vectors, strict=True):
        if rng.random() < 0.5:
            a, h, lo, c = -a, -lo, -h, 2 * drift - c
            h, lo = max(h, a, c), min(lo, a, c)
        out.append(Bar(b.time, price * math.exp(a), price * math.exp(h), price * math.exp(lo), price * math.exp(c)))
        price *= math.exp(c)
    return out


def fake_history(kind: str, bars: Sequence[Bar], seed: int) -> list[Bar]:
    rng = random.Random(seed)
    if kind == "shuffle":
        return shuffled_bars(bars, rng)
    if kind == "signflip":
        return signflip_bars(bars, rng)
    raise ValueError(f"unknown null kind {kind}")
