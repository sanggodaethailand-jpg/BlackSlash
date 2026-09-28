"""Command line: doctor | math | export | backtest | sweep | lab | demo | report | live | golive | levels"""

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


def _history(cfg: Config, csv_path: str | None, days: int | None) -> tuple[list, str, dict]:
    """Bars plus the broker's spec/spread/equity/currency (MT5), or bars from a CSV with config defaults."""
    if csv_path:
        from anon.data import load_bars

        bars, source, market = load_bars(csv_path, static_offset_hours(cfg)), csv_path, {}
    else:
        broker = _mt5(cfg)
        try:
            bars = broker.closed_bars(days * 24)
            spec, account = broker.spec(), broker.account()
            bid, ask = broker.quote()
        finally:
            broker.shutdown()
        market = {
            "spec": spec,
            "spread": ask - bid,
            "starting_balance": account.equity,
            "currency": account.currency,
        }
        source = (f"MT5 {broker.symbol} H1 {days} วัน (spread ตอนนี้ {ask - bid:.1f}, "
                  f"เริ่มที่ equity จริง {account.equity:,.2f} {account.currency}{_cent_note(account.currency)})")
    if not bars:
        sys.exit("ไม่มีแท่งให้ backtest")
    return bars, source, market


def cmd_sweep(
    cfg: Config,
    csv_path: str | None,
    days: int | None,
    rrs: list[float],
    lookbacks: list[int],
    null_trials: int = 0,
    target: tuple[int, float] = (72, 1.5),
) -> None:
    from anon.research import format_null, format_sweep, run_null_test, run_sweep

    bars, source, market = _history(cfg, csv_path, days)
    print(f"{source}\n{len(bars)} bars {bars[0].time:%Y-%m-%d} → {bars[-1].time:%Y-%m-%d} · ระดับอัตโนมัติ · "
          f"ลอง {len(rrs) * len(lookbacks)} แบบ + เทียบข้อมูลสุ่ม {null_trials} ชุด (อาจใช้เวลาหลายนาที)\n", flush=True)
    rows = run_sweep(cfg, bars, rrs, lookbacks, **market)
    print(format_sweep(rows, current=target), flush=True)
    if null_trials > 0:
        print()
        print(format_null(run_null_test(cfg, bars, target[0], target[1], null_trials, **market)))


def cmd_lab(
    cfg: Config, csv_path: str | None, names: list[str], trials: int | None, list_only: bool, smoke: bool, workers: int = 1,
    forward: bool = False,
) -> None:
    from anon.data import load_bars
    from anon.lab.core import Costs, idea_hash
    from anon.lab.gauntlet import HOLDOUT_FRACTION, format_report, run_gauntlet
    from anon.lab.ideas import load_ideas
    from anon.lab.ledger import Ledger, threshold

    lab = cfg.lab
    ledger = Ledger.open(None if smoke else lab.ledger)
    if list_only:
        attempts, results = ledger.attempts(), ledger.results()
        print(f"สมุดทะเบียน {lab.ledger}: ลองแล้ว k={len(attempts)} → ไอเดียถัดไปต้องได้ p ≤ {threshold(len(attempts) + 1):.4f}")
        for key in attempts:
            res = results.get(key)
            verdict = "ยังไม่มีผล" if res is None else "✅ ผ่าน" if res["passed"] else f"❌ ตกด่าน {res['failed_gate']}"
            print(f"  {key[0]} ({key[1]}): {verdict}")
        return
    if forward:
        from anon.lab.forward import format_forward, run_forward, watches

        if not watches(ledger):
            sys.exit("ยังไม่มีไอเดียที่ลงทะเบียนเฝ้าดูในสมุด")
        if not csv_path:
            sys.exit("ต้องใส่ --csv ไฟล์แท่ง H1 (anon export)")
        bars = load_bars(csv_path, static_offset_hours(cfg))
        costs = Costs(lab.spread, lab.slippage, lab.swap_mode, lab.swap_long, lab.swap_short, lab.point,
                      tuple(lab.swap_days), lab.rollover, lab.commission)
        for report in run_forward(ledger, load_ideas(), bars, costs):
            print(format_forward(report))
        return
    if smoke:
        from anon.lab.ideas._template import IDEA

        ideas = {IDEA.name: IDEA}
        trials = trials or 20
    else:
        ideas = load_ideas()
        if names:
            missing = [n for n in names if n not in ideas]
            if missing:
                sys.exit(f"ไม่มีไอเดียชื่อ {', '.join(missing)} (มี: {', '.join(ideas) or '-'})")
            ideas = {n: ideas[n] for n in names}
    if not ideas:
        sys.exit("ยังไม่มีไอเดียลงทะเบียนใน anon/lab/ideas/ (ดู AGENTS.md และ _template.py)")
    if not csv_path:
        sys.exit("ต้องใส่ --csv ไฟล์แท่ง H1 (anon export)")
    bars = load_bars(csv_path, static_offset_hours(cfg))
    costs = Costs(lab.spread, lab.slippage, lab.swap_mode, lab.swap_long, lab.swap_short, lab.point,
                  tuple(lab.swap_days), lab.rollover, lab.commission)
    if smoke:
        print("ทดลองเครื่อง: รันแม่แบบ ไม่บันทึกลงสมุด ผลนี้ไม่ใช่หลักฐานอะไร\n")
    cut = int(len(bars) * (1 - HOLDOUT_FRACTION))
    data = {"bars": len(bars), "first": bars[0].time.isoformat(), "last": bars[-1].time.isoformat(),
            "locked_from": bars[cut].time.isoformat()}
    for idea in ideas.values():  # the whole round is on record before the first result
        ledger.register(idea.name, idea_hash(idea), idea.primary, idea.grid, data)
    k = len(ledger.attempts())
    print(f"ลงทะเบียน {len(ideas)} ไอเดีย · ลองแล้วทั้งหมด k={k} → ทุกตัวต้องได้ p ≤ {threshold(k):.5f}\n", flush=True)
    for idea in ideas.values():
        print(f"กำลังทดสอบ {idea.name} ...", flush=True)
        report = run_gauntlet(idea, bars, costs, ledger, trials=trials, workers=workers,
                              progress=lambda msg: print(msg, flush=True))
        print(format_report(report), flush=True)
        print()


def cmd_backtest(
    cfg: Config,
    csv_path: str | None,
    days: int | None,
    journal_path: str | None,
    show_events: bool,
    auto: bool = False,
    min_rr: float | None = None,
    summary: bool = False,
) -> None:
    from anon.backtest import run_backtest

    if auto:
        cfg = replace(cfg, auto=replace(cfg.auto, enabled=True))
    if min_rr is not None:
        cfg = replace(cfg, ghost=replace(cfg.ghost, min_rr=min_rr))
    cfg.validate()

    bars, source, market = _history(cfg, csv_path, days)
    result = run_backtest(cfg, bars, _fresh_backtest_journal(cfg, journal_path), **market)
    mode = (f"ระดับอัตโนมัติ (ย้อน {cfg.auto.lookback_bars} แท่ง วาดใหม่ทุกวัน)" if cfg.auto.enabled
            else "ระดับจาก config")
    header = (f"{source}\n{len(bars)} bars {bars[0].time:%Y-%m-%d} → {bars[-1].time:%Y-%m-%d} · "
              f"{mode} · min RR {cfg.ghost.min_rr}")
    _print_result(result, show_events, header, show_trades=not summary)
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

    lv = cfg.levels
    if cfg.auto.enabled:
        row(True, "เส้นราคา: อัตโนมัติ (วาดใหม่ทุกวันจากกรอบราคาล่าสุด)")
    else:
        from anon.levelset import local_today

        lines = (f"เส้นราคา: A {lv.a_zone_bot:,.0f}–{lv.a_zone_top:,.0f} · เทา {lv.gray_low:,.0f}–{lv.gray_high:,.0f} · "
                 f"TP1 {lv.tp1:,.0f}")
        if not lv.valid_until:
            row(None, lines + " · ไม่มีวันหมดอายุ (ตั้งเส้นประจำสัปดาห์ด้วย levels.bat)")
        elif local_today(cfg).isoformat() > lv.valid_until:
            row(None if ex.dry_run else False, lines + f" · หมดอายุแล้ว ({lv.valid_until}) → ไม่เปิดไม้ใหม่ ตั้งเส้นใหม่ด้วย levels.bat")
        else:
            row(True, lines + f" · ใช้ได้ถึง {lv.valid_until}")

    from anon.chartfeed import resolve_feed_path

    feed_path = resolve_feed_path(ex.chart_feed)
    if feed_path:
        row(True, f"ส่งข้อมูลขึ้นกราฟ: {feed_path}")
    else:
        row(None, "ไม่ได้ส่งข้อมูลขึ้นกราฟ (chart_feed ปิด หรือไม่ใช่ Windows)")

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
        if broker.symbol != ex.symbol:
            row(None, f"โบรกเกอร์นี้ไม่มี {ex.symbol} → dry-run ใช้ {broker.symbol} แทน "
                      f"(ส่งออเดอร์จริงต้องใส่ symbol = \"{broker.symbol}\" ใน config เอง)")
        terminal = getattr(broker.mt5, "terminal_info", lambda: None)()
        algo = getattr(terminal, "trade_allowed", None)
        if algo is not None and ex.dry_run:
            row(True if algo else None, f"Algo Trading ใน MT5 {'เปิด' if algo else 'ปิด'}อยู่ (dry-run ไม่ต้องใช้ · ส่งจริงต้องเปิด)")
        elif algo is not None:
            row(bool(algo), "Algo Trading ใน MT5 เปิดอยู่" if algo else "Algo Trading ใน MT5 ปิดอยู่ → กดปุ่ม Algo Trading ให้เป็นสีเขียว")
        spec = broker.spec()
        row(True, f"{broker.symbol}: contract size {spec.contract_size} · lot min {spec.volume_min} step {spec.volume_step} · "
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


def _print_result(result, show_events: bool, header: str, show_trades: bool = True) -> None:
    print(header)
    if show_events:
        for e in result.events:
            if e.kind not in ("ghost", "levels") or e.detail.startswith("call"):
                print(" ", e)
    acct = result.broker.account()
    print(f"\nbalance {acct.balance:,.2f} equity {acct.equity:,.2f} {acct.currency}")
    kinds: dict[str, int] = {}
    for e in result.events:
        kinds[e.kind] = kinds.get(e.kind, 0) + 1
    print("events:", ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print()
    print(format_report(result.stats, list(result.journal.records.values()), show_trades=show_trades))


def cmd_report(journal_path: str) -> None:
    journal = Journal(journal_path)
    records = list(journal.records.values())
    print(format_report(compute_stats(records), records))


RECONNECT_MAX_WAIT_S = 120.0


def _reconnect(broker, error: Exception, failures: int, poll_s: float, dry_run: bool) -> None:
    """Wait (longer after each failure), then connect to the terminal again. Never gives up on
    its own: orders already sent keep their SL/TP at the broker. Stops only on another account."""
    from anon.broker.mt5 import AccountChanged

    wait = min(RECONNECT_MAX_WAIT_S, max(poll_s, 1.0) * 2 ** min(failures - 1, 6))
    print(
        f"MT5 หลุด ({error}) · ลองต่อใหม่ครั้งที่ {failures} ใน {wait:.0f} วินาที · "
        "ไม้ที่เปิดอยู่ยังมี SL/TP ที่โบรกเกอร์",
        flush=True,
    )
    time.sleep(wait)
    try:
        broker.reconnect()
    except AccountChanged as exc:
        sys.exit(f"หยุด: {exc}")
    except RuntimeError as exc:
        log.warning("reconnect %d failed: %s", failures, exc)
        return
    print(f"ต่อ MT5 ได้แล้ว ({broker.symbol}) · ทำงานต่อ", flush=True)
    terminal = getattr(broker.mt5, "terminal_info", lambda: None)()
    if not dry_run and getattr(terminal, "trade_allowed", True) is False:
        print("[!!] Algo Trading ใน MT5 ปิดอยู่หลังต่อใหม่: ไม้ใหม่จะส่งไม่ออก → กดปุ่ม Algo Trading ให้เป็นสีเขียว", flush=True)


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

    from anon.chartfeed import ChartFeed, resolve_feed_path

    broker = MT5Broker(ex)
    broker.connect()
    terminal = getattr(broker.mt5, "terminal_info", lambda: None)()
    if not ex.dry_run and getattr(terminal, "trade_allowed", True) is False:
        broker.shutdown()
        sys.exit("ส่งออเดอร์จริงไม่ได้: Algo Trading ใน MT5 ปิดอยู่ → กดปุ่ม Algo Trading ให้เป็นสีเขียวแล้วรันใหม่")
    engine = Engine(cfg, broker, approver, journal, ai)
    state_path = Path(ex.state_path)
    if state_path.exists():
        engine.load_state(json.loads(state_path.read_text(encoding="utf-8")))
    feed_path = resolve_feed_path(ex.chart_feed)
    feed = ChartFeed(feed_path) if feed_path else None
    print(f"ANON live on {broker.symbol} | dry_run={ex.dry_run} | approval={ex.approval} | AI={'on' if ai else 'off'}")
    print(f"กราฟ: {feed_path if feed else 'ปิด (execution.chart_feed)'}")
    last_bar = None
    failures = 0
    try:
        while True:
            try:
                bars = broker.closed_bars(300)
            except RuntimeError as exc:
                failures += 1
                _reconnect(broker, exc, failures, poll_s, ex.dry_run)
                continue
            failures = 0
            if bars and bars[-1].time != last_bar:
                last_bar = bars[-1].time
                try:
                    for event in engine.on_bar_close(bars):
                        print(event, flush=True)
                except Exception:  # noqa: BLE001 - keep the loop alive; SL/TP stay on the broker
                    log.exception("bar %s failed", last_bar)
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(engine.to_state(), ensure_ascii=False, indent=2), encoding="utf-8")
                if feed:
                    feed.update(engine, broker.symbol, ex.dry_run, ai is not None)
            if feed:
                feed.write()  # heartbeat every poll so the chart can tell the engine is alive
            time.sleep(poll_s)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        broker.shutdown()


def cmd_golive(cfg: Config, config_path: str | None, poll_s: float, input_fn=input) -> None:
    """Owner-only real-money launch: confirm, write the live config, check everything, then run."""
    from anon.golive import prepare_live_config

    base = Path(config_path) if config_path else Path("config") / "anon.example.toml"
    live = prepare_live_config(base, base.with_name("anon.live.toml"), cfg, input_fn)
    if live is None:
        sys.exit(1)
    print("\n== ตรวจความพร้อมก่อนส่งจริง ==")
    if cmd_doctor(live):
        sys.exit("หยุด: แก้ข้อ [XX] ด้านบนก่อน แล้วรัน live.bat ใหม่ (ยังไม่มีออเดอร์ถูกส่ง)")
    print()
    cmd_live(live, confirm_live=True, poll_s=poll_s)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="anon", description="ANON BTCUSD system")
    parser.add_argument("--config", default=None, help="TOML config (default: built-in handoff values)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="ตรวจความพร้อม: Python, config, MT5, บัญชี, สัญลักษณ์, ไม้ที่เปิด")

    p = sub.add_parser("math", help="พิสูจน์ตัวเลข Risk จาก config")
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--usdthb", type=float, default=None)
    p.add_argument("--mt5", action="store_true", help="read equity, contract size and spread from MT5")

    p = sub.add_parser("sweep", help="robustness: auto-levels backtest over a grid of min RR x lookback")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--mt5", action="store_true")
    p.add_argument("--days", type=int, default=1825)
    p.add_argument("--rr", default="1.0,1.25,1.5,1.75,2.0", help="comma-separated min RR values")
    p.add_argument("--lookback", default="48,72,96", help="comma-separated lookback bars")
    p.add_argument("--null", type=int, default=30, help="shuffled-history trials for the target setting (0 = skip)")
    p.add_argument("--target", default="72,1.5", help="lookback,min_rr to test against shuffled history")

    p = sub.add_parser("lab", help="Idea Lab: run registered ideas through the five gates (see AGENTS.md)")
    p.add_argument("--csv", help="H1 bars from anon export")
    p.add_argument("--ideas", default="", help="comma-separated idea names (default: all registered)")
    p.add_argument("--trials", type=int, default=None, help="shuffled-history trials (default: enough for p <= 0.05/k)")
    p.add_argument("--list", action="store_true", help="show the ledger: attempts so far and verdicts")
    p.add_argument("--smoke", action="store_true", help="machine check on the template idea; nothing is recorded")
    p.add_argument("--workers", type=int, default=1, help="processes for the null test (results do not depend on it)")
    p.add_argument("--forward", action="store_true", help="forward watch: judge watched ideas on bars after their date")

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
    p.add_argument("--auto", action="store_true", help="redraw levels daily from recent structure ([auto] in config)")
    p.add_argument("--min-rr", type=float, default=None, help="override ghost.min_rr for this run")
    p.add_argument("--summary", action="store_true", help="statistics only, no per-trade lines")

    sub.add_parser("demo", help="synthetic walk-through of every gate")

    p = sub.add_parser("report", help="Quant report from a journal")
    p.add_argument("--journal", default="journal/anon_journal.jsonl")

    p = sub.add_parser("live", help="run on MetaTrader 5 (dry-run unless --confirm-live)")
    p.add_argument("--confirm-live", action="store_true")
    p.add_argument("--poll", type=float, default=15.0)

    p = sub.add_parser("golive", help="owner only: confirm, write config/anon.live.toml, doctor, then trade for real")
    p.add_argument("--poll", type=float, default=15.0)

    sub.add_parser("levels", help="set this week's levels (and their last day) in the config, with checks")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.cmd == "doctor":
        sys.exit(1 if cmd_doctor(cfg) else 0)
    elif args.cmd == "math":
        cmd_math(cfg, args.equity, args.usdthb, args.mt5)
    elif args.cmd == "sweep":
        rrs = [float(x) for x in args.rr.split(",") if x.strip()]
        lookbacks = [int(x) for x in args.lookback.split(",") if x.strip()]
        lookback, rr = args.target.split(",")
        cmd_sweep(cfg, args.csv, args.days if args.mt5 else None, rrs, lookbacks, args.null, (int(lookback), float(rr)))
    elif args.cmd == "lab":
        names = [x.strip() for x in args.ideas.split(",") if x.strip()]
        cmd_lab(cfg, args.csv, names, args.trials, args.list, args.smoke, args.workers, args.forward)
    elif args.cmd == "export":
        cmd_export(cfg, args.days, args.out)
    elif args.cmd == "backtest":
        cmd_backtest(
            cfg, args.csv, args.days if args.mt5 else None, args.journal, args.events, args.auto, args.min_rr, args.summary
        )
    elif args.cmd == "demo":
        cmd_demo(cfg)
    elif args.cmd == "report":
        cmd_report(args.journal)
    elif args.cmd == "live":
        cmd_live(cfg, args.confirm_live, args.poll)
    elif args.cmd == "golive":
        cmd_golive(cfg, args.config, args.poll)
    elif args.cmd == "levels":
        from anon.levelset import cmd_levels

        sys.exit(0 if cmd_levels(cfg, args.config) else 1)
