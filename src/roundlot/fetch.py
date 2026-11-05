"""Download what the analysis needs from the QTE history service, using the official SDK.

Per session date: the session's open/close and outages (`session_state`), the feed gaps
(`list_sessions`), our team's own private reports (`fetch_reports`) and each assigned name's
published `book`. Only the history service is read: nothing is sent to the exchange.

Needs `pip install "git+https://github.com/josh-g-s/qte-sdk"` and the team token in `.env`
(QTE_TOKEN=...) in the folder you run from. The token never goes in this repo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import store

DEFAULT_HISTORY_URL = "https://history.trading.queenstowerexchange.com"


def _enum_name(msg, field: str) -> str:
    value = getattr(msg, field)
    enum = msg.DESCRIPTOR.fields_by_name[field].enum_type
    v = enum.values_by_number.get(value)
    return v.name if v else str(value)


def _opt(msg, field: str):
    return getattr(msg, field) if msg.HasField(field) else None


def normalise_report(item) -> dict | None:
    """One SDK report as the plain dict `store` keeps, or None for one we do not use."""
    from qte_sdk.contract.v1.order_events_pb2 import Execution, OrderCancelled, OrderState, RiskNotice

    if isinstance(item, OrderState):
        return {
            "type": "order_state",
            "t": int(item.timestamp),
            "sym": item.instrument,
            "side": int(item.side),
            "price": int(item.price),
            "rem": int(item.remaining_size),
            "state": _enum_name(item, "state"),
            "old_price": _opt(item, "old_price"),
        }
    if isinstance(item, Execution):
        return {
            "type": "execution",
            "t": int(item.timestamp),
            "sym": item.instrument,
            "side": int(item.side),
            "order_price": _opt(item, "order_price"),
            "size": int(item.fill_size),
            "rem": int(item.remaining_size),
            "liq": _enum_name(item, "liquidity"),
        }
    if isinstance(item, OrderCancelled):
        return {
            "type": "cancelled",
            "t": int(item.timestamp),
            "sym": item.instrument,
            "side": int(item.side),
            "price": _opt(item, "price"),
            "size": int(item.cancelled_size),
            "reason": _enum_name(item, "reason_code"),
        }
    if isinstance(item, RiskNotice):
        return {"type": "risk", "t": int(item.timestamp), "kind": _enum_name(item, "kind")}
    return None  # Accepted, Reject, TermCloseFill, Unknown, DecodeFailed


async def _session_info(client, date: str, summaries: dict) -> store.SessionInfo:
    from qte_sdk.contract.v1.market_data_pb2 import SessionState

    open_time = close_time = None
    outages: list[list[int]] = []
    outage_start = None
    async for m in client.fetch_session_state(date):
        if not isinstance(m, SessionState):
            continue
        if m.open_time:
            open_time = int(m.open_time)
        if m.close_time:
            close_time = int(m.close_time)
        if m.outage_active and outage_start is None:
            outage_start = int(m.grid_time)
        elif not m.outage_active and outage_start is not None:
            outages.append([outage_start, int(m.grid_time)])
            outage_start = None
    if open_time is None or close_time is None:
        raise RuntimeError(f"{date}: the session_state stream gave no open/close time")
    if outage_start is not None:
        outages.append([outage_start, close_time])
    gaps = []
    summary = summaries.get(date)
    for g in getattr(summary, "feed_gaps", ()) if summary else ():
        if g.link != "stock":
            continue
        gaps.append({"instrument": g.instrument, "start": g.start_ts, "end": g.end_ts})
    return store.SessionInfo(date=date, open_time=open_time, close_time=close_time, outages=outages, feed_gaps=gaps)


async def _books(client, date: str, symbol: str) -> list[store.BookRow]:
    from qte_sdk.contract.v1.market_data_pb2 import Book

    rows = []
    async for m in client.fetch(date, symbol, "book"):
        if not isinstance(m, Book):
            continue
        bid = max((lv.price for lv in m.bid_levels), default=None)
        ask = min((lv.price for lv in m.ask_levels), default=None)
        rows.append(store.BookRow(int(m.grid_time), bid, ask))
    return rows


async def fetch(
    root: Path,
    dates: list[str],
    team: str,
    symbols: list[str],
    url: str | None = None,
    concurrency: int = 6,
    refresh: bool = False,
    log=print,
) -> None:
    try:
        from qte_sdk.history import HistoryClient
    except ImportError as error:  # pragma: no cover - depends on the user's install
        raise SystemExit(
            'The QTE SDK is not installed. Run: pip install "git+https://github.com/josh-g-s/qte-sdk"'
        ) from error

    client = HistoryClient(url=url or DEFAULT_HISTORY_URL, max_wait=600)
    summaries = {s.session_date: s for s in await client.list_sessions()}
    for date in dates:
        if date not in summaries:
            log(f"{date}: the history service lists no such session (not closed yet, or a holiday); skipped")
            continue
        if not summaries[date].ready:
            log(f"{date}: still being rebuilt by the service; try again later")
            continue
        day = store.day_dir(root, date)
        if refresh or not (day / "session.json").is_file():
            info = await _session_info(client, date, summaries)
            store.write_session(root, info)
            log(f"{date}: session {info.open_time}..{info.close_time}, {len(info.outages)} outages, {len(info.feed_gaps)} feed gaps")
        if refresh or not (day / "reports.jsonl.gz").is_file():
            reports = []
            async for item in client.fetch_reports(date, team):
                r = normalise_report(item)
                if r is not None:
                    reports.append(r)
            n = store.write_reports(root, date, reports)
            log(f"{date}: {n:,} own reports kept")
        todo = [s for s in symbols if refresh or not store.book_path(root, date, s).is_file()]
        sem = asyncio.Semaphore(concurrency)
        done = 0

        async def one(sym: str) -> None:
            nonlocal done
            async with sem:
                rows = await _books(client, date, sym)
                store.write_books(root, date, sym, rows)
                done += 1
                if done % 10 == 0 or done == len(todo):
                    log(f"{date}: books {done}/{len(todo)}")

        await asyncio.gather(*(one(s) for s in todo))
