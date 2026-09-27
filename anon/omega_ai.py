"""Claude as a veto-only Ω reviewer.

Called only when Ghost has a signal *and* the rule-based tag is already favorable,
so it costs one request per candidate trade. Any error is raised to ``Omega.tag``,
which applies the fail-closed policy.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from anon.config import LevelsConfig, OmegaConfig
from anon.models import Bar, Signal
from anon.omega import AIVerdict, OmegaTag

REGIMES = ["trend_up", "trend_down", "range", "shock"]

SYSTEM_PROMPT = """You review market regime for a disciplined BTCUSD H1 trading desk.
A rule engine has already proposed a long setup and tagged the regime as favorable.
Your only power is to veto: set favorable=false when the recent bars argue against
taking this long now (for example a clear downtrend, a volatility shock, or price
action that contradicts the setup). Otherwise set favorable=true.
You never suggest entries, targets or position sizes. Judge only from the data given.
Keep reason under 200 characters."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "regime": {"type": "string", "enum": REGIMES},
        "favorable": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["regime", "favorable", "confidence", "reason"],
    "additionalProperties": False,
}


class ClaudeReviewer:
    def __init__(self, levels: LevelsConfig, cfg: OmegaConfig, client=None) -> None:
        self.lv = levels
        self.cfg = cfg
        if client is None:
            import anthropic

            client = anthropic.Anthropic(timeout=cfg.ai_timeout_s, max_retries=cfg.ai_max_retries)
        self.client = client

    def build_payload(self, bars: Sequence[Bar], signal: Signal, rule: OmegaTag) -> dict:
        lv = self.lv
        recent = bars[-self.cfg.ai_bars :]
        return {
            "symbol": "BTCUSD",
            "timeframe": "H1",
            "levels": {
                "a_zone": [lv.a_zone_bot, lv.a_zone_top],
                "gray_band": [lv.gray_low, lv.gray_high],
                "tp1": lv.tp1,
                "liquidity_above": lv.liq_top,
            },
            "setup": {
                "name": signal.setup,
                "variant": signal.variant,
                "side": signal.side,
                "entry": round(signal.entry, 1),
                "stop": round(signal.stop, 1),
                "tp1": signal.tp1,
                "reward_risk": round(signal.rr, 2),
            },
            "rule_regime": {"regime": rule.regime, "reason": rule.reason},
            "bars_oldest_first": [
                [b.time.strftime("%Y-%m-%d %H:%M"), b.open, b.high, b.low, b.close] for b in recent
            ],
        }

    def review(self, bars: Sequence[Bar], signal: Signal, rule: OmegaTag) -> AIVerdict:
        payload = self.build_payload(bars, signal, rule)
        response = self.client.beta.messages.create(
            model=self.cfg.ai_model,
            max_tokens=4096,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.cfg.ai_effort,
                "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
            },
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload, separators=(",", ":"))}],
        )
        if response.stop_reason != "end_turn":
            raise RuntimeError(f"AI stop_reason={response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise RuntimeError("AI returned no text block")
        return parse_verdict(text)


def parse_verdict(text: str) -> AIVerdict:
    data = json.loads(text)
    regime = data["regime"]
    if regime not in REGIMES:
        raise ValueError(f"unknown regime {regime!r}")
    favorable = data["favorable"]
    if not isinstance(favorable, bool):
        raise ValueError("favorable must be a boolean")
    confidence = min(1.0, max(0.0, float(data["confidence"])))
    return AIVerdict(regime, favorable, confidence, str(data["reason"])[:300])
