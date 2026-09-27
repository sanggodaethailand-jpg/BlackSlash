"""Owner/head approval — the step between Zen and pressing the button."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class Approver(Protocol):
    def approve(self, summary: str, trade_id: str) -> bool: ...


class ManualApprover:
    """Requires the exact phrase ``อนุมัติ #Txx``; anything else (including Enter) skips."""

    def __init__(self, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print) -> None:
        self.input_fn = input_fn
        self.output_fn = output_fn

    def approve(self, summary: str, trade_id: str) -> bool:
        self.output_fn(summary)
        try:
            answer = self.input_fn(f'พิมพ์ "อนุมัติ {trade_id}" เพื่อกด (อย่างอื่น = ข้าม): ')
        except EOFError:
            return False
        return answer.strip() == f"อนุมัติ {trade_id}"


class AutoApprover:
    """Only for backtest/paper, or live after Quant's evidence gate has passed."""

    def approve(self, summary: str, trade_id: str) -> bool:
        return True
