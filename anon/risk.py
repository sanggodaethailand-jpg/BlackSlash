"""Risk — hard vetoes and ceilings. Every position counts toward the caps,
including off-plan ones Quant ignores (e.g. #208190412)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from anon.config import RiskConfig
from anon.models import Account, Position, Signal, SymbolSpec


@dataclass(frozen=True)
class RiskContext:
    account: Account
    positions: Sequence[Position]
    spec: SymbolSpec
    day_start_equity: float
    peak_equity: float
    bot_magic: int


@dataclass
class RiskDecision:
    vetoes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    lot: float = 0.0
    risk_money: float = 0.0
    risk_pct: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.vetoes


def risk_money(entry: float, stop: float, lot: float, contract_size: float) -> float:
    return abs(entry - stop) * lot * contract_size


def _on_step(volume: float, step: float) -> bool:
    ratio = volume / step
    return math.isclose(ratio, round(ratio), abs_tol=1e-6)


class Risk:
    def __init__(self, cfg: RiskConfig) -> None:
        self.cfg = cfg

    def check(self, signal: Signal, ctx: RiskContext) -> RiskDecision:
        cfg = self.cfg
        d = RiskDecision(lot=cfg.lot)
        equity = ctx.account.equity

        numeric_stop = isinstance(signal.stop, (int, float)) and math.isfinite(signal.stop)
        wrong_side = numeric_stop and (
            (signal.side == "buy" and signal.stop >= signal.entry)
            or (signal.side == "sell" and signal.stop <= signal.entry)
        )
        if not numeric_stop or wrong_side:
            d.vetoes.append("no_numeric_stop")

        if cfg.lot > cfg.max_lot:
            d.vetoes.append(f"lot_{cfg.lot}_above_max_{cfg.max_lot}")
        if cfg.lot < ctx.spec.volume_min or not _on_step(cfg.lot, ctx.spec.volume_step):
            d.vetoes.append(f"lot_{cfg.lot}_not_tradable_min_{ctx.spec.volume_min}_step_{ctx.spec.volume_step}")

        if equity <= 0:
            d.vetoes.append("equity_non_positive")
            return d

        if numeric_stop and not wrong_side:
            d.risk_money = risk_money(signal.entry, signal.stop, cfg.lot, ctx.spec.contract_size)
            d.risk_pct = d.risk_money / equity * 100
            if d.risk_pct > cfg.risk_per_trade_pct + 1e-9:
                d.vetoes.append(f"risk_{d.risk_pct:.2f}pct_above_{cfg.risk_per_trade_pct}pct")

        day_cap = ctx.day_start_equity * cfg.daily_loss_pct / 100
        lost_today = max(0.0, ctx.day_start_equity - equity)
        if lost_today >= day_cap:
            d.vetoes.append(f"daily_cap_hit_{lost_today:.2f}_of_{day_cap:.2f}")
        elif lost_today + d.risk_money > day_cap + 1e-9:
            d.vetoes.append(f"daily_cap_would_break_{lost_today + d.risk_money:.2f}_of_{day_cap:.2f}")

        open_tickets = {p.ticket for p in ctx.positions}
        for t in cfg.veto_tickets:
            if t in open_tickets:
                d.vetoes.append(f"veto_ticket_open_#{t}")
        foreign = [p for p in ctx.positions if p.magic != ctx.bot_magic]
        if cfg.veto_if_unmanaged_open and foreign:
            d.vetoes.append("unmanaged_position_open_" + ",".join(f"#{p.ticket}" for p in foreign))
        if any(p.side != signal.side for p in ctx.positions):
            d.vetoes.append("opposite_position_open_no_hedge")
        if any(p.magic == ctx.bot_magic for p in ctx.positions):
            d.vetoes.append("plan_position_already_open")

        if ctx.peak_equity > 0:
            dd = (ctx.peak_equity - equity) / ctx.peak_equity * 100
            if dd >= cfg.peak_dd_warn_pct:
                d.warnings.append(f"peak_drawdown_{dd:.2f}pct")
        return d
