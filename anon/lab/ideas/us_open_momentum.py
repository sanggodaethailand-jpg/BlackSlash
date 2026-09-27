"""US-session intraday momentum (proposed by Claude, round 1: the time-of-day idea).

New York weekdays only. The first w H1 bars from the 09:00 New York bar (the one holding the
09:30 cash open) set the direction: close of the last of them minus open of the first.
Enter that way at the next open, stop m x WilderATR(24) from the fill, and leave at the close
of the 15:00 New York bar (the cash close), before the 17:00 swap rollover.
"""

from __future__ import annotations

from datetime import date, timedelta

from anon.indicators import atr
from anon.lab.core import Entry, Idea


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def new_york_hour(t) -> tuple[date, int]:
    """(New York date, hour) of a UTC bar time, US daylight saving by date."""
    day = t.date()
    dst = _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)
    local = t - timedelta(hours=4 if dst else 5)
    return local.date(), local.hour


class UsOpenMomentum(Idea):
    name = "us_open_momentum"
    family = "intraday"
    hypothesis = (
        "(Claude) เงินจากฝั่งสหรัฐฯ (ETF, กองทุน, คนเทรดหุ้น) เข้ามาหลังตลาดหุ้นเปิด ทิศของชั่วโมงแรกมักถูกตามต่อทั้งช่วงบ่าย "
        "เหมือน intraday momentum ในหุ้น ผู้เสียเงิน: คนที่สวนทิศช่วงเปิดและคนปรับสถานะช้าก่อนตลาดปิด "
        "อาจล้มเพราะ: ผลตามเวลาในคริปโตไม่คงทนข้ามช่วงเวลา (Codex เตือนไว้), BTC เทรด 24 ชม. เงินสหรัฐฯ อาจไม่ใช่แรงหลัก, "
        "การเคลื่อนไหวชั่วโมงเดียวเล็กเมื่อเทียบกับ spread และ stop"
    )
    primary = {"w": 1, "m": 1.5}
    grid = {"w": [1, 2, 3], "m": [1.0, 1.5, 2.0]}

    def prepare(self, bars, p):
        return [new_york_hour(b.time) for b in bars], atr(bars, 24)

    def entry(self, i, bars, state, p):
        clock, a24 = state
        w = p["w"]
        day, hour = clock[i]
        if day.weekday() >= 5 or hour != 9 + w - 1 or i < w + 24:
            return None
        first = i - w + 1
        if clock[first] != (day, 9):  # the window must be complete on the same New York day
            return None
        move = bars[i].close - bars[first].open
        if move == 0:
            return None
        return Entry("buy" if move > 0 else "sell", stop_distance=p["m"] * a24[i], max_hold=16 - (9 + w))


IDEA = UsOpenMomentum()
