import math
from dataclasses import replace

import pytest

from anon.config import RiskConfig
from anon.models import Account, Position, Signal, SymbolSpec
from anon.risk import Risk, RiskContext, risk_money
from tests.helpers import T0

SPEC = SymbolSpec(contract_size=1.0, volume_min=0.01, volume_step=0.01)
MAGIC = 7700700


def sig(entry=83500.0, stop=83000.0, side="buy") -> Signal:
    return Signal("A", "zone", side, T0, entry, stop, 85500.0, True, 4.0)


def ctx(equity=1000.0, positions=(), day_start=1000.0, peak=1000.0) -> RiskContext:
    return RiskContext(Account(equity, equity), list(positions), SPEC, day_start, peak, MAGIC)


def test_handoff_example_passes_at_1000_usd():
    d = Risk(RiskConfig()).check(sig(), ctx())
    assert d.ok, d.vetoes
    assert d.risk_money == pytest.approx(5.0)
    assert d.risk_pct == pytest.approx(0.5)


def test_same_trade_vetoed_on_smaller_account():
    d = Risk(RiskConfig()).check(sig(), ctx(equity=800, day_start=800, peak=800))
    assert any(v.startswith("risk_") for v in d.vetoes)


def test_lot_1_is_vetoed():
    with pytest.raises(ValueError):
        from anon.config import Config

        replace(Config(), risk=RiskConfig(lot=1.0)).validate()
    d = Risk(RiskConfig(lot=1.0, max_lot=0.01)).check(sig(), ctx())
    assert any("above_max" in v for v in d.vetoes)


def test_missing_or_wrong_side_stop_is_vetoed():
    for bad in (math.nan, 83600.0):
        d = Risk(RiskConfig()).check(sig(stop=bad), ctx())
        assert "no_numeric_stop" in d.vetoes


def test_offplan_ticket_open_is_vetoed_and_counts_toward_daily_cap():
    ghost_ticket = Position(208190412, "sell", 0.01, 84000.0, None, None, -15.0, magic=0)
    # equity already down 16 from day start because of the off-plan sell
    d = Risk(RiskConfig()).check(sig(entry=83300, stop=82800), ctx(equity=984, positions=[ghost_ticket]))
    assert "veto_ticket_open_#208190412" in d.vetoes
    assert any(v.startswith("unmanaged_position_open") for v in d.vetoes)
    assert "opposite_position_open_no_hedge" in d.vetoes
    assert any(v.startswith("daily_cap_would_break") for v in d.vetoes)


def test_daily_cap_hit_blocks_everything():
    d = Risk(RiskConfig()).check(sig(entry=83100), ctx(equity=979, day_start=1000))
    assert any(v.startswith("daily_cap_hit") for v in d.vetoes)


def test_one_plan_position_at_a_time():
    mine = Position(1, "buy", 0.01, 83400.0, 82950.0, 85500.0, 0.0, MAGIC)
    d = Risk(RiskConfig(veto_if_unmanaged_open=True)).check(sig(entry=83100), ctx(positions=[mine]))
    assert "plan_position_already_open" in d.vetoes


def test_peak_drawdown_warns_without_veto():
    d = Risk(RiskConfig()).check(sig(entry=83100), ctx(equity=940, day_start=940, peak=1000))
    assert d.ok
    assert d.warnings and d.warnings[0].startswith("peak_drawdown_6.00")


def test_risk_money_formula():
    assert risk_money(83500, 83000, 0.01, 1.0) == pytest.approx(5.0)
    assert risk_money(83500, 83000, 1.0, 1.0) == pytest.approx(500.0)
