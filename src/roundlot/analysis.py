"""Run the per-second evaluation over every assigned name and session on disk, and write the
outputs."""

from __future__ import annotations

from pathlib import Path

from . import html, report, store
from .deskconfig import DeskConfig
from .instruments import Instrument
from .presence import SessionResult, evaluate


def run(
    root: Path,
    dates: list[str],
    instruments: dict[str, Instrument],
    configs: dict[str, DeskConfig],
    default_config: DeskConfig,
    out_dir: Path,
    desk: str,
    log=print,
) -> dict[str, Path]:
    results: dict[str, list[SessionResult]] = {}
    for date in dates:
        info = store.read_session(root, date)
        all_reports = store.read_reports(root, date)
        halts = list(store.iter_halts(all_reports))
        halt = min(halts) if halts else None
        per_sym = store.reports_by_symbol(all_reports)
        config = configs.get(date, default_config)
        missing_books = 0
        for sym, inst in instruments.items():
            books = store.read_books(root, date, sym)
            if books is None:
                missing_books += 1
                continue
            res = evaluate(sym, inst.round_lot, info, books, per_sym.get(sym, []), config, halt)
            results.setdefault(sym, []).append(res)
        log(f"{date}: {len(instruments) - missing_books} names evaluated" + (f", {missing_books} without a book file" if missing_books else "") + (" (loss halt seen)" if halt else ""))
    if not results:
        raise SystemExit("nothing to analyse: run the fetch step first (or `demo`)")

    suspended = report.infer_suspensions(results)
    lot_change = {s: i.lot_change for s, i in instruments.items()}
    last_config = configs.get(dates[-1], default_config)
    size_lots = {s: last_config.lots_shown(s) for s in instruments}
    sessions = report.session_rows(results, suspended, lot_change)
    summary = report.summary_rows(results, suspended, lot_change, size_lots)

    tag = f"{dates[0]}_{dates[-1]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": out_dir / f"presence_summary_{tag}.csv",
        "sessions": out_dir / f"presence_by_session_{tag}.csv",
        "markdown": out_dir / f"summary_{tag}.md",
    }
    report.write_csv(paths["summary"], summary, report.SUMMARY_COLUMNS)
    report.write_csv(paths["sessions"], sessions, report.SESSION_COLUMNS)
    paths["markdown"].write_text(report.markdown(summary, sessions, dates, desk), encoding="utf-8")
    paths["html"] = html.build(paths["summary"], paths["sessions"], out_dir / f"report_{tag}.html", desk)
    return paths
