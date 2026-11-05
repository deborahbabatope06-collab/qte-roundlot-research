"""A made-up desk for the demo and the tests: no token, no exchange, nothing real.

Six invented names quoted by a toy version of the desk's engine for three one-hour sessions.
It reproduces the three ways a name loses presence: a config lot below the official round lot
(DEMOA), quotes eaten by fills and topped up late (DEMOB), and walls running through the quote
so it goes stale (DEMOC). The others are healthy.
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

from . import store
from .store import BUY, SELL

TICK = 10_000  # $0.01 in micro-dollars

# symbol: (price $, official lot, band ticks, fill rate /s, stale jumps /min)
NAMES = {
    "DEMOA": (310.0, 100, 12, 0.05, 0.2),  # price table says 40; official is 100
    "DEMOB": (120.0, 100, 10, 0.60, 0.2),  # busy: fills eat the size
    "DEMOC": (45.0, 100, 6, 0.05, 6.0),  # walls run through the quote
    "DEMOD": (520.0, 40, 14, 0.05, 0.2),
    "DEMOE": (80.0, 100, 8, 0.05, 0.2),
    "DEMOF": (2300.0, 10, 30, 0.02, 0.2),
}

DESK_TOML = """\
# Demo desk.toml: same shape as the team's file.
[quoting]
size_lots = 2
lot_by_price = [[250.0, 100], [1000.0, 40], [10000.0, 10]]

[quoting.size_overrides]

[stocks.DEMOB]
size_lots = 1
"""

INSTRUMENTS_CSV_HEADER = "symbol,asset_class,tier,tick_usd,lot_size,lot_unit,applies_to,lot_vs_live_term_2026_10_28\n"


def write_demo_inputs(folder: Path) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    toml = folder / "desk.toml"
    toml.write_text(DESK_TOML, encoding="utf-8")
    csv_path = folder / "instruments.csv"
    lines = [INSTRUMENTS_CSV_HEADER]
    for sym, (_, lot, *_rest) in NAMES.items():
        lines.append(f'{sym},equity,2,0.01,{lot},shares (round lot),"Market Making: Tokyo; London",same\n')
    lines.append('XOTHER,equity,2,0.01,100,shares (round lot),"Market Making: Chicago; London",same\n')
    csv_path.write_text("".join(lines), encoding="utf-8")
    return toml, csv_path


def _config_lot(price: float) -> int:
    for up_to, lot in ((250.0, 100), (1000.0, 40), (10000.0, 10)):
        if price <= up_to:
            return lot
    return 1


def simulate_day(root: Path, date: str, open_time: int, minutes: int = 60, seed: int = 0) -> None:
    rng = random.Random(f"{date}-{seed}")
    close = open_time + minutes * 60_000
    gap_start = open_time + 20 * 60_000
    info = store.SessionInfo(
        date=date,
        open_time=open_time,
        close_time=close,
        outages=[],
        feed_gaps=[{"instrument": "DEMOE", "start": gap_start, "end": gap_start + 120_000}],
    )
    store.write_session(root, info)
    reports: list[dict] = []
    for sym, (price, _lot, band_ticks, fill_rate, stale_per_min) in NAMES.items():
        size_lots = 1 if sym == "DEMOB" else 2
        target = _config_lot(price) * size_lots
        mid = int(price * 1_000_000)
        half = band_ticks * TICK // 2
        books = []
        quotes: dict[int, dict] = {}  # side -> {"price", "rem", "stale_at", "gone_until"}
        for t in range(open_time - 60_000, close + 1, 100):
            if rng.random() < 0.02:
                mid += rng.choice((-TICK, TICK))
            jump = rng.random() < stale_per_min / 600
            wall_bid, wall_ask = mid - half, mid + half
            if jump:  # a burst moves the wall through one of our quotes for a moment
                side = rng.choice((BUY, SELL))
                if side == BUY:
                    wall_bid = mid + half // 4
                else:
                    wall_ask = mid - half // 4
            books.append(store.BookRow(t, wall_bid, wall_ask))
            if t < open_time:
                continue
            inset = max(TICK, (half * 2) // 4 // TICK * TICK)
            for side in (BUY, SELL):
                q = quotes.get(side)
                if q and "price" in q and q.get("stale_at") is None:
                    if (side == BUY and q["price"] <= wall_bid) or (side == SELL and q["price"] >= wall_ask):
                        q["stale_at"] = t
                if q and q.get("stale_at") is not None and t >= q["stale_at"] + 750:
                    reports.append({"type": "cancelled", "t": t, "sym": sym, "side": side, "price": q["price"], "size": q["rem"], "reason": "PURGE_STALE"})
                    quotes[side] = {"gone_until": t + rng.randint(500, 2500)}
                    continue
                if q is None or ("price" not in q and t >= q.get("gone_until", 0)):
                    px = (wall_bid + inset) if side == BUY else (wall_ask - inset)
                    if wall_bid < px < wall_ask:
                        quotes[side] = {"price": px, "rem": target, "stale_at": None, "topup_at": None}
                        reports.append({"type": "order_state", "t": t, "sym": sym, "side": side, "price": px, "rem": target, "state": "RESTING", "old_price": None})
                    continue
                if "price" not in q or q.get("stale_at") is not None:
                    continue
                if rng.random() < fill_rate / 10:
                    size = max(1, int(target * rng.uniform(0.2, 0.9)))
                    rem = max(0, q["rem"] - size)
                    reports.append({"type": "execution", "t": t, "sym": sym, "side": side, "order_price": q["price"], "size": q["rem"] - rem, "rem": rem, "liq": "MAKER"})
                    if rem == 0:
                        quotes[side] = {"gone_until": t + rng.randint(300, 1500)}
                        continue
                    q["rem"] = rem
                    q["topup_at"] = t + rng.randint(800, 3000)
                if q.get("topup_at") and t >= q["topup_at"]:
                    q["rem"] = target
                    q["topup_at"] = None
                    reports.append({"type": "order_state", "t": t, "sym": sym, "side": side, "price": q["price"], "rem": target, "state": "RESTING", "old_price": q["price"]})
        store.write_books(root, date, sym, books)
    reports.sort(key=lambda r: r["t"])
    store.write_reports(root, date, reports)


def build_demo(root: Path, inputs: Path) -> tuple[list[str], Path, Path]:
    toml, csv_path = write_demo_inputs(inputs)
    dates = ["2026-11-02", "2026-11-03", "2026-11-04"]
    # 14:30 London (09:30 New York) on each day, in ms since the epoch
    opens = [int(datetime.fromisoformat(f"{d}T14:30:00+00:00").timestamp() * 1000) for d in dates]
    for date, t0 in zip(dates, opens):
        simulate_day(root, date, t0)
    return dates, toml, csv_path
