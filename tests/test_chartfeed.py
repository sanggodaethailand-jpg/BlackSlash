"""The engine → chart file must carry every key the MT5 indicator reads, in the shape it splits."""

import re
from dataclasses import replace
from pathlib import Path

from anon.chartfeed import ChartFeed, render, resolve_feed_path
from anon.config import Config, OmegaConfig
from tests.helpers import flat, then
from tests.test_engine import DIP, feed, make_engine

MQ5 = Path(__file__).resolve().parents[1] / "mql5" / "Indicators" / "ANON_Levels_v2.mq5"
TAG_ONLY = replace(Config(), omega=OmegaConfig(unfavorable_action="tag"))


def engine_with_open_trade():
    engine, broker = make_engine(TAG_ONLY)
    feed(engine, broker, then(flat(84000, 60), [DIP, (83420, 83600, 83400, 83500)]))
    return engine


def keys(lines):
    return {line.split("=", 1)[0] for line in lines}


def test_every_key_the_indicator_reads_is_written():
    text = MQ5.read_text(encoding="utf-8-sig")
    wanted = set(re.findall(r'Feed(?:Get|All)\("(\w+)"', text))
    assert {"symbol", "call", "event", "open", "zen_locked"} <= wanted
    written = keys(render(engine_with_open_trade(), "BTCUSDc", True, False)) | {"heartbeat"}
    assert wanted <= written, wanted - written


def test_list_fields_have_the_counts_the_indicator_splits():
    lines = render(engine_with_open_trade(), "BTCUSDc", True, False)
    fields = {}
    for line in lines:
        key, value = line.split("=", 1)
        fields.setdefault(key, []).append(value.split("|"))
    assert all(len(f) == 5 for f in fields["open"])  # id|setup|entry|stop|tp
    assert all(len(f) == 3 for f in fields["event"])  # epoch|kind|detail
    assert all(len(f) == 6 for f in fields["call"])  # epoch|setup|entry|stop|tp|outcome
    assert fields["call"][-1][1] == "A" and fields["call"][-1][5] == "order"


def test_call_outcomes_record_what_the_pipeline_did():
    engine, broker = make_engine(Config())  # Ω veto by default
    feed(engine, broker, then(flat(84600, 60), [(84500, 84520, 83300, 83420)]))
    assert engine.calls[-1].outcome == "omega"
    assert engine_with_open_trade().calls[-1].outcome == "order"


def test_details_cannot_break_the_line_format():
    engine = engine_with_open_trade()
    from anon.engine import EngineEvent

    engine.events.append(EngineEvent(engine.events[-1].time, "risk_veto", "a|b\nc"))
    last = render(engine, "BTCUSDc", True, False)
    assert all("\n" not in line for line in last)
    event_lines = [line for line in last if line.startswith("event=")]
    assert event_lines[-1].endswith("|risk_veto|a/b c")


def test_write_is_crlf_utf8_with_heartbeat_first(tmp_path):
    target = tmp_path / "Common" / "Files" / "ANON_feed.txt"
    chart = ChartFeed(target)
    chart.update(engine_with_open_trade(), "BTCUSDc", True, True)
    assert chart.write(now_epoch=1_790_000_000)
    raw = target.read_bytes()
    assert raw.startswith(b"heartbeat=1790000000\r\nversion=1\r\n")
    assert "ai=on" in raw.decode("utf-8") and not target.with_suffix(".tmp").exists()


def test_feed_path_resolution(monkeypatch, tmp_path):
    assert resolve_feed_path("") is None
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert resolve_feed_path("auto") == tmp_path / "MetaQuotes" / "Terminal" / "Common" / "Files" / "ANON_feed.txt"
    monkeypatch.delenv("APPDATA")
    assert resolve_feed_path("auto") is None
    assert resolve_feed_path("x/feed.txt") == Path("x/feed.txt")
