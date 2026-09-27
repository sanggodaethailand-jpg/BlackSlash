"""Daily Donchian breakout, both ways (proposed by Claude, round 1; the classic 20/10 turtle rule).

Once a UTC day has closed:
- long when the day closed above the highest high of the previous N days, short when it closed
  below the lowest low of the previous N days
- the stop is the M-day channel on the other side (lowest low / highest high of the last M days,
  today included); it trails with that channel after every daily close and is the only exit
"""

from __future__ import annotations

from anon.lab.core import Entry, Idea
from anon.lab.frames import resample


class DonchianD1(Idea):
    name = "donchian_d1"
    family = "trend"
    hypothesis = (
        "(Claude) เงินก้อนใหญ่และคนตามเทรนด์เข้าช้าเป็นสัปดาห์ ราคาที่ทะลุกรอบหลายสัปดาห์จึงมักไปต่อ "
        "ผู้เสียเงิน: คนขายสวนตอนทำ high ใหม่และคนที่ short ช้า (ขาลงก็กลับกัน) "
        "อาจล้มเพราะ: เบรกหลอกบ่อยในช่วงไซด์เวย์, stop ตามกรอบอยู่ไกลและคืนกำไรเยอะ, swap ของ long ที่ถือหลายสัปดาห์, "
        "ราคากระโดดข้าม stop"
    )
    primary = {"n": 20, "m": 10}
    grid = {"n": [20, 40, 55], "m": [10, 15, 20]}

    def prepare(self, bars, p):
        days, last_closed = resample(bars, 24)
        return days, last_closed

    def _new_day(self, i, last_closed):
        return i > 0 and last_closed[i] != last_closed[i - 1] and last_closed[i] >= 0

    def entry(self, i, bars, state, p):
        days, last_closed = state
        if not self._new_day(i, last_closed):
            return None
        d, n, m = last_closed[i], p["n"], p["m"]
        if d < n:
            return None
        before = days[d - n : d]
        channel = days[d - m + 1 : d + 1]
        close = days[d].close
        if close > max(x.high for x in before):
            return Entry("buy", stop=min(x.low for x in channel))
        if close < min(x.low for x in before):
            return Entry("sell", stop=max(x.high for x in channel))
        return None

    def update(self, i, bars, state, p, trade):
        days, last_closed = state
        if not self._new_day(i, last_closed):
            return None
        d, m = last_closed[i], p["m"]
        channel = days[max(0, d - m + 1) : d + 1]
        return min(x.low for x in channel) if trade.side == "buy" else max(x.high for x in channel)


IDEA = DonchianD1()
