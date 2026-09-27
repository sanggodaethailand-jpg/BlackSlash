"""Buy the dip in a daily uptrend, sell the rip in a daily downtrend (proposed by Claude, round 1).

- daily trend from closed UTC days: up when EMA20 > EMA50 and the close is above EMA50; down mirrored
- on each closed 4H block (UTC): RSI(2) of 4H closes <= t in an uptrend -> buy; >= 100 - t in a
  downtrend -> sell (the Connors short-term pullback rule)
- stop m x WilderATR(14) of the 4H blocks from the fill; leave at the next open when RSI(2) crosses
  back past 70 (30 for shorts) on a closed 4H block, or after 5 days (120 H1 bars)
"""

from __future__ import annotations

from anon.indicators import atr, ema
from anon.lab.core import EXIT, Entry, Idea
from anon.lab.frames import resample


def wilder_rsi(closes: list[float], period: int) -> list[float]:
    out = [50.0] * len(closes)
    gain = loss = 0.0
    for j in range(1, len(closes)):
        change = closes[j] - closes[j - 1]
        up, down = max(change, 0.0), max(-change, 0.0)
        if j <= period:
            gain, loss = gain + up / period, loss + down / period
        else:
            gain, loss = (gain * (period - 1) + up) / period, (loss * (period - 1) + down) / period
        if j >= period:
            out[j] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


class PullbackTrend(Idea):
    name = "pullback_trend"
    family = "trend+reversal"
    hypothesis = (
        "(Claude) ในเทรนด์รายวัน การย่อแรงระยะสั้นมักเป็นการตกใจเกินเหตุ คนตามเทรนด์และผู้ให้สภาพคล่องรับซื้อ ราคาจึงเด้งกลับทางเทรนด์ "
        "ผู้เสียเงิน: คนขายตื่นตระหนกตอนย่อ (ขาลงคือคนไล่ซื้อตอนเด้ง) "
        "อาจล้มเพราะ: การย่ออาจเป็นจุดเริ่มเทรนด์ใหม่, เทรนด์รายวันจาก EMA ช้าเกินไป, ต้นทุนเทียบกับการเด้งระยะสั้นสูง"
    )
    primary = {"t": 10, "m": 2.0}
    grid = {"t": [5, 10, 20], "m": [1.5, 2.0, 3.0]}

    def prepare(self, bars, p):
        days, day_closed = resample(bars, 24)
        blocks, block_closed = resample(bars, 4)
        day_closes = [d.close for d in days]
        e20, e50 = ema(day_closes, 20), ema(day_closes, 50)
        trend = [
            (1 if e20[d] > e50[d] and day_closes[d] > e50[d] else -1 if e20[d] < e50[d] and day_closes[d] < e50[d] else 0)
            if d >= 50 else 0
            for d in range(len(days))
        ]
        return day_closed, trend, block_closed, wilder_rsi([b.close for b in blocks], 2), atr(blocks, 14)

    def entry(self, i, bars, state, p):
        day_closed, trend, block_closed, rsi, a4 = state
        k = block_closed[i]
        if i == 0 or k == block_closed[i - 1] or k < 20 or day_closed[i] < 0:
            return None
        direction = trend[day_closed[i]]
        if direction > 0 and rsi[k] <= p["t"]:
            return Entry("buy", stop_distance=p["m"] * a4[k], max_hold=120)
        if direction < 0 and rsi[k] >= 100 - p["t"]:
            return Entry("sell", stop_distance=p["m"] * a4[k], max_hold=120)
        return None

    def update(self, i, bars, state, p, trade):
        _, _, block_closed, rsi, _ = state
        k = block_closed[i]
        if i == 0 or k == block_closed[i - 1] or k < 0:
            return None
        if (trade.side == "buy" and rsi[k] > 70) or (trade.side == "sell" and rsi[k] < 30):
            return EXIT
        return None


IDEA = PullbackTrend()
