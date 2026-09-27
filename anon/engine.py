"""The pipeline, run once per closed H1 bar:

sync broker → manage open plan trade → Ghost → Ω → Risk → Zen → approval → order → Quant
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta

from anon.approval import Approver
from anon.autolevels import draw_levels
from anon.broker.base import Broker
from anon.config import Config
from anon.ghost import Ghost, reprice
from anon.models import Bar, ClosedTrade, Position, Signal
from anon.omega import AIReviewer, Omega
from anon.quant import Journal, TradeRecord, realized_r
from anon.risk import Risk, RiskContext
from anon.zen import Zen, ZenState

# Broker-side SL can differ from the planned stop by price rounding; below this it is not a move.
STOP_TOLERANCE = 0.01


@dataclass(frozen=True)
class EngineEvent:
    time: datetime
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.time:%Y-%m-%d %H:%M} [{self.kind}] {self.detail}"


@dataclass(frozen=True)
class CallMark:
    """A Ghost call and what the pipeline did with it (the last event it produced)."""

    bar_time: datetime
    setup: str
    entry: float
    stop: float
    tp1: float
    outcome: str
    detail: str


class Engine:
    def __init__(
        self,
        cfg: Config,
        broker: Broker,
        approver: Approver,
        journal: Journal,
        ai: AIReviewer | None = None,
    ) -> None:
        self.cfg = cfg
        self.broker = broker
        self.approver = approver
        self.journal = journal
        self.levels = cfg.levels
        self.ghost = Ghost(cfg.levels, cfg.ghost)
        self.omega = Omega(cfg.levels, cfg.omega, ai)
        self.levels_day: str | None = None  # auto mode: local day the current levels were drawn for
        self.levels_ok = not cfg.auto.enabled
        self.calls: deque[CallMark] = deque(maxlen=200)
        self.risk = Risk(cfg.risk)
        self.zen = Zen(cfg.zen)
        self.magic = cfg.execution.magic
        self.events: list[EngineEvent] = []
        self.known_foreign: set[int] = set()
        self.thesis_exits: set[int] = set()
        self.started_at: datetime | None = None
        self.last_sync: datetime | None = None
        self.day_key: str | None = None
        self.expired_noted: str | None = None
        self.day_start_equity: float = 0.0
        self.peak_equity: float = 0.0

    # --- public ----------------------------------------------------------
    def on_bar_close(self, bars: Sequence[Bar]) -> list[EngineEvent]:
        now = bars[-1].close_time
        first = len(self.events)
        self._roll_day(now)
        if self.cfg.auto.enabled and self.levels_day != self.day_key:
            self._redraw_levels(bars, now)
        self.zen.on_bar()
        self._sync(now)
        self._manage(bars, now)
        self._maybe_trade(bars, now)
        return self.events[first:]

    # --- helpers ---------------------------------------------------------
    def _emit(self, now: datetime, kind: str, detail: str) -> None:
        self.events.append(EngineEvent(now, kind, detail))

    def _local_day(self, t: datetime) -> date:
        return (t + timedelta(hours=self.cfg.execution.day_utc_offset_hours)).date()

    def _day_start_utc(self, day: date) -> datetime:
        return datetime.combine(day, time(0), tzinfo=UTC) - timedelta(hours=self.cfg.execution.day_utc_offset_hours)

    def _roll_day(self, now: datetime) -> None:
        day = self._local_day(now)
        account = self.broker.account()
        if self.day_key != day.isoformat():
            if self.day_key is None:
                # Fresh start mid-day: rebuild the day's opening balance from today's closed deals.
                realized = sum(c.profit for c in self.broker.closed_since(self._day_start_utc(day)))
                self.day_start_equity = account.balance - realized
            else:
                self.day_start_equity = account.equity
            self.day_key = day.isoformat()
            self.zen.roll_day(day)
        self.peak_equity = max(self.peak_equity, account.equity, self.day_start_equity)

    def _redraw_levels(self, bars: Sequence[Bar], now: datetime) -> None:
        """Auto mode: draw today's levels from bars closed by the start of the local day."""
        day_start = self._day_start_utc(self._local_day(now))
        history = [b for b in bars if b.close_time <= day_start]
        drawn = draw_levels(history, self.cfg.auto, self.cfg.ghost, self.cfg.omega.atr_period)
        self.levels_day = self.day_key
        if isinstance(drawn, str):
            self.levels_ok = False
            self._emit(now, "levels", f"ไม่วาดเส้นวันนี้: {drawn}")
            return
        self.levels_ok = True
        self.levels = drawn.levels
        self.ghost = Ghost(drawn.levels, drawn.ghost)
        self.omega.lv = drawn.levels
        self._emit(now, "levels", drawn.describe())

    def _thb(self, pl: float) -> float | None:
        currency = self.broker.account().currency
        if currency == "THB":
            return pl
        if currency in ("USD", "USC") and self.cfg.execution.usdthb:
            usd = pl / 100 if currency == "USC" else pl  # USC = US cents
            return usd * self.cfg.execution.usdthb
        return None

    # --- sync ------------------------------------------------------------
    def _sync(self, now: datetime) -> None:
        first_sync = self.started_at is None
        if first_sync:
            self.started_at = now
        since = self.last_sync or now - timedelta(days=7)
        for c in self.broker.closed_since(since):
            self._on_closed(c, now)
        for p in self.broker.positions():
            if p.magic == self.magic:
                self._check_plan_position(p, now)
            elif p.ticket not in self.known_foreign:
                self.known_foreign.add(p.ticket)
                if first_sync:
                    detail = f"#{p.ticket} {p.side} {p.volume} existed at start — Risk counts it, Quant does not"
                    self._emit(now, "foreign_open", detail)
                else:
                    self._off_plan_opened(p, now)
        self.last_sync = now

    def _on_closed(self, c: ClosedTrade, now: datetime) -> None:
        rec = self.journal.by_ticket(c.ticket)
        if rec is not None and rec.status == "open":
            self._finalize(rec, c, now)
            return
        if rec is None and c.magic != self.magic and c.ticket not in self.known_foreign:
            if self.started_at and c.time_open >= self.started_at:
                # Opened and closed by hand between two bars.
                self.known_foreign.add(c.ticket)
                pos = Position(c.ticket, c.side, c.volume, c.price_open, None, None, 0.0, c.magic)
                self._off_plan_opened(pos, now)
                self._finalize(self.journal.by_ticket(c.ticket), c, now)

    def _off_plan_opened(self, p: Position, now: datetime) -> None:
        self.zen.record_off_plan(f"manual #{p.ticket}")
        self.journal.save(
            TradeRecord(
                id=f"OFF-{p.ticket}",
                ticket=p.ticket,
                setup="-",
                variant="manual",
                regime="-",
                entry_plan=p.price_open,
                stop=p.sl or 0.0,
                tp1=p.tp or 0.0,
                lot=p.volume,
                on_plan=False,
                opened_at=now.isoformat(),
                entry_fill=p.price_open,
                note="หลุดแผน: เปิดมือนอกระบบ",
            )
        )
        lock = " → ปิดจอวันนี้" if self.zen.state.locked else ""
        self._emit(now, "off_plan", f"#{p.ticket} {p.side} {p.volume} opened outside the plan{lock}")

    def _finalize(self, rec: TradeRecord, c: ClosedTrade, now: datetime) -> None:
        rec.status = "closed"
        rec.entry_fill = c.price_open
        rec.exit_price = c.price_close
        rec.closed_at = c.time_close.isoformat()
        rec.minutes = (c.time_close - c.time_open).total_seconds() / 60
        rec.pl = c.profit
        rec.pl_ccy = self.broker.account().currency
        rec.pl_thb = self._thb(c.profit)
        rec.exit_reason = "thesis" if c.ticket in self.thesis_exits else c.exit_reason
        if rec.id.startswith("#T"):
            rec.r = realized_r(c.side, c.price_open, rec.stop, c.price_close)
            if rec.exit_reason == "manual":
                rec.on_plan = False
                rec.note += " | ปิดมือก่อน SL/TP"
                self.zen.record_off_plan(f"manual close {rec.id}")
            elif rec.on_plan:
                self.zen.record_plan_trade(rec.r)
        self.journal.save(rec)
        self._emit(now, "closed", rec.form_line())

    def _check_plan_position(self, p: Position, now: datetime) -> None:
        rec = self.journal.by_ticket(p.ticket)
        if rec is None or rec.status != "open":
            self._emit(now, "warning", f"plan position #{p.ticket} has no open journal record")
            return
        if rec.entry_fill is None:
            rec.entry_fill = p.price_open
            self.journal.save(rec)
        widened = p.sl is None or (
            p.sl < rec.stop - STOP_TOLERANCE if p.side == "buy" else p.sl > rec.stop + STOP_TOLERANCE
        )
        if widened:
            rec.on_plan = False
            rec.note += f" | stop ถูกขยาย/ลบ ({p.sl})"
            self.journal.save(rec)
            self.zen.record_off_plan(f"stop widened {rec.id}")
            restored = self.cfg.zen.restore_widened_stop and self.broker.modify_sl(p.ticket, rec.stop)
            self._emit(now, "off_plan", f"{rec.id} stop moved to {p.sl}; restored={bool(restored)}")

    # --- manage ----------------------------------------------------------
    def _manage(self, bars: Sequence[Bar], now: datetime) -> None:
        close = bars[-1].close
        for p in self.broker.positions():
            if p.magic != self.magic or p.ticket in self.thesis_exits:
                continue
            rec = self.journal.by_ticket(p.ticket)
            if rec is None or rec.setup != "A":
                continue
            thesis = rec.thesis_below if rec.thesis_below is not None else self.cfg.levels.a_zone_bot
            if close < thesis and self.broker.close_position(p.ticket):
                self.thesis_exits.add(p.ticket)
                self._emit(now, "thesis_exit", f"{rec.id} H1 close {close:.1f} < {thesis:.0f}")

    # --- trade -----------------------------------------------------------
    def levels_expired(self, now: datetime) -> bool:
        """Weekly levels past their last day may still manage open trades but never open new ones."""
        until = self.cfg.levels.valid_until
        return bool(until) and not self.cfg.auto.enabled and self._local_day(now) > date.fromisoformat(until)

    def _maybe_trade(self, bars: Sequence[Bar], now: datetime) -> None:
        if any(r.status == "open" and r.id.startswith("#T") for r in self.journal.records.values()):
            return
        if not self.levels_ok:
            return
        if self.levels_expired(now):
            if self.expired_noted != self.day_key:
                self.expired_noted = self.day_key
                until = self.cfg.levels.valid_until
                self._emit(now, "levels_expired", f"เส้นราคาหมดอายุ ({until}) → ไม่เปิดไม้ใหม่ จนกว่าจะตั้งเส้นใหม่ (levels.bat)")
            return
        result = self.ghost.evaluate(bars)
        if result.signal is None:
            if result.reason != "no_setup":
                self._emit(now, "ghost", result.reason)
            return
        bid, ask = self.broker.quote()
        sig = reprice(result.signal, ask if result.signal.side == "buy" else bid)
        if sig.entry >= sig.tp1 or sig.rr < self.cfg.ghost.min_rr:
            self._emit(now, "ghost", f"{sig.setup}: rr {sig.rr:.2f} after spread below min")
            return
        self._emit(
            now,
            "ghost",
            f"call {sig.setup}/{sig.variant} entry {sig.entry:.1f} stop {sig.stop:.1f} tp1 {sig.tp1:.0f} rr {sig.rr:.2f}",
        )
        mark = len(self.events)
        self._decide(sig, bars, now)
        last = self.events[-1] if len(self.events) > mark else EngineEvent(now, "none", "")
        self.calls.append(CallMark(sig.bar_time, sig.setup, sig.entry, sig.stop, sig.tp1, last.kind, last.detail))

    def _decide(self, sig: Signal, bars: Sequence[Bar], now: datetime) -> None:
        """Ω → Risk → Zen → approval → order for one Ghost call."""
        tag = self.omega.tag(bars, sig)
        if not tag.favorable:
            self._emit(now, "omega", f"ไม่เอื้อ ({tag.regime}): {tag.reason}")
            if self.cfg.omega.unfavorable_action == "veto":
                return

        ctx = RiskContext(
            account=self.broker.account(),
            positions=self.broker.positions(),
            spec=self.broker.spec(),
            day_start_equity=self.day_start_equity,
            peak_equity=self.peak_equity,
            bot_magic=self.magic,
        )
        decision = self.risk.check(sig, ctx)
        for w in decision.warnings:
            self._emit(now, "risk_warn", w)
        if not decision.ok:
            self._emit(now, "risk_veto", ", ".join(decision.vetoes))
            return

        zen = self.zen.check(sig, decision, self.journal.ready())
        if not zen.ok:
            self._emit(now, "zen_block", ", ".join(zen.failed))
            return

        trade_id = self.journal.next_id()
        summary = (
            f"{trade_id} | {sig.setup}/{sig.variant} | Ω {tag.regime} | "
            f"เข้า {sig.entry:.1f} / หยุด {sig.stop:.1f} / TP1 {sig.tp1:.0f} | lot {decision.lot:.2f} | "
            f"เสี่ยง {decision.risk_money:.2f} {ctx.account.currency} ({decision.risk_pct:.2f}%) | RR {sig.rr:.2f}"
        )
        if not self.approver.approve(summary, trade_id):
            self._emit(now, "not_approved", summary)
            return
        # Approval can take a while; re-check the live price before pressing.
        bid, ask = self.broker.quote()
        sig = reprice(sig, ask if sig.side == "buy" else bid)
        fresh = self.risk.check(sig, replace(ctx, account=self.broker.account(), positions=self.broker.positions()))
        if sig.entry >= sig.tp1 or sig.rr < self.cfg.ghost.min_rr or not fresh.ok:
            why = ", ".join(fresh.vetoes) or f"rr {sig.rr:.2f}"
            self._emit(now, "stale_after_approval", f"{trade_id} price now {sig.entry:.1f}: {why}")
            return
        ticket = self.broker.open_market(
            sig.side, decision.lot, sig.stop, sig.tp1, self.magic, f"ANON {trade_id} {sig.setup}"
        )
        if ticket is None:
            self._emit(now, "dry_run", summary)
            return
        self.journal.save(
            TradeRecord(
                id=trade_id,
                ticket=ticket,
                setup=sig.setup,
                variant=sig.variant,
                regime=tag.regime,
                entry_plan=sig.entry,
                stop=sig.stop,
                tp1=sig.tp1,
                lot=decision.lot,
                on_plan=True,
                opened_at=now.isoformat(),
                note=sig.note,
                thesis_below=self.levels.a_zone_bot if sig.setup == "A" else None,
            )
        )
        self._emit(now, "order", summary)

    # --- persistence (live) ----------------------------------------------
    def to_state(self) -> dict:
        return {
            "zen": self.zen.to_dict(),
            "known_foreign": sorted(self.known_foreign),
            "thesis_exits": sorted(self.thesis_exits),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "day_key": self.day_key,
            "day_start_equity": self.day_start_equity,
            "peak_equity": self.peak_equity,
        }  # auto levels are redrawn from history after a restart, so they are not stored

    def load_state(self, data: dict) -> None:
        self.zen = Zen(self.cfg.zen, ZenState(**data["zen"]))
        self.known_foreign = set(data["known_foreign"])
        self.thesis_exits = set(data["thesis_exits"])
        self.started_at = datetime.fromisoformat(data["started_at"]) if data["started_at"] else None
        self.last_sync = datetime.fromisoformat(data["last_sync"]) if data["last_sync"] else None
        self.day_key = data["day_key"]
        self.day_start_equity = data["day_start_equity"]
        self.peak_equity = data["peak_equity"]
