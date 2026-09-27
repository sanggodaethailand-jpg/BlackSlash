"""Engine → MT5 chart bridge.

The live loop writes a small UTF-8 text file into MT5's shared ``Common\\Files`` folder;
the ANON_Levels_v2 indicator reads it every second and shows what the engine is doing.
One ``key=value`` per line (CRLF), repeated keys for lists, ``|`` between fields.
It is display only: a failed write is logged and never interrupts trading.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from anon.quant import compute_stats

log = logging.getLogger(__name__)

FEED_NAME = "ANON_feed.txt"
MAX_EVENTS = 8
MAX_CALLS = 100


def resolve_feed_path(setting: str) -> Path | None:
    """``"auto"`` → MT5's Common\\Files folder (Windows); ``""`` → off; anything else is a path."""
    if setting == "":
        return None
    if setting != "auto":
        return Path(setting)
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / FEED_NAME


def _clean(text: str) -> str:
    return " ".join(str(text).replace("|", "/").split())


def render(engine, symbol: str, dry_run: bool, ai_on: bool) -> list[str]:
    cfg = engine.cfg
    lv = engine.levels
    zen = engine.zen.state
    records = list(engine.journal.records.values())
    stats = compute_stats(records)
    cooldown = 0
    if zen.bars_since_loss is not None and zen.bars_since_loss < cfg.zen.cooldown_bars_after_loss:
        cooldown = cfg.zen.cooldown_bars_after_loss - zen.bars_since_loss
    lines = [
        "version=1",
        f"mode={'dry-run' if dry_run else 'LIVE'}",
        f"symbol={symbol}",
        f"ai={'on' if ai_on else 'off'}",
        f"levels_source={'auto' if cfg.auto.enabled else 'config'}",
        f"levels_ok={int(engine.levels_ok)}",
        f"a_bot={lv.a_zone_bot:.2f}",
        f"a_top={lv.a_zone_top:.2f}",
        f"b_ref={lv.b_inv_ref:.2f}",
        f"gray_low={lv.gray_low:.2f}",
        f"gray_high={lv.gray_high:.2f}",
        f"tp1={lv.tp1:.2f}",
        f"liq={lv.liq_top:.2f}",
        f"levels_until={'' if cfg.auto.enabled else cfg.levels.valid_until}",
        f"levels_expired={int(engine.levels_expired(datetime.now(UTC)))}",
        f"zen_off_plan={zen.consecutive_off_plan}",
        f"zen_max={cfg.zen.max_consecutive_off_plan}",
        f"zen_locked={int(zen.locked)}",
        f"zen_cooldown={cooldown}",
        f"quant_n={stats.n}",
        f"quant_win={stats.win_rate:.1f}",
        f"quant_er={stats.expectancy_r:.3f}",
    ]
    open_rec = next((r for r in records if r.status == "open" and r.id.startswith("#T")), None)
    if open_rec is not None:
        entry = open_rec.entry_fill or open_rec.entry_plan
        lines.append(f"open={open_rec.id}|{open_rec.setup}|{entry:.2f}|{open_rec.stop:.2f}|{open_rec.tp1:.2f}")
    for e in engine.events[-MAX_EVENTS:]:
        lines.append(f"event={int(e.time.timestamp())}|{_clean(e.kind)}|{_clean(e.detail)}")
    for c in list(engine.calls)[-MAX_CALLS:]:
        lines.append(
            f"call={int(c.bar_time.timestamp())}|{c.setup}|{c.entry:.2f}|{c.stop:.2f}|{c.tp1:.2f}|{_clean(c.outcome)}"
        )
    return lines


class ChartFeed:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.body: list[str] = []
        self.warned = False

    def update(self, engine, symbol: str, dry_run: bool, ai_on: bool) -> None:
        self.body = render(engine, symbol, dry_run, ai_on)

    def write(self, now_epoch: float | None = None) -> bool:
        stamp = int(time.time() if now_epoch is None else now_epoch)
        text = "\r\n".join([f"heartbeat={stamp}", *self.body]) + "\r\n"
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(text.encode("utf-8"))
            for attempt in range(5):  # the indicator may hold the file open for a moment
                try:
                    os.replace(tmp, self.path)
                    return True
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
            self.path.write_bytes(text.encode("utf-8"))
            return True
        except OSError as exc:
            if not self.warned:
                log.warning("chart feed not written to %s: %s", self.path, exc)
                self.warned = True
            return False
