import json
from types import SimpleNamespace

import pytest

from anon.config import LevelsConfig, OmegaConfig
from anon.models import Signal
from anon.omega import AIVerdict, Omega
from anon.omega_ai import OUTPUT_SCHEMA, ClaudeReviewer, parse_verdict
from anon.synthetic import waypoint_bars
from tests.helpers import T0

LV = LevelsConfig()
SIG = Signal("A", "zone", "buy", T0, 83300, 82950, 85500, True, 6.0)
UP = waypoint_bars([82000, 84000], bars_per_leg=80, noise=10)
DOWN = waypoint_bars([86000, 83300], bars_per_leg=80, noise=10)


class FixedAI:
    def __init__(self, favorable=True, boom=False):
        self.favorable, self.boom, self.calls = favorable, boom, 0

    def review(self, bars, signal, rule):
        self.calls += 1
        if self.boom:
            raise TimeoutError("timeout")
        return AIVerdict("trend_up", self.favorable, 0.8, "ok")


def test_rule_regimes():
    om = Omega(LV, OmegaConfig())
    assert om.rule_tag(UP).regime == "trend_up" and om.rule_tag(UP).favorable
    assert om.rule_tag(DOWN).regime == "trend_down" and not om.rule_tag(DOWN).favorable
    assert om.rule_tag(UP[:10]).regime == "unknown"


def test_ai_cannot_override_unfavorable_rule_and_is_not_even_called():
    ai = FixedAI(favorable=True)
    tag = Omega(LV, OmegaConfig(), ai).tag(DOWN, SIG)
    assert not tag.favorable and ai.calls == 0


def test_ai_can_veto_favorable_rule():
    tag = Omega(LV, OmegaConfig(), FixedAI(favorable=False)).tag(UP, SIG)
    assert not tag.favorable and tag.ai_favorable is False


def test_ai_failure_is_fail_closed_by_default():
    tag = Omega(LV, OmegaConfig(), FixedAI(boom=True)).tag(UP, SIG)
    assert not tag.favorable and "fail-closed" in tag.reason
    tag = Omega(LV, OmegaConfig(ai_fail_closed=False), FixedAI(boom=True)).tag(UP, SIG)
    assert tag.favorable


class FakeMessages:
    def __init__(self, stop_reason="end_turn", text=None):
        self.stop_reason, self.text, self.kwargs = stop_reason, text, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        content = [SimpleNamespace(type="thinking", thinking="")]
        if self.text is not None:
            content.append(SimpleNamespace(type="text", text=self.text))
        return SimpleNamespace(stop_reason=self.stop_reason, content=content)


def fake_client(**kw):
    msgs = FakeMessages(**kw)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def test_claude_request_shape_and_parse():
    good = json.dumps({"regime": "range", "favorable": False, "confidence": 0.7, "reason": "lower highs"})
    client, msgs = fake_client(text=good)
    verdict = ClaudeReviewer(LV, OmegaConfig(), client).review(UP, SIG, Omega(LV, OmegaConfig()).rule_tag(UP))
    assert verdict.favorable is False and verdict.regime == "range"
    kw = msgs.kwargs
    assert kw["model"] == "claude-opus-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["schema"] == OUTPUT_SCHEMA
    assert kw["thinking"] == {"type": "adaptive"}
    payload = json.loads(kw["messages"][0]["content"])
    assert len(payload["bars_oldest_first"]) == OmegaConfig().ai_bars
    assert payload["setup"]["stop"] == 82950


@pytest.mark.parametrize("stop_reason,text", [("refusal", None), ("max_tokens", None), ("end_turn", None)])
def test_claude_bad_responses_raise(stop_reason, text):
    client, _ = fake_client(stop_reason=stop_reason, text=text)
    with pytest.raises(RuntimeError):
        ClaudeReviewer(LV, OmegaConfig(), client).review(UP, SIG, Omega(LV, OmegaConfig()).rule_tag(UP))


def test_parse_verdict_validates():
    with pytest.raises(ValueError):
        parse_verdict(json.dumps({"regime": "moon", "favorable": True, "confidence": 1, "reason": ""}))
    with pytest.raises(ValueError):
        parse_verdict(json.dumps({"regime": "range", "favorable": "yes", "confidence": 1, "reason": ""}))
    assert parse_verdict(json.dumps({"regime": "range", "favorable": True, "confidence": 5, "reason": "x"})).confidence == 1.0
