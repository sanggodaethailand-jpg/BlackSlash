"""Load H1 bars from CSV: MT5 "Export Bars" files or a plain time,open,high,low,close file."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anon.models import Bar


def _parse_time(value: str) -> datetime:
    value = value.strip()
    if value.isdigit():
        return datetime.fromtimestamp(int(value), tz=UTC)
    if value[4:5] == "." and value[7:8] == ".":  # MT5 style 2026.09.27
        value = f"{value[:4]}-{value[5:7]}-{value[8:]}"
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def load_bars(path: str | Path, server_utc_offset_hours: float = 0.0) -> list[Bar]:
    """MT5 exports (<DATE>/<TIME> columns) are server time and are shifted to UTC here;
    ISO or epoch ``time`` columns are taken as UTC."""
    text = Path(path).read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delimiter = "\t" if "\t" in first else ","
    rows = list(csv.reader(text.splitlines(), delimiter=delimiter))
    header = [h.strip().strip("<>").lower() for h in rows[0]]
    idx = {name: i for i, name in enumerate(header)}
    shift = timedelta(hours=server_utc_offset_hours)
    bars: list[Bar] = []
    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        if "date" in idx:
            stamp = row[idx["date"]].strip()
            if "time" in idx:
                stamp += " " + row[idx["time"]].strip()
            t = _parse_time(stamp) - shift
        else:
            t = _parse_time(row[idx["time"]])
        bars.append(
            Bar(
                t,
                float(row[idx["open"]]),
                float(row[idx["high"]]),
                float(row[idx["low"]]),
                float(row[idx["close"]]),
            )
        )
    bars.sort(key=lambda b: b.time)
    return bars
