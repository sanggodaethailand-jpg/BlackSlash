"""Adaptive serial-dependence regime (proposed by Codex, round 1; its lowest prior).

On non-overlapping 6H bars (UTC): x_j = ln(C_j / C_(j-1)). Over the last M = 4W returns:
rho = Spearman correlation of (x_t, x_(t-1)); tau = z / sqrt(M). Only when the latest |x| is above
the window's median |x|: rho > tau -> follow the latest 6H move, rho < -tau -> fade it.
Fill at the next H1 open, stop 1.5 x WilderATR24 (H1) from the fill, target 1.5R, time exit
after 6 bars, at most one entry per 24 bars.
"""

from __future__ import annotations

import math
import statistics

from anon.indicators import atr
from anon.lab.core import Entry, Idea
from anon.lab.frames import resample


def _ranks(values: list[float]) -> list[int]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0] * len(values)
    for position, index in enumerate(order):
        ranks[index] = position
    return ranks


def lag1_spearman(window: list[float]) -> float:
    now, before = _ranks(window[1:]), _ranks(window[:-1])
    n = len(now)
    d2 = sum((a - b) ** 2 for a, b in zip(now, before, strict=True))
    return 1 - 6 * d2 / (n * (n * n - 1))


class SerialRegime(Idea):
    name = "serial_regime"
    family = "regime"
    hypothesis = (
        "(Codex) BTC อาจสลับระหว่างช่วงที่ข้อมูลค่อย ๆ ซึมเข้าราคา (ตามได้) กับช่วงที่แรงซื้อขายเร่งรีบถูกผู้ให้สภาพคล่องดูดกลับ (สวนได้) "
        "ผู้เสียเงิน: คนปรับตัวช้าในช่วงแรก และคนใจร้อนในช่วงหลัง "
        "อาจล้มเพราะ: autocorrelation ใกล้ศูนย์และไม่เสถียร, ตัววัดช้าและมี noise ภายใต้หางอ้วน, regime เปลี่ยนก่อนวัดทัน, "
        "edge ระดับ 6 ชม. เล็กกว่าต้นทุน"
    )
    primary = {"w": 60, "z": 1.96}
    grid = {"w": [30, 60, 120], "z": [1.64, 1.96, 2.58]}

    def prepare(self, bars, p):
        six, last_closed = resample(bars, 6)
        m = 4 * p["w"]
        tau = p["z"] / math.sqrt(m)
        x = [0.0] + [math.log(six[j].close / six[j - 1].close) for j in range(1, len(six))]
        direction = {}  # 6H index -> +1 / -1
        closed_upto = last_closed[-1] if last_closed else -1
        for j in range(m, closed_upto + 1):
            window = x[j - m + 1 : j + 1]
            latest = window[-1]
            if abs(latest) <= statistics.median(abs(v) for v in window):
                continue
            rho = lag1_spearman(window)
            if rho > tau:
                direction[j] = 1 if latest > 0 else -1
            elif rho < -tau:
                direction[j] = -1 if latest > 0 else 1
        return last_closed, direction, atr(bars, 24)

    def entry(self, i, bars, state, p):
        last_closed, direction, a24 = state
        j = last_closed[i]
        if i == 0 or j == last_closed[i - 1] or j not in direction:  # act only on the bar that closes a 6H block
            return None
        if bars.last_entry is not None and i + 1 - bars.last_entry < 24:
            return None
        side = "buy" if direction[j] > 0 else "sell"
        return Entry(side, stop_distance=1.5 * a24[i], target_r=1.5, max_hold=6)


IDEA = SerialRegime()
