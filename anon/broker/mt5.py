"""MetaTrader 5 adapter (Windows, needs the ``MetaTrader5`` package and a running terminal).

Dry-run is the default: requests are built and logged but never sent. Credentials
come from the environment (MT5_LOGIN / MT5_PASSWORD / MT5_SERVER / MT5_PATH),
never from the config file.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from anon.config import ExecutionConfig
from anon.models import Account, Bar, ClosedTrade, Position, Side, SymbolSpec

log = logging.getLogger(__name__)

_REASONS = {0: "manual", 1: "manual", 2: "manual", 3: "expert", 4: "sl", 5: "tp", 6: "stop_out"}
_ENTRY_IN, _ENTRY_OUT, _ENTRY_OUT_BY = 0, 1, 3


class MT5Broker:
    def __init__(self, cfg: ExecutionConfig, mt5_module=None) -> None:
        if mt5_module is None:
            import MetaTrader5 as mt5_module  # type: ignore[import-not-found]
        self.mt5 = mt5_module
        self.cfg = cfg
        self.symbol = cfg.symbol
        self.offset = timedelta(hours=cfg.server_utc_offset_hours)
        self.sent: list[dict] = []  # every request, sent or dry-run, for audit

    # --- session ---------------------------------------------------------
    def connect(self) -> None:
        mt5 = self.mt5
        kwargs = {}
        if os.environ.get("MT5_LOGIN"):
            kwargs = {
                "login": int(os.environ["MT5_LOGIN"]),
                "password": os.environ.get("MT5_PASSWORD", ""),
                "server": os.environ.get("MT5_SERVER", ""),
            }
        path = os.environ.get("MT5_PATH")
        ok = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
        if not ok:
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
        if not mt5.symbol_select(self.symbol, True):
            raise RuntimeError(f"cannot select {self.symbol}: {mt5.last_error()}")

    def shutdown(self) -> None:
        self.mt5.shutdown()

    # --- time ------------------------------------------------------------
    def _to_utc(self, server_epoch: int) -> datetime:
        return datetime.fromtimestamp(int(server_epoch), tz=UTC) - self.offset

    def _to_server_epoch(self, dt: datetime) -> int:
        return int((dt + self.offset).timestamp())

    # --- market data -----------------------------------------------------
    def closed_bars(self, count: int) -> list[Bar]:
        """Closed H1 bars only: position 0 is the forming bar, so start at 1."""
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_H1, 1, count)
        if rates is None:
            raise RuntimeError(f"copy_rates_from_pos failed: {self.mt5.last_error()}")
        return [
            Bar(self._to_utc(r["time"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            for r in rates
        ]

    def spec(self) -> SymbolSpec:
        info = self.mt5.symbol_info(self.symbol)
        if info is None:
            raise RuntimeError(f"symbol_info failed: {self.mt5.last_error()}")
        return SymbolSpec(float(info.trade_contract_size), float(info.volume_min), float(info.volume_step))

    def quote(self) -> tuple[float, float]:
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            raise RuntimeError(f"symbol_info_tick failed: {self.mt5.last_error()}")
        return float(tick.bid), float(tick.ask)

    # --- account ---------------------------------------------------------
    def account(self) -> Account:
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError(f"account_info failed: {self.mt5.last_error()}")
        return Account(float(info.balance), float(info.equity), str(info.currency))

    def positions(self) -> list[Position]:
        mt5 = self.mt5
        out = []
        for p in mt5.positions_get(symbol=self.symbol) or ():
            out.append(
                Position(
                    ticket=int(p.ticket),
                    side="buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
                    volume=float(p.volume),
                    price_open=float(p.price_open),
                    sl=float(p.sl) or None,
                    tp=float(p.tp) or None,
                    profit=float(p.profit),
                    magic=int(p.magic),
                    comment=str(p.comment),
                    time=self._to_utc(p.time),
                )
            )
        return out

    def closed_since(self, since: datetime) -> list[ClosedTrade]:
        mt5 = self.mt5
        now = datetime.now(UTC) + timedelta(days=1)
        deals = mt5.history_deals_get(self._to_server_epoch(since), self._to_server_epoch(now)) or ()
        position_ids = {
            int(d.position_id)
            for d in deals
            if d.symbol == self.symbol and d.entry in (_ENTRY_OUT, _ENTRY_OUT_BY)
        }
        out = []
        for pid in sorted(position_ids):
            all_deals = mt5.history_deals_get(position=pid) or ()
            ins = [d for d in all_deals if d.entry == _ENTRY_IN]
            outs = [d for d in all_deals if d.entry in (_ENTRY_OUT, _ENTRY_OUT_BY)]
            if not ins or not outs:
                continue
            first, last = ins[0], outs[-1]
            net = sum(
                float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, "fee", 0.0))
                for d in all_deals
            )
            out.append(
                ClosedTrade(
                    ticket=pid,
                    side="buy" if first.type == mt5.DEAL_TYPE_BUY else "sell",
                    volume=float(first.volume),
                    price_open=float(first.price),
                    price_close=float(last.price),
                    profit=net,
                    time_open=self._to_utc(first.time),
                    time_close=self._to_utc(last.time),
                    magic=int(first.magic),
                    exit_reason=_REASONS.get(int(last.reason), "other"),
                    comment=str(first.comment),
                )
            )
        return out

    # --- orders ----------------------------------------------------------
    def _filling(self) -> int:
        mt5 = self.mt5
        mode = int(self.mt5.symbol_info(self.symbol).filling_mode)
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict) -> int | None:
        self.sent.append(request)
        if self.cfg.dry_run:
            log.warning("DRY-RUN (not sent): %s", request)
            return None
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            detail = self.mt5.last_error() if result is None else f"{result.retcode} {result.comment}"
            raise RuntimeError(f"order_send failed: {detail}")
        return int(result.order)

    def _digits(self) -> int:
        return int(self.mt5.symbol_info(self.symbol).digits)

    def open_market(self, side: Side, volume: float, sl: float, tp: float, magic: int, comment: str) -> int | None:
        mt5 = self.mt5
        bid, ask = self.quote()
        nd = self._digits()
        return self._send(
            {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
                "price": ask if side == "buy" else bid,
                "sl": round(sl, nd),
                "tp": round(tp, nd),
                "deviation": self.cfg.deviation,
                "magic": magic,
                "comment": comment[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(),
            }
        )

    def close_position(self, ticket: int) -> bool:
        mt5 = self.mt5
        pos = next((p for p in self.positions() if p.ticket == ticket), None)
        if pos is None:
            return False
        bid, ask = self.quote()
        self._send(
            {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "position": ticket,
                "volume": pos.volume,
                "type": mt5.ORDER_TYPE_SELL if pos.side == "buy" else mt5.ORDER_TYPE_BUY,
                "price": bid if pos.side == "buy" else ask,
                "deviation": self.cfg.deviation,
                "magic": pos.magic,
                "comment": "anon thesis exit",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(),
            }
        )
        return True

    def modify_sl(self, ticket: int, sl: float) -> bool:
        pos = next((p for p in self.positions() if p.ticket == ticket), None)
        if pos is None:
            return False
        self._send(
            {
                "action": self.mt5.TRADE_ACTION_SLTP,
                "symbol": self.symbol,
                "position": ticket,
                "sl": round(sl, self._digits()),
                "tp": pos.tp or 0.0,
            }
        )
        return True
