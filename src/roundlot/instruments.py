"""The exchange's published round lots (rule 4002), read from the Instruments CSV.

The CSV the exchange publishes for the 2 November 2026 term has one row per instrument with
`lot_size` (the QTE round lot Li in shares) and `applies_to` (which Market Making desks are
assigned the name, e.g. "Market Making: Chicago; Frankfurt; Tokyo").

Careful: this `lot_size` is the *round lot*. The SDK's `InstrumentInfo.lot_size` is a
different thing (the smallest order size, currently 1 share).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Instrument:
    symbol: str
    round_lot: int  # Li, shares, the minimum to display on each side
    tier: int
    desks: tuple[str, ...]
    lot_change: str  # e.g. "same" or "changed: 40 -> 100" against the 28 October term


def load_instruments(path: str | Path, desk: str | None = "Tokyo") -> dict[str, Instrument]:
    """Equity/ETF rows of the CSV, keyed by symbol. With `desk`, only the names assigned to
    that Market Making desk (option rows and other desks' names are dropped)."""
    out: dict[str, Instrument] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            applies = row.get("applies_to", "")
            if not applies.startswith("Market Making:"):
                continue  # Options Market Making rows have a contract multiplier, not a lot
            desks = tuple(d.strip() for d in applies.split(":", 1)[1].split(";") if d.strip())
            if desk and desk not in desks:
                continue
            out[row["symbol"]] = Instrument(
                symbol=row["symbol"],
                round_lot=int(float(row["lot_size"])),
                tier=int(row.get("tier") or 0),
                desks=desks,
                lot_change=(row.get("lot_vs_live_term_2026_10_28") or "").strip(),
            )
    if not out:
        raise ValueError(f"no Market Making rows for desk {desk!r} in {path}")
    return out
