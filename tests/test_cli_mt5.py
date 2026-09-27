"""CLI commands that talk to MT5, run against a fake MetaTrader5 module."""

import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from anon import cli
from anon.broker.mt5 import MT5Broker
from anon.config import Config, ExecutionConfig
from anon.data import load_bars
from anon.synthetic import waypoint_bars

SERVER_OFFSET = 3 * 3600  # broker clock = UTC+3


class FakeMT5:
    TIMEFRAME_H1 = 16385
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, positions=()):
        self._positions = positions
        self.initialized = False

    def initialize(self, *args, **kwargs):
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    def last_error(self):
        return (0, "ok")

    def symbol_select(self, symbol, enable):
        return True

    def symbol_info(self, symbol):
        return SimpleNamespace(trade_contract_size=1.0, volume_min=0.01, volume_step=0.01, digits=2, filling_mode=2)

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=84600.0, ask=84618.0, time=int(time.time()) + SERVER_OFFSET)

    def account_info(self):
        return SimpleNamespace(login=5550123, server="Broker-Demo", trade_mode=0, currency="USD",
                               balance=1200.0, equity=1188.0)

    def positions_get(self, symbol=None):
        return self._positions

    def history_deals_get(self, *args, **kwargs):
        return ()

    def copy_rates_from_pos(self, symbol, tf, start, count):
        now_server = int(time.time()) + SERVER_OFFSET
        last_closed = now_server // 3600 * 3600 - 3600 * start
        path = [84000, 84900, 84200, 84800, 83300, 82950, 83400, 84650, 84950, 85200, 85600]
        bars = waypoint_bars(path * (count // 60 + 1), bars_per_leg=6, noise=40, seed=5)[-count:]
        first = last_closed - 3600 * (len(bars) - 1)
        return [
            {"time": first + 3600 * i, "open": b.open, "high": b.high, "low": b.low, "close": b.close}
            for i, b in enumerate(bars)
        ]


VETO_SELL = SimpleNamespace(ticket=208190412, type=1, volume=0.01, price_open=84000.0, sl=0.0, tp=0.0,
                            profit=-6.0, magic=0, comment="", time=int(time.time()))


@pytest.fixture
def fake(monkeypatch):
    mod = FakeMT5(positions=(VETO_SELL,))
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    return mod


def test_server_offset_is_detected_from_the_tick(fake):
    broker = MT5Broker(ExecutionConfig())
    broker.connect()
    assert broker.offset == timedelta(hours=3)


def test_doctor_reports_account_symbol_and_veto_ticket(fake, capsys):
    problems = cli.cmd_doctor(Config())
    out = capsys.readouterr().out
    assert problems == 0
    assert "5550123 @ Broker-Demo · DEMO" in out
    assert "contract size 1.0" in out and "UTC+3.0" in out
    assert "#208190412 sell 0.01 ยังเปิดอยู่" in out
    assert "gray_half_width" in out  # unconfirmed values are listed


def test_doctor_without_mt5_package(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)  # makes the import fail
    assert cli.cmd_doctor(Config()) == 1
    assert "ไม่มีแพ็กเกจ MetaTrader5" in capsys.readouterr().out


def test_export_writes_utc_bars_that_load_back(fake, tmp_path):
    out = tmp_path / "BTCUSD_H1.csv"
    cli.cmd_export(Config(), days=5, out=str(out))
    bars = load_bars(out)
    assert len(bars) == 5 * 24
    last_closed_utc = datetime.fromtimestamp(time.time() // 3600 * 3600 - 3600, tz=UTC)
    assert bars[-1].time in (last_closed_utc, last_closed_utc - timedelta(hours=1))  # hour may roll mid-test


def test_backtest_from_mt5_uses_broker_equity(fake, tmp_path, capsys):
    journal = tmp_path / "bt.jsonl"
    journal.write_text("stale\n")
    cli.cmd_backtest(Config(), None, 30, str(journal), False)
    out = capsys.readouterr().out
    assert "MT5 BTCUSD H1 30 วัน" in out and "1,188.00" in out
    assert "stale" not in journal.read_text()


def test_backtest_refuses_to_touch_the_live_journal(fake):
    with pytest.raises(SystemExit):
        cli.cmd_backtest(Config(), None, 5, ExecutionConfig().journal_path, False)


def test_real_orders_need_confirmed_by(fake):
    cfg = replace(Config(), execution=replace(ExecutionConfig(), dry_run=False))
    with pytest.raises(SystemExit, match="confirmed_by"):
        cli.cmd_live(cfg, confirm_live=True, poll_s=0)
    assert fake.initialized is False  # refused before touching the terminal


def test_math_reads_the_real_account(fake, capsys):
    cli.cmd_math(Config(), equity=1000, usdthb=None, use_mt5=True)
    out = capsys.readouterr().out
    assert "จาก MT5: equity 1,188.00 USD" in out and "จากโบรกเกอร์" in out


class CentFakeMT5(FakeMT5):
    def symbol_info(self, symbol):
        info = super().symbol_info(symbol)
        info.trade_tick_size, info.trade_tick_value_loss = 0.01, 1.0  # 100 USC per point per lot
        return info

    def account_info(self):
        return SimpleNamespace(login=257501588, server="Exness-MT5Real36", trade_mode=2, currency="USC",
                               balance=298428.0, equity=298428.0)


@pytest.fixture
def cent(monkeypatch):
    mod = CentFakeMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    return mod


def test_math_on_a_cent_account_uses_cents(cent, capsys):
    cli.cmd_math(Config(), equity=0, usdthb=33.42, use_mt5=True)
    out = capsys.readouterr().out
    assert "298,428.00 USC (บัญชีเซ็นต์" in out
    assert "lot 0.01 = 1 USC ต่อ 1 จุด" in out
    assert "A ขอบบน: เข้า 83,518 หยุด 82,950 → เสี่ยง 568.00 USC (0.19%)" in out
    assert "ต้องได้ 29,922 จุดต่อวัน" in out  # same point target as a USD account


def test_doctor_flags_real_and_cent_account(cent, capsys):
    assert cli.cmd_doctor(Config()) == 0
    out = capsys.readouterr().out
    assert "REAL" in out and "บัญชี REAL: dry-run ไม่ส่งออเดอร์" in out
    assert "บัญชีเซ็นต์ (USC)" in out


def test_backtest_auto_summary_with_rr_override(fake, capsys):
    cli.cmd_backtest(Config(), None, 60, None, False, auto=True, min_rr=1.5, summary=True)
    out = capsys.readouterr().out
    assert "ระดับอัตโนมัติ (ย้อน 72 แท่ง วาดใหม่ทุกวัน) · min RR 1.5" in out
    assert "#Txx | A/B" not in out  # summary: no per-trade table


def test_sweep_from_mt5_prints_the_grid(fake, capsys):
    cli.cmd_sweep(Config(), None, 60, rrs=[1.0, 1.5], lookbacks=[72], null_trials=2, target=(72, 1.5))
    out = capsys.readouterr().out
    assert "ลอง 2 แบบ" in out and "ช่อง" in out and "เทียบกับความบังเอิญ" in out
    assert out.count("|") > 6
