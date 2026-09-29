"""Order-flow data for the Idea Lab: Binance klines, bars that carry volume and market buys,
null histories that keep price and flow together, and the guards around a second dataset."""

import hashlib
import io
import random
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from anon import cli
from anon.binance import DownloadError, download, kline_url, months, parse_klines
from anon.config import Config
from anon.data import load_bars
from anon.lab.core import Costs, History, idea_hash
from anon.lab.frames import resample
from anon.lab.gauntlet import holdout_cut, run_gauntlet
from anon.lab.ideas import load_ideas
from anon.lab.ideas.absorption_shift import IDEA as ABSORPTION
from anon.lab.ledger import Ledger
from anon.lab.nulls import signflip_bars
from anon.models import H1, Bar
from anon.research import shuffled_bars

T0 = datetime(2021, 1, 4, tzinfo=UTC)


def flow_bars(n=400, seed=3):
    rng = random.Random(seed)
    out, price = [], 20000.0
    for i in range(n):
        close = price * (1 + rng.gauss(0, 0.004))
        volume = rng.uniform(50, 150)
        out.append(Bar(T0 + i * H1, price, max(price, close) + 20, min(price, close) - 20, close,
                       volume, volume * rng.uniform(0.3, 0.7)))
        price = close
    return out


# --- data in -------------------------------------------------------------------------

def test_csv_with_flow_columns_loads_them_and_mt5_files_stay_zero(tmp_path):
    flow = tmp_path / "flow.csv"
    flow.write_text("time,open,high,low,close,volume,buy_volume\n2021-01-04T00:00:00+00:00,1,2,0.5,1.5,10,4\n")
    bar = load_bars(flow)[0]
    assert (bar.volume, bar.buy_volume) == (10.0, 4.0)
    mt5 = tmp_path / "mt5.csv"
    mt5.write_text("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\n2021.01.04\t00:00\t1\t2\t0.5\t1.5\t99\t0\n")
    bar = load_bars(mt5)[0]
    assert (bar.volume, bar.buy_volume) == (0.0, 0.0)


def test_resample_adds_up_the_flow():
    bars = flow_bars(48)
    days, _ = resample(bars, 24)
    assert days[0].volume == pytest.approx(sum(b.volume for b in bars[:24]))
    assert days[1].buy_volume == pytest.approx(sum(b.buy_volume for b in bars[24:]))


# --- null histories ------------------------------------------------------------------

def test_shuffle_moves_each_bars_flow_with_its_shape():
    bars = flow_bars()
    fake = shuffled_bars(bars, random.Random(1))
    shape = {round(b.volume, 9): (round(b.close / b.open, 12), b.buy_volume) for b in bars}
    assert sorted(b.volume for b in fake) == sorted(b.volume for b in bars)
    for b in fake:
        ratio, buy = shape[round(b.volume, 9)]
        assert (round(b.close / b.open, 12), b.buy_volume) == (ratio, buy)


def test_signflip_mirrors_flow_with_the_bar_and_keeps_prices_unchanged():
    bars = flow_bars()
    fake = signflip_bars(bars, random.Random(2))
    plain = signflip_bars([replace(b, volume=0.0, buy_volume=0.0) for b in bars], random.Random(2))
    assert [(b.open, b.close) for b in fake] == [(b.open, b.close) for b in plain]  # same draws as before flow existed
    mean_share = sum(b.buy_volume / b.volume for b in bars) / len(bars)
    flipped = 0
    for real, f in zip(bars, fake, strict=True):
        assert f.volume == real.volume
        if f.buy_volume != real.buy_volume:
            flipped += 1
            assert f.buy_volume / f.volume == pytest.approx(2 * mean_share - real.buy_volume / real.volume)
    assert 150 < flipped < 250  # about half


# --- anon binance --------------------------------------------------------------------

def kline_zip(month, rows, micro=False, header=False):
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,x,ignore"] if header else []
    for t, (o, h, lo, c, v, buy) in rows:
        stamp = int(t.timestamp() * (1_000_000 if micro else 1000))
        lines.append(f"{stamp},{o},{h},{lo},{c},{v},{stamp + 3599999},0,10,{buy},0,0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(f"BTCUSDT-1h-{month}.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def fake_binance(files, corrupt=()):
    store = {}
    for month, blob in files.items():
        url = kline_url("BTCUSDT", month)
        store[url] = blob
        digest = hashlib.sha256(blob + (b"x" if month in corrupt else b"")).hexdigest()
        store[url + ".CHECKSUM"] = f"{digest}  BTCUSDT-1h-{month}.zip\n".encode()

    def fetch(url):
        if url not in store:
            raise DownloadError(f"ไม่มีไฟล์นี้บน Binance: {url}")
        return store[url]

    return fetch


def test_months_and_timestamps():
    assert months("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]
    with pytest.raises(ValueError):
        months("2025-02", "2024-11")
    t = datetime(2025, 1, 1, tzinfo=UTC)
    ms = kline_zip("2025-01", [(t, (1, 2, 0.5, 1.5, 10, 4))])
    us = kline_zip("2025-01", [(t, (1, 2, 0.5, 1.5, 10, 4))], micro=True, header=True)
    for blob in (ms, us):
        text = zipfile.ZipFile(io.BytesIO(blob)).read("BTCUSDT-1h-2025-01.csv").decode()
        assert parse_klines(text) == [(t, 1.0, 2.0, 0.5, 1.5, 10.0, 4.0)]


def test_download_checks_every_file_and_writes_loadable_flow_bars(tmp_path):
    dec = [(datetime(2024, 12, 31, 22, tzinfo=UTC) + timedelta(hours=h), (100 + h, 102 + h, 99 + h, 101 + h, 5, 2))
           for h in range(2)]
    jan = [(datetime(2025, 1, 1, h, tzinfo=UTC), (200 + h, 202 + h, 199 + h, 201 + h, 7, 3)) for h in (0, 1, 3)]
    fetch = fake_binance({"2024-12": kline_zip("2024-12", dec), "2025-01": kline_zip("2025-01", jan, micro=True)})
    out, said = tmp_path / "b.csv", []
    assert download("BTCUSDT", "2024-12", "2025-01", out, fetch, said.append) == 5
    bars = load_bars(out)
    assert [b.time.hour for b in bars] == [22, 23, 0, 1, 3] and bars[-1].buy_volume == 3.0
    assert "ช่วงที่ขาดหาย 1 จุด" in said[-1] and all("SHA-256 ตรง" in line for line in said[:-1])


def test_download_refuses_a_file_that_does_not_match_its_checksum(tmp_path):
    rows = [(datetime(2025, 1, 1, tzinfo=UTC), (1, 2, 0.5, 1.5, 10, 4))]
    fetch = fake_binance({"2025-01": kline_zip("2025-01", rows)}, corrupt={"2025-01"})
    with pytest.raises(DownloadError, match="SHA-256"):
        download("BTCUSDT", "2025-01", "2025-01", tmp_path / "b.csv", fetch, lambda _m: None)
    fetch = fake_binance({"2025-01": kline_zip("2025-01", rows)})
    with pytest.raises(DownloadError, match="ไม่มีไฟล์"):  # a month Binance has not published
        download("BTCUSDT", "2025-01", "2025-02", tmp_path / "b.csv", fetch, lambda _m: None)
    assert not (tmp_path / "b.csv").exists()


# --- guards around a second dataset ----------------------------------------------------

def test_locked_part_never_starts_after_a_lock_already_on_record():
    bars = flow_bars(1000)
    assert holdout_cut(bars, Ledger.open(None)) == 700
    ledger = Ledger.open(None)
    ledger.register("older", "h", {}, {}, {"locked_from": bars[500].time.isoformat()})
    assert holdout_cut(bars, ledger) == 500
    ledger.register("later", "h2", {}, {}, {"locked_from": bars[900].time.isoformat()})
    assert holdout_cut(bars, ledger) == 500  # the earliest lock wins


def test_repo_ledger_lock_keeps_the_2024_lockbox():
    bars = [Bar(datetime(2018, 2, 9, tzinfo=UTC) + i * H1, 1, 1, 1, 1, 1, 0.5) for i in range(8 * 8766)]
    cut = holdout_cut(bars, Ledger.open("research/ledger.jsonl"))
    assert bars[cut].time <= datetime(2024, 2, 26, 23, tzinfo=UTC)


def lab_cfg(tmp_path):
    return replace(Config(), lab=replace(Config().lab, ledger=str(tmp_path / "ledger.jsonl")))


def write_csv(path, bars):
    lines = ["time,open,high,low,close"] + [f"{b.time.isoformat()},{b.open},{b.high},{b.low},{b.close}" for b in bars]
    path.write_text("\n".join(lines) + "\n")


def test_lab_never_retests_a_judged_idea_nor_runs_flow_ideas_without_flow(tmp_path, capsys):
    cfg = lab_cfg(tmp_path)
    donchian = load_ideas()["donchian_d1"]
    ledger = Ledger.open(cfg.lab.ledger)
    ledger.register("donchian_d1", idea_hash(donchian), {}, {}, {})
    ledger.record_result("donchian_d1", idea_hash(donchian), {"passed": False, "failed_gate": 4})
    before = (tmp_path / "ledger.jsonl").read_text()
    csv = tmp_path / "mt5.csv"
    write_csv(csv, flow_bars(300))
    cli.cmd_lab(cfg, str(csv), ["donchian_d1", "absorption_shift"], None, False, False)
    out = capsys.readouterr().out
    assert "donchian_d1: มีผลในสมุดแล้ว (❌ ตกด่าน 4) → ไม่รันซ้ำ" in out
    assert "absorption_shift: ต้องใช้ข้อมูลที่มี volume/buy_volume" in out
    assert (tmp_path / "ledger.jsonl").read_text() == before  # nothing registered, nothing run


def test_gauntlet_refuses_a_flow_idea_on_bars_without_flow():
    bars = [replace(b, volume=0.0, buy_volume=0.0) for b in flow_bars(300)]
    ledger = Ledger.open(None)
    with pytest.raises(ValueError, match="volume/buy_volume"):
        run_gauntlet(ABSORPTION, bars, Costs(spread=10.0), ledger, trials=2)
    assert ledger.rows == []


# --- the idea's rule -----------------------------------------------------------------

def base_market(n=200):
    """Quiet market: 19,900-20,100 range, market buys alternating 49% / 51% of volume 100."""
    return [Bar(T0 + i * H1, 20000, 20100, 19900, 20000, 100, 49 if i % 2 else 51) for i in range(n)]


def decide(bars):
    state = ABSORPTION.prepare(bars, ABSORPTION.primary)
    i = len(bars) - 1
    return ABSORPTION.entry(i, History(bars, i), state, ABSORPTION.primary)


def test_sellers_absorbed_at_the_bottom_then_buyers_take_over_is_a_long():
    a = Bar(T0 + 200 * H1, 20000, 20010, 19800, 19990, 150, 30)  # sold hard (20% buys), closed near its high
    s = Bar(T0 + 201 * H1, 19990, 20060, 19980, 20050, 100, 60)  # then bought, closed higher
    e = decide(base_market() + [a, s])
    assert e is not None and e.side == "buy" and e.target_r == 1.5 and e.max_hold == 24
    assert 19770 < e.stop < 19800  # just under the absorption low


def test_buyers_absorbed_at_the_top_then_sellers_take_over_is_a_short():
    a = Bar(T0 + 200 * H1, 20000, 20200, 19990, 20010, 150, 120)  # bought hard, closed near its low
    s = Bar(T0 + 201 * H1, 20010, 20020, 19940, 19950, 100, 40)
    e = decide(base_market() + [a, s])
    assert e is not None and e.side == "sell" and 20200 < e.stop < 20230


@pytest.mark.parametrize(
    "a, s",
    [
        (Bar(T0 + 200 * H1, 20000, 20010, 19800, 19820, 150, 30), None),  # closed near its low: not absorbed
        (Bar(T0 + 200 * H1, 20000, 20010, 19800, 19990, 60, 12), None),  # below-average volume
        (Bar(T0 + 200 * H1, 20000, 20010, 19800, 19990, 150, 75), None),  # ordinary flow, nothing absorbed
        (None, Bar(T0 + 201 * H1, 19990, 20000, 19950, 19960, 100, 60)),  # next bar closed lower
    ],
)
def test_no_trade_without_every_piece(a, s):
    a = a or Bar(T0 + 200 * H1, 20000, 20010, 19800, 19990, 150, 30)
    s = s or Bar(T0 + 201 * H1, 19990, 20060, 19980, 20050, 100, 60)
    assert decide(base_market() + [a, s]) is None
