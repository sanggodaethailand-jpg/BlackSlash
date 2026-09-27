"""Weekly levels in one step (``anon levels``, ``levels.bat``).

The owner types this week's lines from the room's handoff; Enter keeps a value. Nothing is
written unless the result passes the same checks as the config and the owner types "บันทึก".
The live config is rebuilt from this file on every live.bat launch, so one file is enough.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from anon.config import Config
from anon.golive import set_toml_keys

SAVE_PHRASE = "บันทึก"
FIELDS = (
    ("a_zone_bot", "A โซน ล่าง"),
    ("a_zone_top", "A โซน บน"),
    ("b_inv_ref", "B ref (อ้างอิง)"),
    ("gray_center", "เทา NO CHASE (กลาง)"),
    ("gray_half_width", "ครึ่งความกว้างเทา"),
    ("tp1", "TP1"),
    ("liq_top", "liq บน"),
)


def local_today(cfg: Config) -> date:
    return (datetime.now(UTC) + timedelta(hours=cfg.execution.day_utc_offset_hours)).date()


def cmd_levels(
    cfg: Config, config_path: str | None, input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print, today: date | None = None,
) -> Path | None:
    target = Path(config_path) if config_path else Path("config") / "anon.toml"
    example = Path("config") / "anon.example.toml"
    lv = cfg.levels
    today = today or local_today(cfg)
    output_fn(f"== เส้นราคาประจำสัปดาห์ → {target} ==")
    output_fn("พิมพ์ตัวเลขใหม่ หรือกด Enter เพื่อใช้ค่าเดิมในวงเล็บ")
    values = {}
    for key, label in FIELDS:
        current = getattr(lv, key)
        raw = input_fn(f"{label} [{current:,.1f}]: ").strip().replace(",", "")
        try:
            values[key] = float(raw) if raw else current
        except ValueError:
            output_fn(f"ยกเลิก: '{raw}' ไม่ใช่ตัวเลข (ไม่มีอะไรเปลี่ยน)")
            return None
    default_until = (today + timedelta(days=6)).isoformat()
    until = input_fn(f"ใช้ได้ถึงวันที่ (YYYY-MM-DD) [{default_until}]: ").strip() or default_until
    new = replace(lv, **values, valid_until=until)
    try:
        replace(cfg, levels=new).validate()
    except ValueError as exc:
        output_fn(f"ยกเลิก: {exc} (ไม่มีอะไรเปลี่ยน)")
        return None
    output_fn(
        f"\nA {new.a_zone_bot:,.1f}–{new.a_zone_top:,.1f} · B ref {new.b_inv_ref:,.1f} · "
        f"เทา {new.gray_low:,.1f}–{new.gray_high:,.1f} · TP1 {new.tp1:,.1f} · liq {new.liq_top:,.1f} · ใช้ได้ถึง {until}"
    )
    if input_fn(f'พิมพ์ "{SAVE_PHRASE}" เพื่อเขียนลง config (อย่างอื่น = ยกเลิก): ').strip() != SAVE_PHRASE:
        output_fn("ยกเลิก (ไม่มีอะไรเปลี่ยน)")
        return None
    source = target if target.exists() else example
    text = source.read_text(encoding="utf-8-sig") if source.exists() else ""
    keys = {key: repr(float(values[key])) for key, _ in FIELDS}
    keys["valid_until"] = f'"{until}"'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(set_toml_keys(text, "levels", keys), encoding="utf-8")
    output_fn(
        "บันทึกแล้ว · ถ้า live.bat เปิดอยู่ ให้กด Ctrl+C แล้วเปิดใหม่ (config เงินจริงสร้างจากไฟล์นี้ทุกครั้งที่เปิด)\n"
        "กราฟจะแสดงเส้นที่ engine ใช้จริงให้เอง (เส้นสีฟ้า ENGINE)"
    )
    return target
