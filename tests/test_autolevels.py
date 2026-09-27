import random
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from anon.autolevels import draw_levels
from anon.backtest import run_backtest
from anon.config import AutoLevelsConfig, Config, GhostConfig
from anon.models import H1, Bar
from tests.helpers import bars_from

AUTO = replace(Config(), auto=replace(Config().auto, enabled=True))


def box(low: float, high: float, n: int = 72) -> list[Bar]:
    """n quiet bars whose extremes are exactly low and high."""
    mid = (low + high) / 2
    rows = [(mid, mid + 10, mid - 10, mid)] * n
    rows[5] = (mid, mid + 10, low, mid)
    rows[40] = (mid, high, mid - 10, mid)
    return bars_from(rows)


def test_handoff_geometry_is_reproduced_exactly():
    drawn = draw_levels(box(83000, 87000), AutoLevelsConfig(), GhostConfig(), atr_period=14)
    lv = drawn.levels
    assert (lv.a_zone_bot, lv.a_zone_top) == (83000, 83500)
    assert lv.b_inv_ref == pytest.approx(84400)
    assert (lv.gray_low, lv.gray_high) == pytest.approx((84550, 84850))
    assert (lv.tp1, lv.liq_top) == (85500, 87000)
    assert drawn.ghost.stop_buffer == pytest.approx(50) and drawn.ghost.retest_tolerance == pytest.approx(100)


def test_no_levels_when_history_is_short_or_range_is_flat():
    assert "need 72" in draw_levels(box(83000, 87000, n=72)[:10], AutoLevelsConfig(), GhostConfig(), 14)
    flat = bars_from([(84000, 84010, 83990, 84000)] * 72)
    assert "range" in draw_levels(flat, AutoLevelsConfig(), GhostConfig(), 14)


def test_bad_fractions_rejected():
    with pytest.raises(ValueError):
        replace(Config(), auto=AutoLevelsConfig(tp1=0.4)).validate()


def random_walk(days: int, seed: int) -> list[Bar]:
    rng = random.Random(seed)
    p, t, out = 60000.0, datetime(2024, 9, 1, tzinfo=UTC), []
    for _ in range(24 * days):
        o = p
        p *= 1 + rng.gauss(0, 0.006)
        out.append(Bar(t, o, max(o, p) * (1 + abs(rng.gauss(0, 0.002))), min(o, p) * (1 - abs(rng.gauss(0, 0.002))), p))
        t += H1
    return out


def level_events(result):
    return {e.time.date(): e.detail for e in result.events if e.kind == "levels"}


def test_levels_never_use_bars_from_the_day_they_are_used():
    bars = random_walk(20, seed=3)
    base = run_backtest(AUTO, bars)
    # rewrite everything after the start of the 10th Thai day; that day's levels must not move
    cut = next(i for i, b in enumerate(bars) if b.close_time.hour == 17 and b.time.day == 11)
    spiked = bars[: cut + 1] + [replace(b, high=b.high * 1.5) for b in bars[cut + 1 :]]
    changed = run_backtest(AUTO, spiked)
    day = bars[cut].close_time.date()
    assert level_events(base)[day] == level_events(changed)[day]


def test_random_walk_shows_no_edge():
    """A look-ahead leak would show up as a confident edge on pure noise."""
    result = run_backtest(AUTO, random_walk(730, seed=11))
    assert result.stats.n >= 30
    assert (result.stats.prob_edge_positive or 0) < 0.9
    assert {"A", "B"} <= set(result.stats.by_setup)


def test_a_trades_remember_the_level_they_were_opened_under():
    result = run_backtest(AUTO, random_walk(730, seed=11))
    a_trades = [r for r in result.journal.records.values() if r.setup == "A"]
    assert a_trades and all(r.thesis_below is not None for r in a_trades)
    assert all(r.thesis_below is None for r in result.journal.records.values() if r.setup == "B")
