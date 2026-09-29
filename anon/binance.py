"""Binance public klines (data.binance.vision) as H1 bars with order flow, for the Idea Lab.

Runs on the owner's PC (``anon binance`` / binance.bat): one zip per month, each checked
against the SHA-256 that Binance publishes next to it. The CSV written here has
time (UTC), open, high, low, close, volume and buy_volume: the base asset traded in the
hour and the part bought by market orders (Binance's "taker buy base asset volume").
"""

from __future__ import annotations

import csv
import hashlib
import io
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

BASE = "https://data.binance.vision/data/spot/monthly/klines"
INTERVAL = "1h"  # the lab replays H1 bars only


class DownloadError(RuntimeError):
    pass


def months(start: str, end: str) -> list[str]:
    """'2018-02', '2018-03', ... up to ``end`` inclusive."""
    y, m = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    if (y, m) > (ey, em):
        raise ValueError(f"start {start} is after end {end}")
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def kline_url(symbol: str, month: str) -> str:
    return f"{BASE}/{symbol}/{INTERVAL}/{symbol}-{INTERVAL}-{month}.zip"


def _unit(stamp: int) -> int:
    return 1_000_000 if stamp >= 10**14 else 1000  # spot files use microseconds from 2025


def parse_klines(text: str) -> list[tuple[datetime, float, float, float, float, float, float]]:
    """Rows of (open time UTC, open, high, low, close, volume, taker buy volume); a header line,
    if the file has one, is skipped. Every row must be one whole hour starting on the hour
    (close time in the same unit as the open time), with low <= open/close <= high, prices
    above 0, 0 <= buy volume <= volume, and no hour twice."""
    rows, seen = [], set()
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0].strip().isdigit():
            continue
        opened, closed = int(row[0]), int(row[6])
        unit = _unit(opened)
        start, span = opened / unit, (closed - opened) / unit
        t = datetime.fromtimestamp(start, tz=UTC)
        o, h, lo, c, vol = (float(x) for x in row[1:6])
        buy = float(row[9])
        problem = None
        if start % 3600 or not 3599 < span <= 3600:
            problem = f"ไม่ใช่แท่ง 1 ชั่วโมงเต็ม (เปิด {row[0]} ปิด {row[6]})"
        elif not (0 < lo <= min(o, c) and max(o, c) <= h):
            problem = f"ราคาไม่สอดคล้อง O {o} H {h} L {lo} C {c}"
        elif vol < 0 or buy < 0 or buy > vol + 1e-9:
            problem = f"volume ผิด (volume {vol}, ซื้อด้วยคำสั่งตลาด {buy})"
        elif t in seen:
            problem = "ชั่วโมงซ้ำ"
        if problem:
            raise DownloadError(f"แท่งผิดรูป {t:%Y-%m-%d %H:%M} UTC: {problem}")
        seen.add(t)
        rows.append((t, o, h, lo, c, vol, buy))
    return rows


def fetch(url: str, timeout: float = 60.0, attempts: int = 4) -> bytes:
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise DownloadError(f"ไม่มีไฟล์นี้บน Binance: {url}") from exc
            if attempt == attempts - 1:
                raise DownloadError(f"โหลดไม่สำเร็จ ({exc.code}): {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == attempts - 1:
                raise DownloadError(f"โหลดไม่สำเร็จ ({exc}): {url}") from exc
        time.sleep(2 ** (attempt + 1))
    raise DownloadError(url)


def month_rows(symbol: str, month: str, fetch_fn: Callable[[str], bytes] = fetch) -> list[tuple]:
    url = kline_url(symbol, month)
    blob = fetch_fn(url)
    expected = fetch_fn(url + ".CHECKSUM").decode("utf-8").split()[0].lower()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise DownloadError(f"SHA-256 ไม่ตรงกับที่ Binance ประกาศ ({month}): ได้ {actual[:12]}… ควรเป็น {expected[:12]}…")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise DownloadError(f"{url}: expected one CSV inside, found {names}")
        rows = parse_klines(archive.read(names[0]).decode("utf-8"))
    outside = [r[0] for r in rows if r[0].strftime("%Y-%m") != month]
    if outside:
        raise DownloadError(f"ไฟล์เดือน {month} มีแท่งของเดือนอื่น ({outside[0]:%Y-%m-%d %H:%M} UTC)")
    return rows


def download(
    symbol: str, start: str, end: str, out: str | Path,
    fetch_fn: Callable[[str], bytes] = fetch, output_fn: Callable[[str], None] = print,
) -> int:
    """Write every hour from ``start`` to ``end`` (months, inclusive); returns the bar count."""
    bars: dict[datetime, tuple] = {}
    for month in months(start, end):
        rows = month_rows(symbol, month, fetch_fn)
        for row in rows:
            if row[0] in bars:
                raise DownloadError(f"ชั่วโมงซ้ำข้ามไฟล์: {row[0]:%Y-%m-%d %H:%M} UTC")
            bars[row[0]] = row
        output_fn(f"{symbol} {month}: {len(rows)} แท่ง (SHA-256 ตรง)")
    ordered = [bars[t] for t in sorted(bars)]
    if not ordered:
        raise DownloadError("ไม่ได้แท่งเลยสักแท่ง")
    gaps = sum(1 for a, b in zip(ordered, ordered[1:], strict=False) if (b[0] - a[0]).total_seconds() > 3600)
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "open", "high", "low", "close", "volume", "buy_volume"])
        for t, o, h, lo, c, vol, buy in ordered:
            writer.writerow([t.isoformat(), o, h, lo, c, vol, buy])
    output_fn(
        f"บันทึก {len(ordered):,} แท่ง {ordered[0][0]:%Y-%m-%d} → {ordered[-1][0]:%Y-%m-%d %H:%M} UTC ที่ {path} "
        f"(มีช่วงที่ขาดหาย {gaps} จุด เช่นตอน Binance ปิดปรับปรุงระบบ)"
    )
    return len(ordered)
