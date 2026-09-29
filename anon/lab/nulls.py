"""Fake histories for the null test: same candles, the pattern under test removed.

- ``shuffle``: bars in random order (research.shuffled_bars): keeps each bar's shape and the
  overall drift, destroys every pattern in time, volatility clustering included.
- ``signflip``: each bar mirrored around the drift with probability 1/2: keeps timestamps,
  bar sizes, volatility clustering and the drift, destroys only which way the bars went.
A rule that trades direction has to beat both.

Order flow (volume, buy volume) stays with its bar. Null hypothesis for flow: which side's
market orders drove a bar carries no information, just like which way the bar went. So a
mirrored bar also mirrors its share of market buys, in log-odds around the mean log-odds of
the data (the flow's "drift"), exactly as prices are mirrored in log space around the drift:
the share never leaves (0, 1), volume is kept, and mirroring twice gives the bar back. A bar
traded entirely one way (share 0 or 1, infinite log-odds) simply switches sides.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence

from anon.models import Bar
from anon.research import shuffled_bars

KINDS = ("shuffle", "signflip")


def share_logit(share: float) -> float:
    return math.log(share / (1 - share))


def mirror_share(share: float, center: float) -> float:
    """The buy share mirrored in log-odds around ``center`` (a mean log-odds); 0 and 1 swap."""
    if share <= 0.0 or share >= 1.0:
        return 1.0 - min(1.0, max(0.0, share))
    x = share_logit(share) - 2 * center
    return 0.0 if x > 700 else 1 / (1 + math.exp(x))  # 700: past exp's range the share is 0 anyway


def flow_center(bars: Sequence[Bar]) -> float:
    """Mean log-odds of the buy share over bars traded both ways (0 when there are none)."""
    odds = [share_logit(b.buy_volume / b.volume) for b in bars if 0 < b.buy_volume < b.volume]
    return statistics.fmean(odds) if odds else 0.0


def signflip_bars(bars: Sequence[Bar], rng: random.Random) -> list[Bar]:
    prev = bars[0].open
    vectors = []
    for b in bars:
        vectors.append((math.log(b.open / prev), math.log(b.high / prev), math.log(b.low / prev), math.log(b.close / prev)))
        prev = b.close
    drift = statistics.fmean(v[3] for v in vectors)
    center = flow_center(bars)
    out: list[Bar] = []
    price = bars[0].open
    for b, (a, h, lo, c) in zip(bars, vectors, strict=True):
        buy = b.buy_volume
        if rng.random() < 0.5:
            a, h, lo, c = -a, -lo, -h, 2 * drift - c
            h, lo = max(h, a, c), min(lo, a, c)
            if b.volume > 0:  # the other side's market orders drove the mirrored bar
                buy = b.volume * mirror_share(b.buy_volume / b.volume, center)
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
