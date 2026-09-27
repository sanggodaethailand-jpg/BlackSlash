"""Liquidation-shock exhaustion reversal (proposed by Codex, round 1).

At the close of H1 bar t with lookback k:
- A0 = median(TR over the 24 bars t-k-23 .. t-k); I = C_t - C_(t-k)
- a shock: max TR over t-k+1 .. t >= 2 x A0
- long: I <= -z x A0 x sqrt(k), the bar closed up (C > O) in its top 30% (CLV >= 0.70) and its low
  is the lowest of the last k bars; short mirrored
- stop 0.25 x A0 beyond the bar's extreme, target 1.5R, time exit after 24 bars,
  at most one entry per 24 bars; a gap past the stop skips the trade
"""

from __future__ import annotations

import math
import statistics

from anon.indicators import true_ranges
from anon.lab.core import Entry, Idea


class LiquidationReversal(Idea):
    name = "liquidation_reversal"
    family = "reversal"
    hypothesis = (
        "(Codex) การล้างพอร์ตและคำสั่งตื่นตระหนกมีแรงกดราคาชั่วคราว พอแรงบังคับหมด ผู้ให้สภาพคล่องดันราคากลับ "
        "ผู้เสียเงิน: คนที่ถูกล้างพอร์ตและคนไล่ราคาช่วงท้าย (ไม่สวนทันที รอแท่งกลับตัวปิดก่อน) "
        "อาจล้มเพราะ: แรงกระแทกมาจากข่าวจริงแล้ววิ่งต่อ, H1 มองไม่เห็นลำดับในแท่ง, spread แย่สุดตรงจุดเข้า, "
        "ฝั่ง long/short ไม่สมมาตรเพราะ drift และ short squeeze"
    )
    primary = {"k": 8, "z": 2.0}
    grid = {"k": [4, 8, 12], "z": [1.5, 2.0, 2.5]}

    def prepare(self, bars, p):
        return true_ranges(bars)

    def entry(self, i, bars, state, p):
        tr, k = state, p["k"]
        if i < k + 24:
            return None
        if bars.last_entry is not None and i + 1 - bars.last_entry < 24:
            return None
        b = bars[i]
        if b.high <= b.low:
            return None
        clv = (b.close - b.low) / (b.high - b.low)
        up, down = b.close > b.open and clv >= 0.70, b.close < b.open and clv <= 0.30
        if not (up or down):
            return None
        a0 = statistics.median(tr[i - k - 23 : i - k + 1])
        if a0 <= 0 or max(tr[i - k + 1 : i + 1]) < 2 * a0:
            return None
        impulse, limit = b.close - bars[i - k].close, p["z"] * a0 * math.sqrt(k)
        recent = bars[i - k + 1 : i + 1]
        if up and impulse <= -limit and b.low <= min(x.low for x in recent):
            return Entry("buy", stop=b.low - 0.25 * a0, target_r=1.5, max_hold=24)
        if down and impulse >= limit and b.high >= max(x.high for x in recent):
            return Entry("sell", stop=b.high + 0.25 * a0, target_r=1.5, max_hold=24)
        return None


IDEA = LiquidationReversal()
