"""Real-money launch for the owner (``anon golive``, ``live.bat``).

The owner types who is approving and a confirmation phrase on every launch. The dry-run
config is left untouched: the real-money settings go to a separate file next to it, with
the broker's exact symbol, per-trade manual approval, and its own journal and state.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from anon.config import Config, load_config

CONFIRM_PHRASE = "เงินจริง"
LIVE_JOURNAL = "journal/anon_live.jsonl"
LIVE_STATE = "state/anon_live_state.json"


def set_toml_keys(text: str, section: str, values: dict[str, str]) -> str:
    """Set ``key = value`` lines (values already TOML literals) inside ``[section]`` only."""
    lines = text.splitlines()
    header = f"[{section}]"
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == header)
    except StopIteration:
        lines += ["", header]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    missing = dict(values)
    for i in range(start + 1, end):
        match = re.match(r"\s*([A-Za-z0-9_]+)\s*=", lines[i])
        if match and match.group(1) in missing:
            key = match.group(1)
            lines[i] = f"{key} = {missing.pop(key)}  # anon golive"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    for key, value in missing.items():
        lines.insert(insert_at, f"{key} = {value}  # anon golive")
        insert_at += 1
    return "\n".join(lines) + "\n"


def live_config_text(base_text: str, symbol: str, owner: str) -> str:
    return set_toml_keys(
        base_text,
        "execution",
        {
            "symbol": json.dumps(symbol),
            "dry_run": "false",
            "confirmed_by": json.dumps(owner, ensure_ascii=False),
            "approval": '"manual"',
            "journal_path": json.dumps(LIVE_JOURNAL),
            "state_path": json.dumps(LIVE_STATE),
        },
    )


def resolve_symbol(cfg: Config) -> tuple[str, str]:
    """The broker's exact symbol (found in dry-run, which may take the suffixed name) and the account line."""
    from anon.broker.mt5 import MT5Broker

    broker = MT5Broker(replace(cfg.execution, dry_run=True))
    broker.connect()
    try:
        info = broker.mt5.account_info()
        mode = {0: "DEMO", 1: "CONTEST", 2: "REAL"}.get(int(info.trade_mode), str(info.trade_mode))
        account = f"{info.login} @ {info.server} · {mode} · {info.currency} · equity {info.equity:,.2f}"
        return broker.symbol, account
    finally:
        broker.shutdown()


def prepare_live_config(
    base_path: Path, live_path: Path, cfg: Config, input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> Config | None:
    """Ask the owner, then write and load the real-money config; None when the owner backs out."""
    symbol, account = resolve_symbol(cfg)
    output_fn(
        "\n== โหมดเงินจริง ==\n"
        f"บัญชี {account}\n"
        f"สัญลักษณ์ {symbol} · lot {cfg.risk.lot} · เสี่ยงไม่เกิน {cfg.risk.risk_per_trade_pct}% ต่อไม้ · "
        f"{cfg.risk.daily_loss_pct}% ต่อวัน\n"
        "ทุกไม้ยังต้องพิมพ์ \"อนุมัติ #Txx\" ก่อนส่ง · หยุดได้ด้วย Ctrl+C (SL/TP ค้างอยู่ที่โบรกเกอร์)\n"
        "ยังไม่มีกลยุทธ์ไหนผ่านเกณฑ์ว่าได้เปรียบตลาด: นี่คือการทดลองระบบกับเงินจริงก้อนเล็กที่สุด"
    )
    owner = input_fn("ชื่อผู้อนุมัติการใช้เงินจริง: ").strip()
    if not owner or any(c in owner for c in "\n\r"):
        output_fn("ยกเลิก: ไม่มีชื่อผู้อนุมัติ (ไม่มีอะไรเปลี่ยน)")
        return None
    if input_fn(f'พิมพ์ "{CONFIRM_PHRASE}" เพื่อยืนยัน (อย่างอื่น = ยกเลิก): ').strip() != CONFIRM_PHRASE:
        output_fn("ยกเลิก: ไม่ได้ยืนยัน (ไม่มีอะไรเปลี่ยน)")
        return None
    base_text = base_path.read_text(encoding="utf-8-sig") if base_path.exists() else ""
    live_path.parent.mkdir(parents=True, exist_ok=True)
    live_path.write_text(live_config_text(base_text, symbol, owner), encoding="utf-8")
    live = load_config(live_path)
    ex = live.execution
    if ex.dry_run or ex.approval != "manual" or ex.confirmed_by != owner or ex.symbol != symbol:
        raise RuntimeError(f"live config did not come out as written: {live_path}")
    output_fn(f"บันทึก config เงินจริงที่ {live_path} (config dry-run เดิมไม่ถูกแตะ)")
    return live
