"""Command line.

    python -m roundlot fetch   --team "<team>" --from 2026-11-02 --to 2026-11-04
    python -m roundlot analyse --from 2026-11-02 --to 2026-11-04 --desk-toml config/desk.toml
    python -m roundlot demo                       # made-up data, no token needed
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from . import analysis, store
from .deskconfig import load_desk_config
from .instruments import load_instruments

DEFAULT_INSTRUMENTS = "config/instruments_1102.csv"
DEFAULT_DESK_TOML = "config/desk.toml"


def weekdays(start: str, end: str) -> list[str]:
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while a <= b:
        if a.weekday() < 5:
            out.append(a.isoformat())
        a += timedelta(days=1)
    return out


def _desk_toml_for(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values or []:
        day, _, path = v.partition("=")
        if not path:
            raise SystemExit(f"--desk-toml-for wants DATE=PATH, got {v!r}")
        out[day] = path
    return out


def cmd_fetch(args: argparse.Namespace) -> int:
    from .fetch import fetch

    team = args.team or os.environ.get("QTE_TEAM")
    if not team:
        raise SystemExit("--team is required (your team name exactly as the exchange states it; desk.log shows it after 'connected as team')")
    instruments = load_instruments(args.instruments, args.desk)
    dates = weekdays(args.date_from, args.date_to)
    asyncio.run(
        fetch(Path(args.data), dates, team, sorted(instruments), url=args.history_url or os.environ.get("QTE_HISTORY_URL"), concurrency=args.concurrency, refresh=args.refresh)
    )
    print("done; now run: python -m roundlot analyse --from", args.date_from, "--to", args.date_to)
    return 0


def cmd_analyse(args: argparse.Namespace) -> int:
    instruments = load_instruments(args.instruments, args.desk)
    on_disk = set(store.dates_on_disk(Path(args.data)))
    dates = [d for d in weekdays(args.date_from, args.date_to) if d in on_disk]
    if not dates:
        raise SystemExit(f"no fetched sessions between {args.date_from} and {args.date_to} in {args.data}/raw")
    toml = args.desk_toml if Path(args.desk_toml).is_file() else None
    if toml is None:
        print(f"warning: {args.desk_toml} not found; using the engine's default lot table with no overrides")
    default = load_desk_config(toml)
    per_day = {d: load_desk_config(p) for d, p in _desk_toml_for(args.desk_toml_for).items()}
    paths = analysis.run(Path(args.data), dates, instruments, per_day, default, Path(args.out), args.desk)
    for name, p in paths.items():
        print(f"{name:>9}: {p}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from . import html

    out = Path(args.out)
    candidates = sorted(out.glob("presence_summary_*.csv"), key=lambda p: p.stat().st_mtime)
    if args.date_from and args.date_to:
        candidates = [p for p in candidates if p.stem.endswith(f"{args.date_from}_{args.date_to}")] or candidates
    if not candidates:
        raise SystemExit(f"no presence_summary_*.csv in {out}: run analyse first")
    summary = candidates[-1]
    tag = summary.stem.removeprefix("presence_summary_")
    sessions = out / f"presence_by_session_{tag}.csv"
    path = html.build(summary, sessions, out / f"report_{tag}.html", args.desk)
    print(f"   report: {path}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .synthetic import build_demo

    root = Path(args.data)
    dates, toml, csv_path = build_demo(root, root / "inputs")
    instruments = load_instruments(csv_path, "Tokyo")
    paths = analysis.run(root, dates, instruments, {}, load_desk_config(toml), Path(args.out), "Tokyo (demo)")
    for name, p in paths.items():
        print(f"{name:>9}: {p}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="roundlot", description="QTE market-maker presence and round-lot size check (rule 4002).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--from", dest="date_from", default="2026-11-02")
        sp.add_argument("--to", dest="date_to", default="2026-11-04")
        sp.add_argument("--instruments", default=DEFAULT_INSTRUMENTS, help="the exchange's Instruments CSV with round lots")
        sp.add_argument("--desk", default="Tokyo", help="your Market Making desk as named in applies_to")
        sp.add_argument("--data", default="data", help="folder for downloaded data")

    f = sub.add_parser("fetch", help="download sessions from the history service (needs the token)")
    common(f)
    f.add_argument("--team", help="team name as the exchange states it (or set QTE_TEAM)")
    f.add_argument("--history-url", help="default https://history.trading.queenstowerexchange.com")
    f.add_argument("--concurrency", type=int, default=6)
    f.add_argument("--refresh", action="store_true", help="download again even if files exist")
    f.set_defaults(func=cmd_fetch)

    a = sub.add_parser("analyse", aliases=["analyze"], help="compute presence and size failures from fetched data")
    common(a)
    a.add_argument("--desk-toml", default=DEFAULT_DESK_TOML, help="the team's desk.toml that ran")
    a.add_argument("--desk-toml-for", action="append", metavar="DATE=PATH", help="a different desk.toml for one date")
    a.add_argument("--out", default="outputs")
    a.set_defaults(func=cmd_analyse)

    r = sub.add_parser("report", help="rebuild the HTML report from the latest output CSVs")
    r.add_argument("--from", dest="date_from")
    r.add_argument("--to", dest="date_to")
    r.add_argument("--out", default="outputs")
    r.add_argument("--desk", default="Tokyo")
    r.set_defaults(func=cmd_report)

    d = sub.add_parser("demo", help="run end to end on made-up data")
    d.add_argument("--data", default="data/demo")
    d.add_argument("--out", default="outputs/demo")
    d.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
