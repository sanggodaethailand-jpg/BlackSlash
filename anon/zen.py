"""Zen — discipline. Three-item checklist before any order, and the day lock
after consecutive off-plan events. A day with no signal is a passing day."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from anon.config import ZenConfig
from anon.models import Signal
from anon.risk import RiskDecision


@dataclass
class ZenState:
    day: str = ""
    consecutive_off_plan: int = 0
    locked: bool = False
    bars_since_loss: int | None = None
    off_plan_log: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ZenDecision:
    ok: bool
    failed: tuple[str, ...]


class Zen:
    def __init__(self, cfg: ZenConfig, state: ZenState | None = None) -> None:
        self.cfg = cfg
        self.state = state or ZenState()

    def roll_day(self, day: date) -> None:
        key = day.isoformat()
        if self.state.day != key:
            self.state.day = key
            self.state.consecutive_off_plan = 0
            self.state.locked = False

    def on_bar(self) -> None:
        if self.state.bars_since_loss is not None:
            self.state.bars_since_loss += 1

    def record_off_plan(self, what: str) -> None:
        s = self.state
        s.consecutive_off_plan += 1
        s.off_plan_log.append(f"{s.day} {what}")
        if s.consecutive_off_plan >= self.cfg.max_consecutive_off_plan:
            s.locked = True

    def record_plan_trade(self, r: float) -> None:
        self.state.consecutive_off_plan = 0
        if r < 0:
            self.state.bars_since_loss = 0

    def check(self, signal: Signal, risk: RiskDecision, journal_ready: bool) -> ZenDecision:
        failed: list[str] = []
        if self.state.locked:
            failed.append("day_locked_after_off_plan")
        # (1) Ghost called it outside gray
        if not signal.outside_gray:
            failed.append("1_not_outside_gray")
        # (2) lot 0.01 + numeric inv + not chasing losses
        lot_or_stop = [v for v in risk.vetoes if v.startswith(("lot_", "no_numeric_stop"))]
        if lot_or_stop:
            failed.append("2_lot_or_stop:" + ",".join(lot_or_stop))
        cooling = self.state.bars_since_loss
        if cooling is not None and cooling < self.cfg.cooldown_bars_after_loss:
            failed.append(f"2_cooldown_after_loss_{cooling}/{self.cfg.cooldown_bars_after_loss}_bars")
        # (3) journal ready for #Txx
        if not journal_ready:
            failed.append("3_journal_not_ready")
        return ZenDecision(not failed, tuple(failed))

    def to_dict(self) -> dict:
        return asdict(self.state)

    @classmethod
    def from_dict(cls, cfg: ZenConfig, data: dict) -> Zen:
        return cls(cfg, ZenState(**data))
