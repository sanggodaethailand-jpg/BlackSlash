"""Squeeze -> impulse -> continuation (proposed by Codex, round 1).

At the close of H1 bar t, with A = SMA(TR,168) and a = SMA(TR,12), both at t-1:
- skip when A < 10 x spread; compression: a / A <= q
- expansion: the bar opened inside [LL_N, HH_N] of the previous N bars and TR_t >= e x A
- long: close > HH_N + 0.05A and close location value >= 0.75; short mirrored (<= 0.25)
- fill at the next open unless it gapped more than 0.25A from the close
- stop 1.5A from the fill, target 4R, time exit after 48 bars, 12-bar cooldown after an exit
- after 6 bars held: chandelier stop, extreme since entry -/+ 2.5 x WilderATR(24)
"""

from __future__ import annotations

from anon.indicators import atr, true_ranges
from anon.lab.core import Entry, Idea


def rolling_mean(values: list[float], n: int) -> list[float]:
    out, total = [], 0.0
    for j, v in enumerate(values):
        total += v
        if j >= n:
            total -= values[j - n]
        out.append(total / min(j + 1, n))
    return out


def _extreme_since_entry(bars, i, trade, high: bool) -> float:
    memo = trade.memo
    if memo.get("i") == i - 1:
        value = max(memo["x"], bars[i].high) if high else min(memo["x"], bars[i].low)
    else:
        window = bars[trade.entry_index : i + 1]
        value = max(b.high for b in window) if high else min(b.low for b in window)
    memo["i"], memo["x"] = i, value
    return value


class SqueezeImpulse(Idea):
    name = "squeeze_impulse"
    family = "volatility"
    hypothesis = (
        "(Codex) หลังความผันผวนหดตัว คนเล่นกรอบและคนขาย volatility สะสมสถานะและจุด stop ไว้ พอมีแท่งระเบิดที่ปิดใกล้ปลายแท่ง "
        "การปิดสถานะและราคาที่ปรับตัวช้าอาจพาวิ่งต่อ ผู้เสียเงิน: คนเล่นกรอบที่สวนเร็ว และคนที่โดน stop นอกกรอบ "
        "อาจล้มเพราะ: ความผันผวนต่อเนื่องไม่ได้แปลว่าทิศต่อเนื่อง, แท่งระเบิดอาจเป็นปลายทาง, เบรกหลอก, spread กว้างตอนเกิดสัญญาณ, "
        "กำไรกระจุกในเหตุการณ์ล้างพอร์ตไม่กี่ครั้ง"
    )
    primary = {"n": 24, "q": 0.60, "e": 1.60}
    grid = {"n": [12, 24], "q": [0.60, 0.75], "e": [1.25, 1.60]}

    def prepare(self, bars, p):
        tr = true_ranges(bars)
        return tr, rolling_mean(tr, 168), rolling_mean(tr, 12), atr(bars, 24)

    def entry(self, i, bars, state, p):
        tr, slow, fast, _ = state
        n = p["n"]
        if i < 168 + n + 1:
            return None
        if bars.last_exit is not None and i - bars.last_exit < 12:
            return None
        big, small = slow[i - 1], fast[i - 1]
        if big < 10 * bars.spread or small / big > p["q"] or tr[i] < p["e"] * big:
            return None
        b = bars[i]
        window = bars[i - n : i]
        hh, ll = max(x.high for x in window), min(x.low for x in window)
        if not ll <= b.open <= hh or b.high <= b.low:
            return None
        clv = (b.close - b.low) / (b.high - b.low)
        common = {"stop_distance": 1.5 * big, "target_r": 4.0, "max_hold": 48, "max_gap": 0.25 * big}
        if b.close > hh + 0.05 * big and clv >= 0.75:
            return Entry("buy", **common)
        if b.close < ll - 0.05 * big and clv <= 0.25:
            return Entry("sell", **common)
        return None

    def update(self, i, bars, state, p, trade):
        a24 = state[3]
        high = trade.side == "buy"
        extreme = _extreme_since_entry(bars, i, trade, high)  # keep the running value current every bar
        if i - trade.entry_index + 1 < 6:
            return None
        return extreme - 2.5 * a24[i] if high else extreme + bars.spread + 2.5 * a24[i]


IDEA = SqueezeImpulse()
