"""Append-only research ledger: every attempt counts, so the bar rises as ideas pile up."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FAMILY_ALPHA = 0.05


@dataclass
class Ledger:
    path: Path | None
    rows: list[dict[str, Any]]

    @classmethod
    def open(cls, path: str | Path | None) -> Ledger:
        if path is None:
            return cls(None, [])
        p = Path(path)
        rows = []
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return cls(p, rows)

    def _append(self, row: dict[str, Any]) -> None:
        row = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **row}
        self.rows.append(row)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def attempts(self) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for row in self.rows:
            if row["type"] != "attempt":
                continue
            key = (row["idea"], row["hash"])
            if key not in seen:
                seen.append(key)
        return seen

    def register(self, idea: str, digest: str, primary: dict, grid: dict, data: dict) -> int:
        """Record the attempt before any result is seen; returns k, the attempts so far."""
        if (idea, digest) not in self.attempts():
            self._append({"type": "attempt", "idea": idea, "hash": digest, "primary": primary, "grid": grid, "data": data})
        return len(self.attempts())

    def holdout_openings(self, idea: str) -> int:
        return sum(1 for row in self.rows if row["type"] == "holdout" and row["idea"] == idea)

    def _opened(self, idea: str, digest: str) -> bool:
        return any(r["type"] == "holdout" and r["idea"] == idea and r.get("hash") == digest for r in self.rows)

    def record_holdout(self, idea: str, digest: str, params: dict) -> int:
        """The locked part opens once per attempt, and never after the attempt was judged."""
        if self._opened(idea, digest) or (idea, digest) in self.results():
            raise ValueError(f"{idea} ({digest}): ช่วงล็อกของ attempt นี้เปิดหรือตัดสินไปแล้ว เปิดซ้ำไม่ได้")
        self._append({"type": "holdout", "idea": idea, "hash": digest, "params": params})
        return self.holdout_openings(idea)

    def record_result(self, idea: str, digest: str, result: dict[str, Any]) -> None:
        """One verdict per attempt."""
        if (idea, digest) in self.results():
            raise ValueError(f"{idea} ({digest}): มีผลในสมุดแล้ว บันทึกซ้ำไม่ได้")
        self._append({"type": "result", "idea": idea, "hash": digest, **result})

    def spent(self, idea: str, digest: str) -> str | None:
        """Why this exact attempt may never run again (on any data), or None."""
        res = self.results().get((idea, digest))
        if res is not None:
            verdict = "✅ ผ่าน" if res.get("passed") else f"❌ ตกด่าน {res.get('failed_gate')}"
            return f"มีผลในสมุดแล้ว ({verdict}) → ไม่รันซ้ำ ทั้งกับข้อมูลเดิมและข้อมูลชุดอื่น"
        if self._opened(idea, digest):
            return "เปิดช่วงล็อกไปแล้วแต่ไม่มีผลบันทึก (โปรแกรมหยุดกลางทาง) → นับว่าใช้ไปแล้ว ต้องเขียนเป็นไอเดียใหม่"
        return None

    def results(self) -> dict[tuple[str, str], dict[str, Any]]:
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.rows:
            if row["type"] == "result":
                out[(row["idea"], row["hash"])] = row
        return out


def threshold(k: int) -> float:
    """Bonferroni: with k attempts on the same history, each must clear alpha / k."""
    return FAMILY_ALPHA / max(k, 1)
