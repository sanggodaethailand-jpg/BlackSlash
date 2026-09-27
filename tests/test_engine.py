from dataclasses import replace

import pytest

from anon.approval import ManualApprover
from anon.backtest import run_backtest
from anon.broker.paper import PaperBroker
from anon.config import Config, ExecutionConfig, OmegaConfig
from anon.engine import Engine
from anon.models import SymbolSpec
from anon.quant import Journal
from tests.helpers import flat, then

TAG_ONLY = replace(Config(), omega=OmegaConfig(unfavorable_action="tag"))
DIP = (83900, 83950, 83300, 83420)  # touches the A zone, closes inside it


def kinds(events):
    return [e.kind for e in events]


def test_a_trade_hits_tp1_and_is_journaled_with_real_r():
    bars = then(flat(84000, 60), [DIP, (83420, 84200, 83400, 84100), (84100, 85600, 84050, 85550)])
    res = run_backtest(TAG_ONLY, bars)
    rec = res.journal.records["#T01"]
    fill = 83420 + TAG_ONLY.execution.spread
    assert rec.status == "closed" and rec.exit_reason == "tp" and rec.on_plan
    assert rec.entry_fill == pytest.approx(fill)
    assert rec.r == pytest.approx((85500 - fill) / (fill - 82950))
    assert rec.pl == pytest.approx((85500 - fill) * 0.01)
    assert rec.pl_thb is None  # no rate supplied → no baht invented
    assert res.stats.n == 1


def test_thesis_exit_on_h1_close_below_zone():
    bars = then(flat(84000, 60), [DIP, (83420, 83450, 82960, 82990), (82990, 83100, 82970, 83050)])
    res = run_backtest(TAG_ONLY, bars)
    rec = res.journal.records["#T01"]
    assert rec.exit_reason == "thesis"
    assert -1.0 < rec.r < 0  # out before the hard stop
    assert "thesis_exit" in kinds(res.events)


def test_hard_stop_counts_as_minus_one_r_and_triggers_cooldown():
    bars = then(flat(84000, 60), [DIP, (83420, 83450, 82900, 83100)])
    res = run_backtest(TAG_ONLY, bars)
    rec = res.journal.records["#T01"]
    assert rec.exit_reason == "sl" and rec.r == pytest.approx(-1.0)
    assert res.engine.zen.state.bars_since_loss == 0


def test_small_account_is_vetoed_by_risk():
    cfg = replace(TAG_ONLY, execution=ExecutionConfig(starting_balance=500))
    res = run_backtest(cfg, then(flat(84000, 60), [DIP]))
    assert "risk_veto" in kinds(res.events)
    assert not res.journal.records


def test_unfavorable_omega_vetoes_by_default():
    res = run_backtest(Config(), then(flat(84600, 60), [(84500, 84520, 83300, 83420)]))
    assert "omega" in kinds(res.events)
    assert not res.journal.records


def make_engine(cfg=TAG_ONLY, approver=None):
    ex = cfg.execution
    broker = PaperBroker(SymbolSpec(ex.contract_size, ex.volume_min, ex.volume_step), ex.starting_balance, ex.spread)
    from anon.approval import AutoApprover

    return Engine(cfg, broker, approver or AutoApprover(), Journal(None)), broker


def feed(engine, broker, bars, start=0):
    for i in range(start, len(bars)):
        broker.process_bar(bars[i])
        engine.on_bar_close(bars[: i + 1])


def test_foreign_position_at_start_is_counted_by_risk_not_quant():
    engine, broker = make_engine()
    broker.add_foreign_position(208190412, "sell", 0.01, 84000)
    bars = then(flat(84000, 60), [DIP])
    feed(engine, broker, bars)
    assert "foreign_open" in kinds(engine.events)
    vetoes = [e.detail for e in engine.events if e.kind == "risk_veto"]
    assert vetoes and "veto_ticket_open_#208190412" in vetoes[0]
    assert not engine.journal.records
    assert not engine.zen.state.locked


def test_two_manual_trades_after_start_lock_the_day():
    engine, broker = make_engine()
    bars = then(flat(84000, 60), [DIP])
    feed(engine, broker, bars[:58])
    broker.add_foreign_position(5001, "buy", 0.05, 84000)
    feed(engine, broker, bars[:59], start=58)
    broker.add_foreign_position(5002, "buy", 0.05, 84000)
    feed(engine, broker, bars, start=59)
    assert engine.zen.state.locked
    assert {"OFF-5001", "OFF-5002"} <= set(engine.journal.records)
    assert kinds(engine.events).count("off_plan") == 2


def test_widened_stop_is_off_plan_and_restored():
    engine, broker = make_engine()
    bars = then(flat(84000, 60), [DIP, (83420, 83600, 83400, 83500)])
    feed(engine, broker, bars[:61])
    ticket = engine.journal.records["#T01"].ticket
    feed(engine, broker, bars, start=61)
    broker.modify_sl(ticket, 82000)  # someone drags the stop down
    bars = then(bars, [(83500, 83600, 83450, 83550)])
    feed(engine, broker, bars, start=62)
    rec = engine.journal.records["#T01"]
    assert rec.on_plan is False
    assert broker.positions()[0].sl == rec.stop


def test_manual_approval_requires_exact_phrase():
    said = []
    engine, broker = make_engine(approver=ManualApprover(lambda _: "ok", said.append))
    feed(engine, broker, then(flat(84000, 60), [DIP]))
    assert "not_approved" in kinds(engine.events) and not engine.journal.records

    engine, broker = make_engine(approver=ManualApprover(lambda _: "อนุมัติ #T01", said.append))
    feed(engine, broker, then(flat(84000, 60), [DIP]))
    assert "order" in kinds(engine.events)
    assert said and said[-1].startswith("#T01 | A/zone")


def test_state_roundtrip():
    engine, broker = make_engine()
    feed(engine, broker, flat(84000, 5))
    state = engine.to_state()
    other, _ = make_engine()
    other.load_state(state)
    assert other.to_state() == state


def test_price_moving_during_approval_cancels_the_order():
    engine, broker = make_engine()

    def slow_boss(prompt):
        broker._bid = 85400.0  # price ran toward TP1 while waiting
        return "อนุมัติ #T01"

    engine.approver = ManualApprover(slow_boss, lambda _: None)
    feed(engine, broker, then(flat(84000, 60), [DIP]))
    assert "stale_after_approval" in kinds(engine.events)
    assert not engine.journal.records


def test_paper_pnl_uses_the_broker_money_per_point():
    from anon.models import Bar

    cent = SymbolSpec(1.0, 0.01, 0.01, value_per_point=100.0)
    broker = PaperBroker(cent, 298_428.0, 0.0, "USC")
    bars = flat(84000, 1)
    broker.process_bar(bars[0])
    broker.open_market("buy", 0.01, 83000, 85000, 1, "x")
    t = bars[0].close_time
    broker.process_bar(Bar(t, 84000, 85100, 83900, 85050))  # fills at 84000, hits TP 85000
    (closed,) = broker.closed_since(t)
    assert closed.profit == pytest.approx(1000 * 0.01 * 100)  # 1000 points = 1,000 USC = $10
    assert broker.account().currency == "USC"
