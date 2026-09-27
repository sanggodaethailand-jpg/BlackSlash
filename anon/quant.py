"""Quant — the #Txx journal and the statistics built from it.

Only on-plan #Txx trades feed E[R]. Off-plan trades are journaled so "% on plan"
can be measured, but never mixed into the edge estimate. THB is filled only
from a real P/L and an explicitly supplied rate; nothing is forecast in baht.
"""

from __future__ import annotations

import json
import os
import random
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class TradeRecord:
    id: str
    ticket: int
    setup: str
    variant: str
    regime: str
    entry_plan: float
    stop: float
    tp1: float
    lot: float
    on_plan: bool
    opened_at: str
    status: str = "open"
    entry_fill: float | None = None
    exit_price: float | None = None
    closed_at: str | None = None
    exit_reason: str | None = None
    r: float | None = None
    pl: float | None = None
    pl_ccy: str = "USD"
    pl_thb: float | None = None
    minutes: float | None = None
    note: str = ""
    thesis_below: float | None = None  # A: exit if an H1 bar closes below this level

    def form_line(self) -> str:
        r = "-" if self.r is None else f"{self.r:+.2f}R"
        thb = "-" if self.pl_thb is None else f"{self.pl_thb:+,.0f}฿"
        mins = "-" if self.minutes is None else f"{self.minutes:.0f}"
        return (
            f"{self.id} | {self.setup} | {self.regime} | "
            f"{self.entry_fill or self.entry_plan:.1f}/{self.stop:.1f}/{self.tp1:.1f} | "
            f"lot {self.lot:.2f} | {r} / {thb} | {mins} | "
            f"{'Y' if self.on_plan else 'N'} | {self.note}"
        )


def realized_r(side: str, entry: float, stop: float, exit_price: float) -> float:
    risk = abs(entry - stop)
    if risk == 0:
        raise ValueError("zero risk distance")
    move = exit_price - entry if side == "buy" else entry - exit_price
    return move / risk


class Journal:
    """Append-only JSONL; the last snapshot of each id wins."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.records: dict[str, TradeRecord] = {}
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = TradeRecord(**json.loads(line))
                    self.records[rec.id] = rec

    def ready(self) -> bool:
        if self.path is None:
            return True
        directory = self.path.parent
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        return os.access(directory, os.W_OK)

    def next_id(self) -> str:
        n = sum(1 for k in self.records if k.startswith("#T"))
        return f"#T{n + 1:02d}"

    def save(self, rec: TradeRecord) -> None:
        self.records[rec.id] = rec
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    def by_ticket(self, ticket: int) -> TradeRecord | None:
        for rec in self.records.values():
            if rec.ticket == ticket:
                return rec
        return None

    def closed(self) -> list[TradeRecord]:
        return [r for r in self.records.values() if r.status == "closed"]


@dataclass
class QuantStats:
    n: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    sum_r: float = 0.0
    profit_factor: float | None = None
    avg_minutes: float | None = None
    max_drawdown_r: float = 0.0
    max_consecutive_losses: int = 0
    pct_on_plan: float | None = None
    prob_edge_positive: float | None = None
    by_regime: dict[str, dict[str, float]] = field(default_factory=dict)
    by_setup: dict[str, dict[str, float]] = field(default_factory=dict)
    by_year: dict[str, dict[str, float]] = field(default_factory=dict)


def bootstrap_prob_positive(rs: list[float], iters: int = 2000, seed: int = 7) -> float:
    if not rs:
        return 0.0
    rng = random.Random(seed)
    n = len(rs)
    hits = sum(1 for _ in range(iters) if sum(rng.choice(rs) for _ in range(n)) > 0)
    return hits / iters


def compute_stats(records: list[TradeRecord]) -> QuantStats:
    closed = [r for r in records if r.status == "closed"]
    plan = [r for r in closed if r.on_plan and r.id.startswith("#T") and r.r is not None]
    s = QuantStats()
    if closed:
        s.pct_on_plan = 100 * sum(1 for r in closed if r.on_plan) / len(closed)
    if not plan:
        return s
    rs = [r.r for r in plan]
    s.n = len(rs)
    s.wins = sum(1 for x in rs if x > 0)
    s.losses = sum(1 for x in rs if x < 0)
    s.win_rate = 100 * s.wins / s.n
    s.sum_r = sum(rs)
    s.expectancy_r = statistics.fmean(rs)
    gross_win = sum(x for x in rs if x > 0)
    gross_loss = -sum(x for x in rs if x < 0)
    s.profit_factor = gross_win / gross_loss if gross_loss > 0 else None
    mins = [r.minutes for r in plan if r.minutes is not None]
    s.avg_minutes = statistics.fmean(mins) if mins else None
    equity = peak = 0.0
    streak = 0
    for x in rs:
        equity += x
        peak = max(peak, equity)
        s.max_drawdown_r = max(s.max_drawdown_r, peak - equity)
        streak = streak + 1 if x < 0 else 0
        s.max_consecutive_losses = max(s.max_consecutive_losses, streak)
    s.prob_edge_positive = bootstrap_prob_positive(rs)
    s.by_regime = _group(plan, lambda r: r.regime)
    s.by_setup = _group(plan, lambda r: r.setup)
    s.by_year = _group(plan, lambda r: r.opened_at[:4])
    return s


def _group(plan: list[TradeRecord], key) -> dict[str, dict[str, float]]:
    groups: dict[str, list[float]] = {}
    for r in plan:
        groups.setdefault(key(r), []).append(r.r)
    return {
        k: {"n": len(v), "expectancy_r": statistics.fmean(v), "sum_r": sum(v), "win_rate": 100 * sum(x > 0 for x in v) / len(v)}
        for k, v in groups.items()
    }


def auto_live_allowed(stats: QuantStats, min_n: int, min_prob: float) -> tuple[bool, str]:
    if stats.n < min_n:
        return False, f"n={stats.n} < {min_n}"
    if stats.expectancy_r <= 0:
        return False, f"E[R]={stats.expectancy_r:.3f} <= 0"
    if (stats.prob_edge_positive or 0) < min_prob:
        return False, f"P(edge>0)={stats.prob_edge_positive:.2f} < {min_prob}"
    return True, "ok"


def format_report(stats: QuantStats, records: list[TradeRecord], show_trades: bool = True) -> str:
    lines: list[str] = []
    if show_trades:
        lines.append("#Txx | A/B | ระบอบ | เข้า/หยุด/TP1 | lot | ผล R/THB | นาที | ตามแผน | โน้ต")
        lines += [r.form_line() for r in records if r.status == "closed"]
        lines.append("")
    if stats.n == 0:
        lines.append("Quant n=0 — ยังไม่มีไม้ตามแผนที่ปิดแล้ว (ห้ามสรุป E[R])")
    else:
        pf = "-" if stats.profit_factor is None else f"{stats.profit_factor:.2f}"
        lines.append(
            f"n={stats.n} win={stats.win_rate:.1f}% E[R]={stats.expectancy_r:+.3f} "
            f"ΣR={stats.sum_r:+.2f} PF={pf} maxDD={stats.max_drawdown_r:.2f}R "
            f"แพ้ติด={stats.max_consecutive_losses} P(edge>0)={stats.prob_edge_positive:.2f}"
        )
        for label, groups in (("setup", stats.by_setup), ("ปี", stats.by_year), ("Ω", stats.by_regime)):
            for key, v in sorted(groups.items()):
                lines.append(
                    f"  {label} {key}: n={v['n']:.0f} win={v['win_rate']:.0f}% "
                    f"E[R]={v['expectancy_r']:+.3f} ΣR={v['sum_r']:+.1f}"
                )
    if stats.pct_on_plan is not None:
        lines.append(f"KPI %ตามแผน = {stats.pct_on_plan:.1f}%")
    return "\n".join(lines)
