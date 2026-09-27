from __future__ import annotations

from datetime import UTC, datetime

from anon.models import H1, Bar

T0 = datetime(2026, 9, 27, 0, tzinfo=UTC)


def bars_from(ohlc: list[tuple[float, float, float, float]], start: datetime = T0) -> list[Bar]:
    return [Bar(start + i * H1, o, h, lo, c) for i, (o, h, lo, c) in enumerate(ohlc)]


def flat(price: float, n: int, start: datetime = T0, wick: float = 20.0) -> list[Bar]:
    return bars_from([(price, price + wick, price - wick, price)] * n, start)


def then(bars: list[Bar], ohlc: list[tuple[float, float, float, float]]) -> list[Bar]:
    return bars + bars_from(ohlc, bars[-1].time + H1)
