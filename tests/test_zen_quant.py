from datetime import date

import pytest

from anon.config import ZenConfig
from anon.models import Signal
from anon.quant import (
    Journal,
    TradeRecord,
    auto_live_allowed,
    compute_stats,
    format_report,
    realized_r,
)
from anon.risk import RiskDecision
from anon.zen import Zen
from tests.helpers import T0

SIG = Signal("A", "zone", "buy", T0, 83300, 82950, 85500, True, 6.0)


def test_two_consecutive_off_plan_locks_the_day_and_next_day_unlocks():
    z = Zen(ZenConfig())
    z.roll_day(date(2026, 9, 27))
    z.record_off_plan("manual #1")
    assert z.check(SIG, RiskDecision(), True).ok
    z.record_off_plan("manual #2")
    res = z.check(SIG, RiskDecision(), True)
    assert not res.ok and "day_locked_after_off_plan" in res.failed
    z.roll_day(date(2026, 9, 28))
    assert z.check(SIG, RiskDecision(), True).ok


def test_plan_trade_resets_off_plan_streak():
    z = Zen(ZenConfig())
    z.roll_day(date(2026, 9, 27))
    z.record_off_plan("x")
    z.record_plan_trade(1.0)
    z.record_off_plan("y")
    assert not z.state.locked


def test_cooldown_after_loss_then_clears():
    z = Zen(ZenConfig(cooldown_bars_after_loss=2))
    z.record_plan_trade(-1.0)
    assert not z.check(SIG, RiskDecision(), True).ok
    z.on_bar()
    assert not z.check(SIG, RiskDecision(), True).ok
    z.on_bar()
    assert z.check(SIG, RiskDecision(), True).ok


def test_checklist_items_1_2_3():
    z = Zen(ZenConfig())
    inside = Signal("A", "zone", "buy", T0, 84700, 84000, 85500, False, 1.1)
    res = z.check(inside, RiskDecision(vetoes=["no_numeric_stop"]), journal_ready=False)
    assert {f.split(":")[0] for f in res.failed} == {"1_not_outside_gray", "2_lot_or_stop", "3_journal_not_ready"}


def rec(i, r, on_plan=True, regime="range", prefix="#T"):
    return TradeRecord(
        id=f"{prefix}{i:02d}", ticket=i, setup="A", variant="zone", regime=regime,
        entry_plan=83300, stop=82950, tp1=85500, lot=0.01, on_plan=on_plan,
        opened_at=T0.isoformat(), status="closed", r=r, minutes=60,
    )


def test_realized_r():
    assert realized_r("buy", 83500, 83000, 85500) == pytest.approx(4.0)
    assert realized_r("buy", 83500, 83000, 83000) == pytest.approx(-1.0)
    assert realized_r("sell", 84000, 84500, 83000) == pytest.approx(2.0)


def test_stats_ignore_off_plan_but_kpi_counts_it():
    records = [rec(1, 2.0), rec(2, -1.0), rec(3, -1.0, on_plan=False, prefix="OFF-")]
    s = compute_stats(records)
    assert s.n == 2
    assert s.expectancy_r == pytest.approx(0.5)
    assert s.pct_on_plan == pytest.approx(200 / 3)
    assert s.profit_factor == pytest.approx(2.0)


def test_n_zero_report_refuses_to_conclude():
    assert "n=0" in format_report(compute_stats([]), [])


def test_auto_live_gate_requires_evidence():
    few = compute_stats([rec(i, 1.0) for i in range(1, 6)])
    assert auto_live_allowed(few, 30, 0.9)[0] is False
    losing = compute_stats([rec(i, -0.5 if i % 2 else 0.4) for i in range(1, 41)])
    assert auto_live_allowed(losing, 30, 0.9)[0] is False
    good = compute_stats([rec(i, 2.0 if i % 2 else -1.0) for i in range(1, 41)])
    assert auto_live_allowed(good, 30, 0.9)[0] is True


def test_journal_roundtrip_and_ids(tmp_path):
    path = tmp_path / "j.jsonl"
    j = Journal(path)
    assert j.ready() and j.next_id() == "#T01"
    r = rec(1, None)
    r.status = "open"
    j.save(r)
    r.status, r.r = "closed", 1.5
    j.save(r)
    again = Journal(path)
    assert again.records["#T01"].status == "closed" and again.records["#T01"].r == 1.5
    assert again.next_id() == "#T02"
