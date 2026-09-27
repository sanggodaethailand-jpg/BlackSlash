"""Ghost — calls A/B setups on closed H1 bars. It never places orders.

A: buy in the 83000–83500 zone, or a sweep below 83000 that closes back inside
   (sweep-and-reclaim). The hard stop is a number below the sweep low, so a sweep
   cannot take it; "H1 close below 83000" is handled by the engine as a thesis exit.
B: buy the retest after a clear H1 close above the gray band. The stop is the
   recent swing low minus a buffer — never pinned to b_inv_ref.
Nothing is called while the signal bar closes inside the gray band (NO CHASE).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from anon.config import GhostConfig, LevelsConfig
from anon.models import Bar, Signal


@dataclass(frozen=True)
class GhostResult:
    signal: Signal | None
    reason: str


def reward_risk(entry: float, stop: float, tp1: float) -> float:
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return (tp1 - entry) / risk


def reprice(signal: Signal, entry: float) -> Signal:
    """Re-anchor a signal to the price it would actually fill at (e.g. the ask)."""
    return replace(signal, entry=entry, rr=reward_risk(entry, signal.stop, signal.tp1))


class Ghost:
    def __init__(self, levels: LevelsConfig, cfg: GhostConfig) -> None:
        self.lv = levels
        self.cfg = cfg

    def evaluate(self, bars: Sequence[Bar]) -> GhostResult:
        if not bars:
            return GhostResult(None, "no_data")
        bar = bars[-1]
        lv = self.lv
        if lv.gray_low <= bar.close <= lv.gray_high:
            return GhostResult(None, "inside_gray_no_chase")

        candidate = self._setup_a(bar) or self._setup_b(bars)
        if candidate is None:
            return GhostResult(None, "no_setup")
        if candidate.entry >= candidate.tp1:
            return GhostResult(None, f"{candidate.setup}:entry_at_or_above_tp1")
        if candidate.rr < self.cfg.min_rr:
            return GhostResult(None, f"{candidate.setup}:rr_{candidate.rr:.2f}_below_min")
        return GhostResult(candidate, f"call_{candidate.setup}_{candidate.variant}")

    def _setup_a(self, bar: Bar) -> Signal | None:
        lv = self.lv
        touched = bar.low <= lv.a_zone_top
        held = bar.close >= lv.a_zone_bot
        if not (touched and held and bar.close < lv.gray_low):
            return None
        swept = bar.low < lv.a_zone_bot
        stop = min(bar.low, lv.a_zone_bot) - self.cfg.stop_buffer
        return Signal(
            setup="A",
            variant="sweep_reclaim" if swept else "zone",
            side="buy",
            bar_time=bar.time,
            entry=bar.close,
            stop=stop,
            tp1=lv.tp1,
            outside_gray=True,
            rr=reward_risk(bar.close, stop, lv.tp1),
            note=f"thesis exit: H1 close < {lv.a_zone_bot:.0f}",
        )

    def _setup_b(self, bars: Sequence[Bar]) -> Signal | None:
        lv, cfg = self.lv, self.cfg
        i = len(bars) - 1
        bar = bars[i]
        if bar.close <= lv.gray_high:
            return None
        breakout = self._armed_breakout(bars)
        if breakout is None or breakout >= i:
            return None
        if bar.low > lv.gray_high + cfg.retest_tolerance:
            return None
        window = bars[max(0, i - cfg.swing_lookback + 1) : i + 1]
        stop = min(b.low for b in window) - cfg.stop_buffer
        return Signal(
            setup="B",
            variant="breakout_retest",
            side="buy",
            bar_time=bar.time,
            entry=bar.close,
            stop=stop,
            tp1=lv.tp1,
            outside_gray=True,
            rr=reward_risk(bar.close, stop, lv.tp1),
            note=f"breakout bar {bars[breakout].time:%Y-%m-%d %H:%M}; ref {lv.b_inv_ref:.0f} not used",
        )

    def _armed_breakout(self, bars: Sequence[Bar]) -> int | None:
        """Index of the latest fresh close above gray, if every close since stayed above."""
        gh = self.lv.gray_high
        i = len(bars) - 1
        lo = max(1, i - self.cfg.breakout_expiry_bars)
        for j in range(i, lo - 1, -1):
            if bars[j].close <= gh:
                return None
            if bars[j - 1].close <= gh:
                return j
        return None
