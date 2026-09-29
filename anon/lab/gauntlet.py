"""The five gates every idea walks through, in order; the first failure ends the walk.

1. registered in the ledger before any result is seen (k = attempts so far)
2. history split: the oldest 70% for development, the newest 30% locked away (and never later
   than a lock already on record, whichever dataset of the market it came from)
3. development part: the pre-registered setting has >= 30 trades and E[R] > 0, most grid
   neighbours are positive, it survives stressed costs, most calendar years are positive,
   and it is still positive without its best year
4. beats both fake histories (shuffled order and sign-flipped bars) at p <= 0.05 / k,
   measured on the t-statistic of trade R
5. the locked part, opened once: >= 10 trades and E[R] > 0
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from anon.lab.core import Costs, Idea, LabStats, grid_cells, idea_hash, lab_stats, simulate, t_stat, validate_idea
from anon.lab.ledger import Ledger, threshold
from anon.lab.nulls import KINDS, fake_history
from anon.models import Bar

LAB_VERSION = 3  # 3: fill-bar gap stops, swap on time exits, max_wait, flow data
MIN_DEV_TRADES = 30
MIN_HOLDOUT_TRADES = 10
PLATEAU_SHARE = 2 / 3
YEARS_POSITIVE_SHARE = 2 / 3
HOLDOUT_FRACTION = 0.3


def null_trials_for(alpha: float) -> int:
    """Enough fake histories that the smallest reachable p is well under the bar."""
    return max(200, math.ceil(2 / alpha))


@dataclass
class Cell:
    params: dict[str, Any]
    stats: LabStats


@dataclass
class NullResult:
    kind: str
    real_t: float
    fake_t: list[float]

    @property
    def p_value(self) -> float:
        beaten = sum(1 for t in self.fake_t if t >= self.real_t)
        return (beaten + 1) / (len(self.fake_t) + 1)


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
    stressed: LabStats | None = None
    nulls: list[NullResult] = field(default_factory=list)
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

    @property
    def null_p(self) -> float | None:
        return max(n.p_value for n in self.nulls) if self.nulls else None

    def summary(self) -> dict[str, Any]:
        dev = self.dev
        return {
            "lab": LAB_VERSION,
            "family": self.family,
            "k": self.k,
            "alpha": round(self.alpha, 5),
            "data": self.data,
            "costs": asdict(self.costs),
            "plateau": round(self.plateau_share, 3),
            "dev_n": dev.n if dev else 0,
            "dev_er": round(dev.expectancy_r, 4) if dev else None,
            "dev_t": round(dev.t_stat, 3) if dev and math.isfinite(dev.t_stat) else None,
            "dev_years": {str(y): round(r, 2) for y, r in dev.by_year.items()} if dev else {},
            "stress_er": round(self.stressed.expectancy_r, 4) if self.stressed else None,
            "null_p": {n.kind: round(n.p_value, 5) for n in self.nulls},
            "null_trials": len(self.nulls[0].fake_t) if self.nulls else 0,
            "holdout_n": self.holdout.n if self.holdout else None,
            "holdout_er": round(self.holdout.expectancy_r, 4) if self.holdout else None,
            "holdout_opening": self.holdout_opening,
            "passed": self.passed,
            "failed_gate": self.failed_gate,
            "reason": self.reason,
        }


def _fake_t(args: tuple) -> list[float]:
    idea, params, bars, costs, kind, seeds = args
    out = []
    for seed in seeds:
        rs = [t.r for t in simulate(idea, params, fake_history(kind, bars, seed), costs) if t.r is not None]
        out.append(t_stat(rs))
    return out


def run_null(
    idea: Idea, params: dict[str, Any], bars: Sequence[Bar], costs: Costs, kind: str, trials: int, seed: int,
    workers: int = 1, progress: Callable[[str], None] | None = None,
) -> list[float]:
    base = seed * 1_000_003 + KINDS.index(kind) * 100_003
    seeds = [base + j for j in range(trials)]
    chunk = 10 if workers <= 1 else max(10, math.ceil(trials / (workers * 3)))
    tasks = [(idea, params, list(bars), costs, kind, seeds[j : j + chunk]) for j in range(0, trials, chunk)]
    done, results = 0, {}
    if workers <= 1:
        for j, task in enumerate(tasks):
            results[j] = _fake_t(task)
            done += len(task[5])
            if progress and (done % 50 == 0 or done == trials):
                progress(f"  {kind}: {done}/{trials}")
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fake_t, task): j for j, task in enumerate(tasks)}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
                done += chunk
                if progress and (done % 50 == 0 or done >= trials):
                    progress(f"  {kind}: {min(done, trials)}/{trials}")
    return [t for j in range(len(tasks)) for t in results[j]]


def _dev_checks(rep: Report) -> None:
    dev = rep.dev
    if dev is None or dev.n < MIN_DEV_TRADES:
        rep.failed_gate, rep.reason = 3, f"ไม้น้อยเกินตัดสิน (ค่าหลักได้ {dev.n if dev else 0} ไม้ ต้อง ≥ {MIN_DEV_TRADES})"
    elif dev.expectancy_r <= 0:
        rep.failed_gate, rep.reason = 3, f"ค่าหลักขาดทุนในช่วงพัฒนา (E[R] {dev.expectancy_r:+.3f})"
    elif rep.plateau_share < PLATEAU_SHARE:
        rep.failed_gate, rep.reason = 3, f"ไม่ใช่ที่ราบ: ช่องข้าง ๆ บวกแค่ {rep.plateau_share:.0%} (ต้อง ≥ {PLATEAU_SHARE:.0%})"
    elif rep.stressed is not None and rep.stressed.expectancy_r <= 0:
        rep.failed_gate, rep.reason = 3, f"ต้นทุนหนักแล้วขาดทุน (E[R] {rep.stressed.expectancy_r:+.3f})"
    else:
        years = dev.by_year
        positive = sum(1 for r in years.values() if r > 0)
        best = max(years.values())
        if positive < YEARS_POSITIVE_SHARE * len(years):
            rep.failed_gate, rep.reason = 3, f"บวกแค่ {positive}/{len(years)} ปี (ต้อง ≥ {YEARS_POSITIVE_SHARE:.0%})"
        elif dev.sum_r - best <= 0:
            rep.failed_gate, rep.reason = 3, f"กำไรกระจุกในปีเดียว (ตัดปีที่ดีสุดออกเหลือ {dev.sum_r - best:+.1f}R)"


def holdout_cut(bars: Sequence[Bar], ledger: Ledger) -> int:
    """Index where the locked part starts: the newest HOLDOUT_FRACTION, and never later than a
    lock already in the ledger, so another dataset of the same market cannot show the locked
    months in its development part."""
    cut = int(len(bars) * (1 - HOLDOUT_FRACTION))
    locks = [
        datetime.fromisoformat(row["data"]["locked_from"])
        for row in ledger.rows
        if row["type"] == "attempt" and "locked_from" in row.get("data", {})
    ]
    if locks:
        first_lock = min(locks)
        cut = min(cut, next((i for i, b in enumerate(bars) if b.time >= first_lock), len(bars)))
    return cut


def split_data(bars: Sequence[Bar], ledger: Ledger, source: str = "") -> tuple[int, dict[str, Any]]:
    cut = holdout_cut(bars, ledger)
    data = {
        "source": source,
        "bars": len(bars),
        "first": bars[0].time.isoformat(),
        "last": bars[-1].time.isoformat(),
        "locked_from": bars[cut].time.isoformat(),
        "flow": has_flow(bars),
    }
    return cut, data


def has_flow(bars: Sequence[Bar]) -> bool:
    """Exchange data with market-order volume (anon binance), not MT5 CFD bars."""
    return any(b.volume > 0 for b in bars)


def run_gauntlet(
    idea: Idea,
    bars: Sequence[Bar],
    costs: Costs,
    ledger: Ledger,
    trials: int | None = None,
    seed: int = 7,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
    source: str = "",
) -> Report:
    validate_idea(idea)
    if idea.needs_flow and not has_flow(bars):
        raise ValueError(f"{idea.name} needs volume/buy_volume columns (anon binance); this data has none")
    digest = idea_hash(idea)
    why = ledger.spent(idea.name, digest)
    if why:
        raise ValueError(f"{idea.name} ({digest}): {why}")
    cut, data = split_data(bars, ledger, source)
    dev_bars = bars[:cut]
    # gate 1: the attempt is on record before anything is measured
    k = ledger.register(idea.name, digest, idea.primary, idea.grid, data)
    rep = Report(idea.name, idea.family, idea.hypothesis, digest, k, threshold(k), data, costs, primary=dict(idea.primary))

    # gate 3: the development part only
    for params in grid_cells(idea):
        rep.cells.append(Cell(params, lab_stats(simulate(idea, params, dev_bars, costs), dev_bars, costs)))
    stress = costs.stressed()
    rep.stressed = lab_stats(simulate(idea, idea.primary, dev_bars, stress), dev_bars, stress, bootstrap=False)
    _dev_checks(rep)

    # gate 4: fake histories
    if rep.passed:
        n = trials or null_trials_for(rep.alpha)
        for kind in KINDS:
            fake = run_null(idea, idea.primary, dev_bars, costs, kind, n, seed, workers, progress)
            rep.nulls.append(NullResult(kind, rep.dev.t_stat, fake))
        if rep.null_p > rep.alpha:
            worst = max(rep.nulls, key=lambda x: x.p_value)
            rep.failed_gate, rep.reason = 4, f"แยกไม่ออกจากข้อมูลสุ่มแบบ {worst.kind} (p {worst.p_value:.4f} > {rep.alpha:.4f})"

    # gate 5: open the locked part, once
    if rep.passed:
        rep.holdout_opening = ledger.record_holdout(idea.name, digest, idea.primary)
        locked = [t for t in simulate(idea, idea.primary, bars, costs) if t.entry_index >= cut]
        rep.holdout = lab_stats(locked, bars, costs, span=bars[cut:])
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
        f"swap long {c.swap_long:g} / short {c.swap_short:g} ({'% ต่อปี' if c.swap_mode == 'percent' else 'points'})"
        if c.swap_set
        else "⚠️ swap = 0 (ยังไม่ได้ใส่ [lab] swap_long/swap_short → ไม้ที่ถือข้ามคืนดูดีเกินจริง)"
    )
    lines = [
        f"== {rep.idea} ({rep.family}) · ไอเดียที่ลองแล้ว k={rep.k} → เกณฑ์ p ≤ {rep.alpha:.4f} · รหัส {rep.digest} ==",
        f"สมมติฐาน: {' '.join(rep.hypothesis.split())}",
        f"ข้อมูล {d['bars']:,} แท่ง {d['first'][:10]} → {d['last'][:10]} · ช่วงล็อกเริ่ม {d['locked_from'][:10]}",
        f"ต้นทุน: spread {c.spread:g} · slippage {c.slippage:g} · {swap}",
        "",
        "ด่าน 3 · ช่วงพัฒนา",
        "  ค่า                                  | n     win%   E[R]    t     PF    maxDD  swap/ไม้ | ไม้/ปี ถือเฉลี่ย",
    ]
    for cell in rep.cells:
        s = cell.stats
        mark = " ◀ ค่าหลัก" if cell.params == rep.primary else ""
        lines.append(
            f"  {_params(cell.params):<36} | {s.n:>5} {s.win_rate:>5.1f}% {s.expectancy_r:>+6.3f} {s.t_stat:>+5.2f} "
            f"{_fmt(s.profit_factor, '>5.2f')} {s.max_drawdown_r:>6.1f}R {s.swap_r:>+6.3f}R | "
            f"{s.per_year:>5.1f} {_fmt(s.avg_hours, '>6.1f')} ชม.{mark}"
        )
    lines.append(f"  ช่องที่บวก {rep.plateau_share:.0%} (ต้อง ≥ {PLATEAU_SHARE:.0%} และค่าหลักบวก ≥ {MIN_DEV_TRADES} ไม้)")
    if rep.stressed is not None:
        lines.append(f"  ต้นทุนหนัก (spread ×2 ≥ 20, slippage 5/ข้าง, swap ×2): E[R] {rep.stressed.expectancy_r:+.3f}")
    dev = rep.dev
    if dev is not None and dev.by_year:
        years = " ".join(f"{y}:{r:+.1f}" for y, r in sorted(dev.by_year.items()))
        lines.append(f"  รายปี (ผลรวม R): {years}")
    for null in rep.nulls:
        ranked = sorted(null.fake_t)
        p95 = ranked[int(0.95 * (len(ranked) - 1))]
        if null is rep.nulls[0]:
            lines += ["", f"ด่าน 4 · เทียบข้อมูลสุ่ม ({len(ranked)} ชุดต่อแบบ, วัดด้วยค่า t ของ R)"]
        label = "สลับลำดับแท่ง" if null.kind == "shuffle" else "กลับทิศแท่ง (คงความผันผวน)"
        lines.append(
            f"  {label}: ของจริง t {null.real_t:+.2f} · สุ่มเฉลี่ย {statistics.fmean(ranked):+.2f} · "
            f"95% ไม่เกิน {p95:+.2f} · p = {null.p_value:.4f}"
        )
    if rep.holdout:
        h = rep.holdout
        again = f" ⚠️ เปิดครั้งที่ {rep.holdout_opening} ผลเชื่อได้น้อยลง" if rep.holdout_opening > 1 else ""
        lines += [
            "",
            f"ด่าน 5 · ช่วงล็อก (เปิดครั้งที่ {rep.holdout_opening}){again}",
            f"  n {h.n} · win {h.win_rate:.1f}% · E[R] {h.expectancy_r:+.3f} · t {h.t_stat:+.2f} · "
            f"P(edge>0) {_fmt(h.prob_edge_positive, '.2f')}",
        ]
    verdict = "✅" if rep.passed else f"❌ ตกด่าน {rep.failed_gate}:"
    lines += ["", f"ผล: {verdict} {rep.reason}"]
    return "\n".join(lines)
