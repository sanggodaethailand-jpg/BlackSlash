"""Command line: math | backtest | demo | report | live"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from anon.config import Config, load_config
from anon.quant import Journal, auto_live_allowed, compute_stats, format_report

log = logging.getLogger("anon")


def cmd_math(cfg: Config, equity: float, usdthb: float | None) -> None:
    lv, rk, ex, g = cfg.levels, cfg.risk, cfg.execution, cfg.ghost
    per_point = rk.lot * ex.contract_size
    print(f"สมมติ contract size {ex.contract_size} → lot {rk.lot} = {per_point} {'USD'} ต่อ 1 จุด (เช็ก Specification ใน MT5)")
    max_stop = equity * rk.risk_per_trade_pct / 100 / per_point
    print(f"พอร์ต {equity:,.0f} USD → stop ได้ไกลสุด {max_stop:,.0f} จุด ต่อไม้ (≤{rk.risk_per_trade_pct}%)")
    a_stop = lv.a_zone_bot - g.stop_buffer
    for label, entry in (("A ขอบบน", lv.a_zone_top + ex.spread), ("A กลางโซน", (lv.a_zone_bot + lv.a_zone_top) / 2 + ex.spread)):
        risk = (entry - a_stop) * per_point
        rr = (lv.tp1 - entry) / (entry - a_stop)
        need = risk / (rk.risk_per_trade_pct / 100)
        print(f"{label}: เข้า {entry:,.0f} หยุด {a_stop:,.0f} → เสี่ยง {risk:.2f} USD, RR {rr:.2f}, ต้องมีพอร์ต ≥ {need:,.0f} USD")
    b_stop = lv.b_inv_ref - g.stop_buffer
    b_max = (lv.tp1 + g.min_rr * b_stop) / (1 + g.min_rr)
    print(f"B (ตัวอย่าง stop {b_stop:,.0f}): เข้าได้ไม่เกิน {b_max:,.0f} ถึงจะได้ RR ≥ {g.min_rr} "
          f"→ หน้าต่าง {lv.gray_high:,.0f}–{b_max:,.0f} ({max(0.0, b_max - lv.gray_high):,.0f} จุด)")
    print(f"เพดานวัน {rk.daily_loss_pct}% ของ {equity:,.0f} = {equity * rk.daily_loss_pct / 100:,.2f} USD")
    if usdthb:
        pts = 10_000 / usdthb / per_point
        print(f"10,000 บาท/วัน ที่ lot {rk.lot}: ต้องได้ {pts:,.0f} จุดต่อวัน → VETO ถูกต้อง (ไม่ใช้เป็น KPI)")


def cmd_backtest(cfg: Config, csv_path: str, journal_path: str | None, show_events: bool) -> None:
    from anon.backtest import run_backtest
    from anon.data import load_bars

    bars = load_bars(csv_path, cfg.execution.server_utc_offset_hours)
    result = run_backtest(cfg, bars, Journal(journal_path) if journal_path else None)
    _print_result(result, show_events, f"{len(bars)} bars {bars[0].time:%Y-%m-%d} → {bars[-1].time:%Y-%m-%d}")


def cmd_demo(cfg: Config) -> None:
    from anon.backtest import run_backtest
    from anon.synthetic import waypoint_bars

    path = [84000, 84900, 84200, 84800, 84100, 84600, 83900, 84500, 83800, 84200,
            83300, 82950, 83400, 84000, 84650, 84700, 84950, 85200, 84980, 85300, 85600]
    bars = waypoint_bars(path, bars_per_leg=6, noise=40, seed=3)
    result = run_backtest(cfg, bars)
    print("DEMO บนข้อมูลสังเคราะห์ — ทดสอบกลไกเท่านั้น ไม่ได้พิสูจน์ว่าได้กำไรในตลาดจริง\n")
    _print_result(result, True, f"{len(bars)} synthetic bars")


def _print_result(result, show_events: bool, header: str) -> None:
    print(header)
    if show_events:
        for e in result.events:
            if e.kind != "ghost" or e.detail.startswith("call"):
                print(" ", e)
    acct = result.broker.account()
    print(f"\nbalance {acct.balance:,.2f} equity {acct.equity:,.2f} {acct.currency}")
    kinds: dict[str, int] = {}
    for e in result.events:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    print("events:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print()
    print(format_report(result.stats, list(result.journal.records.values())))


def cmd_report(journal_path: str) -> None:
    journal = Journal(journal_path)
    records = list(journal.records.values())
    print(format_report(compute_stats(records), records))


def cmd_live(cfg: Config, confirm_live: bool, poll_s: float) -> None:
    from anon.approval import AutoApprover, ManualApprover
    from anon.broker.mt5 import MT5Broker
    from anon.engine import Engine

    ex = cfg.execution
    if not confirm_live and not ex.dry_run:
        print("ไม่มี --confirm-live → บังคับ dry-run")
    if not confirm_live:
        cfg = replace(cfg, execution=replace(ex, dry_run=True))
        ex = cfg.execution

    journal = Journal(ex.journal_path)
    if ex.approval == "auto":
        allowed, why = auto_live_allowed(compute_stats(list(journal.records.values())), ex.live_auto_min_n, ex.live_auto_min_prob)
        if not allowed and not ex.dry_run:
            sys.exit(f"auto-approve ถูกปฏิเสธ: Quant ยังไม่ผ่านเกณฑ์ ({why}). ใช้ approval = \"manual\" ไปก่อน")
        approver = AutoApprover()
    else:
        approver = ManualApprover()

    ai = None
    if cfg.omega.ai_enabled:
        from anon.omega_ai import ClaudeReviewer

        ai = ClaudeReviewer(cfg.levels, cfg.omega)

    broker = MT5Broker(ex)
    broker.connect()
    engine = Engine(cfg, broker, approver, journal, ai)
    state_path = Path(ex.state_path)
    if state_path.exists():
        engine.load_state(json.loads(state_path.read_text(encoding="utf-8")))
    print(f"ANON live on {ex.symbol} | dry_run={ex.dry_run} | approval={ex.approval} | AI={'on' if ai else 'off'}")
    last_bar = None
    try:
        while True:
            bars = broker.closed_bars(300)
            if bars and bars[-1].time != last_bar:
                last_bar = bars[-1].time
                try:
                    for event in engine.on_bar_close(bars):
                        print(event, flush=True)
                except Exception:  # noqa: BLE001 - keep the loop alive; SL/TP stay on the broker
                    log.exception("bar %s failed", last_bar)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(engine.to_state(), ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(poll_s)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        broker.shutdown()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="anon", description="ANON BTCUSD system")
    parser.add_argument("--config", default=None, help="TOML config (default: built-in handoff values)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("math", help="พิสูจน์ตัวเลข Risk จาก config")
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--usdthb", type=float, default=None)

    p = sub.add_parser("backtest", help="replay CSV bars through the full pipeline")
    p.add_argument("--csv", required=True)
    p.add_argument("--journal", default=None)
    p.add_argument("--events", action="store_true")

    sub.add_parser("demo", help="synthetic walk-through of every gate")

    p = sub.add_parser("report", help="Quant report from a journal")
    p.add_argument("--journal", default="journal/anon_journal.jsonl")

    p = sub.add_parser("live", help="run on MetaTrader 5 (dry-run unless --confirm-live)")
    p.add_argument("--confirm-live", action="store_true")
    p.add_argument("--poll", type=float, default=15.0)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.cmd == "math":
        cmd_math(cfg, args.equity, args.usdthb)
    elif args.cmd == "backtest":
        cmd_backtest(cfg, args.csv, args.journal, args.events)
    elif args.cmd == "demo":
        cmd_demo(cfg)
    elif args.cmd == "report":
        cmd_report(args.journal)
    elif args.cmd == "live":
        cmd_live(cfg, args.confirm_live, args.poll)
