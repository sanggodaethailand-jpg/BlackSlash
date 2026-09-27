"""Forward watch: ideas that nearly passed, judged only on bars nobody had seen.

A watch row in the ledger fixes, before any new data exists, which ideas are watched, the
date after which trades count, and the one rule that decides: once the watched ideas have
closed MIN_TRADES trades together, the mean R must be positive with t >= T_PASS (one-sided
p about 0.025). Reports before that show progress only; the rule is applied once.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from anon.lab.core import Costs, Idea, Trade, idea_hash, simulate, t_stat
from anon.lab.ledger import Ledger
from anon.models import Bar

MIN_TRADES = 30
T_PASS = 2.05


def register_watch(ledger: Ledger, ideas: dict[str, Idea], since: str, note: str) -> dict[str, Any]:
    row = {
        "type": "watch",
        "ideas": {name: idea_hash(idea) for name, idea in ideas.items()},
        "since": since,
        "rule": f"pooled closed trades after since >= {MIN_TRADES}: pass if mean R > 0 and t >= {T_PASS}",
        "note": note,
    }
    ledger._append(row)
    return row


def watches(ledger: Ledger) -> list[dict[str, Any]]:
    return [row for row in ledger.rows if row["type"] == "watch"]


@dataclass
class ForwardReport:
    since: str
    last_bar: str
    per_idea: dict[str, list[float]] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)  # ideas whose code no longer matches the watch
    verdict: str | None = None

    @property
    def pooled(self) -> list[float]:
        return [r for rs in self.per_idea.values() for r in rs]


def forward_trades(idea: Idea, bars: Sequence[Bar], costs: Costs, since: datetime) -> list[Trade]:
    closed = [t for t in simulate(idea, idea.primary, bars, costs) if t.reason != "end" and t.r is not None]
    return [t for t in closed if bars[t.entry_index].time > since]


def run_forward(ledger: Ledger, registry: dict[str, Idea], bars: Sequence[Bar], costs: Costs) -> list[ForwardReport]:
    reports = []
    for watch in watches(ledger):
        since = datetime.fromisoformat(watch["since"])
        rep = ForwardReport(watch["since"], bars[-1].time.isoformat())
        for name, digest in watch["ideas"].items():
            idea = registry.get(name)
            if idea is None or idea_hash(idea) != digest:
                rep.changed.append(name)
                continue
            rep.per_idea[name] = [t.r for t in forward_trades(idea, bars, costs, since)]
        done = next((r for r in ledger.rows if r["type"] == "watch_result" and r["since"] == watch["since"]), None)
        if done is not None:
            rep.verdict = done["verdict"]
        elif len(rep.pooled) >= MIN_TRADES and not rep.changed:
            pooled = rep.pooled
            ok = statistics.fmean(pooled) > 0 and t_stat(pooled) >= T_PASS
            rep.verdict = "pass" if ok else "fail"
            ledger._append(
                {
                    "type": "watch_result",
                    "since": watch["since"],
                    "last_bar": rep.last_bar,
                    "n": len(pooled),
                    "mean_r": round(statistics.fmean(pooled), 4),
                    "t": round(t_stat(pooled), 3),
                    "verdict": rep.verdict,
                }
            )
        reports.append(rep)
    return reports


def format_forward(rep: ForwardReport) -> str:
    lines = [f"== เฝ้าดูข้อมูลใหม่ (นับเฉพาะไม้หลัง {rep.since[:16]} UTC) · ข้อมูลถึง {rep.last_bar[:16]} =="]
    for name, rs in rep.per_idea.items():
        mean = f"{statistics.fmean(rs):+.3f}" if rs else "-"
        lines.append(f"  {name:<14} ไม้ปิดแล้ว {len(rs):>3} · E[R] {mean} · รวม {sum(rs):+.2f}R")
    for name in rep.changed:
        lines.append(f"  {name}: โค้ดไม่ตรงกับที่ลงทะเบียนไว้ → ไม่นับ (ห้ามแก้ไอเดียที่เฝ้าดูอยู่)")
    pooled = rep.pooled
    t_now = t_stat(pooled)
    lines.append(f"  รวม {len(pooled)}/{MIN_TRADES} ไม้ · E[R] {statistics.fmean(pooled) if pooled else 0:+.3f} · t {t_now:+.2f}")
    if rep.verdict == "pass":
        lines.append(f"ผล: ✅ ผ่าน (E[R] > 0 และ t ≥ {T_PASS}) → คุยเรื่องขั้นต่อไปกับบอส ยังไม่ขยาย lot เอง")
    elif rep.verdict == "fail":
        lines.append(f"ผล: ❌ ไม่ผ่านเกณฑ์ที่ตั้งไว้ล่วงหน้า (ต้อง E[R] > 0 และ t ≥ {T_PASS} ที่ {MIN_TRADES} ไม้)")
    else:
        lines.append(f"ผล: ⏳ ยังไม่ถึง {MIN_TRADES} ไม้ ดูได้แต่ความคืบหน้า ยังห้ามสรุป")
    return "\n".join(lines)
