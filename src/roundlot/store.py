"""The local data folder: what `fetch` writes and `analyse` reads.

    data/raw/<YYYY-MM-DD>/session.json        open, close, outages, feed gaps
    data/raw/<YYYY-MM-DD>/reports.jsonl.gz    our team's own reports, one JSON object a line
    data/raw/<YYYY-MM-DD>/books/<SYM>.csv.gz  grid_time,wall_bid,wall_ask (micro-dollars)

Prices are integer micro-dollars and times integer milliseconds since the Unix epoch, as the
exchange publishes them. Keeping the analysis on these plain files means it runs offline,
reruns in seconds, and is testable without a token.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

BUY, SELL = 1, 2


@dataclass
class SessionInfo:
    date: str
    open_time: int
    close_time: int
    outages: list[list[int]] = field(default_factory=list)  # [start, end] exchange outages (outage_active)
    feed_gaps: list[dict] = field(default_factory=list)  # {"instrument": str|None, "start": int, "end": int|None}


@dataclass(frozen=True)
class BookRow:
    t: int
    wall_bid: int | None
    wall_ask: int | None


def day_dir(root: Path, date: str) -> Path:
    return Path(root) / "raw" / date


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_session(root: Path, info: SessionInfo) -> None:
    _atomic_write(day_dir(root, info.date) / "session.json", json.dumps(asdict(info), indent=1).encode())


def read_session(root: Path, date: str) -> SessionInfo:
    raw = json.loads((day_dir(root, date) / "session.json").read_text(encoding="utf-8"))
    return SessionInfo(**raw)


def write_reports(root: Path, date: str, reports: Iterable[dict]) -> int:
    buf = io.BytesIO()
    n = 0
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for r in reports:
            gz.write((json.dumps(r, separators=(",", ":")) + "\n").encode())
            n += 1
    _atomic_write(day_dir(root, date) / "reports.jsonl.gz", buf.getvalue())
    return n


def read_reports(root: Path, date: str) -> list[dict]:
    path = day_dir(root, date) / "reports.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def book_path(root: Path, date: str, symbol: str) -> Path:
    return day_dir(root, date) / "books" / f"{symbol}.csv.gz"


def write_books(root: Path, date: str, symbol: str, rows: Iterable[BookRow]) -> int:
    """Only rows where the best wall prices change are kept: the analysis needs nothing else."""
    text = io.StringIO()
    w = csv.writer(text)
    w.writerow(["grid_time", "wall_bid", "wall_ask"])
    last = None
    n = 0
    for r in rows:
        key = (r.wall_bid, r.wall_ask)
        if key == last:
            continue
        last = key
        w.writerow([r.t, "" if r.wall_bid is None else r.wall_bid, "" if r.wall_ask is None else r.wall_ask])
        n += 1
    _atomic_write(book_path(root, date, symbol), gzip.compress(text.getvalue().encode()))
    return n


def read_books(root: Path, date: str, symbol: str) -> list[BookRow] | None:
    path = book_path(root, date, symbol)
    if not path.is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        out = []
        for row in csv.DictReader(handle):
            out.append(
                BookRow(
                    int(row["grid_time"]),
                    int(row["wall_bid"]) if row["wall_bid"] else None,
                    int(row["wall_ask"]) if row["wall_ask"] else None,
                )
            )
        return out


def dates_on_disk(root: Path) -> list[str]:
    base = Path(root) / "raw"
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "session.json").is_file())


def reports_by_symbol(reports: Iterable[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in reports:
        sym = r.get("sym")
        if sym:
            out.setdefault(sym, []).append(r)
    return out


def iter_halts(reports: Iterable[dict]) -> Iterator[int]:
    """Times the desk entered its daily loss halt (rule 3004): no eligible time after it."""
    for r in reports:
        if r.get("type") == "risk" and r.get("kind") == "LOSS_HALT_ENTERED":
            yield int(r["t"])
