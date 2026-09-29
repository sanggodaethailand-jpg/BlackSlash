"""Robustness sweep: the same history under neighbouring settings.

A setting worth trusting sits on a plateau: its neighbours are positive too, and it
holds up in both halves of the period. A lone good cell is more likely luck.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, replace

from anon.backtest import run_backtest
from anon.config import Config
from anon.models import Bar
from anon.quant import compute_stats


@dataclass(frozen=True)
class SweepRow:
    lookback: int
    min_rr: float
    n: int
    win_rate: float
    expectancy_r: float
    profit_factor: float | None
    max_drawdown_r: float
    prob_edge_positive: float | None
    first_half_r: float | None
    second_half_r: float | None
    avg_hours: float | None

    @property
    def holds_up(self) -> bool:
        """Positive overall and in both halves of the period."""
        return (
            self.n > 0
            and self.expectancy_r > 0
            and (self.first_half_r or 0) > 0
            and (self.second_half_r or 0) > 0
        )


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def run_sweep(
    cfg: Config,
    bars: Sequence[Bar],
    rrs: Sequence[float],
    lookbacks: Sequence[int],
    **backtest_kwargs,
) -> list[SweepRow]:
    midpoint = bars[len(bars) // 2].time.isoformat()
    rows = []
    for lookback in lookbacks:
        for rr in rrs:
            variant = replace(
                cfg,
                auto=replace(cfg.auto, enabled=True, lookback_bars=lookback),
                ghost=replace(cfg.ghost, min_rr=rr),
            )
            variant.validate()
            result = run_backtest(variant, bars, **backtest_kwargs)
            records = list(result.journal.records.values())
            stats = compute_stats(records)
            plan = [r for r in records if r.status == "closed" and r.on_plan and r.id.startswith("#T") and r.r is not None]
            rows.append(
                SweepRow(
                    lookback=lookback,
                    min_rr=rr,
                    n=stats.n,
                    win_rate=stats.win_rate,
                    expectancy_r=stats.expectancy_r,
                    profit_factor=stats.profit_factor,
                    max_drawdown_r=stats.max_drawdown_r,
                    prob_edge_positive=stats.prob_edge_positive,
                    first_half_r=_mean([r.r for r in plan if r.opened_at < midpoint]),
                    second_half_r=_mean([r.r for r in plan if r.opened_at >= midpoint]),
                    avg_hours=None if stats.avg_minutes is None else stats.avg_minutes / 60,
                )
            )
    return rows


def _fmt(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def format_sweep(rows: Sequence[SweepRow], current: tuple[int, float] | None = None) -> str:
    lines = [
        "กรอบ  RR    | n    win%   E[R]    PF    maxDD   P(edge>0) | ครึ่งแรก ครึ่งหลัง | ถือเฉลี่ย | ผ่าน",
        "-" * 96,
    ]
    for r in rows:
        mark = " ◀ ตอนนี้" if current == (r.lookback, r.min_rr) else ""
        lines.append(
            f"{r.lookback:>4} {r.min_rr:>5.2f} | {r.n:>4} {r.win_rate:>5.1f}% {r.expectancy_r:>+6.3f} "
            f"{_fmt(r.profit_factor, '>5.2f')} {r.max_drawdown_r:>6.1f}R {_fmt(r.prob_edge_positive, '>8.2f')}  | "
            f"{_fmt(r.first_half_r, '>+7.3f')}  {_fmt(r.second_half_r, '>+7.3f')}  | "
            f"{_fmt(r.avg_hours, '>6.1f')} ชม. | {'✓' if r.holds_up else '✗'}{mark}"
        )
    passed = sum(r.holds_up for r in rows)
    lines += [
        "-" * 96,
        f"ผ่าน (E[R] > 0 ทั้งช่วง และบวกทั้งครึ่งแรกและครึ่งหลัง) {passed}/{len(rows)} ช่อง",
        "อ่านผล: ควรเลือกค่าที่ช่องรอบ ๆ ผ่านด้วย ไม่ใช่ช่องเดียวที่สวยสุด · "
        "P(edge>0) ต้อง ≥ 0.9 ถึงจะผ่านเกณฑ์ auto",
    ]
    return "\n".join(lines)


def shuffled_bars(bars: Sequence[Bar], rng: random.Random) -> list[Bar]:
    """Same candles in random order: volatility and candle shapes stay, any pattern in time goes.

    Each bar is kept as ratios (gap from the previous close, high/low/close relative to its
    open) and the ratios are replayed in shuffled order from the same starting price, so the
    shuffled path also ends at the same final price. A bar's volume and buy volume travel
    with its shape."""
    shapes = []
    prev_close = bars[0].open
    for b in bars:
        shapes.append((b.open / prev_close, b.high / b.open, b.low / b.open, b.close / b.open, b.volume, b.buy_volume))
        prev_close = b.close
    rng.shuffle(shapes)
    out: list[Bar] = []
    price = bars[0].open
    for b, (gap, high, low, close, volume, buy_volume) in zip(bars, shapes, strict=True):
        o = price * gap
        out.append(Bar(b.time, o, o * high, o * low, o * close, volume, buy_volume))
        price = o * close
    return out


@dataclass(frozen=True)
class NullTest:
    lookback: int
    min_rr: float
    real_r: float
    real_n: int
    shuffled_r: list[float]

    @property
    def p_value(self) -> float:
        """Share of shuffled histories that did at least as well as the real one."""
        beaten = sum(1 for r in self.shuffled_r if r >= self.real_r)
        return (beaten + 1) / (len(self.shuffled_r) + 1)


def run_null_test(
    cfg: Config, bars: Sequence[Bar], lookback: int, min_rr: float, trials: int, seed: int = 7, **backtest_kwargs
) -> NullTest:
    variant = replace(
        cfg, auto=replace(cfg.auto, enabled=True, lookback_bars=lookback), ghost=replace(cfg.ghost, min_rr=min_rr)
    )
    variant.validate()
    real = run_backtest(variant, bars, **backtest_kwargs).stats
    rng = random.Random(seed)
    shuffled = [run_backtest(variant, shuffled_bars(bars, rng), **backtest_kwargs).stats.expectancy_r for _ in range(trials)]
    return NullTest(lookback, min_rr, real.expectancy_r, real.n, shuffled)


def format_null(test: NullTest) -> str:
    ranked = sorted(test.shuffled_r)
    p95 = ranked[int(0.95 * (len(ranked) - 1))] if ranked else float("nan")
    verdict = (
        "ของจริงดีกว่าข้อมูลสุ่มเกือบทุกชุด → มีหลักฐานว่าไม่ใช่ฟลุ๊ก"
        if test.p_value <= 0.05
        else "ของจริงดีกว่าข้อมูลสุ่มส่วนใหญ่ แต่ยังไม่ขาด → หลักฐานอ่อน"
        if test.p_value <= 0.2
        else "ของจริงแยกไม่ออกจากข้อมูลสุ่ม → ยังเป็นไปได้มากว่าบังเอิญ"
    )
    return "\n".join(
        [
            f"เทียบกับความบังเอิญ (กรอบ {test.lookback} · RR {test.min_rr}): สลับลำดับแท่งจริง {len(ranked)} ชุด",
            f"  ของจริง E[R] {test.real_r:+.3f} (n={test.real_n}) · ข้อมูลสุ่ม: เฉลี่ย "
            f"{statistics.fmean(ranked) if ranked else float('nan'):+.3f} · 95% ของชุดสุ่มไม่เกิน {p95:+.3f}",
            f"  p = {test.p_value:.2f} → {verdict}",
        ]
    )
