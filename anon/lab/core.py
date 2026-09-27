"""Idea Lab core: a strategy ("idea") replayed over closed H1 bars with real costs.

Execution model, kept deliberately conservative:
- a decision is taken at the close of bar i and filled at the open of bar i+1;
- bars are bid prices: a buy fills at ask (bid + spread) and exits at bid, a sell the reverse;
- slippage moves every fill and every exit against the trade;
- a stop and a target inside the same bar count as the stop;
- a gap through the stop fills at the open, i.e. worse than the stop;
- swap is charged at the broker's rollover (17:00 New York by default, Friday x3, weekend 0),
  as a yearly rate on the fill price so it scales with price across the years;
- R is measured from the real fill to the initial stop, so spread is inside every R.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import inspect
import itertools
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from anon.models import Bar, Side
from anon.quant import bootstrap_prob_positive

EXIT = "exit"  # returned by Idea.update to leave at the next open
MAX_GRID = 9


@dataclass(frozen=True)
class Entry:
    """Give either ``stop`` (a price) or ``stop_distance`` (from the executable fill)."""

    side: Side
    stop: float | None = None
    target: float | None = None
    max_hold: int | None = None  # H1 bars counted from the fill bar; None = no time exit
    stop_distance: float | None = None
    target_r: float | None = None  # target at this many R from the fill
    max_gap: float | None = None  # skip when the next open is further than this from the signal close


@dataclass
class Trade:
    side: Side
    entry_index: int  # bar whose open filled the entry
    fill: float
    stop: float  # current stop (update may tighten it)
    initial_stop: float
    target: float | None
    max_hold: int | None
    swap_days: float = 0.0  # rollover charges, Friday counted 3
    exit_index: int | None = None
    exit_price: float | None = None
    reason: str = ""
    r: float | None = None
    memo: dict[str, Any] = field(default_factory=dict)  # scratch space for update(); see Idea.update


class History(Sequence[Bar]):
    """``bars[: upto + 1]`` without copying: what an idea may see at the close of bar ``upto``.

    ``len()`` and negative indices work as on that prefix; reading a later bar raises.
    ``last_entry`` / ``last_exit`` are the fill and exit bars of the previous trade (or None)
    and ``spread`` the cost model's spread, for cooldowns and cost filters."""

    __slots__ = ("_bars", "_n", "last_entry", "last_exit", "spread")

    def __init__(self, bars: Sequence[Bar], upto: int, last_entry: int | None = None, last_exit: int | None = None,
                 spread: float = 0.0) -> None:
        self._bars, self._n = bars, upto + 1
        self.last_entry, self.last_exit, self.spread = last_entry, last_exit, spread

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

    def entry(self, i: int, bars: History, state: Any, p: dict[str, Any]) -> Entry | None:
        raise NotImplementedError

    def update(self, i: int, bars: History, state: Any, p: dict[str, Any], trade: Trade) -> float | str | None:
        """New (tighter) stop, EXIT, or None to leave the trade alone.

        ``trade.memo`` may cache running values, but only together with the bar index they
        were computed at (recompute when ``i`` is not the next bar): the lookahead check calls
        this out of order."""
        return None


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


# --- costs -------------------------------------------------------------------------------

def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def new_york_rollover(day: date) -> datetime:
    """17:00 New York on ``day`` in UTC: 21:00 under US daylight time, 22:00 otherwise."""
    dst = _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)
    return datetime(day.year, day.month, day.day, 21 if dst else 22, tzinfo=UTC)


@dataclass(frozen=True)
class Costs:
    spread: float = 10.0  # price units, added on the ask side
    slippage: float = 0.0  # price units against the trade on every fill and exit
    swap_mode: Literal["points", "percent"] = "percent"
    swap_long: float = 0.0  # per rollover; points, or % per year of the fill price; negative = the position pays
    swap_short: float = 0.0
    point: float = 0.01  # price of one point (BTCUSDc: 2 digits)
    swap_days: tuple[float, ...] = (1, 1, 1, 1, 3, 0, 0)  # Monday..Sunday multipliers (MT5 "Swap rates")
    rollover: Literal["new_york", "utc_midnight"] = "new_york"
    commission: float = 0.0  # price units per round turn

    @property
    def swap_set(self) -> bool:
        return self.swap_long != 0 or self.swap_short != 0

    def swap_per_day(self, side: Side, price: float) -> float:
        rate = self.swap_long if side == "buy" else self.swap_short
        if self.swap_mode == "percent":
            return price * rate / 100 / 365
        return rate * self.point

    def rollover_at(self, day: date) -> datetime:
        return new_york_rollover(day) if self.rollover == "new_york" else datetime(day.year, day.month, day.day, tzinfo=UTC)

    def rollover_weights(self, bars: Sequence[Bar]) -> list[float]:
        """weights[i] = swap multiplier of the rollovers after the open of bar i-1, up to the open of bar i."""
        weights = [0.0] * len(bars)
        if len(bars) < 2:
            return weights
        rolls = []
        day, end = bars[0].time.date(), bars[-1].time.date()
        while day <= end:
            rolls.append((self.rollover_at(day), self.swap_days[day.weekday()]))
            day += timedelta(days=1)
        j = 0
        for i in range(1, len(bars)):
            a, b = bars[i - 1].time, bars[i].time
            while j < len(rolls) and rolls[j][0] <= a:
                j += 1
            while j < len(rolls) and rolls[j][0] <= b:
                weights[i] += rolls[j][1]
                j += 1
        return weights

    def stressed(self) -> Costs:
        """What the red team asked for: spread 2x (at least 20), slippage 5 a side, swap 2x."""
        return Costs(max(20.0, 2 * self.spread), max(5.0, self.slippage), self.swap_mode, 2 * self.swap_long,
                     2 * self.swap_short, self.point, self.swap_days, self.rollover, self.commission)


# --- replay ------------------------------------------------------------------------------

def _close(trade: Trade, index: int, price: float, reason: str, costs: Costs) -> None:
    price = price - costs.slippage if trade.side == "buy" else price + costs.slippage
    trade.exit_index, trade.exit_price, trade.reason = index, price, reason
    move = price - trade.fill if trade.side == "buy" else trade.fill - price
    swap = trade.swap_days * costs.swap_per_day(trade.side, trade.fill)
    risk = abs(trade.fill - trade.initial_stop)
    trade.r = (move + swap - costs.commission) / risk


def _open(e: Entry, i: int, bars: Sequence[Bar], costs: Costs) -> Trade | None:
    bar = bars[i]
    if e.max_gap is not None and abs(bar.open - bars[i - 1].close) > e.max_gap:
        return None
    buy = e.side == "buy"
    fill = bar.open + costs.spread + costs.slippage if buy else bar.open - costs.slippage
    if e.stop_distance is not None:
        stop = fill - e.stop_distance if buy else fill + e.stop_distance
    elif e.stop is not None:
        stop = e.stop
    else:
        raise ValueError("Entry needs stop or stop_distance")
    target = e.target
    if e.target_r is not None:
        target = fill + e.target_r * (fill - stop) if buy else fill - e.target_r * (stop - fill)
    ok = stop < fill if buy else stop > fill
    if ok and target is not None:
        ok = target > fill if buy else target < fill
    return Trade(e.side, i, fill, stop, stop, target, e.max_hold) if ok else None


def simulate(idea: Idea, p: dict[str, Any], bars: Sequence[Bar], costs: Costs) -> list[Trade]:
    state = idea.prepare(bars, p)
    spread = costs.spread
    weights = costs.rollover_weights(bars) if costs.swap_set else None
    trades: list[Trade] = []
    trade: Trade | None = None
    pending: Entry | None = None
    exit_next = False
    last_entry = last_exit = None
    for i, bar in enumerate(bars):
        if trade is not None and exit_next:
            _close(trade, i, bar.open if trade.side == "buy" else bar.open + spread, trade.reason or "rule", costs)
            last_exit, trade, exit_next = i, None, False
        if pending is not None:
            trade, pending = _open(pending, i, bars, costs), None
            if trade is not None:
                trades.append(trade)
                last_entry = i
        if trade is not None:
            if weights is not None and i > trade.entry_index:
                trade.swap_days += weights[i]
            if trade.side == "buy":
                lo, hi, open_ = bar.low, bar.high, bar.open
            else:
                lo, hi, open_ = bar.low + spread, bar.high + spread, bar.open + spread
            later = i > trade.entry_index
            buy = trade.side == "buy"
            gap_stop = later and (open_ <= trade.stop if buy else open_ >= trade.stop)
            if gap_stop or (lo <= trade.stop if buy else hi >= trade.stop):
                _close(trade, i, open_ if gap_stop else trade.stop, "stop", costs)
            elif trade.target is not None and (hi >= trade.target if buy else lo <= trade.target):
                if later:
                    price = max(open_, trade.target) if buy else min(open_, trade.target)
                else:
                    price = trade.target
                _close(trade, i, price, "target", costs)
            if trade.exit_index is not None:
                last_exit, trade = i, None
        if i == len(bars) - 1:
            break
        if trade is not None:
            if trade.max_hold is not None and i - trade.entry_index + 1 >= trade.max_hold:
                trade.reason, exit_next = "time", True
                continue
            decision = idea.update(i, History(bars, i, last_entry, last_exit, spread), state, p, trade)
            if decision == EXIT:
                trade.reason, exit_next = "rule", True
            elif isinstance(decision, int | float):
                trade.stop = max(trade.stop, decision) if trade.side == "buy" else min(trade.stop, decision)
        elif pending is None:
            pending = idea.entry(i, History(bars, i, last_entry, last_exit, spread), state, p)
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
    entries = [t.entry_index for t in trades]
    exits = sorted(t.exit_index for t in trades if t.exit_index is not None)
    problems: list[str] = []
    for i in sorted(set(chosen)):
        if i < 1:
            continue
        k, m = bisect.bisect_right(entries, i), bisect.bisect_right(exits, i)
        last_entry, last_exit = (entries[k - 1] if k else None), (exits[m - 1] if m else None)
        short = bars[: i + 1]
        short_state = idea.prepare(short, p)
        seen_short = idea.entry(i, History(short, i, last_entry, last_exit, costs.spread), short_state, p)
        seen_full = idea.entry(i, History(bars, i, last_entry, last_exit, costs.spread), full_state, p)
        if seen_short != seen_full:
            problems.append(f"entry decision at bar {i} changes once later bars exist")
        t = managed.get(i)
        if t is not None:
            upd_short = idea.update(i, History(short, i, last_entry, last_exit, costs.spread), short_state, p, copy.deepcopy(t))
            upd_full = idea.update(i, History(bars, i, last_entry, last_exit, costs.spread), full_state, p, copy.deepcopy(t))
            if upd_short != upd_full:
                problems.append(f"trade management at bar {i} changes once later bars exist")
    return problems


# --- statistics --------------------------------------------------------------------------

@dataclass
class LabStats:
    n: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    sum_r: float = 0.0
    t_stat: float = 0.0  # mean R / standard error: evidence that grows with the number of trades
    profit_factor: float | None = None
    max_drawdown_r: float = 0.0
    prob_edge_positive: float | None = None
    avg_hours: float | None = None
    per_year: float = 0.0
    swap_r: float = 0.0  # average R paid (negative) or earned in swap
    by_year: dict[int, float] = field(default_factory=dict)  # sum of R per calendar year of the fill
    reasons: dict[str, int] = field(default_factory=dict)


def t_stat(rs: Sequence[float]) -> float:
    if len(rs) < 2:
        return 0.0
    mean, sd = statistics.fmean(rs), statistics.stdev(rs)
    if sd > 0:
        return mean / sd * math.sqrt(len(rs))
    return math.copysign(math.inf, mean) if mean else 0.0  # identical trades: all evidence one way


def lab_stats(
    trades: Sequence[Trade], bars: Sequence[Bar], costs: Costs | None = None, bootstrap: bool = True,
    span: Sequence[Bar] | None = None,
) -> LabStats:
    """``bars`` is the list the trades index into; ``span`` the stretch they cover (default: all of it)."""
    rs = [t.r for t in trades if t.r is not None]
    s = LabStats()
    span = bars if span is None else span
    years = (span[-1].close_time - span[0].time).total_seconds() / (365.25 * 86400) if span else 0
    if not rs:
        return s
    s.n = len(rs)
    s.win_rate = 100 * sum(1 for x in rs if x > 0) / s.n
    s.sum_r = sum(rs)
    s.expectancy_r = statistics.fmean(rs)
    s.t_stat = t_stat(rs)
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
    if costs is not None and costs.swap_set:
        s.swap_r = statistics.fmean(
            t.swap_days * costs.swap_per_day(t.side, t.fill) / abs(t.fill - t.initial_stop) for t in trades
        )
    for t in trades:
        s.reasons[t.reason] = s.reasons.get(t.reason, 0) + 1
        if t.r is not None:
            year = bars[t.entry_index].time.year
            s.by_year[year] = s.by_year.get(year, 0.0) + t.r
    return s
