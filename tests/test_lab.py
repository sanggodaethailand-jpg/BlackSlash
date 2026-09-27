"""Idea Lab: fills and costs, the lookahead guard, the ledger and the five gates."""

import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from anon import cli
from anon.config import Config, LabConfig
from anon.lab.core import (
    EXIT,
    Costs,
    Entry,
    Idea,
    grid_cells,
    lookahead_violations,
    new_york_rollover,
    simulate,
    t_stat,
    validate_idea,
)
from anon.lab.frames import resample
from anon.lab.gauntlet import format_report, null_trials_for, run_gauntlet
from anon.lab.ideas import load_ideas
from anon.lab.ideas._template import IDEA as TEMPLATE
from anon.lab.ledger import Ledger, threshold
from anon.lab.nulls import fake_history
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
    costs = Costs(spread=0.0, swap_mode="points", swap_long=-500, point=0.01, swap_days=(1,) * 7, rollover="utc_midnight")
    t = simulate(Scripted([1], stop_off=100), {"side": "buy"}, bars, costs)[0]  # -5 price units per night
    assert t.reason == "end" and t.swap_days == 3
    assert t.r == pytest.approx(-0.15)
    gap = row(1000, 3, start) + row(1000, 3, datetime(2026, 1, 4, 5, tzinfo=UTC))  # missing hours over 3 midnights
    assert simulate(Scripted([0]), {"side": "buy"}, gap, costs)[0].swap_days == 3
    percent = Costs(spread=0.0, swap_mode="percent", swap_long=-36.5)
    assert percent.swap_per_day("buy", 1000) == pytest.approx(-1.0)


def test_rollover_is_17_new_york_with_friday_triple_and_free_weekend():
    costs = Costs(swap_long=-1.0)
    assert new_york_rollover(date(2026, 7, 1)).hour == 21 and new_york_rollover(date(2026, 1, 15)).hour == 22
    assert new_york_rollover(date(2026, 3, 8)).hour == 21 and new_york_rollover(date(2026, 3, 7)).hour == 22  # DST starts
    assert new_york_rollover(date(2026, 11, 1)).hour == 22 and new_york_rollover(date(2026, 10, 31)).hour == 21
    week = row(1000, 24 * 7, datetime(2026, 1, 12, 0, tzinfo=UTC))  # Monday 12 Jan → Sunday 18 Jan (winter: 22:00 UTC)
    weights = costs.rollover_weights(week)
    charged = {week[i].time: w for i, w in enumerate(weights) if w}  # paid by a position still open when 22:00 starts
    assert charged == {datetime(2026, 1, d, 22, tzinfo=UTC): (3 if d == 16 else 1) for d in (12, 13, 14, 15, 16)}
    assert sum(weights) == 7  # a full week costs seven days, paid Monday..Friday


def test_stop_distance_target_r_gap_filter_and_slippage():
    bars = row(1000, 3) + bars_from([(1000, 1200, 990, 1100)], row(1000, 4)[3].time)

    class Rel(Scripted):
        def entry(self, i, bars, state, p):
            return Entry("buy", stop_distance=50, target_r=2, max_gap=self.gap) if i == 1 else None

    rel = Rel([])
    rel.gap = None
    t = simulate(rel, {"side": "buy"}, bars, Costs(spread=10.0, slippage=5.0))[0]
    assert (t.fill, t.initial_stop, t.target) == (1015.0, 965.0, 1115.0)
    assert t.reason == "target" and t.exit_price == 1110.0 and t.r == pytest.approx(1.9)  # slippage on the way out too
    rel.gap = 5.0
    jump = row(1000, 2) + bars_from([(1010, 1020, 1000, 1010), (1010, 1020, 1000, 1010)], row(1000, 3)[2].time)
    assert simulate(rel, {"side": "buy"}, jump, NO_COST) == []  # opened 10 away from the signal close
    stressed = Costs(spread=10.0, swap_long=-8.0).stressed()
    assert (stressed.spread, stressed.slippage, stressed.swap_long) == (20.0, 5.0, -16.0)


def test_history_tells_ideas_about_the_previous_trade():
    seen = {}

    class Cooldown(Scripted):
        def entry(self, i, bars, state, p):
            seen[i] = (bars.last_entry, bars.last_exit, bars.spread)
            return super().entry(i, bars, state, p)

    bars = row(1000, 4) + bars_from([(1000, 1000, 800, 900)], row(1000, 5)[4].time) + row(1000, 3, row(1000, 6)[5].time)
    simulate(Cooldown([1]), {"side": "buy"}, bars, Costs(spread=3.0))
    assert seen[1] == (None, None, 3.0) and seen[4] == (2, 4, 3.0)


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


def market_like(n=16000, seed=4):
    """Volatility regimes and occasional 4% jumps, so every kind of idea finds trades."""
    rng = random.Random(seed)
    out, price, vol = [], 20000.0, 0.004
    t0 = datetime(2021, 1, 4, tzinfo=UTC)
    for i in range(n):
        if rng.random() < 0.01:
            vol = rng.choice([0.0015, 0.003, 0.006, 0.012])
        r = rng.gauss(0, vol) + (rng.choice([-1, 1]) * 0.04 if rng.random() < 0.002 else 0.0)
        close = price * math.exp(r)
        wick = abs(rng.gauss(0, vol)) * price
        out.append(bars_from([(price, max(price, close) + wick, min(price, close) - wick * rng.random(), close)],
                             t0 + (datetime(2021, 1, 4, 1, tzinfo=UTC) - t0) * i)[0])
        price = close
    return out


def trending(n=16000):
    rng = random.Random(11)
    path = [20000.0]
    for _ in range(n // 24 + 1):
        path.append(path[-1] * (1 + rng.gauss(0, 0.03)))
    return waypoint_bars(path, bars_per_leg=24, noise=150, seed=3)[:n]


GUARD_DATA = {"market_like": market_like(), "trending": trending()}


@pytest.mark.parametrize("idea", registered_and_template(), ids=lambda i: i.name)
def test_every_idea_is_valid_and_never_looks_ahead(idea):
    validate_idea(idea)
    cells = grid_cells(idea)
    for data_name, bars in GUARD_DATA.items():
        for params in {str(c): c for c in (idea.primary, cells[-1])}.values():  # the primary and a far corner
            costs = Costs(spread=10.0, swap_long=-8.0)
            assert simulate(idea, params, bars, costs) is not None
            assert lookahead_violations(idea, params, bars, costs, max_checks=60) == [], (idea.name, data_name, params)


def test_the_guard_data_gives_every_idea_trades():
    for idea in registered_and_template():
        assert simulate(idea, idea.primary, GUARD_DATA["market_like"], Costs(spread=10.0)), idea.name


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


class TenOClock(Idea):
    """Buys the 10:00 UTC bar, which the synthetic market below always pushes up."""

    name, family, hypothesis = "ten_oclock", "test", HYPOTHESIS
    primary = {"stop": 200}
    grid = {"stop": [150, 200, 250]}

    def entry(self, i, bars, state, p):
        return Entry("buy", stop_distance=p["stop"], target_r=1, max_hold=1) if bars[i].time.hour == 9 else None


def planted_edge(n=26000, seed=9):
    """A random walk except that every 10:00 bar rises 300: a real, exploitable pattern."""
    rng = random.Random(seed)
    out, price = [], 20000.0
    for i in range(n):
        t = datetime(2020, 1, 1, tzinfo=UTC) + i * (datetime(2020, 1, 1, 1, tzinfo=UTC) - datetime(2020, 1, 1, tzinfo=UTC))
        move = 300.0 if t.hour == 10 else rng.uniform(-60, 60)
        close = price + move
        out.append(bars_from([(price, max(price, close) + 10, min(price, close) - 10, close)], t)[0])
        price = close
    return out


def test_a_planted_edge_walks_through_all_five_gates(tmp_path):
    ledger = Ledger.open(tmp_path / "l.jsonl")
    ledger.register("anon_zones", "pre-lab", {}, {}, {})
    messages = []
    rep = run_gauntlet(TenOClock(), planted_edge(), Costs(spread=10.0, swap_long=-8.0), ledger, trials=40,
                       progress=messages.append)
    assert rep.passed, rep.reason
    assert [n.kind for n in rep.nulls] == ["shuffle", "signflip"] and rep.null_p <= rep.alpha
    assert rep.holdout is not None and rep.holdout.expectancy_r > 0 and rep.holdout_opening == 1
    assert rep.stressed.expectancy_r > 0 and messages[-1] == "  signflip: 40/40"
    assert "✅" in format_report(rep) and ledger.results()[("ten_oclock", rep.digest)]["passed"] is True
    again = run_gauntlet(TenOClock(), planted_edge(), Costs(spread=10.0), ledger, trials=40)
    assert again.holdout_opening == 2 and "เปิดครั้งที่ 2" in format_report(again)


def test_concentrated_or_unstable_profits_fail_gate_three():
    from anon.lab.core import LabStats
    from anon.lab.gauntlet import Cell, Report, _dev_checks

    def report(by_year):
        s = LabStats(n=60, expectancy_r=0.2, sum_r=sum(by_year.values()), by_year=by_year)
        rep = Report("x", "t", HYPOTHESIS, "h", 1, 0.05, {}, Costs(), [Cell({"a": 1}, s)], {"a": 1}, LabStats(expectancy_r=0.1))
        _dev_checks(rep)
        return rep

    assert report({2019: 3.0, 2020: 2.0, 2021: 1.0}).passed
    assert "ปี" in report({2019: 9.0, 2020: -1.0, 2021: -1.0}).reason  # one good year out of three
    assert "กระจุก" in report({2019: 12.0, 2020: 0.5, 2021: 0.5, 2022: -1.5}).reason


def test_null_histories_keep_what_they_should():
    bars = wiggly(4000, seed=3)
    flipped = fake_history("signflip", bars, 1)
    assert [b.time for b in flipped] == [b.time for b in bars]
    assert all(b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high for b in flipped)
    def mean_size(bs):
        return sum(math.log(b.high / b.low) for b in bs) / len(bs)

    assert mean_size(flipped) == pytest.approx(mean_size(bars), rel=0.02)  # bar sizes kept (up to the drift nudge)
    real_drift = math.log(bars[-1].close / bars[0].open)
    fake_drift = sum(math.log(fake_history("signflip", bars, s)[-1].close / bars[0].open) for s in range(40)) / 40
    assert abs(fake_drift - real_drift) < 0.1  # the drift is kept on average, not the path
    assert fake_history("signflip", bars, 1) == flipped and fake_history("signflip", bars, 2) != flipped
    assert t_stat([1.0, 1.0]) == math.inf and t_stat([0.0, 0.0]) == 0.0 and t_stat([1.0]) == 0.0
    assert t_stat([1.0, 3.0]) == pytest.approx(2.0 / 2 ** 0.5 * 2 ** 0.5)


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
