from dataclasses import replace

import pytest

from anon.backtest import run_backtest
from anon.config import Config
from anon.research import SweepRow, format_sweep, run_sweep
from tests.test_autolevels import random_walk


def test_sweep_cell_matches_a_direct_backtest():
    bars = random_walk(240, seed=5)
    rows = run_sweep(Config(), bars, rrs=[1.0, 1.5], lookbacks=[48, 72])
    assert [(r.lookback, r.min_rr) for r in rows] == [(48, 1.0), (48, 1.5), (72, 1.0), (72, 1.5)]
    cfg = replace(Config(), auto=replace(Config().auto, enabled=True), ghost=replace(Config().ghost, min_rr=1.5))
    direct = run_backtest(cfg, bars).stats
    cell = rows[-1]
    assert (cell.n, cell.expectancy_r) == (direct.n, pytest.approx(direct.expectancy_r))


def row(**kw):
    base = dict(lookback=72, min_rr=1.5, n=85, win_rate=32.9, expectancy_r=0.197, profit_factor=1.29,
                max_drawdown_r=10.6, prob_edge_positive=0.82, first_half_r=0.2, second_half_r=0.15, avg_hours=14.0)
    return SweepRow(**{**base, **kw})


def test_holds_up_needs_both_halves_positive():
    assert row().holds_up
    assert not row(second_half_r=-0.1).holds_up
    assert not row(expectancy_r=-0.01).holds_up
    assert not row(n=0, first_half_r=None, second_half_r=None).holds_up


def test_format_marks_current_setting_and_counts_passes():
    text = format_sweep([row(min_rr=1.0, second_half_r=-0.08), row()], current=(72, 1.5))
    assert "◀ ตอนนี้" in text and "ผ่าน (E[R] > 0" in text and "1/2 ช่อง" in text


def test_shuffled_bars_keep_the_candles_but_not_the_order():
    import random

    from anon.research import shuffled_bars

    bars = random_walk(30, seed=2)
    mixed = shuffled_bars(bars, random.Random(1))
    assert len(mixed) == len(bars) and [b.time for b in mixed] == [b.time for b in bars]
    assert mixed[0].open == pytest.approx(bars[0].open)
    assert mixed[-1].close == pytest.approx(bars[-1].close)  # same total move
    assert [round(b.close, 6) for b in mixed] != [round(b.close, 6) for b in bars]
    ranges = sorted(round((b.high - b.low) / b.open, 9) for b in bars)
    assert sorted(round((b.high - b.low) / b.open, 9) for b in mixed) == ranges


def test_null_test_on_noise_is_not_significant():
    from anon.research import format_null, run_null_test

    test = run_null_test(Config(), random_walk(360, seed=4), 72, 1.5, trials=8)
    assert len(test.shuffled_r) == 8 and 0 < test.p_value <= 1
    assert test.p_value > 0.05  # noise must not look like an edge
    assert "p = " in format_null(test)
