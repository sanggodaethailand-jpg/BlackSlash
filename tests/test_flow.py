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
from anon.lab.core import Costs, Entry, History, Idea, idea_hash, simulate
from anon.lab.frames import resample
from anon.lab.gauntlet import holdout_cut, run_gauntlet
from anon.lab.ideas import load_ideas
from anon.lab.ideas.absorption_shift import IDEA as ABSORPTION
from anon.lab.ledger import Ledger
from anon.lab.nulls import flow_center, mirror_share, share_logit, signflip_bars
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
    center = flow_center(bars)
    flipped = 0
    for real, f in zip(bars, fake, strict=True):
        assert f.volume == real.volume and 0 < f.buy_volume < f.volume
        if f.buy_volume != real.buy_volume:
            flipped += 1
            assert share_logit(f.buy_volume / f.volume) == pytest.approx(2 * center - share_logit(real.buy_volume / real.volume))
    assert 150 < flipped < 250  # about half


class AlwaysFlip(random.Random):
    def random(self):
        return 0.0


@pytest.mark.parametrize(
    "shares, expected",
    [
        ([0.0, 0.0, 1.0], [1.0, 1.0, 0.0]),  # traded one way only: the sides switch, nothing leaves [0, 1]
        ([0.2, 0.2, 0.2], [0.2, 0.2, 0.2]),  # every bar at the average: mirrored onto itself
        ([0.1, 0.5, 1.0], None),
        ([0.49, 0.51, 0.47, 0.55], None),
    ],
)
def test_signflip_flow_null_holds_at_the_extremes(shares, expected):
    """Flow mirrors in log-odds around the mean log-odds: the share stays in [0, 1], bars traded
    both ways keep their mean log-odds exactly when all flip, and flipping twice gives them back."""
    bars = [Bar(T0 + i * H1, 100, 101, 99, 100.5, 10.0, 10.0 * f) for i, f in enumerate(shares)]
    once = signflip_bars(bars, AlwaysFlip())
    got = [b.buy_volume / b.volume for b in once]
    assert all(0.0 <= f <= 1.0 for f in got)
    if expected is not None:
        assert got == pytest.approx(expected)
    both = [(f, g) for f, g in zip(shares, got, strict=True) if 0 < f < 1]
    if both:
        before = sum(share_logit(f) for f, _ in both) / len(both)
        assert sum(share_logit(g) for _, g in both) / len(both) == pytest.approx(before)
    center = flow_center(bars)
    assert [mirror_share(g, center) for g in got] == pytest.approx(shares)


# --- anon binance --------------------------------------------------------------------

def kline_zip(month, rows, micro=False, header=False):
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,x,ignore"] if header else []
    unit = 1_000_000 if micro else 1000
    for t, (o, h, lo, c, v, buy) in rows:
        stamp = int(t.timestamp() * unit)
        lines.append(f"{stamp},{o},{h},{lo},{c},{v},{stamp + 3600 * unit - 1},0,10,{buy},0,0")
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


def kline_line(open_ms, close_ms, o=1, h=2, lo=0.5, c=1.5, v=10, buy=4):
    return f"{open_ms},{o},{h},{lo},{c},{v},{close_ms},0,10,{buy},0,0"


JAN = int(datetime(2025, 1, 1, tzinfo=UTC).timestamp())


@pytest.mark.parametrize(
    "line, problem",
    [
        (kline_line(JAN * 10**6, JAN * 10**6 + 3_599_999), "1 ชั่วโมงเต็ม"),  # microsecond open, 3.6-second bar
        (kline_line(JAN * 1000 + 60_000, JAN * 1000 + 3_659_999), "1 ชั่วโมงเต็ม"),  # not on the hour
        (kline_line(JAN * 1000, JAN * 1000 + 3_599_999, o=3), "ราคาไม่สอดคล้อง"),  # open above the high
        (kline_line(JAN * 1000, JAN * 1000 + 3_599_999, lo=0), "ราคาไม่สอดคล้อง"),
        (kline_line(JAN * 1000, JAN * 1000 + 3_599_999, buy=11), "volume ผิด"),  # more market buys than volume
        (kline_line(JAN * 1000, JAN * 1000 + 3_599_999) + "\n" + kline_line(JAN * 1000, JAN * 1000 + 3_599_999), "ซ้ำ"),
    ],
)
def test_parser_refuses_malformed_bars(line, problem):
    with pytest.raises(DownloadError, match=problem):
        parse_klines(line + "\n")


def test_download_refuses_bars_of_another_month_or_twice(tmp_path):
    jan = [(datetime(2025, 1, 1, tzinfo=UTC), (1, 2, 0.5, 1.5, 10, 4))]
    fetch = fake_binance({"2025-02": kline_zip("2025-02", jan)})
    with pytest.raises(DownloadError, match="เดือนอื่น"):
        download("BTCUSDT", "2025-02", "2025-02", tmp_path / "b.csv", fetch, lambda _m: None)
    last = [(datetime(2025, 1, 31, 23, tzinfo=UTC), (1, 2, 0.5, 1.5, 10, 4))]
    fetch = fake_binance({"2025-01": kline_zip("2025-01", last), "2025-02": kline_zip("2025-01", last)})
    with pytest.raises(DownloadError, match="เดือนอื่น"):  # a repeated hour in the next file is caught as out of month
        download("BTCUSDT", "2025-01", "2025-02", tmp_path / "b.csv", fetch, lambda _m: None)


# --- execution fixes found by the red team (lab v3) -----------------------------------

class Once(Idea):
    name, family = "once", "test"
    hypothesis = "Test idea: exercises the machinery only, says nothing about any market."
    primary = {"x": 1}
    grid = {"x": [1]}

    def __init__(self, at, side="buy", **entry):
        self.at, self.side, self.kw = at, side, entry

    def entry(self, i, bars, state, p):
        return Entry(self.side, **self.kw) if i == self.at else None


def test_fill_bar_opening_past_the_stop_exits_at_that_open():
    bars = [Bar(T0 + k * H1, 100, 101, 99, 100) for k in range(3)]  # bid 99-101, ask = bid + 10
    buy = simulate(Once(0, stop=105.0), {"x": 1}, bars, Costs(spread=10.0))[0]
    assert (buy.fill, buy.exit_price, buy.reason) == (110.0, 100.0, "stop") and buy.r == pytest.approx(-2.0)
    sell = simulate(Once(0, side="sell", stop=105.0), {"x": 1}, bars, Costs(spread=10.0))[0]
    assert (sell.fill, sell.exit_price, sell.reason) == (100.0, 110.0, "stop") and sell.r == pytest.approx(-2.0)


def test_time_exit_pays_a_rollover_inside_a_data_gap():
    times = [T0 + timedelta(hours=h) for h in (-3, -2, -1, 1, 2)]  # 21:00 22:00 23:00, midnight missing, 01:00 02:00
    bars = [Bar(t, 1000, 1005, 995, 1000) for t in times]
    costs = Costs(spread=0.0, swap_mode="points", swap_long=-500, point=0.01, swap_days=(1,) * 7, rollover="utc_midnight")
    t = simulate(Once(1, stop=900.0, max_hold=1), {"x": 1}, bars, costs)[0]  # filled 23:00, out at 01:00
    assert (t.reason, t.swap_days) == ("time", 1.0) and t.r == pytest.approx(-0.05)


def test_a_signal_is_not_filled_across_missing_hours():
    bars = [Bar(T0 + k * H1, 100, 101, 99, 100) for k in (0, 1, 4, 5)]  # two hours missing after the signal
    assert simulate(Once(1, stop=90.0, max_wait=0), {"x": 1}, bars, Costs(spread=0.0)) == []
    assert simulate(Once(1, stop=90.0), {"x": 1}, bars, Costs(spread=0.0))[0].entry_index == 2


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


def test_an_opened_lockbox_without_a_result_is_spent(tmp_path, capsys):
    cfg = lab_cfg(tmp_path)
    ledger = Ledger.open(cfg.lab.ledger)
    ledger.register("absorption_shift", idea_hash(ABSORPTION), {}, {}, {})
    ledger.record_holdout("absorption_shift", idea_hash(ABSORPTION), {})  # then the program died
    with pytest.raises(ValueError, match="เปิดช่วงล็อกไปแล้ว"):
        run_gauntlet(ABSORPTION, flow_bars(400), Costs(spread=10.0), ledger, trials=2)
    csv = tmp_path / "flow.csv"
    csv.write_text("time,open,high,low,close,volume,buy_volume\n" + "".join(
        f"{b.time.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume},{b.buy_volume}\n" for b in flow_bars(400)))
    cli.cmd_lab(cfg, str(csv), ["absorption_shift"], None, False, False)
    assert "absorption_shift: เปิดช่วงล็อกไปแล้วแต่ไม่มีผลบันทึก" in capsys.readouterr().out


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
    assert e is not None and e.side == "buy" and e.target_r == 1.5 and e.max_hold == 24 and e.max_wait == 0
    assert decide(base_market() + [a, replace(s, time=s.time + H1)]) is None  # an hour missing in between
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
