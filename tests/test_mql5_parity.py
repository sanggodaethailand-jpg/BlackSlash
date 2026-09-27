"""The MT5 indicator must show the same numbers the engine trades on."""

import re
from pathlib import Path

import pytest

from anon.config import Config

MQ5 = Path(__file__).resolve().parents[1] / "mql5" / "Indicators" / "ANON_Levels_v2.mq5"

CFG = Config()
EXPECTED = {
    "InpAZoneBot": CFG.levels.a_zone_bot,
    "InpAZoneTop": CFG.levels.a_zone_top,
    "InpBInvRef": CFG.levels.b_inv_ref,
    "InpGrayCenter": CFG.levels.gray_center,
    "InpGrayHalf": CFG.levels.gray_half_width,
    "InpTP1": CFG.levels.tp1,
    "InpLiqTop": CFG.levels.liq_top,
    "InpStopBuffer": CFG.ghost.stop_buffer,
    "InpSwingLookback": CFG.ghost.swing_lookback,
    "InpBreakoutExpiry": CFG.ghost.breakout_expiry_bars,
    "InpRetestTol": CFG.ghost.retest_tolerance,
    "InpMinRR": CFG.ghost.min_rr,
    "InpLot": CFG.risk.lot,
    "InpMaxLot": CFG.risk.max_lot,
    "InpRiskPct": CFG.risk.risk_per_trade_pct,
    "InpDailyPct": CFG.risk.daily_loss_pct,
    "InpPeakWarnPct": CFG.risk.peak_dd_warn_pct,
    "InpVetoTicket": CFG.risk.veto_tickets[0],
    "InpBotMagic": CFG.execution.magic,
    "InpVetoUnmanaged": CFG.risk.veto_if_unmanaged_open,
    "InpDayUtcOffset": CFG.execution.day_utc_offset_hours,
    "InpEmaFast": CFG.omega.ema_fast,
    "InpEmaSlow": CFG.omega.ema_slow,
    "InpAtrPeriod": CFG.omega.atr_period,
    "InpShockMult": CFG.omega.shock_atr_mult,
    "InpOmegaVeto": CFG.omega.unfavorable_action == "veto",
}


def inputs() -> dict[str, float | bool]:
    text = MQ5.read_text(encoding="utf-8-sig")
    found = {}
    for typ, name, value in re.findall(r"^input\s+(double|int|long|bool)\s+(\w+)\s*=\s*([^;]+);", text, re.M):
        value = value.strip()
        found[name] = value == "true" if typ == "bool" else float(value)
    return found


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_indicator_default_matches_engine_config(name):
    assert inputs()[name] == EXPECTED[name]


def test_window_matches_engine():
    text = MQ5.read_text(encoding="utf-8-sig")
    assert "#define WINDOW_BARS 300" in text  # engine passes 300 closed bars (cli.cmd_live)


def test_indicator_never_trades():
    text = MQ5.read_text(encoding="utf-8-sig")
    for fn in ("OrderSend", "OrderSendAsync", "OrderCheck", "PositionClose", "CTrade"):
        assert fn not in text


def test_saved_with_bom_for_metaeditor():
    assert MQ5.read_bytes()[:3] == b"\xef\xbb\xbf"
