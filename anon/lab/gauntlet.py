"""The five gates every idea walks through, in order; the first failure ends the walk.

1. registered in the ledger before any result is seen (k = attempts so far)
2. history split: the oldest part for development, the newest part locked away
3. plateau on the development part: the pre-registered setting and most neighbours positive
4. beats shuffled history (same candles, random order) at p <= 0.05 / k
5. the locked part, opened once: still positive
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from anon.lab.core import Costs, Idea, LabStats, grid_cells, idea_hash, lab_stats, simulate, validate_idea
from anon.lab.ledger import Ledger, threshold
from anon.models import Bar
from anon.research import shuffled_bars

MIN_DEV_TRADES = 30
MIN_HOLDOUT_TRADES = 10
PLATEAU_SHARE = 2 / 3
HOLDOUT_FRACTION = 0.3


def null_trials_for(alpha: float) -> int:
    """Enough shuffles that the smallest reachable p is well under the bar."""
    return max(200, math.ceil(2 / alpha))


@dataclass
class Cell:
    params: dict[str, Any]
    stats: LabStats


@dataclass
class NullResult:
    real_r: float
    shuffled_r: list[float]

    @property
    def p_value(self) -> float:
        beaten = sum(1 for r in self.shuffled_r if r >= self.real_r)
        return (beaten + 1) / (len(self.shuffled_r) + 1)


@dataclass
class Report:
    idea: str
    family: str
    hypothesis: str
    digest: str
    k: int
    alpha: float
    data: dict[str, Any]
    costs: Costs
    cells: list[Cell] = field(default_factory=list)
    primary: dict[str, Any] = field(default_factory=dict)
    null: NullResult | None = None
    holdout: LabStats | None = None
    holdout_opening: int = 0
    failed_gate: int | None = None
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.failed_gate is None

    @property
    def dev(self) -> LabStats | None:
        return next((c.stats for c in self.cells if c.params == self.primary), None)

    @property
    def plateau_share(self) -> float:
        return sum(1 for c in self.cells if c.stats.n and c.stats.expectancy_r > 0) / len(self.cells) if self.cells else 0.0

    def summary(self) -> dict[str, Any]:
        dev = self.dev
        return {
            "family": self.family,
            "k": self.k,
            "alpha": round(self.alpha, 5),
            "data": self.data,
            "costs": asdict(self.costs),
            "plateau": round(self.plateau_share, 3),
            "dev_n": dev.n if dev else 0,
            "dev_er": round(dev.expectancy_r, 4) if dev else None,
            "null_p": round(self.null.p_value, 4) if self.null else None,
            "null_trials": len(self.null.shuffled_r) if self.null else 0,
            "holdout_n": self.holdout.n if self.holdout else None,
            "holdout_er": round(self.holdout.expectancy_r, 4) if self.holdout else None,
            "holdout_opening": self.holdout_opening,
            "passed": self.passed,
            "failed_gate": self.failed_gate,
            "reason": self.reason,
        }


def _mean_r(idea: Idea, p: dict[str, Any], bars: Sequence[Bar], costs: Costs) -> float:
    rs = [t.r for t in simulate(idea, p, bars, costs) if t.r is not None]
    return statistics.fmean(rs) if rs else 0.0


def run_gauntlet(
    idea: Idea,
    bars: Sequence[Bar],
    costs: Costs,
    ledger: Ledger,
    trials: int | None = None,
    seed: int = 7,
    progress: Callable[[str], None] | None = None,
) -> Report:
    validate_idea(idea)
    say = progress or (lambda _msg: None)
    cut = int(len(bars) * (1 - HOLDOUT_FRACTION))
    dev_bars, digest = bars[:cut], idea_hash(idea)
    data = {
        "bars": len(bars),
        "first": bars[0].time.isoformat(),
        "last": bars[-1].time.isoformat(),
        "locked_from": bars[cut].time.isoformat(),
    }
    # gate 1: the attempt is on record before anything is measured
    k = ledger.register(idea.name, digest, idea.primary, idea.grid, data)
    rep = Report(idea.name, idea.family, idea.hypothesis, digest, k, threshold(k), data, costs, primary=dict(idea.primary))

    # gate 3: plateau on the development part only
    for params in grid_cells(idea):
        rep.cells.append(Cell(params, lab_stats(simulate(idea, params, dev_bars, costs), dev_bars)))
    dev = rep.dev
    if dev is None or dev.n < MIN_DEV_TRADES:
        rep.failed_gate, rep.reason = 3, f"ไม้น้อยเกินตัดสิน (ค่าหลักได้ {dev.n if dev else 0} ไม้ ต้อง ≥ {MIN_DEV_TRADES})"
    elif dev.expectancy_r <= 0:
        rep.failed_gate, rep.reason = 3, f"ค่าหลักขาดทุนในช่วงพัฒนา (E[R] {dev.expectancy_r:+.3f})"
    elif rep.plateau_share < PLATEAU_SHARE:
        rep.failed_gate, rep.reason = 3, f"ไม่ใช่ที่ราบ: ช่องข้าง ๆ บวกแค่ {rep.plateau_share:.0%} (ต้อง ≥ {PLATEAU_SHARE:.0%})"

    # gate 4: same candles in random order
    if rep.passed:
        n = trials or null_trials_for(rep.alpha)
        rng = random.Random(seed)
        shuffled = []
        for t in range(n):
            shuffled.append(_mean_r(idea, idea.primary, shuffled_bars(dev_bars, rng), costs))
            if (t + 1) % 25 == 0 or t + 1 == n:
                say(f"  สลับข้อมูลแล้ว {t + 1}/{n} ชุด")
        rep.null = NullResult(dev.expectancy_r, shuffled)
        if rep.null.p_value > rep.alpha:
            rep.failed_gate, rep.reason = 4, f"แยกไม่ออกจากข้อมูลสุ่ม (p {rep.null.p_value:.4f} > {rep.alpha:.4f})"

    # gate 5: open the locked part, once
    if rep.passed:
        rep.holdout_opening = ledger.record_holdout(idea.name, digest, idea.primary)
        locked = [t for t in simulate(idea, idea.primary, bars, costs) if t.entry_index >= cut]
        rep.holdout = lab_stats(locked, bars[cut:])
        if rep.holdout.n < MIN_HOLDOUT_TRADES:
            rep.failed_gate, rep.reason = 5, f"ช่วงล็อกมีไม้น้อยเกินตัดสิน ({rep.holdout.n} ต้อง ≥ {MIN_HOLDOUT_TRADES})"
        elif rep.holdout.expectancy_r <= 0:
            rep.failed_gate, rep.reason = 5, f"ช่วงล็อกขาดทุน (E[R] {rep.holdout.expectancy_r:+.3f})"

    if rep.passed:
        rep.reason = "ผ่านทุกด่าน → ทดลอง dry-run ต่อ (ยังไม่ใช่เงินจริง)"
    ledger.record_result(idea.name, digest, rep.summary())
    return rep


def _fmt(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def _params(p: dict[str, Any]) -> str:
    return " ".join(f"{k}={v}" for k, v in p.items())


def format_report(rep: Report) -> str:
    d, c = rep.data, rep.costs
    swap = (
        f"swap long {c.swap_long:g} / short {c.swap_short:g} ({c.swap_mode})"
        if c.swap_set
        else "⚠️ swap = 0 (ยังไม่ได้ใส่ [lab] swap_long/swap_short → ไม้ที่ถือข้ามคืนดูดีเกินจริง)"
    )
    lines = [
        f"== {rep.idea} ({rep.family}) · ไอเดียที่ลองแล้ว k={rep.k} → เกณฑ์ p ≤ {rep.alpha:.4f} · รหัส {rep.digest} ==",
        f"สมมติฐาน: {rep.hypothesis.strip()}",
        f"ข้อมูล {d['bars']:,} แท่ง {d['first'][:10]} → {d['last'][:10]} · ช่วงล็อกเริ่ม {d['locked_from'][:10]}",
        f"ต้นทุน: spread {c.spread:g} · {swap}",
        "",
        "ด่าน 3 · ที่ราบ (ช่วงพัฒนา)",
        "  ค่า                              | n     win%   E[R]    PF    maxDD   P(edge>0) | ไม้/ปี ถือเฉลี่ย",
    ]
    for cell in rep.cells:
        s = cell.stats
        mark = " ◀ ค่าหลัก" if cell.params == rep.primary else ""
        lines.append(
            f"  {_params(cell.params):<32} | {s.n:>5} {s.win_rate:>5.1f}% {s.expectancy_r:>+6.3f} "
            f"{_fmt(s.profit_factor, '>5.2f')} {s.max_drawdown_r:>6.1f}R {_fmt(s.prob_edge_positive, '>8.2f')}  | "
            f"{s.per_year:>5.1f} {_fmt(s.avg_hours, '>6.1f')} ชม.{mark}"
        )
    lines.append(f"  ช่องที่บวก {rep.plateau_share:.0%} (ต้อง ≥ {PLATEAU_SHARE:.0%} และค่าหลักบวก ≥ {MIN_DEV_TRADES} ไม้)")
    if rep.null:
        ranked = sorted(rep.null.shuffled_r)
        p95 = ranked[int(0.95 * (len(ranked) - 1))]
        lines += [
            "",
            f"ด่าน 4 · เทียบข้อมูลสุ่ม {len(ranked)} ชุด",
            f"  ของจริง E[R] {rep.null.real_r:+.3f} · สุ่มเฉลี่ย {statistics.fmean(ranked):+.3f} · 95% ของชุดสุ่มไม่เกิน {p95:+.3f}",
            f"  p = {rep.null.p_value:.4f} (ต้อง ≤ {rep.alpha:.4f})",
        ]
    if rep.holdout:
        h = rep.holdout
        again = f" ⚠️ เปิดครั้งที่ {rep.holdout_opening} ผลเชื่อได้น้อยลง" if rep.holdout_opening > 1 else ""
        lines += [
            "",
            f"ด่าน 5 · ช่วงล็อก (เปิดครั้งที่ {rep.holdout_opening}){again}",
            f"  n {h.n} · win {h.win_rate:.1f}% · E[R] {h.expectancy_r:+.3f} · P(edge>0) {_fmt(h.prob_edge_positive, '.2f')}",
        ]
    verdict = "✅" if rep.passed else f"❌ ตกด่าน {rep.failed_gate}:"
    lines += ["", f"ผล: {verdict} {rep.reason}"]
    return "\n".join(lines)
