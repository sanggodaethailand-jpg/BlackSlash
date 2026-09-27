"""Configuration for the ANON trading system.

ทุกตัวเลขมาจากใบส่งมอบ ANON ยกเว้นค่าที่ใบส่งมอบไม่ได้กำหนด ซึ่งมีคอมเมนต์ ``ต้องยืนยัน`` กำกับไว้
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class LevelsConfig:
    a_zone_bot: float = 83000.0  # A bot / inv
    a_zone_top: float = 83500.0  # A top
    b_inv_ref: float = 84400.0  # B inv ref — reference only, never used as entry/stop
    gray_center: float = 84700.0  # เทา NO CHASE
    gray_half_width: float = 150.0  # ต้องยืนยัน: ใบส่งมอบให้เทาเป็นเส้นเดียว ไม่มีความกว้าง
    tp1: float = 85500.0
    liq_top: float = 87000.0

    @property
    def gray_low(self) -> float:
        return self.gray_center - self.gray_half_width

    @property
    def gray_high(self) -> float:
        return self.gray_center + self.gray_half_width


@dataclass(frozen=True)
class GhostConfig:
    stop_buffer: float = 50.0  # hard stop sits this far beyond the sweep/swing low
    swing_lookback: int = 6  # bars scanned for the B swing low
    breakout_expiry_bars: int = 12  # B breakout stays armed this many bars
    retest_tolerance: float = 100.0  # retest bar low may sit this far above gray_high
    min_rr: float = 1.0  # ต้องยืนยัน: reward/risk to TP1 must reach at least this


@dataclass(frozen=True)
class RiskConfig:
    lot: float = 0.01
    max_lot: float = 0.01
    risk_per_trade_pct: float = 0.5
    daily_loss_pct: float = 2.0
    peak_dd_warn_pct: float = 5.0
    veto_tickets: tuple[int, ...] = (208190412,)
    veto_if_unmanaged_open: bool = True


@dataclass(frozen=True)
class ZenConfig:
    max_consecutive_off_plan: int = 2
    cooldown_bars_after_loss: int = 3  # ต้องยืนยัน: "ไม่ไล่คืนทุน" as a cooldown
    restore_widened_stop: bool = True


@dataclass(frozen=True)
class OmegaConfig:
    unfavorable_action: Literal["veto", "tag"] = "veto"  # ต้องยืนยัน
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    shock_atr_mult: float = 3.0
    ai_enabled: bool = False
    ai_model: str = "claude-opus-5"
    ai_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    ai_timeout_s: float = 60.0
    ai_max_retries: int = 2
    ai_bars: int = 48
    ai_fail_closed: bool = True


@dataclass(frozen=True)
class ExecutionConfig:
    mode: Literal["backtest", "paper", "live"] = "backtest"
    symbol: str = "BTCUSD"
    magic: int = 7700700
    approval: Literal["manual", "auto"] = "manual"
    live_auto_min_n: int = 30
    live_auto_min_prob: float = 0.9
    dry_run: bool = True
    deviation: int = 50
    server_utc_offset_hours: float = 0.0  # MT5 server clock minus UTC
    day_utc_offset_hours: float = 7.0  # daily caps reset at midnight Asia/Bangkok
    contract_size: float = 1.0  # paper/backtest only; live reads it from the broker
    volume_min: float = 0.01
    volume_step: float = 0.01
    spread: float = 20.0  # paper/backtest only
    starting_balance: float = 1000.0  # paper/backtest only
    journal_path: str = "journal/anon_journal.jsonl"
    state_path: str = "state/anon_state.json"
    usdthb: float | None = None  # only for THB on real P/L; never used to forecast


@dataclass(frozen=True)
class Config:
    levels: LevelsConfig = field(default_factory=LevelsConfig)
    ghost: GhostConfig = field(default_factory=GhostConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    zen: ZenConfig = field(default_factory=ZenConfig)
    omega: OmegaConfig = field(default_factory=OmegaConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    def validate(self) -> None:
        lv, rk = self.levels, self.risk
        if not lv.a_zone_bot < lv.a_zone_top < lv.gray_low < lv.gray_high < lv.tp1:
            raise ValueError("levels must satisfy a_zone_bot < a_zone_top < gray < tp1")
        if lv.gray_half_width <= 0:
            raise ValueError("gray_half_width must be > 0")
        if rk.lot > rk.max_lot:
            raise ValueError(f"lot {rk.lot} exceeds max_lot {rk.max_lot}")
        if not 0 < rk.risk_per_trade_pct <= rk.daily_loss_pct:
            raise ValueError("risk_per_trade_pct must be > 0 and <= daily_loss_pct")
        if self.ghost.min_rr <= 0:
            raise ValueError("min_rr must be > 0")


def _build(cls: type, data: dict[str, Any]) -> Any:
    unknown = set(data) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"unknown keys for [{cls.__name__}]: {sorted(unknown)}")
    defaults = cls()
    kwargs: dict[str, Any] = {}
    for name, value in data.items():
        current = getattr(defaults, name)
        if is_dataclass(current):
            kwargs[name] = _build(type(current), value)
        elif isinstance(current, tuple):
            kwargs[name] = tuple(value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_config(path: str | Path | None) -> Config:
    if path is None:
        cfg = Config()
    else:
        with open(path, "rb") as fh:
            cfg = _build(Config, tomllib.load(fh))
    cfg.validate()
    return cfg
