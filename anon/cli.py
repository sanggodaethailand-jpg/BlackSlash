"""Command line: doctor | math | export | backtest | demo | report | live"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import platform
import sys
import time
from dataclasses import replace
from pathlib import Path

from anon.config import Config, load_config, static_offset_hours, unconfirmed_values
from anon.quant import Journal, auto_live_allowed, compute_stats, format_report

log = logging.getLogger("anon")


def _mt5(cfg: Config):
    from anon.broker.mt5 import MT5Broker

    broker = MT5Broker(cfg.execution)
    broker.connect()
    return broker


def _cent_note(ccy: str) -> str:
    return " (บัญชีเซ็นต์: 100 USC = 1 USD)" if ccy == "USC" else ""


def cmd_math(cfg: Config, equity: float, usdthb: float | None, use_mt5: bool) -> None:
    lv, rk, g = cfg.levels, cfg.risk, cfg.ghost
    if use_mt5:
        broker = _mt5(cfg)
        try:
            spec, account = broker.spec(), broker.account()
            bid, ask = broker.quote()
        finally:
            broker.shutdown()
        equity, ccy, money_per_point, spread = account.equity, account.currency, spec.money_per_point, ask - bid
        print(f"จาก MT5: equity {equity:,.2f} {ccy}{_cent_note(ccy)} · contract size {spec.contract_size} · spread {spread:.1f}")
        source = "จากโบรกเกอร์"
    else:
        ccy, money_per_point, spread = "USD", cfg.execution.contract_size, cfg.execution.spread
        source = "สมมติ — ใช้ --mt5 เพื่ออ่านค่าจริง"
    per_point = rk.lot * money_per_point
    print(f"lot {rk.lot} = {per_point:,.4g} {ccy} ต่อ 1 จุด ({source})")
    max_stop = equity * rk.risk_per_trade_pct / 100 / per_point
    print(f"พอร์ต {equity:,.0f} {ccy} → stop ได้ไกลสุด {max_stop:,.0f} จุด ต่อไม้ (≤{rk.risk_per_trade_pct}%)")
    a_stop = lv.a_zone_bot - g.stop_buffer
    for label, entry in (("A ขอบบน", lv.a_zone_top + spread), ("A กลางโซน", (lv.a_zone_bot + lv.a_zone_top) / 2 + spread)):
        risk = (entry - a_stop) * per_point
        rr = (lv.tp1 - entry) / (entry - a_stop)
        need = risk / (rk.risk_per_trade_pct / 100)
        print(f"{label}: เข้า {entry:,.0f} หยุด {a_stop:,.0f} → เสี่ยง {risk:,.2f} {ccy} ({risk / equity * 100:.2f}%), "
              f"RR {rr:.2f}, ต้องมีพอร์ต ≥ {need:,.0f} {ccy}")
    b_stop = lv.b_inv_ref - g.stop_buffer
    b_max = (lv.tp1 + g.min_rr * b_stop) / (1 + g.min_rr)
    print(f"B (ตัวอย่าง stop {b_stop:,.0f}): เข้าได้ไม่เกิน {b_max:,.0f} ถึงจะได้ RR ≥ {g.min_rr} "
          f"→ หน้าต่าง {lv.gray_high:,.0f}–{b_max:,.0f} ({max(0.0, b_max - lv.gray_high):,.0f} จุด)")
    print(f"เพดานวัน {rk.daily_loss_pct}% ของ {equity:,.0f} = {equity * rk.daily_loss_pct / 100:,.2f} {ccy}")
    if usdthb and ccy in ("USD", "USC"):
        target = 10_000 / usdthb * (100 if ccy == "USC" else 1)
        pts = target / per_point
        print(f"10,000 บาท/วัน ที่ lot {rk.lot}: ต้องได้ {pts:,.0f} จุดต่อวัน → VETO ถูกต้อง (ไม่ใช้เป็น KPI)")


def _fresh_backtest_journal(cfg: Config, journal_path: str | None) -> Journal | None:
    if not journal_path:
        return None
    path = Path(journal_path)
    if path.resolve() == Path(cfg.execution.journal_path).resolve():
        sys.exit("backtest ห้ามเขียนทับ journal ของ live — ใช้ไฟล์อื่น เช่น journal/backtest.jsonl")
    if path.exists():
        path.unlink()  # a backtest is reproducible; start its journal clean
    return Journal(path)


def cmd_backtest(
    cfg: Config, csv_path: str | None, days: int | None, journal_path: str | None, show_events: bool
) -> None:
    from anon.backtest import run_backtest

    spec = spread = balance = None
    currency = "USD"
    if csv_path:
        from anon.data import load_bars

        bars = load_bars(csv_path, static_offset_hours(cfg))
        source = csv_path
    else:
        broker = _mt5(cfg)
        try:
            bars = broker.closed_bars(days * 24)
            spec, account = broker.spec(), broker.account()
            bid, ask = broker.quote()
        finally:
            broker.shutdown()
        spread, balance, currency = ask - bid, account.equity, account.currency
        source = (f"MT5 {cfg.execution.symbol} H1 {days} วัน (spread ตอนนี้ {spread:.1f}, "
                  f"เริ่มที่ equity จริง {balance:,.2f} {currency}{_cent_note(currency)})")
    if not bars:
        sys.exit("ไม่มีแท่งให้ backtest")
    result = run_backtest(
        cfg,
        bars,
        _fresh_backtest_journal(cfg, journal_path),
        spec=spec,
        spread=spread,
        starting_balance=balance,
        currency=currency,
    )
    _print_result(result, show_events, f"{source}\n{len(bars)} bars {bars[0].time:%Y-%m-%d} → {bars[-1].time:%Y-%m-%d}")
    if result.stats.n < cfg.execution.live_auto_min_n:
        print(f"\n⚠️ ไม้ตามแผน n={result.stats.n} < {cfg.execution.live_auto_min_n} — ยังสรุปไม่ได้ว่าระบบได้เปรียบ")


def cmd_export(cfg: Config, days: int, out: str) -> None:
    broker = _mt5(cfg)
    try:
        bars = broker.closed_bars(days * 24)
    finally:
        broker.shutdown()
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "open", "high", "low", "close"])
        for b in bars:
            writer.writerow([b.time.isoformat(), b.open, b.high, b.low, b.close])
    print(f"บันทึก {len(bars)} แท่ง (เวลา UTC) → {path}")


def cmd_doctor(cfg: Config) -> int:
    problems = 0

    def row(ok: bool | None, text: str) -> None:
        nonlocal problems
        mark = {True: "OK ", False: "XX ", None: "!! "}[ok]
        problems += ok is False
        print(f"[{mark}] {text}")

    row(sys.version_info >= (3, 11), f"Python {platform.python_version()} ({sys.executable})")
    ex = cfg.execution
    row(True, f"โหมด dry_run={ex.dry_run} · approval={ex.approval} · journal {ex.journal_path}")
    if ex.confirmed_by:
        row(True, f"ค่าที่ต้องยืนยันถูกเคาะแล้วโดย: {ex.confirmed_by}")
    else:
        row(None, "ยังไม่มีคนเคาะค่าที่ต้องยืนยัน (execution.confirmed_by ว่าง) → ส่งออเดอร์จริงไม่ได้")
        for item in unconfirmed_values(cfg):
            print(f"        - {item}")

    if cfg.omega.ai_enabled:
        try:
            import anthropic  # noqa: F401

            has_sdk = True
        except ImportError:
            has_sdk = False
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        row(has_sdk, "anthropic SDK ติดตั้งแล้ว" if has_sdk else "ต้อง pip install anthropic")
        key_note = "มี credential ของ Claude" if has_key else "ไม่เห็น ANTHROPIC_API_KEY (ถ้าใช้ ant auth login อยู่แล้วข้ามได้)"
        row(has_key or None, key_note)
    else:
        row(True, "Ω AI ปิดอยู่ (ใช้ EMA/ATR อย่างเดียว)")

    try:
        import MetaTrader5  # noqa: F401
    except ImportError:
        row(False, "ไม่มีแพ็กเกจ MetaTrader5 (pip install MetaTrader5 — ใช้ได้บน Windows)")
        return problems
    try:
        broker = _mt5(cfg)
    except Exception as exc:  # noqa: BLE001 - report every connection failure the same way
        row(False, f"ต่อ MT5 ไม่ได้: {exc} (เปิด MT5 และล็อกอินไว้ก่อน)")
        return problems
    try:
        info = broker.mt5.account_info()
        mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(int(info.trade_mode), str(info.trade_mode))
        row(True, f"บัญชี {info.login} @ {info.server} · {mode} · {info.currency} · "
                  f"balance {info.balance:,.2f} equity {info.equity:,.2f}")
        if mode == "REAL" and not ex.dry_run:
            row(None, "บัญชีจริง + dry_run=false → ทุกไม้จะใช้เงินจริง")
        elif mode == "REAL":
            row(None, "บัญชี REAL: dry-run ไม่ส่งออเดอร์ แต่ก่อนส่งจริงให้ทดสอบบนบัญชี DEMO ก่อน")
        spec = broker.spec()
        row(True, f"{ex.symbol}: contract size {spec.contract_size} · lot min {spec.volume_min} step {spec.volume_step} · "
                  f"lot {cfg.risk.lot} = {cfg.risk.lot * spec.money_per_point:,.4g} {info.currency} ต่อ 1 จุด")
        if info.currency == "USC":
            row(None, "บัญชีเซ็นต์ (USC): ตัวเลขเงินทั้งหมดเป็นเซ็นต์ 100 USC = 1 USD — Risk คิดเป็น USC ให้แล้ว")
        steps = cfg.risk.lot / spec.volume_step
        tradable = cfg.risk.lot >= spec.volume_min - 1e-9 and abs(steps - round(steps)) < 1e-6
        row(tradable, f"lot {cfg.risk.lot} {'ส่งได้' if tradable else 'ส่งไม่ได้'}กับโบรกเกอร์นี้")
        bid, ask = broker.quote()
        offset_h = broker.offset.total_seconds() / 3600
        row(True, f"ราคา bid {bid:,.1f} ask {ask:,.1f} · spread {ask - bid:.1f} · เวลาเซิร์ฟเวอร์ UTC{offset_h:+.1f}")
        bars = broker.closed_bars(24 * 365)
        enough = len(bars) >= cfg.omega.ema_slow + 1
        row(enough, f"ประวัติ H1: {len(bars)} แท่ง ({bars[0].time:%Y-%m-%d} → {bars[-1].time:%Y-%m-%d})" if bars else "ไม่มีประวัติ H1")
        for p in broker.positions():
            if p.ticket in cfg.risk.veto_tickets:
                row(None, f"#{p.ticket} {p.side} {p.volume} ยังเปิดอยู่ → Risk จะ VETO ไม้ใหม่จนกว่าจะปิด")
            elif p.magic != ex.magic:
                row(None, f"#{p.ticket} {p.side} {p.volume} เป็นไม้นอกระบบ → Risk จะ VETO ไม้ใหม่")
            else:
                row(True, f"#{p.ticket} ไม้ตามแผน SL {p.sl} TP {p.tp}")
    finally:
        broker.shutdown()
    print("\nพร้อม" if problems == 0 else f"\nมีปัญหา {problems} ข้อ")
    return problems


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
    if confirm_live and not ex.dry_run and not ex.confirmed_by:
        sys.exit("ส่งออเดอร์จริงไม่ได้: ใส่ชื่อคนเคาะค่าใน execution.confirmed_by ก่อน (ดู anon doctor)")
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

    sub.add_parser("doctor", help="ตรวจความพร้อม: Python, config, MT5, บัญชี, สัญลักษณ์, ไม้ที่เปิด")

    p = sub.add_parser("math", help="พิสูจน์ตัวเลข Risk จาก config")
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--usdthb", type=float, default=None)
    p.add_argument("--mt5", action="store_true", help="read equity, contract size and spread from MT5")

    p = sub.add_parser("export", help="save closed H1 bars from MT5 to CSV (UTC)")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--out", default="data/BTCUSD_H1.csv")

    p = sub.add_parser("backtest", help="replay H1 bars (CSV or MT5 history) through the full pipeline")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--mt5", action="store_true", help="pull history straight from MT5")
    p.add_argument("--days", type=int, default=365)
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
    if args.cmd == "doctor":
        sys.exit(1 if cmd_doctor(cfg) else 0)
    elif args.cmd == "math":
        cmd_math(cfg, args.equity, args.usdthb, args.mt5)
    elif args.cmd == "export":
        cmd_export(cfg, args.days, args.out)
    elif args.cmd == "backtest":
        cmd_backtest(cfg, args.csv, args.days if args.mt5 else None, args.journal, args.events)
    elif args.cmd == "demo":
        cmd_demo(cfg)
    elif args.cmd == "report":
        cmd_report(args.journal)
    elif args.cmd == "live":
        cmd_live(cfg, args.confirm_live, args.poll)
