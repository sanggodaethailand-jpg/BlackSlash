"""MT5 adapter against a fake MetaTrader5 module (the real one is Windows-only)."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from anon.broker.mt5 import MT5Broker
from anon.config import ExecutionConfig

EPOCH = int(datetime(2026, 9, 27, 12, tzinfo=UTC).timestamp())


class FakeMT5:
    TIMEFRAME_H1 = 16385
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self):
        self.sent, self.rates_args = [], None
        self.info = SimpleNamespace(trade_contract_size=1.0, volume_min=0.01, volume_step=0.01, digits=2, filling_mode=2)

    def copy_rates_from_pos(self, symbol, tf, start, count):
        self.rates_args = (symbol, tf, start, count)
        return [{"time": EPOCH + 3 * 3600, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]

    def symbol_info(self, symbol):
        return self.info

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=84600.0, ask=84620.0)

    def account_info(self):
        return SimpleNamespace(balance=1000.0, equity=990.0, currency="USD")

    def positions_get(self, symbol=None):
        return (SimpleNamespace(ticket=208190412, type=1, volume=0.01, price_open=84000.0, sl=0.0, tp=0.0,
                                profit=-6.2, magic=0, comment="", time=EPOCH),)

    def order_send(self, request):
        self.sent.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=777, comment="done")

    def history_deals_get(self, *args, position=None):
        d_in = SimpleNamespace(position_id=9, symbol="BTCUSD", entry=0, type=0, volume=0.01, price=83400.0,
                               profit=0.0, commission=-0.1, swap=0.0, fee=0.0, time=EPOCH, magic=7700700,
                               reason=3, comment="ANON #T01 A")
        d_out = replace_ns(d_in, entry=1, type=1, price=85500.0, profit=21.0, commission=0.0, time=EPOCH + 7200, reason=5)
        return (d_in, d_out) if position == 9 else (d_out,)

    def last_error(self):
        return (0, "ok")


def replace_ns(ns, **kw):
    return SimpleNamespace(**{**vars(ns), **kw})


def broker(dry_run=True, offset=3.0):
    return MT5Broker(replace(ExecutionConfig(), dry_run=dry_run, server_utc_offset_hours=offset), FakeMT5())


def test_bars_skip_forming_bar_and_convert_server_time():
    b = broker()
    bars = b.closed_bars(300)
    assert b.mt5.rates_args[2] == 1  # start at 1 = last closed bar
    assert bars[0].time == datetime(2026, 9, 27, 12, tzinfo=UTC)  # server UTC+3 → UTC


def test_dry_run_never_calls_order_send():
    b = broker(dry_run=True)
    assert b.open_market("buy", 0.01, 82950.123, 85500.0, 7700700, "ANON #T01 A") is None
    assert b.mt5.sent == [] and len(b.sent) == 1


def test_live_request_shape():
    b = broker(dry_run=False)
    assert b.open_market("buy", 0.01, 82950.123, 85500.0, 7700700, "ANON #T01 A") == 777
    req = b.mt5.sent[0]
    assert req["price"] == 84620.0 and req["sl"] == 82950.12 and req["tp"] == 85500.0
    assert req["type_filling"] == FakeMT5.ORDER_FILLING_IOC and req["magic"] == 7700700


def test_failed_send_raises():
    b = broker(dry_run=False)
    b.mt5.order_send = lambda r: SimpleNamespace(retcode=10019, order=0, comment="no money")
    with pytest.raises(RuntimeError, match="10019"):
        b.open_market("buy", 0.01, 82950, 85500, 1, "x")


def test_positions_and_closed_trades_mapping():
    b = broker()
    (p,) = b.positions()
    assert p.ticket == 208190412 and p.side == "sell" and p.sl is None and p.magic == 0
    (c,) = b.closed_since(datetime(2026, 9, 27, tzinfo=UTC))
    assert c.ticket == 9 and c.exit_reason == "tp" and c.side == "buy"
    assert c.profit == pytest.approx(20.9)
    assert (c.time_close - c.time_open).total_seconds() == 7200
