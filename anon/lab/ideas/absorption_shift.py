"""Absorption, then a shift of market-order flow (proposed by Claude, round 2: "ลุย delta").

Needs exchange klines with market-buy volume (anon binance); pre-registration and the
red-team checklist are in research/delta_plan.md. At the close of bar ``s`` (bar ``a`` = s-1):

- flow share = buy_volume / volume; its z-score uses the mean and sd of the share over the
  WINDOW bars before ``a`` (both a and s are scored against that same baseline)
- long: bar a was sold hard (z_a <= -z) on at least average volume (mean of those WINDOW bars),
  reached the lowest DISCOUNT of the RANGE-bar range and still closed in its upper half
  (sellers absorbed); then bar s was bought (z_s >= SHIFT_Z) and closed above bar a's close
- short: the mirror image (bought hard, reached the top DISCOUNT, closed in its lower half,
  then sold with z_s <= -SHIFT_Z and a lower close)
- stop just beyond the two bars' extreme (BUFFER of the average bar range), target at
  ``target_r`` R, out after MAX_HOLD bars at the latest
"""

from __future__ import annotations

from anon.lab.core import Entry, Idea

WINDOW = 168  # one week of H1 bars: the normal share of market buys and the normal volume
RANGE = 48  # two days: where the absorption bar sits in the recent range
DISCOUNT = 0.3  # the bar reached the lowest 30% of that range (a 70%+ pullback); shorts mirror
HELD = 0.5  # the absorption bar closed in its upper half; shorts: lower half
SHIFT_Z = 0.5  # the next bar leans the other way by at least half a normal sd
RANGE_AVG = 24  # average bar range over a day, for the stop buffer
BUFFER = 0.1  # stop this fraction of the average range beyond the extreme
MAX_HOLD = 24  # a day


class AbsorptionShift(Idea):
    name = "absorption_shift"
    family = "orderflow"
    needs_flow = True
    hypothesis = (
        "(Claude, รอบ 2 จากคลิป order flow) ตอนราคาย่อลึกในกรอบ 2 วัน ถ้ามีคำสั่งขายตลาดหนักผิดปกติ "
        "แต่แท่งกลับปิดครึ่งบน แปลว่ามีคนตั้งรับซื้อดูดไว้หมด พอแท่งถัดไปฝั่งซื้อตลาดเริ่มคุมและปิดสูงกว่า "
        "คนที่ขายไล่ลงจะติดผิดฝั่งและต้องซื้อคืน ราคาจึงเด้ง (ขาขึ้นกลับกัน) "
        "ผู้เสียเงิน: คนขายไล่ราคาและคนที่ถูกบังคับปิดที่ก้นกรอบ (ขาขึ้นคือคนไล่ซื้อที่ยอดกรอบ) "
        "อาจล้มเพราะ: แท่ง H1 หยาบกว่า footprint 5 นาทีมาก, flow ของ Binance spot เป็นแค่ส่วนหนึ่งของตลาด (perp ใหญ่กว่า), "
        "ช่วงค่าธรรมเนียม 0% ปี 2022-2023 ทำให้สัดส่วน taker เพี้ยน, ราคาที่เทรดจริงคือ Exness CFD ไม่ใช่ Binance, "
        "stop แคบทำให้ spread และ slippage กินสัดส่วน R สูง"
    )
    primary = {"z": 2.0, "target_r": 1.5}
    grid = {"z": [1.5, 2.0, 2.5], "target_r": [1.0, 1.5, 2.0]}

    def prepare(self, bars, p):
        """Per bar j, from bars before j only: mean and sd of the buy share, and mean volume;
        plus the average range of the RANGE_AVG bars up to and including j."""
        n = len(bars)
        mean: list[float | None] = [None] * n
        sd: list[float | None] = [None] * n
        vol: list[float | None] = [None] * n
        rng: list[float | None] = [None] * n
        s1 = s2 = vsum = rsum = 0.0
        count = 0
        for j, b in enumerate(bars):
            if j >= WINDOW:
                vol[j] = vsum / WINDOW
                if count >= WINDOW // 2:
                    m = s1 / count
                    var = s2 / count - m * m
                    if var > 1e-12:
                        mean[j], sd[j] = m, var**0.5
            if b.volume > 0:
                share = b.buy_volume / b.volume
                s1, s2, count = s1 + share, s2 + share * share, count + 1
            vsum += b.volume
            rsum += b.high - b.low
            if j >= RANGE_AVG - 1:
                if j >= RANGE_AVG:
                    rsum -= bars[j - RANGE_AVG].high - bars[j - RANGE_AVG].low
                rng[j] = rsum / RANGE_AVG
            if j >= WINDOW:
                old = bars[j - WINDOW]
                if old.volume > 0:
                    share = old.buy_volume / old.volume
                    s1, s2, count = s1 - share, s2 - share * share, count - 1
                vsum -= old.volume
        return mean, sd, vol, rng

    def entry(self, i, bars, state, p):
        mean, sd, vol, rng = state
        a, s = i - 1, i
        if a < max(WINDOW, RANGE) or mean[a] is None or rng[s] is None:
            return None
        bar_a, bar_s = bars[a], bars[s]
        if bar_a.volume <= 0 or bar_s.volume <= 0 or bar_a.volume < vol[a] or bar_a.high <= bar_a.low:
            return None
        z_a = (bar_a.buy_volume / bar_a.volume - mean[a]) / sd[a]
        z_s = (bar_s.buy_volume / bar_s.volume - mean[a]) / sd[a]
        held = (bar_a.close - bar_a.low) / (bar_a.high - bar_a.low)
        window = bars[a - RANGE + 1 : a + 1]
        low, high = min(x.low for x in window), max(x.high for x in window)
        if high <= low:
            return None
        buffer = BUFFER * rng[s]
        if (z_a <= -p["z"] and held >= HELD and (bar_a.low - low) / (high - low) <= DISCOUNT
                and z_s >= SHIFT_Z and bar_s.close > bar_a.close):
            stop = min(bar_a.low, bar_s.low) - buffer
            return Entry("buy", stop=stop, target_r=p["target_r"], max_hold=MAX_HOLD)
        if (z_a >= p["z"] and held <= 1 - HELD and (high - bar_a.high) / (high - low) <= DISCOUNT
                and z_s <= -SHIFT_Z and bar_s.close < bar_a.close):
            stop = max(bar_a.high, bar_s.high) + buffer
            return Entry("sell", stop=stop, target_r=p["target_r"], max_hold=MAX_HOLD)
        return None


IDEA = AbsorptionShift()
