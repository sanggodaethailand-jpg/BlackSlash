"""Auto levels: the handoff's level geometry redrawn from recent price structure.

Levels are derived from closed bars only and then held for the whole local day,
so a backtest never sees a level that was drawn with future data.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from anon.config import AutoLevelsConfig, GhostConfig, LevelsConfig
from anon.indicators import atr
from anon.models import Bar


@dataclass(frozen=True)
class DrawnLevels:
    levels: LevelsConfig
    ghost: GhostConfig
    low: float
    high: float
    atr: float

    def describe(self) -> str:
        lv = self.levels
        return (
            f"A {lv.a_zone_bot:,.0f}–{lv.a_zone_top:,.0f} · เทา {lv.gray_low:,.0f}–{lv.gray_high:,.0f} · "
            f"TP1 {lv.tp1:,.0f} · liq {lv.liq_top:,.0f} (กรอบ {self.high - self.low:,.0f} จุด, ATR {self.atr:,.0f})"
        )


def draw_levels(
    history: Sequence[Bar], auto: AutoLevelsConfig, ghost: GhostConfig, atr_period: int
) -> DrawnLevels | str:
    """Levels from the last ``lookback_bars`` of ``history``, or the reason there are none."""
    window = history[-auto.lookback_bars :]
    if len(window) < auto.lookback_bars:
        return f"need {auto.lookback_bars} bars, have {len(window)}"
    low = min(b.low for b in window)
    high = max(b.high for b in window)
    span = high - low
    bar_atr = atr(window, atr_period)[-1]
    if span <= 0 or span < auto.min_range_atr * bar_atr:
        return f"range {span:,.0f} < {auto.min_range_atr}×ATR {bar_atr:,.0f}"
    levels = LevelsConfig(
        a_zone_bot=low,
        a_zone_top=low + auto.a_top * span,
        b_inv_ref=low + auto.b_ref * span,
        gray_center=low + auto.gray_center * span,
        gray_half_width=auto.gray_half * span,
        tp1=low + auto.tp1 * span,
        liq_top=high,
    )
    scaled = replace(ghost, stop_buffer=auto.stop_buffer * span, retest_tolerance=auto.retest_tolerance * span)
    return DrawnLevels(levels, scaled, low, high, bar_atr)
