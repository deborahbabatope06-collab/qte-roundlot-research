"""What lot the desk's own strategy *thought* a name had, from its desk.toml.

The team's engine (desk/data.py `round_lot`) does not read the exchange's Li. It uses
`[quoting.size_overrides]` when a stock has one, otherwise the rulebook 4002 price table
(`lot_by_price`) applied to the live wall mid, (wall bid + wall ask) / 2. It then shows
`size_lots` of those lots per side (per-stock `[stocks.X] size_lots` overrides the default),
capped by its order and name caps. This module reproduces only the lot and the pre-cap target,
which is all that is needed to tell "the config lot was too small" from "the quote was
depleted by fills".
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LOT_BY_PRICE: tuple[tuple[float, int], ...] = ((250.0, 100), (1000.0, 40), (10000.0, 10))


@dataclass(frozen=True)
class DeskConfig:
    lot_by_price: tuple[tuple[float, int], ...] = DEFAULT_LOT_BY_PRICE
    size_overrides: dict[str, int] = field(default_factory=dict)
    size_lots: int = 2
    stock_size_lots: dict[str, int] = field(default_factory=dict)
    source: str = "defaults"

    def historical_lot(self, symbol: str, mid_dollars: float | None) -> int | None:
        """The lot the engine would have used at this mid (None without a mid to price)."""
        override = self.size_overrides.get(symbol)
        if override:
            return int(override)
        if mid_dollars is None:
            return None
        for up_to, lot in self.lot_by_price:
            if mid_dollars <= up_to:
                return int(lot)
        return 1

    def lots_shown(self, symbol: str) -> int:
        return int(self.stock_size_lots.get(symbol, self.size_lots))


def load_desk_config(path: str | Path | None) -> DeskConfig:
    """Parse a desk.toml. With no path, the engine's built-in defaults."""
    if path is None:
        return DeskConfig()
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    quoting = data.get("quoting", {})
    table = quoting.get("lot_by_price", DEFAULT_LOT_BY_PRICE)
    table = tuple(sorted((float(a), int(b)) for a, b in table))
    overrides = {str(k): int(v) for k, v in quoting.get("size_overrides", {}).items()}
    stocks = data.get("stocks", {})
    per_stock = {str(sym): int(v["size_lots"]) for sym, v in stocks.items() if isinstance(v, dict) and "size_lots" in v}
    return DeskConfig(
        lot_by_price=table,
        size_overrides=overrides,
        size_lots=int(quoting.get("size_lots", 2)),
        stock_size_lots=per_stock,
        source=str(path),
    )
