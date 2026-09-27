"""Slow hysteretic EMA time-series momentum (proposed by Codex, round 1).

At the close of H1 bar t: d = (EMA_fast - EMA_slow) / WilderATR(48).
- long when d crosses up through h, short when it crosses down through -h; only the first
  crossing after d was last on the other side of zero (a stopped-out trade waits for d to
  cross zero again)
- initial stop 3 x ATR48 beyond the bid (long) / ask (short) at the fill
- trail after every close: highest bid high since entry - 3 x ATR48 (long), lowest ask low + 3 x ATR48 (short)
- leave at the next open once d is back on the other side of zero
Periods are in H1 bars: 168/672 = 7/28 days, 336/1344 = 14/56 days, 504/2016 = 21/84 days.
"""

from __future__ import annotations

from anon.indicators import atr, ema
from anon.lab.core import EXIT, Entry, Idea


def _extreme_since_entry(bars, i, trade, high: bool) -> float:
    memo = trade.memo
    if memo.get("i") == i - 1:
        value = max(memo["x"], bars[i].high) if high else min(memo["x"], bars[i].low)
    else:
        window = bars[trade.entry_index : i + 1]
        value = max(b.high for b in window) if high else min(b.low for b in window)
    memo["i"], memo["x"] = i, value
    return value


class TsmomSlow(Idea):
    name = "tsmom_slow"
    family = "trend"
    hypothesis = (
        "(Codex) ข้อมูลและเงินก้อนใหญ่สะท้อนเข้าราคาช้า รวมกับ herding และการถูกบังคับลดสถานะ ทำให้เทรนด์ 1–3 เดือนต่อเนื่อง "
        "ผู้เสียเงิน: คนสวนเทรนด์ คนที่ถูกบังคับปิดสถานะ และผู้ให้สภาพคล่องที่รับ order flow ต่อเนื่อง "
        "อาจล้มเพราะ: BTC มีแค่ความผันผวนเป็นกลุ่มแต่ไม่มีทิศที่ต่อเนื่อง, แพ้ V-reversal และตลาดไซด์เวย์, "
        "ผลมาจากวัฏจักรขาขึ้น/ขาลงแค่ 1–2 รอบ, swap ของฝั่ง long"
    )
    primary = {"ema": (336, 1344), "h": 0.5}
    grid = {"ema": [(168, 672), (336, 1344), (504, 2016)], "h": [0.25, 0.5, 0.75]}

    def prepare(self, bars, p):
        fast, slow = p["ema"]
        h = p["h"]
        closes = [b.close for b in bars]
        a = atr(bars, 48)
        d = [(f - s) / x if x > 0 else 0.0 for f, s, x in zip(ema(closes, fast), ema(closes, slow), a, strict=True)]
        go_long, go_short = [False] * len(bars), [False] * len(bars)
        armed_long = armed_short = True
        for t in range(1, len(bars)):
            if d[t] <= 0:
                armed_long = True
            if d[t] >= 0:
                armed_short = True
            if t < slow:
                continue
            if armed_long and d[t] >= h > d[t - 1]:
                go_long[t], armed_long = True, False
            if armed_short and d[t] <= -h < d[t - 1]:
                go_short[t], armed_short = True, False
        return d, go_long, go_short, a

    def entry(self, i, bars, state, p):
        _, go_long, go_short, a = state
        if go_long[i]:
            return Entry("buy", stop_distance=3 * a[i] + bars.spread)
        if go_short[i]:
            return Entry("sell", stop_distance=3 * a[i] + bars.spread)
        return None

    def update(self, i, bars, state, p, trade):
        d, _, _, a = state
        if (trade.side == "buy" and d[i] <= 0) or (trade.side == "sell" and d[i] >= 0):
            return EXIT
        if trade.side == "buy":
            return _extreme_since_entry(bars, i, trade, high=True) - 3 * a[i]
        return _extreme_since_entry(bars, i, trade, high=False) + bars.spread + 3 * a[i]


IDEA = TsmomSlow()
