"""Idea Lab core: a strategy ("idea") replayed over closed H1 bars with real costs.

Execution model, kept deliberately conservative:
- a decision is taken at the close of bar i and filled at the open of bar i+1;
- bars are bid prices: a buy fills at ask (bid + spread) and exits at bid, a sell the reverse;
- a stop and a target inside the same bar count as the stop;
- a gap through the stop fills at the open, i.e. worse than the stop;
- swap is charged for every 00:00 UTC rollover the position is held over (weekends and gaps too);
- R is measured from the real fill to the initial stop, so spread is inside every R.
"""

from __future__ import annotations

import hashlib
import inspect
import itertools
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from anon.models import Bar, Side
from anon.quant import bootstrap_prob_positive

EXIT = "exit"  # returned by Idea.update to leave at the next open
MAX_GRID = 9


@dataclass(frozen=True)
class Entry:
    side: Side
    stop: float
    target: float | None = None
    max_hold: int | None = None  # H1 bars after the fill; None = no time exit


@dataclass
class Trade:
    side: Side
    entry_index: int  # bar whose open filled the entry
    fill: float
    stop: float  # current stop (update may tighten it)
    initial_stop: float
    target: float | None
    max_hold: int | None
    nights: int = 0
    exit_index: int | None = None
    exit_price: float | None = None
    reason: str = ""
    r: float | None = None


class Idea:
    """Subclass, set the class attributes and implement ``entry`` (``prepare``/``update`` optional).

    ``entry``/``update`` run at the close of bar ``i`` and may read only ``bars[: i + 1]``
    and ``state`` values computed from them; ``lookahead_violations`` checks exactly that."""

    name: str = ""
    family: str = ""
    hypothesis: str = ""  # why the market should pay this rule, and who pays
    primary: dict[str, Any] = {}  # the one pre-registered setting that faces the null test
    grid: dict[str, list[Any]] = {}  # neighbours for the plateau check, at most MAX_GRID cells

    def prepare(self, bars: Sequence[Bar], p: dict[str, Any]) -> Any:
        return None

    def entry(self, i: int, bars: Sequence[Bar], state: Any, p: dict[str, Any]) -> Entry | None:
        raise NotImplementedError

    def update(self, i: int, bars: Sequence[Bar], state: Any, p: dict[str, Any], trade: Trade) -> float | str | None:
        """New (tighter) stop, EXIT, or None to leave the trade alone."""
        return None


class History(Sequence[Bar]):
    """``bars[: upto + 1]`` without copying: what an idea may see at the close of bar ``upto``.

    ``len()`` and negative indices work as on that prefix; reading a later bar raises."""

    __slots__ = ("_bars", "_n")

    def __init__(self, bars: Sequence[Bar], upto: int) -> None:
        self._bars, self._n = bars, upto + 1

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, slice):
            start, stop, step = key.indices(self._n)
            return list(self._bars[start:stop]) if step == 1 else [self._bars[j] for j in range(start, stop, step)]
        index = key + self._n if key < 0 else key
        if not 0 <= index < self._n:
            raise IndexError(f"bar {key} is not visible at the close of bar {self._n - 1}")
        return self._bars[index]


def grid_cells(idea: Idea) -> list[dict[str, Any]]:
    keys = list(idea.grid)
    return [dict(zip(keys, values, strict=True)) for values in itertools.product(*(idea.grid[k] for k in keys))]


def validate_idea(idea: Idea) -> None:
    if not idea.name or not idea.name.isidentifier():
        raise ValueError(f"idea name must be an identifier: {idea.name!r}")
    if len(idea.hypothesis.strip()) < 20:
        raise ValueError(f"{idea.name}: write the hypothesis (why it should pay) before testing")
    cells = grid_cells(idea)
    if not 1 <= len(cells) <= MAX_GRID:
        raise ValueError(f"{idea.name}: grid has {len(cells)} cells, allowed 1..{MAX_GRID}")
    if idea.primary not in cells:
        raise ValueError(f"{idea.name}: primary {idea.primary} must be one of the grid cells")


def idea_hash(idea: Idea) -> str:
    """Fingerprint of the idea's source: any edit after seeing results is a new attempt."""
    source = inspect.getsource(type(idea))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class Costs:
    spread: float = 10.0  # price units, added on the ask side
    swap_mode: Literal["points", "percent"] = "points"
    swap_long: float = 0.0  # per night; negative = the position pays
    swap_short: float = 0.0
    point: float = 0.01  # price of one point (BTCUSDc: 2 digits)
    commission: float = 0.0  # price units per round turn

    @property
    def swap_set(self) -> bool:
        return self.swap_long != 0 or self.swap_short != 0

    def swap_per_night(self, side: Side, price: float) -> float:
        rate = self.swap_long if side == "buy" else self.swap_short
        if self.swap_mode == "percent":
            return price * rate / 100 / 365
        return rate * self.point


def _close(trade: Trade, index: int, price: float, reason: str, costs: Costs) -> None:
    trade.exit_index, trade.exit_price, trade.reason = index, price, reason
    move = price - trade.fill if trade.side == "buy" else trade.fill - price
    swap = trade.nights * costs.swap_per_night(trade.side, trade.fill)
    risk = abs(trade.fill - trade.initial_stop)
    trade.r = (move + swap - costs.commission) / risk


def simulate(idea: Idea, p: dict[str, Any], bars: Sequence[Bar], costs: Costs) -> list[Trade]:
    state = idea.prepare(bars, p)
    spread = costs.spread
    trades: list[Trade] = []
    trade: Trade | None = None
    pending: Entry | None = None
    exit_next = False
    for i, bar in enumerate(bars):
        if trade is not None and exit_next:
            price = bar.open if trade.side == "buy" else bar.open + spread
            _close(trade, i, price, trade.reason or "rule", costs)
            trade, exit_next = None, False
        if pending is not None:
            e, pending = pending, None
            fill = bar.open + spread if e.side == "buy" else bar.open
            ok = e.stop < fill if e.side == "buy" else e.stop > fill
            if ok and e.target is not None:
                ok = e.target > fill if e.side == "buy" else e.target < fill
            if ok:
                trade = Trade(e.side, i, fill, e.stop, e.stop, e.target, e.max_hold)
                trades.append(trade)
        if trade is not None:
            if i > trade.entry_index:  # every 00:00 UTC rollover since the previous bar, gaps included
                trade.nights += (bar.time.date() - bars[i - 1].time.date()).days
            if trade.side == "buy":
                lo, hi, open_ = bar.low, bar.high, bar.open
                gap_stop = i > trade.entry_index and open_ <= trade.stop
                if gap_stop or lo <= trade.stop:
                    _close(trade, i, open_ if gap_stop else trade.stop, "stop", costs)
                elif trade.target is not None and hi >= trade.target:
                    _close(trade, i, max(open_, trade.target) if i > trade.entry_index else trade.target, "target", costs)
            else:
                lo, hi, open_ = bar.low + spread, bar.high + spread, bar.open + spread
                gap_stop = i > trade.entry_index and open_ >= trade.stop
                if gap_stop or hi >= trade.stop:
                    _close(trade, i, open_ if gap_stop else trade.stop, "stop", costs)
                elif trade.target is not None and lo <= trade.target:
                    _close(trade, i, min(open_, trade.target) if i > trade.entry_index else trade.target, "target", costs)
            if trade.exit_index is not None:
                trade = None
        if i == len(bars) - 1:
            break
        if trade is not None:
            if trade.max_hold is not None and i - trade.entry_index + 1 >= trade.max_hold:
                trade.reason, exit_next = "time", True
                continue
            decision = idea.update(i, History(bars, i), state, p, trade)
            if decision == EXIT:
                trade.reason, exit_next = "rule", True
            elif isinstance(decision, (int, float)):
                trade.stop = max(trade.stop, decision) if trade.side == "buy" else min(trade.stop, decision)
        elif pending is None:
            pending = idea.entry(i, History(bars, i), state, p)
    if trade is not None:
        last = bars[-1]
        _close(trade, len(bars) - 1, last.close if trade.side == "buy" else last.close + spread, "end", costs)
    return trades


def lookahead_violations(
    idea: Idea, p: dict[str, Any], bars: Sequence[Bar], costs: Costs | None = None, max_checks: int = 120
) -> list[str]:
    """Every decision must come out the same when the history stops right at that bar.

    Checks the bars where the full run entered or managed a trade, plus evenly spaced
    bars; ``prepare`` is re-run on each shortened history, so indicators that use later
    bars are caught as well as direct reads of later bars."""
    costs = costs or Costs()
    try:
        trades = simulate(idea, p, bars, costs)
    except IndexError as exc:
        return [f"reads a later bar: {exc}"]
    full_state = idea.prepare(bars, p)
    managed: dict[int, Trade] = {}
    for t in trades:
        for j in range(t.entry_index, t.exit_index if t.exit_index is not None else len(bars) - 1):
            managed.setdefault(j, t)
    decision_bars = sorted({t.entry_index - 1 for t in trades} | set(list(managed)[:: max(1, len(managed) // 40)]))
    step = max(1, len(bars) // 40)
    spaced = list(range(step, len(bars) - 1, step))
    chosen = decision_bars[:: max(1, len(decision_bars) // max_checks + 1)] + spaced
    problems: list[str] = []
    for i in sorted(set(chosen)):
        if i < 1:
            continue
        short = bars[: i + 1]
        short_state = idea.prepare(short, p)
        seen_short = idea.entry(i, History(short, i), short_state, p)
        seen_full = idea.entry(i, History(bars, i), full_state, p)
        if seen_short != seen_full:
            problems.append(f"entry decision at bar {i} changes once later bars exist")
        t = managed.get(i)
        if t is not None:
            upd_short = idea.update(i, History(short, i), short_state, p, replace(t))
            upd_full = idea.update(i, History(bars, i), full_state, p, replace(t))
            if upd_short != upd_full:
                problems.append(f"trade management at bar {i} changes once later bars exist")
    return problems


@dataclass
class LabStats:
    n: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    sum_r: float = 0.0
    profit_factor: float | None = None
    max_drawdown_r: float = 0.0
    prob_edge_positive: float | None = None
    avg_hours: float | None = None
    per_year: float = 0.0
    reasons: dict[str, int] = field(default_factory=dict)


def lab_stats(trades: Sequence[Trade], bars: Sequence[Bar], bootstrap: bool = True) -> LabStats:
    rs = [t.r for t in trades if t.r is not None]
    s = LabStats()
    years = (bars[-1].close_time - bars[0].time).total_seconds() / (365.25 * 86400) if bars else 0
    if not rs:
        return s
    s.n = len(rs)
    s.win_rate = 100 * sum(1 for x in rs if x > 0) / s.n
    s.sum_r = sum(rs)
    s.expectancy_r = statistics.fmean(rs)
    gross_win, gross_loss = sum(x for x in rs if x > 0), -sum(x for x in rs if x < 0)
    s.profit_factor = gross_win / gross_loss if gross_loss > 0 else None
    equity = peak = 0.0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        s.max_drawdown_r = max(s.max_drawdown_r, peak - equity)
    s.prob_edge_positive = bootstrap_prob_positive(rs) if bootstrap else None
    held = [t.exit_index - t.entry_index + 1 for t in trades if t.exit_index is not None]
    s.avg_hours = statistics.fmean(held) if held else None
    s.per_year = s.n / years if years > 0 else 0.0
    for t in trades:
        s.reasons[t.reason] = s.reasons.get(t.reason, 0) + 1
    return s
