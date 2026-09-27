"""Owner-only real-money launch: nothing changes unless the owner confirms, and the live
config always comes out with the exact symbol, manual approval and its own journal."""

import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from anon import cli
from anon.config import Config, ExecutionConfig, load_config
from anon.golive import CONFIRM_PHRASE, LIVE_JOURNAL, set_toml_keys
from tests.test_cli_mt5 import CentFakeMT5

BASE = """[levels]
a_zone_bot = 83000.0   # A

[execution]
mode = "live"
symbol = "BTCUSD"              # comment kept? no: the line is rewritten
dry_run = true
confirmed_by = ""
approval = "auto"

[auto]
enabled = false
"""

OTHER_SECTION = """[execution]
dry_run = true

[other]
dry_run = true
"""


class SuffixCentMT5(CentFakeMT5):
    def __init__(self, algo_trading=True):
        super().__init__()
        self.algo_trading = algo_trading

    def symbol_select(self, symbol, enable):
        return symbol == "BTCUSDc"

    def symbols_get(self, group):
        return tuple(SimpleNamespace(name=n) for n in ("BTCUSDc", "BTCUSDTc", "ETHBTCc"))

    def terminal_info(self):
        return SimpleNamespace(trade_allowed=self.algo_trading)


@pytest.fixture
def broker(monkeypatch):
    mod = SuffixCentMT5()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mod)
    return mod


def answers(*replies):
    queue = list(replies)
    return lambda prompt: queue.pop(0)


def test_set_toml_keys_only_touches_the_section():
    out = set_toml_keys(BASE, "execution", {"symbol": '"BTCUSDc"', "dry_run": "false", "state_path": '"s.json"'})
    cfg_lines = out.splitlines()
    assert 'symbol = "BTCUSDc"  # anon golive' in cfg_lines and "dry_run = false  # anon golive" in cfg_lines
    assert "enabled = false" in cfg_lines and "a_zone_bot = 83000.0   # A" in cfg_lines
    other = set_toml_keys(OTHER_SECTION, "execution", {"dry_run": "false"})
    assert other.split("[other]")[1].strip() == "dry_run = true"
    execution = out.split("[execution]")[1].split("[auto]")[0]
    assert 'state_path = "s.json"  # anon golive' in execution  # missing key added inside the section
    assert set_toml_keys("", "execution", {"dry_run": "true"}).strip().endswith("dry_run = true  # anon golive")


def test_owner_confirms_then_live_config_doctor_and_loop(broker, tmp_path, monkeypatch, capsys):
    base = tmp_path / "anon.toml"
    base.write_text(BASE, encoding="utf-8")
    started = {}
    monkeypatch.setattr(cli, "cmd_live", lambda cfg, confirm_live, poll_s: started.update(cfg=cfg, confirm=confirm_live))
    cli.cmd_golive(load_config(base), str(base), 1.0, input_fn=answers("บอส", CONFIRM_PHRASE))
    live = load_config(tmp_path / "anon.live.toml")
    ex = live.execution
    assert (ex.symbol, ex.dry_run, ex.confirmed_by) == ("BTCUSDc", False, "บอส")
    assert (ex.approval, ex.journal_path) == ("manual", LIVE_JOURNAL)
    assert live.levels.a_zone_bot == 83000.0 and base.read_text(encoding="utf-8") == BASE  # dry-run config untouched
    assert started["confirm"] is True and started["cfg"].execution.dry_run is False
    out = capsys.readouterr().out
    assert "REAL" in out and "Algo Trading ใน MT5 เปิดอยู่" in out and "บัญชีจริง + dry_run=false" in out


@pytest.mark.parametrize("replies", [("บอส", "ok"), ("", CONFIRM_PHRASE), ("บอส", "เงินจริง!")])
def test_anything_but_the_exact_confirmation_changes_nothing(broker, tmp_path, monkeypatch, replies):
    monkeypatch.setattr(cli, "cmd_live", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit):
        cli.cmd_golive(Config(), str(tmp_path / "anon.toml"), 1.0, input_fn=answers(*replies))
    assert not (tmp_path / "anon.live.toml").exists()


def test_algo_trading_off_stops_before_any_order(broker, tmp_path, monkeypatch, capsys):
    broker.algo_trading = False
    monkeypatch.setattr(cli, "cmd_live", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit, match="XX"):
        cli.cmd_golive(Config(), str(tmp_path / "anon.toml"), 1.0, input_fn=answers("บอส", CONFIRM_PHRASE))
    assert "Algo Trading ใน MT5 ปิดอยู่" in capsys.readouterr().out


def test_live_loop_refuses_real_orders_with_algo_trading_off(broker):
    broker.algo_trading = False
    cfg = replace(Config(), execution=replace(ExecutionConfig(), symbol="BTCUSDc", dry_run=False, confirmed_by="บอส"))
    with pytest.raises(SystemExit, match="Algo Trading"):
        cli.cmd_live(cfg, confirm_live=True, poll_s=0)


def test_doctor_in_dry_run_only_notes_algo_trading(broker, capsys):
    broker.algo_trading = False
    assert cli.cmd_doctor(Config()) == 0
    assert "Algo Trading ใน MT5 ปิดอยู่ (dry-run ไม่ต้องใช้" in capsys.readouterr().out
