import csv
from pathlib import Path

import pytest

from roundlot import cli, report
from roundlot.deskconfig import load_desk_config
from roundlot.instruments import load_instruments
from roundlot.presence import SessionResult

ROOT = Path(__file__).resolve().parents[1]


OFFICIAL_CSV = ROOT / "config" / "instruments_1102.csv"


@pytest.mark.skipif(not OFFICIAL_CSV.is_file(), reason="the exchange's Instruments CSV is not in config/")
def test_official_csv_tokyo_names():
    names = load_instruments(OFFICIAL_CSV, "Tokyo")
    assert len(names) == 108
    assert names["MCD"].round_lot == 100 and names["VMC"].round_lot == 100
    assert names["ABBV"].round_lot == 40


def test_desk_toml_lot(tmp_path):
    toml = tmp_path / "desk.toml"
    toml.write_text(
        "[quoting]\nsize_lots = 2\nlot_by_price = [[250.0, 100], [1000.0, 40], [10000.0, 10]]\n"
        "[quoting.size_overrides]\nABBV = 100\n[stocks.AMD]\nsize_lots = 3\n",
        encoding="utf-8",
    )
    cfg = load_desk_config(toml)
    assert cfg.historical_lot("ABBV", 50.0) == 100  # the override wins
    assert cfg.historical_lot("MCD", 300.0) == 40
    assert cfg.historical_lot("X", 250.0) == 100 and cfg.historical_lot("X", 250.01) == 40
    assert cfg.historical_lot("X", 20000.0) == 1
    assert cfg.lots_shown("AMD") == 3 and cfg.lots_shown("MCD") == 2


def test_ladder_and_suspension():
    assert [report.ladder_band(p) for p in (0.9, 0.8, 0.6, 0.3)] == ["ok", "A", "B", "C"]
    mon = SessionResult("X", "2026-11-02", 100, eligible_s=100, qualifying_s=10, report_events=50)
    tue_quiet = SessionResult("X", "2026-11-03", 100, eligible_s=100, qualifying_s=0, report_events=0)
    assert report.infer_suspensions({"X": [mon, tue_quiet]}) == {"X": ["2026-11-03"]}
    tue_quoting = SessionResult("X", "2026-11-03", 100, eligible_s=100, qualifying_s=0, report_events=9)
    assert report.infer_suspensions({"X": [mon, tue_quoting]}) == {}


def test_demo_end_to_end(tmp_path):
    assert cli.main(["demo", "--data", str(tmp_path / "data"), "--out", str(tmp_path / "out")]) == 0
    summary = next((tmp_path / "out").glob("presence_summary_*.csv"))
    rows = {r["symbol"]: r for r in csv.DictReader(summary.open(encoding="utf-8"))}
    header = list(next(iter(rows.values())).keys())
    assert header[:10] == report.SUMMARY_COLUMNS[:10]
    assert rows["DEMOA"]["dominant_failure_reason"] == "size: config lot 40 < official 100"
    assert rows["DEMOA"]["historical_lot_used"] == "40"
    assert float(rows["DEMOF"]["presence_pct"]) > 85


def test_html_report(tmp_path):
    cli.main(["demo", "--data", str(tmp_path / "data"), "--out", str(tmp_path / "out")])
    page = next((tmp_path / "out").glob("report_*.html")).read_text(encoding="utf-8")
    assert "DEMOA" in page and "Fix: DEMOA = 100 in size_overrides" in page
    assert cli.main(["report", "--out", str(tmp_path / "out")]) == 0
