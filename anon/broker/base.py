from __future__ import annotations

from datetime import datetime
from typing import Protocol

from anon.models import Account, ClosedTrade, Position, Side, SymbolSpec


class Broker(Protocol):
    """What the engine needs from a broker. SL/TP always live on the broker side,
    so an open position stays protected even if this program stops."""

    def spec(self) -> SymbolSpec: ...

    def account(self) -> Account: ...

    def positions(self) -> list[Position]: ...

    def quote(self) -> tuple[float, float]: ...

    def closed_since(self, since: datetime) -> list[ClosedTrade]: ...

    def open_market(
        self, side: Side, volume: float, sl: float, tp: float, magic: int, comment: str
    ) -> int | None: ...

    def close_position(self, ticket: int) -> bool: ...

    def modify_sl(self, ticket: int, sl: float) -> bool: ...
