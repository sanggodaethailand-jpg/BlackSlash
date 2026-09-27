from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from anon.config import Config, LevelsConfig, load_config
from anon.data import load_bars

ROOT = Path(__file__).resolve().parents[1]


def test_example_config_loads_and_matches_handoff():
    cfg = load_config(ROOT / "config" / "anon.example.toml")
    assert cfg.levels.gray_low == 84550 and cfg.levels.gray_high == 84850
    assert cfg.risk.veto_tickets == (208190412,)
    assert cfg.risk.lot == 0.01 and cfg.execution.dry_run is True
    assert cfg.execution.server_utc_offset_hours == "auto" and cfg.execution.confirmed_by == ""


def test_bad_offset_string_rejected(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[execution]\nserver_utc_offset_hours = "bangkok"\n')
    with pytest.raises(ValueError, match="auto"):
        load_config(p)


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("[risk]\nlots = 1.0\n")
    with pytest.raises(ValueError, match="unknown keys"):
        load_config(p)


def test_levels_must_be_ordered():
    with pytest.raises(ValueError):
        replace(Config(), levels=LevelsConfig(tp1=84000)).validate()


def test_load_mt5_export(tmp_path):
    p = tmp_path / "BTCUSD_H1.csv"
    p.write_text(
        "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
        "2026.09.27\t11:00:00\t84500\t84700\t84400\t84650\t100\t0\t20\n"
        "2026.09.27\t10:00:00\t84300\t84550\t84200\t84500\t100\t0\t20\n"
    )
    bars = load_bars(p, server_utc_offset_hours=3)
    assert [b.time for b in bars] == [datetime(2026, 9, 27, 7, tzinfo=UTC), datetime(2026, 9, 27, 8, tzinfo=UTC)]
    assert bars[1].close == 84650


def test_load_plain_csv(tmp_path):
    p = tmp_path / "bars.csv"
    p.write_text("time,open,high,low,close\n2026-09-27T00:00:00Z,1,2,0.5,1.5\n")
    (bar,) = load_bars(p)
    assert bar.time == datetime(2026, 9, 27, tzinfo=UTC) and bar.high == 2
