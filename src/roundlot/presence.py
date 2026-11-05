"""Rebuild one instrument-session second by second and judge it against rule 4002.

Our resting orders come from our own private reports (`order_state`, `execution`,
`order_cancelled`); the wall comes from the published book. At every whole second the state
after all events stamped at or before that second is judged:

- the desk's highest non-stale bid and lowest non-stale offer must both exist;
- the size resting at each of those two prices must be at least the official round lot Li;
- the quoted spread must be at most 90% of the band (wall ask - wall bid; if a wall side is
  missing, the last valid two-sided width).

Eligible seconds exclude the opening five minutes, any second touched by an exchange outage or
a feed gap for the instrument, and everything after the desk's daily loss halt.

A failed second gets one reason, in this order (the same precedence as the team's own
`desk/ledger.py classify`): missing side, stale, no band, size, spread. A size failure is split:

- `size_config_lot`: every short side still showed at least the lot desk.toml implied, but that
  lot was below the official Li. Fixing the config would have fixed the second.
- `size_depleted`: a side showed less than even the config lot (part-filled and not yet topped
  up, capped by the order/name cap, or reduced by the risk layer).

Stale orders: the exchange never reports a STALE transition to the owner (order_events.proto),
so an order is marked stale once a book shows the wall at or through it (rule 2006). Returning
inside the band does not revive it; it stays stale until it is purged or cancelled.

This is a reconstruction, not the exchange's own count: book times are the end of their 100 ms
interval, so a stale transition can be up to 100 ms late here. Expect small differences from the
console, not different conclusions.
"""

from __future__ import annotations

import bisect
from collections import Counter
from dataclasses import dataclass, field
from statistics import median_low

from .deskconfig import DeskConfig
from .store import BUY, SELL, BookRow, SessionInfo

WARMUP_MS = 300_000
SPREAD_NUM, SPREAD_DEN = 9, 10  # spread <= 90% of band width
REASONS = ("missing_side", "stale", "no_band", "size_config_lot", "size_depleted", "spread")


@dataclass
class Order:
    side: int
    price: int
    remaining: int
    stale: bool = False


class OwnBook:
    """Our resting orders in one instrument, one per (side, price) as rule 2004 allows."""

    def __init__(self) -> None:
        self.orders: dict[tuple[int, int], Order] = {}

    def apply(self, r: dict) -> None:
        kind = r["type"]
        side = int(r["side"])
        if kind == "order_state":
            price = int(r["price"])
            old = r.get("old_price")
            prev = None
            if old is not None and int(old) != price:
                prev = self.orders.pop((side, int(old)), None)
            prev = prev or self.orders.get((side, price))
            stale = bool(prev and prev.stale) or r.get("state") == "STALE"
            rem = int(r.get("rem") or 0)
            if r.get("state") in ("RESTING", "STALE") and rem > 0:
                self.orders[(side, price)] = Order(side, price, rem, stale)
            else:
                self.orders.pop((side, price), None)
        elif kind == "execution":
            if r.get("order_price") is None:
                return
            key = (side, int(r["order_price"]))
            order = self.orders.get(key)
            if order is None:
                return  # an aggressive fill on arrival; its remainder is reported by order_state
            rem = int(r.get("rem") or 0)
            if rem <= 0:
                del self.orders[key]
            else:
                order.remaining = rem
        elif kind == "cancelled":
            if r.get("price") is not None:
                self.orders.pop((side, int(r["price"])), None)

    def mark_stale(self, wall_bid: int | None, wall_ask: int | None) -> None:
        for o in self.orders.values():
            if o.stale:
                continue
            if o.side == BUY and wall_bid is not None and o.price <= wall_bid:
                o.stale = True
            elif o.side == SELL and wall_ask is not None and o.price >= wall_ask:
                o.stale = True

    def has_side(self, side: int) -> bool:
        return any(o.side == side for o in self.orders.values())

    def best(self, side: int) -> Order | None:
        live = [o for o in self.orders.values() if o.side == side and not o.stale]
        if not live:
            return None
        return max(live, key=lambda o: o.price) if side == BUY else min(live, key=lambda o: o.price)


@dataclass
class SessionResult:
    symbol: str
    date: str
    official_lot: int
    eligible_s: int = 0
    qualifying_s: int = 0
    lost: dict[str, int] = field(default_factory=lambda: {k: 0 for k in REASONS})
    size_only_s: int = 0  # size failed and nothing else did: what fixing size alone would add
    hist_lots: Counter = field(default_factory=Counter)  # config lot per eligible second
    shown_min: list[int] = field(default_factory=list)  # min(bid size, ask size) when both quoted
    report_events: int = 0

    @property
    def presence(self) -> float | None:
        return self.qualifying_s / self.eligible_s if self.eligible_s else None

    @property
    def size_failure_s(self) -> int:
        return self.lost["size_config_lot"] + self.lost["size_depleted"]

    @property
    def historical_lot(self) -> int | None:
        return self.hist_lots.most_common(1)[0][0] if self.hist_lots else None

    @property
    def median_shown(self) -> int | None:
        return int(median_low(self.shown_min)) if self.shown_min else None


def excluded_intervals(info: SessionInfo, symbol: str) -> list[tuple[int, int]]:
    out = []
    for start, end in info.outages:
        out.append((int(start), int(end if end is not None else info.close_time)))
    for gap in info.feed_gaps:
        if gap.get("instrument") in (None, "", symbol):
            end = gap.get("end")
            out.append((int(gap["start"]), int(end if end is not None else info.close_time)))
    return sorted(out)


def _touched(second_end: int, intervals: list[tuple[int, int]]) -> bool:
    lo = second_end - 1000
    return any(s < second_end and e > lo for s, e in intervals)


def evaluate(
    symbol: str,
    official_lot: int,
    info: SessionInfo,
    books: list[BookRow],
    reports: list[dict],
    config: DeskConfig,
    halt_time: int | None = None,
) -> SessionResult:
    res = SessionResult(symbol=symbol, date=info.date, official_lot=official_lot, report_events=len(reports))
    # One timeline: books before reports at the same millisecond (rule 2007's ordering), reports
    # in the exchange's decided order otherwise.
    events: list[tuple[int, int, int, object]] = [(b.t, 0, i, b) for i, b in enumerate(books)]
    events += [(int(r["t"]), 1, i, r) for i, r in enumerate(reports)]
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    times = [e[0] for e in events]

    book = OwnBook()
    wall_bid = wall_ask = None
    band = None  # last valid two-sided band width
    mid = None  # last two-sided wall mid, micro-dollars
    end = info.close_time if halt_time is None else min(info.close_time, halt_time)
    gaps = excluded_intervals(info, symbol)
    first = info.open_time + WARMUP_MS + 1000
    i = 0
    for t in range(first, end + 1, 1000):
        # the close cancels everything at its own timestamp (rule 2010): judge the last second
        # on the book just before it
        j = bisect.bisect_right(times, t if t < info.close_time else t - 1)
        while i < j:
            _, kind, _, ev = events[i]
            if kind == 0:
                wall_bid, wall_ask = ev.wall_bid, ev.wall_ask
                if wall_bid is not None and wall_ask is not None and wall_ask > wall_bid:
                    band = wall_ask - wall_bid
                    mid = (wall_bid + wall_ask) / 2
                book.mark_stale(wall_bid, wall_ask)
            else:
                book.apply(ev)
            i += 1
        if _touched(t, gaps):
            continue
        res.eligible_s += 1
        hist = config.historical_lot(symbol, None if mid is None else mid / 1_000_000)
        if hist is not None:
            res.hist_lots[hist] += 1
        reason = _judge(book, official_lot, band, hist, res)
        if reason is None:
            res.qualifying_s += 1
        else:
            res.lost[reason] += 1
    return res


def _judge(book: OwnBook, lot: int, band: int | None, hist: int | None, res: SessionResult) -> str | None:
    if not book.has_side(BUY) or not book.has_side(SELL):
        return "missing_side"
    bid, ask = book.best(BUY), book.best(SELL)
    if bid is None or ask is None:
        return "stale"
    res.shown_min.append(min(bid.remaining, ask.remaining))
    if band is None:
        return "no_band"
    spread_bad = (ask.price - bid.price) * SPREAD_DEN > SPREAD_NUM * band
    short = [o.remaining for o in (bid, ask) if o.remaining < lot]
    if short:
        if not spread_bad:
            res.size_only_s += 1
        if hist is not None and hist < lot and all(s >= hist for s in short):
            return "size_config_lot"
        return "size_depleted"
    if spread_bad:
        return "spread"
    return None
