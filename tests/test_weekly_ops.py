"""Weekly levels with an expiry, the levels.bat flow, and the forward watch."""

import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from anon import cli
from anon.backtest import run_backtest
from anon.config import Config, LevelsConfig, OmegaConfig, load_config
from anon.lab.core import Costs, Entry, Idea
from anon.lab.forward import MIN_TRADES, format_forward, register_watch, run_forward
from anon.lab.ledger import Ledger
from anon.levelset import SAVE_PHRASE, cmd_levels
from tests.helpers import bars_from, flat, then
from tests.test_engine import DIP

TAG_ONLY = replace(Config(), omega=OmegaConfig(unfavorable_action="tag"))
A_TRADE = then(flat(84000, 60), [DIP, (83420, 84200, 83400, 84100), (84100, 85600, 84050, 85550)])  # 27–30 Sep 2026


def with_until(until):
    return replace(TAG_ONLY, levels=replace(LevelsConfig(), valid_until=until))


# --- expiry in the engine ------------------------------------------------------------

def test_expired_levels_open_nothing_and_say_so_once_a_day():
    res = run_backtest(with_until("2026-09-26"), A_TRADE)
    assert "#T01" not in res.journal.records
    expired = [e for e in res.events if e.kind == "levels_expired"]
    assert 1 <= len(expired) <= 3 and "levels.bat" in expired[0].detail  # at most once per local day


def test_levels_still_valid_trade_as_before():
    assert "#T01" in run_backtest(with_until("2026-10-10"), A_TRADE).journal.records
    assert "#T01" in run_backtest(TAG_ONLY, A_TRADE).journal.records  # no expiry set


def test_bad_date_is_rejected():
    with pytest.raises(ValueError, match="valid_until"):
        with_until("next friday").validate()


# --- levels.bat ----------------------------------------------------------------------

def answers(*replies):
    queue = list(replies)
    return lambda prompt: queue.pop(0)


def test_levels_writes_this_weeks_lines_and_keeps_the_rest(tmp_path):
    base = tmp_path / "anon.toml"
    base.write_text('[levels]\na_zone_bot = 83000.0\n\n[execution]\ndry_run = true\nsymbol = "BTCUSDc"\n', encoding="utf-8")
    out = []
    reply = answers("84,000", "84500", "", "85700", "", "86500", "88000", "2026-10-04", SAVE_PHRASE)
    assert cmd_levels(load_config(base), str(base), reply, out.append, today=date(2026, 9, 28)) == base
    cfg = load_config(base)
    lv = cfg.levels
    assert (lv.a_zone_bot, lv.a_zone_top, lv.gray_center, lv.tp1, lv.liq_top) == (84000, 84500, 85700, 86500, 88000)
    assert lv.b_inv_ref == LevelsConfig().b_inv_ref and lv.valid_until == "2026-10-04"  # Enter keeps a value
    assert cfg.execution.symbol == "BTCUSDc" and cfg.execution.dry_run is True


@pytest.mark.parametrize(
    "replies",
    [
        ("86000", "", "", "", "", "", "", "", SAVE_PHRASE),  # A bottom above A top: fails the config checks
        ("", "", "", "", "", "", "", "", "no"),  # not confirmed
        ("abc",),  # not a number
        ("", "", "", "", "", "", "", "31/12/2026", SAVE_PHRASE),  # not YYYY-MM-DD
    ],
)
def test_levels_writes_nothing_unless_valid_and_confirmed(tmp_path, replies):
    base = tmp_path / "anon.toml"
    base.write_text("[levels]\na_zone_bot = 83000.0\n", encoding="utf-8")
    assert cmd_levels(Config(), str(base), answers(*replies), lambda _m: None, today=date(2026, 9, 28)) is None
    assert base.read_text(encoding="utf-8") == "[levels]\na_zone_bot = 83000.0\n"


def test_default_expiry_is_one_week(tmp_path):
    base = tmp_path / "anon.toml"
    cmd_levels(Config(), str(base), answers(*[""] * 8, SAVE_PHRASE), lambda _m: None, today=date(2026, 9, 28))
    assert load_config(base).levels.valid_until == "2026-10-04"


def test_doctor_blocks_live_on_expired_levels(monkeypatch, capsys):
    from tests.test_golive import SuffixCentMT5

    monkeypatch.setitem(__import__("sys").modules, "MetaTrader5", SuffixCentMT5())
    live = replace(with_until("2020-01-01"), execution=replace(Config().execution, symbol="BTCUSDc", dry_run=False,
                                                                confirmed_by="บอส"))
    assert cli.cmd_doctor(live) >= 1
    assert "หมดอายุแล้ว (2020-01-01)" in capsys.readouterr().out
    assert cli.cmd_doctor(with_until("2099-01-01")) == 0
    assert "ใช้ได้ถึง 2099-01-01" in capsys.readouterr().out


# --- forward watch -------------------------------------------------------------------

class Hourly(Idea):
    name, family = "hourly", "test"
    hypothesis = "Test idea: buys every 09:00 UTC close; exercises the forward watch only."
    primary = {"x": 1}
    grid = {"x": [1]}

    def entry(self, i, bars, state, p):
        return Entry("buy", stop_distance=100, target_r=1, max_hold=2) if bars[i].time.hour == 9 else None


def market(days=80, drift=40.0):
    rng = random.Random(1)
    out, price, t0 = [], 20000.0, datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(days * 24):
        t = t0 + timedelta(hours=i)
        move = drift if t.hour == 10 else rng.uniform(-30, 30)
        close = price + move
        out.append(bars_from([(price, max(price, close) + 5, min(price, close) - 5, close)], t)[0])
        price = close
    return out


def test_forward_counts_only_new_bars_and_decides_once(tmp_path):
    ledger = Ledger.open(tmp_path / "l.jsonl")
    idea = Hourly()
    since = "2026-10-01T00:00:00+00:00"
    register_watch(ledger, {"hourly": idea}, since, "test")
    bars = market()
    reports = run_forward(ledger, {"hourly": idea}, bars[: 24 * 45], Costs(spread=10.0))
    early = reports[0]
    assert 0 < len(early.pooled) < MIN_TRADES and early.verdict is None and "ยังห้ามสรุป" in format_forward(early)
    done = run_forward(ledger, {"hourly": idea}, bars, Costs(spread=10.0))[0]
    assert len(done.pooled) >= MIN_TRADES and done.verdict == "pass"
    again = run_forward(Ledger.open(tmp_path / "l.jsonl"), {"hourly": idea}, bars[:-24], Costs(spread=10.0))[0]
    assert again.verdict == "pass"  # the recorded decision stands; it is not re-tested on other data
    assert sum(1 for r in Ledger.open(tmp_path / "l.jsonl").rows if r["type"] == "watch_result") == 1


def test_forward_refuses_an_edited_idea(tmp_path):
    ledger = Ledger.open(tmp_path / "l.jsonl")
    register_watch(ledger, {"hourly": Hourly()}, "2026-10-01T00:00:00+00:00", "test")
    ledger.rows[-1]["ideas"]["hourly"] = "0000"  # as if the file changed after registration
    rep = run_forward(ledger, {"hourly": Hourly()}, market(), Costs(spread=10.0))[0]
    assert rep.changed == ["hourly"] and rep.verdict is None and "ห้ามแก้" in format_forward(rep)


def test_repo_ledger_watches_the_round_one_near_misses():
    ledger = Ledger.open("research/ledger.jsonl")
    watch = next(r for r in ledger.rows if r["type"] == "watch")
    assert set(watch["ideas"]) == {"donchian_d1", "tsmom_slow"} and watch["since"].startswith("2026-09-27")
    from anon.lab.core import idea_hash
    from anon.lab.ideas import load_ideas

    ideas = load_ideas()
    assert all(idea_hash(ideas[n]) == h for n, h in watch["ideas"].items())  # watched code is untouched


def test_cli_forward_without_watches(tmp_path):
    cfg = replace(Config(), lab=replace(Config().lab, ledger=str(tmp_path / "none.jsonl")))
    with pytest.raises(SystemExit, match="เฝ้าดู"):
        cli.cmd_lab(cfg, "x.csv", [], None, False, False, forward=True)


def test_feed_carries_the_expiry():
    from anon.chartfeed import render
    from tests.test_engine import make_engine

    engine, _ = make_engine(with_until("2020-01-01"))
    lines = render(engine, "BTCUSDc", False, False)
    assert "levels_until=2020-01-01" in lines and "levels_expired=1" in lines
