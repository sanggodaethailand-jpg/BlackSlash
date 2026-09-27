"""Replay H1 bars through the full pipeline on the paper broker."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from anon.approval import AutoApprover
from anon.broker.paper import PaperBroker
from anon.config import Config
from anon.engine import Engine, EngineEvent
from anon.models import Bar, SymbolSpec
from anon.omega import AIReviewer
from anon.quant import Journal, QuantStats, compute_stats


@dataclass
class BacktestResult:
    engine: Engine
    broker: PaperBroker
    journal: Journal
    stats: QuantStats
    events: list[EngineEvent]


def run_backtest(
    cfg: Config,
    bars: Sequence[Bar],
    journal: Journal | None = None,
    ai: AIReviewer | None = None,
    window: int = 300,
    spec: SymbolSpec | None = None,
    spread: float | None = None,
    starting_balance: float | None = None,
    currency: str = "USD",
) -> BacktestResult:
    ex = cfg.execution
    spec = spec or SymbolSpec(ex.contract_size, ex.volume_min, ex.volume_step)
    broker = PaperBroker(
        spec,
        ex.starting_balance if starting_balance is None else starting_balance,
        ex.spread if spread is None else spread,
        currency,
    )
    journal = journal or Journal(None)
    engine = Engine(cfg, broker, AutoApprover(), journal, ai)
    for i, bar in enumerate(bars):
        broker.process_bar(bar)
        engine.on_bar_close(bars[max(0, i - window + 1) : i + 1])
    return BacktestResult(engine, broker, journal, compute_stats(list(journal.records.values())), engine.events)
