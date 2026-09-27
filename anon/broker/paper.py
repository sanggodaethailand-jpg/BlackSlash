"""Simulated broker driven by H1 bars (bid prices).

Market orders fill at the next bar's open (buy at ask = bid + spread). SL/TP are
checked against each bar's range; when one bar touches both, SL wins. Hit times
are stamped at the bar close, so holding minutes have one-bar resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from anon.models import Account, Bar, ClosedTrade, Position, Side, SymbolSpec


@dataclass
class _Pending:
    ticket: int
    side: Side
    volume: float
    sl: float | None
    tp: float | None
    magic: int
    comment: str


class PaperBroker:
    def __init__(self, spec: SymbolSpec, balance: float, spread: float, currency: str = "USD") -> None:
        self._spec = spec
        self.balance = balance
        self.spread = spread
        self.currency = currency
        self._open: dict[int, Position] = {}
        self._pending_open: list[_Pending] = []
        self._pending_close: list[int] = []
        self._closed: list[ClosedTrade] = []
        self._bid: float | None = None
        self._next_ticket = 1001

    # --- Broker protocol -------------------------------------------------
    def spec(self) -> SymbolSpec:
        return self._spec

    def account(self) -> Account:
        return Account(self.balance, self.balance + sum(self._unrealized(p) for p in self._open.values()), self.currency)

    def positions(self) -> list[Position]:
        return [replace(p, profit=self._unrealized(p)) for p in self._open.values()]

    def quote(self) -> tuple[float, float]:
        if self._bid is None:
            raise RuntimeError("no price yet")
        return self._bid, self._bid + self.spread

    def closed_since(self, since: datetime) -> list[ClosedTrade]:
        return [c for c in self._closed if c.time_close >= since]

    def open_market(self, side: Side, volume: float, sl: float, tp: float, magic: int, comment: str) -> int:
        ticket = self._take_ticket()
        self._pending_open.append(_Pending(ticket, side, volume, sl, tp, magic, comment))
        return ticket

    def close_position(self, ticket: int) -> bool:
        if ticket not in self._open:
            return False
        self._pending_close.append(ticket)
        return True

    def modify_sl(self, ticket: int, sl: float) -> bool:
        pos = self._open.get(ticket)
        if pos is None:
            return False
        self._open[ticket] = replace(pos, sl=sl)
        return True

    # --- simulation ------------------------------------------------------
    def add_foreign_position(
        self, ticket: int, side: Side, volume: float, price_open: float, magic: int = 0, sl: float | None = None
    ) -> None:
        """Inject a manually opened position (e.g. #208190412) for tests and drills."""
        self._open[ticket] = Position(ticket, side, volume, price_open, sl, None, 0.0, magic, "manual")

    def process_bar(self, bar: Bar) -> None:
        for ticket in self._pending_close:
            if ticket in self._open:
                self._settle(self._open[ticket], self._exit_price(self._open[ticket].side, bar.open), bar.time, "expert")
        self._pending_close.clear()

        for o in self._pending_open:
            price = bar.open + self.spread if o.side == "buy" else bar.open
            self._open[o.ticket] = Position(o.ticket, o.side, o.volume, price, o.sl, o.tp, 0.0, o.magic, o.comment, bar.time)
        self._pending_open.clear()

        for pos in list(self._open.values()):
            hit = self._sl_tp_hit(pos, bar)
            if hit:
                self._settle(pos, hit[0], bar.close_time, hit[1])
        self._bid = bar.close

    # --- internals -------------------------------------------------------
    def _take_ticket(self) -> int:
        t = self._next_ticket
        self._next_ticket += 1
        return t

    def _exit_price(self, side: Side, bid: float) -> float:
        return bid if side == "buy" else bid + self.spread

    def _sl_tp_hit(self, pos: Position, bar: Bar) -> tuple[float, str] | None:
        s = 0.0 if pos.side == "buy" else self.spread
        o, h, lo = bar.open + s, bar.high + s, bar.low + s
        if pos.side == "buy":
            if pos.sl is not None and o <= pos.sl:
                return o, "sl"
            if pos.sl is not None and lo <= pos.sl:
                return pos.sl, "sl"
            if pos.tp is not None and o >= pos.tp:
                return o, "tp"
            if pos.tp is not None and h >= pos.tp:
                return pos.tp, "tp"
        else:
            if pos.sl is not None and o >= pos.sl:
                return o, "sl"
            if pos.sl is not None and h >= pos.sl:
                return pos.sl, "sl"
            if pos.tp is not None and o <= pos.tp:
                return o, "tp"
            if pos.tp is not None and lo <= pos.tp:
                return pos.tp, "tp"
        return None

    def _pnl(self, pos: Position, exit_price: float) -> float:
        move = exit_price - pos.price_open if pos.side == "buy" else pos.price_open - exit_price
        return move * pos.volume * self._spec.contract_size

    def _unrealized(self, pos: Position) -> float:
        if self._bid is None:
            return 0.0
        return self._pnl(pos, self._exit_price(pos.side, self._bid))

    def _settle(self, pos: Position, price: float, when: datetime, reason: str) -> None:
        profit = self._pnl(pos, price)
        self.balance += profit
        del self._open[pos.ticket]
        self._closed.append(
            ClosedTrade(
                ticket=pos.ticket,
                side=pos.side,
                volume=pos.volume,
                price_open=pos.price_open,
                price_close=price,
                profit=profit,
                time_open=pos.time or when,
                time_close=when,
                magic=pos.magic,
                exit_reason=reason,
                comment=pos.comment,
            )
        )
