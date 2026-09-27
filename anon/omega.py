"""Ω — regime tag. Rule-based first; the optional AI reviewer can only veto.

The AI can turn a favorable tag into unfavorable, never the reverse, and any AI
failure (timeout, refusal, bad JSON) counts as unfavorable when ``ai_fail_closed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from anon.config import LevelsConfig, OmegaConfig
from anon.indicators import atr, ema
from anon.models import Bar, Signal

FAVORABLE_FOR_LONG = {"trend_up", "range"}


@dataclass(frozen=True)
class OmegaTag:
    regime: str
    favorable: bool
    source: str
    reason: str
    ai_regime: str | None = None
    ai_favorable: bool | None = None


@dataclass(frozen=True)
class AIVerdict:
    regime: str
    favorable: bool
    confidence: float
    reason: str


class AIReviewer(Protocol):
    def review(self, bars: Sequence[Bar], signal: Signal, rule: OmegaTag) -> AIVerdict: ...


class Omega:
    def __init__(self, levels: LevelsConfig, cfg: OmegaConfig, ai: AIReviewer | None = None) -> None:
        self.lv = levels
        self.cfg = cfg
        self.ai = ai

    def rule_tag(self, bars: Sequence[Bar]) -> OmegaTag:
        cfg = self.cfg
        if len(bars) < cfg.ema_slow + 1:
            return OmegaTag("unknown", False, "rule", f"need {cfg.ema_slow + 1} bars, have {len(bars)}")
        closes = [b.close for b in bars]
        fast = ema(closes, cfg.ema_fast)[-1]
        slow = ema(closes, cfg.ema_slow)[-1]
        prev_atr = atr(bars[:-1], cfg.atr_period)[-1]
        last = bars[-1]
        bar_range = last.high - last.low
        if prev_atr > 0 and bar_range > cfg.shock_atr_mult * prev_atr:
            regime = "shock"
            reason = f"range {bar_range:.0f} > {cfg.shock_atr_mult}×ATR {prev_atr:.0f}"
        elif fast > slow and last.close > slow:
            regime, reason = "trend_up", f"EMA{cfg.ema_fast} {fast:.0f} > EMA{cfg.ema_slow} {slow:.0f}"
        elif fast < slow and last.close < slow:
            regime, reason = "trend_down", f"EMA{cfg.ema_fast} {fast:.0f} < EMA{cfg.ema_slow} {slow:.0f}"
        else:
            regime, reason = "range", "EMAs mixed"
        return OmegaTag(regime, regime in FAVORABLE_FOR_LONG, "rule", reason)

    def tag(self, bars: Sequence[Bar], signal: Signal) -> OmegaTag:
        rule = self.rule_tag(bars)
        if self.ai is None or not rule.favorable:
            return rule
        try:
            verdict = self.ai.review(bars, signal, rule)
        except Exception as exc:  # noqa: BLE001 - any AI failure is handled by fail mode
            if self.cfg.ai_fail_closed:
                return OmegaTag(rule.regime, False, "rule+ai", f"AI unavailable (fail-closed): {exc}")
            return OmegaTag(rule.regime, True, "rule", f"{rule.reason}; AI unavailable (fail-open): {exc}")
        return OmegaTag(
            regime=rule.regime,
            favorable=rule.favorable and verdict.favorable,
            source="rule+ai",
            reason=f"{rule.reason}; AI: {verdict.reason}",
            ai_regime=verdict.regime,
            ai_favorable=verdict.favorable,
        )
