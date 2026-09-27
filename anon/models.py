"""Plain data types shared across the system."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Side = Literal["buy", "sell"]
Setup = Literal["A", "B"]

H1 = timedelta(hours=1)


@dataclass(frozen=True)
class Bar:
    """One closed H1 candle. ``time`` is the bar open time (UTC, tz-aware); prices are bid."""

    time: datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def close_time(self) -> datetime:
        return self.time + H1


@dataclass(frozen=True)
class Signal:
    """A Ghost call. It only proposes a trade; nothing is sent from here."""

    setup: Setup
    variant: str
    side: Side
    bar_time: datetime
    entry: float
    stop: float
    tp1: float
    outside_gray: bool
    rr: float
    note: str = ""

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.stop)


@dataclass(frozen=True)
class SymbolSpec:
    contract_size: float
    volume_min: float
    volume_step: float


@dataclass(frozen=True)
class Account:
    balance: float
    equity: float
    currency: str = "USD"


@dataclass(frozen=True)
class Position:
    ticket: int
    side: Side
    volume: float
    price_open: float
    sl: float | None
    tp: float | None
    profit: float
    magic: int
    comment: str = ""
    time: datetime | None = None


@dataclass(frozen=True)
class ClosedTrade:
    ticket: int
    side: Side
    volume: float
    price_open: float
    price_close: float
    profit: float  # account currency, net of commission/swap/fees
    time_open: datetime
    time_close: datetime
    magic: int
    exit_reason: str  # "sl" | "tp" | "expert" | "manual" | "stop_out" | "other"
    comment: str = ""
