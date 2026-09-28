"""The live loop survives a dropped link to MT5 (IPC send failed) and never carries on
with another account."""

import json
import sys
from dataclasses import replace

import pytest

from anon import cli
from anon.broker.mt5 import AccountChanged, MT5Broker
from anon.config import Config, ExecutionConfig
from tests.test_golive import SuffixCentMT5

IPC = (-10001, "IPC send failed")


class FlakyMT5(SuffixCentMT5):
    """Loses the terminal link on the first ``drops`` bar requests; ``login_after`` fakes
    the terminal coming back logged into another account."""

    def __init__(self, drops=2, login_after=None, algo_trading=True):
        super().__init__(algo_trading)
        self.drops, self.login_after, self.inits, self.error = drops, login_after, 0, (0, "ok")
        self.sent = []

    def initialize(self, *args, **kwargs):
        self.inits += 1
        return super().initialize(*args, **kwargs)

    def last_error(self):
        return self.error

    def copy_rates_from_pos(self, symbol, tf, start, count):
        if self.drops:
            self.drops -= 1
            self.error = IPC
            return None
        return super().copy_rates_from_pos(symbol, tf, start, count)

    def account_info(self):
        info = super().account_info()
        if self.login_after is not None and self.inits > 1:
            info.login = self.login_after
        return info

    def order_send(self, request):
        self.sent.append(request)
        raise AssertionError("no order may be sent in these tests")


def run_loop(monkeypatch, tmp_path, mod, polls, dry_run=True):
    """Run cmd_live until ``polls`` sleeps have happened; returns every requested wait."""
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) >= polls:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)
    ex = replace(ExecutionConfig(), symbol="BTCUSDc", dry_run=dry_run, confirmed_by="บอส" if not dry_run else "",
                 journal_path=str(tmp_path / "j.jsonl"), state_path=str(tmp_path / "s.json"), chart_feed="")
    cli.cmd_live(replace(Config(), execution=ex), confirm_live=not dry_run, poll_s=15)
    return waits


def test_dropped_link_reconnects_with_backoff_and_keeps_working(monkeypatch, tmp_path, capsys):
    mod = FlakyMT5(drops=2)
    waits = run_loop(monkeypatch, tmp_path, mod, polls=3)
    out = capsys.readouterr().out
    assert waits == [15, 30, 15]  # two reconnect waits (doubling), then the normal poll
    assert mod.inits == 3 and out.count("MT5 หลุด") == 2 and out.count("ต่อ MT5 ได้แล้ว (BTCUSDc)") == 2
    assert "IPC send failed" in out and "SL/TP ที่โบรกเกอร์" in out and "stopped" in out
    state = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert state  # the bar after the reconnect went through the engine and saved its state


def test_wait_is_capped(monkeypatch, tmp_path):
    waits = run_loop(monkeypatch, tmp_path, FlakyMT5(drops=10), polls=9)
    assert waits == [15, 30, 60, 120, 120, 120, 120, 120, 120]


def test_another_account_after_reconnect_stops_before_any_order(monkeypatch, tmp_path, capsys):
    mod = FlakyMT5(drops=1, login_after=5550123)
    with pytest.raises(SystemExit, match="เปลี่ยนบัญชี.*257501588.*5550123"):
        run_loop(monkeypatch, tmp_path, mod, polls=5, dry_run=False)
    assert mod.sent == [] and mod.initialized is False


def test_algo_trading_off_after_reconnect_is_flagged(monkeypatch, tmp_path, capsys):
    mod = FlakyMT5(drops=1)

    def switch_off(*args, **kwargs):  # MT5 restarted with the Algo Trading button off
        mod.inits += 1
        mod.algo_trading = mod.inits == 1
        mod.initialized = True
        return True

    mod.initialize = switch_off
    run_loop(monkeypatch, tmp_path, mod, polls=2, dry_run=False)
    assert "Algo Trading ใน MT5 ปิดอยู่หลังต่อใหม่" in capsys.readouterr().out


def test_broker_reconnect_keeps_symbol_and_clock(monkeypatch):
    mod = FlakyMT5(drops=0)
    broker = MT5Broker(replace(ExecutionConfig(), dry_run=True), mod)  # BTCUSD → BTCUSDc alias
    broker.connect()
    symbol, offset = broker.symbol, broker.offset
    broker.reconnect()
    assert (broker.symbol, broker.offset, broker.identity) == (symbol, offset, (257501588, "Exness-MT5Real36"))
    mod.login_after = 1
    with pytest.raises(AccountChanged):
        broker.reconnect()
