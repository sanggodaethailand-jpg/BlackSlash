"""Idea Lab: fills and costs, the lookahead guard, the ledger and the five gates."""

import random
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from anon import cli
from anon.config import Config, LabConfig
from anon.lab.core import EXIT, Costs, Entry, Idea, grid_cells, lookahead_violations, simulate, validate_idea
from anon.lab.frames import resample
from anon.lab.gauntlet import format_report, null_trials_for, run_gauntlet
from anon.lab.ideas import load_ideas
from anon.lab.ideas._template import IDEA as TEMPLATE
from anon.lab.ledger import Ledger, threshold
from anon.synthetic import waypoint_bars
from tests.helpers import bars_from

NO_COST = Costs(spread=0.0)
HYPOTHESIS = "Test idea: exercises the machinery only, says nothing about any market."


class Scripted(Idea):
    """Enters on the bars listed in ``p['at']`` with fixed offsets."""

    name, family, hypothesis = "scripted", "test", HYPOTHESIS
    primary = {"side": "buy"}
    grid = {"side": ["buy"]}

    def __init__(self, at, stop_off=100.0, target_off=None, max_hold=None, trail=None):
        self.at, self.stop_off, self.target_off, self.max_hold, self.trail = set(at), stop_off, target_off, max_hold, trail

    def entry(self, i, bars, state, p):
        if i not in self.at:
            return None
        c, sign = bars[i].close, (1 if p["side"] == "buy" else -1)
        target = None if self.target_off is None else c + sign * self.target_off
        return Entry(p["side"], c - sign * self.stop_off, target, self.max_hold)

    def update(self, i, bars, state, p, trade):
        return None if self.trail is None else self.trail(i, bars, trade)


def row(price, n, start=datetime(2026, 1, 1, tzinfo=UTC)):
    return bars_from([(price, price + 10, price - 10, price)] * n, start)


# --- fills and costs ----------------------------------------------------------------

def test_buy_fills_next_open_at_ask_and_stop_is_minus_one_r():
    bars = row(1000, 3) + bars_from([(1000, 1010, 880, 900)], row(1000, 4)[3].time)
    t = simulate(Scripted([1]), {"side": "buy"}, bars, Costs(spread=10.0))[0]
    assert (t.entry_index, t.fill, t.initial_stop) == (2, 1010.0, 900.0)
    assert t.reason == "stop" and t.r == pytest.approx(-1.0)  # spread sits inside the risk


def test_target_and_stop_in_one_bar_counts_as_stop():
    bars = row(1000, 3) + bars_from([(1000, 1300, 800, 1000)], row(1000, 4)[3].time)
    t = simulate(Scripted([1], target_off=150), {"side": "buy"}, bars, NO_COST)[0]
    assert t.reason == "stop" and t.r == pytest.approx(-1.0)


def test_gap_through_the_stop_fills_at_the_open():
    bars = row(1000, 3) + bars_from([(850, 860, 840, 850)], row(1000, 4)[3].time)
    t = simulate(Scripted([1]), {"side": "buy"}, bars, NO_COST)[0]
    assert t.exit_price == 850 and t.r == pytest.approx(-1.5)


def test_sell_exits_at_ask():
    bars = row(1000, 3) + bars_from([(1000, 1005, 845, 850)], row(1000, 4)[3].time)
    t = simulate(Scripted([1], target_off=150), {"side": "sell"}, bars, Costs(spread=5.0))[0]
    # filled at bid 1000, target 850 is hit when ask (low + spread = 850) touches it
    assert (t.fill, t.reason, t.exit_price) == (1000.0, "target", 850.0)
    assert t.r == pytest.approx(1.5)


def test_invalid_entry_after_a_gap_is_skipped():
    bars = row(1000, 2) + bars_from([(800, 810, 790, 800)], row(1000, 3)[2].time)
    assert simulate(Scripted([1]), {"side": "buy"}, bars, NO_COST) == []  # would fill under its own stop


def test_swap_is_charged_per_midnight_held():
    start = datetime(2026, 1, 1, 20, tzinfo=UTC)
    bars = row(1000, 60, start)  # 20:00 day 1 → 07:00 day 4: three midnights after the fill at 22:00
    costs = Costs(spread=0.0, swap_long=-500, point=0.01)  # -5 price units per night
    t = simulate(Scripted([1], stop_off=100), {"side": "buy"}, bars, costs)[0]
    assert t.reason == "end" and t.nights == 3
    assert t.r == pytest.approx(-0.15)
    gap = row(1000, 3, start) + row(1000, 3, datetime(2026, 1, 4, 5, tzinfo=UTC))  # missing hours over 3 midnights
    assert simulate(Scripted([0]), {"side": "buy"}, gap, costs)[0].nights == 3
    percent = Costs(spread=0.0, swap_mode="percent", swap_long=-36.5)
    assert percent.swap_per_night("buy", 1000) == pytest.approx(-1.0)


def test_time_exit_and_trailing_stop():
    bars = row(1000, 12)
    t = simulate(Scripted([1], max_hold=3), {"side": "buy"}, bars, NO_COST)[0]
    assert (t.entry_index, t.exit_index, t.reason) == (2, 5, "time")
    trail = Scripted([1], trail=lambda i, b, tr: 995.0 if i == 3 else 850.0)  # later loosening is ignored
    t = simulate(trail, {"side": "buy"}, row(1000, 5) + bars_from([(1000, 1000, 990, 995)], row(1000, 6)[5].time),
                 NO_COST)[0]
    assert (t.stop, t.reason, t.exit_price) == (995.0, "stop", 995.0)
    leave = Scripted([1], trail=lambda i, b, tr: EXIT)
    assert simulate(leave, {"side": "buy"}, bars, NO_COST)[0].exit_index == 3


# --- higher timeframes ---------------------------------------------------------------

def test_daily_bars_are_visible_only_after_they_close():
    bars = row(1000, 50, datetime(2026, 1, 1, 0, tzinfo=UTC))
    days, last_closed = resample(bars, 24)
    assert len(days) == 3 and days[0].time.hour == 0
    assert last_closed[22] == -1 and last_closed[23] == 0  # 23:00 bar closes the day
    assert last_closed[47] == 1 and last_closed[49] == 1  # day 3 still forming
    with pytest.raises(ValueError):
        resample(bars, 5)


# --- the lookahead guard -------------------------------------------------------------

class PeeksAhead(Idea):
    name, family, hypothesis = "peeks", "test", HYPOTHESIS
    primary = {"x": 1}
    grid = {"x": [1]}

    def entry(self, i, bars, state, p):
        if bars[i + 1].close > bars[i].close:  # the next bar's close: cheating
            return Entry("buy", bars[i].close - 200)
        return None


class CenteredAverage(Idea):
    name, family, hypothesis = "centered", "test", HYPOTHESIS
    primary = {"x": 1}
    grid = {"x": [1]}

    def prepare(self, bars, p):
        closes = [b.close for b in bars]
        return [sum(closes[max(0, j - 5): j + 6]) / len(closes[max(0, j - 5): j + 6]) for j in range(len(closes))]

    def entry(self, i, bars, state, p):
        return Entry("buy", bars[i].close - 200) if bars[i].close < state[i] else None


def wiggly(n=3000, seed=2):
    rng = random.Random(seed)
    return waypoint_bars([20000 + rng.uniform(-900, 900) for _ in range(n // 6 + 1)], bars_per_leg=6, noise=60, seed=seed)[:n]


@pytest.mark.parametrize("cheat", [PeeksAhead(), CenteredAverage()])
def test_lookahead_is_caught(cheat):
    bars = wiggly()
    assert lookahead_violations(cheat, cheat.primary, bars)


def test_ideas_only_see_the_past():
    seen = []

    class Looks(Scripted):
        def entry(self, i, bars, state, p):
            seen.append((i, len(bars), bars[-1].time == bars[i].time, len(bars[i - 3 : i + 50]) if i >= 3 else None))
            return None

    bars = row(1000, 8)
    simulate(Looks([]), {"side": "buy"}, bars, NO_COST)
    assert seen[3] == (3, 4, True, 4) and len(seen) == 7  # no decision on the last bar: nothing left to fill it


def registered_and_template():
    return [*load_ideas().values(), TEMPLATE]


@pytest.mark.parametrize("idea", registered_and_template(), ids=lambda i: i.name)
def test_every_idea_is_valid_and_never_looks_ahead(idea):
    validate_idea(idea)
    rng = random.Random(11)
    path = [20000.0]
    for _ in range(700):
        path.append(path[-1] * (1 + rng.gauss(0, 0.03)))
    bars = waypoint_bars(path, bars_per_leg=24, noise=150, seed=3)[:16000]
    cells = grid_cells(idea)
    for params in {str(c): c for c in (idea.primary, cells[0], cells[-1])}.values():  # the corners and the primary
        assert lookahead_violations(idea, params, bars) == [], (idea.name, params)


def test_idea_rules_are_enforced():
    class Wide(Scripted):
        grid = {"side": ["buy", "sell"], "a": list(range(5))}
        primary = {"side": "buy", "a": 0}

    with pytest.raises(ValueError, match="10 cells"):
        validate_idea(Wide([]))

    class OffGrid(Scripted):
        primary = {"side": "sell"}

    with pytest.raises(ValueError, match="primary"):
        validate_idea(OffGrid([]))

    class Vague(Scripted):
        hypothesis = "it goes up"

    with pytest.raises(ValueError, match="hypothesis"):
        validate_idea(Vague([]))


# --- ledger and gates ----------------------------------------------------------------

def test_ledger_counts_every_distinct_attempt(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger.open(path)
    assert ledger.register("a", "h1", {}, {}, {}) == 1
    assert ledger.register("a", "h1", {}, {}, {}) == 1  # re-running the same code is not a new attempt
    assert ledger.register("a", "h2", {}, {}, {}) == 2  # editing it is
    assert ledger.record_holdout("a", "h2", {}) == 1 and ledger.record_holdout("a", "h3", {}) == 2
    assert Ledger.open(path).attempts() == [("a", "h1"), ("a", "h2")]
    assert threshold(1) == 0.05 and threshold(5) == pytest.approx(0.01)
    assert null_trials_for(0.05) == 200 and null_trials_for(0.05 / 6) == 240


def test_seeded_ledger_counts_the_anon_attempt():
    ledger = Ledger.open(LabConfig().ledger)
    assert ("anon_zones", "pre-lab") in ledger.attempts()


class Coin(Idea):
    """Random entries: should never survive the gates."""

    name, family, hypothesis = "coin", "test", HYPOTHESIS
    primary = {"seed": 1}
    grid = {"seed": [1, 2, 3]}

    def prepare(self, bars, p):
        rng = random.Random(p["seed"])
        return [rng.random() < 0.02 for _ in bars]

    def entry(self, i, bars, state, p):
        if state[i]:
            side = "buy" if i % 2 else "sell"
            c = bars[i].close
            return Entry(side, c - 300 if side == "buy" else c + 300, c + 450 if side == "buy" else c - 450)
        return None


def test_random_entries_do_not_pass_and_are_recorded(tmp_path):
    ledger = Ledger.open(tmp_path / "l.jsonl")
    ledger.register("anon_zones", "pre-lab", {}, {}, {})
    rep = run_gauntlet(Coin(), wiggly(12000, seed=5), Costs(spread=10.0), ledger, trials=10)
    assert not rep.passed and rep.k == 2 and rep.alpha == pytest.approx(0.025)
    text = format_report(rep)
    assert "ตกด่าน" in text and "swap = 0" in text and "◀ ค่าหลัก" in text
    assert ledger.results()[("coin", rep.digest)]["passed"] is False


def test_template_walks_all_gates_on_trending_synthetic_data(tmp_path):
    rng = random.Random(3)
    path = [10000.0]
    for _ in range(2100):
        path.append(max(3000, path[-1] * (1 + rng.gauss(0.001, 0.035))))
    bars = waypoint_bars(path, bars_per_leg=36, noise=40, seed=4)[:75000]
    ledger = Ledger.open(tmp_path / "l.jsonl")
    messages = []
    rep = run_gauntlet(TEMPLATE, bars, Costs(spread=10.0, swap_long=-100), ledger, trials=10, progress=messages.append)
    assert rep.null is not None and len(rep.null.shuffled_r) == 10 and messages[-1].endswith("10/10 ชุด")
    if rep.passed or rep.failed_gate == 5:
        assert rep.holdout is not None and ledger.holdout_openings(TEMPLATE.name) == 1
    assert "ด่าน 4" in format_report(rep)


# --- CLI -----------------------------------------------------------------------------

def write_csv(path, bars):
    lines = ["time,open,high,low,close"] + [f"{b.time.isoformat()},{b.open},{b.high},{b.low},{b.close}" for b in bars]
    path.write_text("\n".join(lines))


def test_cli_lab_smoke_records_nothing(tmp_path, capsys):
    csv_path = tmp_path / "bars.csv"
    write_csv(csv_path, wiggly(6000))
    cfg = replace(Config(), lab=replace(LabConfig(), ledger=str(tmp_path / "l.jsonl")))
    cli.cmd_lab(cfg, str(csv_path), [], 5, False, True)
    out = capsys.readouterr().out
    assert "ทดลองเครื่อง" in out and "template_breakout" in out
    assert not (tmp_path / "l.jsonl").exists()


def test_cli_lab_list_and_missing_ideas(tmp_path, capsys, monkeypatch):
    cfg = replace(Config(), lab=replace(LabConfig(), ledger=str(tmp_path / "l.jsonl")))
    Ledger.open(cfg.lab.ledger).register("anon_zones", "pre-lab", {}, {}, {})
    cli.cmd_lab(cfg, None, [], None, True, False)
    assert "k=1" in capsys.readouterr().out
    monkeypatch.setattr("anon.lab.ideas.load_ideas", lambda: {"coin": Coin()})
    with pytest.raises(SystemExit, match="nope"):
        cli.cmd_lab(cfg, "x.csv", ["nope"], None, False, False)
