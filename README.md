# qte-roundlot-research

How much quoting presence does a market-making desk lose because its quotes are too small?

This project rebuilds a [Queen's Tower Exchange](https://github.com/josh-g-s/qte-sdk) (QTE)
market-making desk's quotes **second by second** from its own order reports. It checks every
second against the exchange's quoting obligation (Rulebook Revision 4, rule 4002), then puts
each lost second down to one cause, so you can tell an undersized quote from a missing,
stale or too-wide one.

Written for a QTE equity market-making desk in Winter term 2026 (sessions 2–4 November). It
works for any desk: pass the desk name and that desk's own strategy config.

## The problem

From 28 October 2026 a market maker must show **at least one official round lot (Lᵢ) on its
best bid and on its best offer, each on its own**, for a second to count. The rule also needs
the quoted spread to be at most 90% of the band, and presence of at least 85% of eligible seconds.
Below 85% the desk loses its market-share score for that name-session and goes down the
rule 4004 penalty ladder (rebate clawback, then $2,000, then $10,000 plus a suspension).

The exchange publishes Lᵢ for each name. A strategy that instead works Lᵢ out for itself
from the live price, using the rulebook's price table (≤ $250 → 100 shares, ≤ $1,000 → 40,
≤ $10,000 → 10), can get it wrong. The exchange sets Lᵢ from an *evaluation-period* average
price, not today's price, and lots can change between terms. A name trading just above a
price boundary, or whose lot was reset, can then be quoted too small all session. A presence
monitor that uses the same inferred lot will not catch it.

This tool measures that gap. It compares what was actually shown with the official Lᵢ, and
with the lot the strategy's config would have inferred.

## What it computes

For every assigned name and session:

1. Rebuild our resting orders from our private reports (`order_state`, `execution`,
   `order_cancelled`). Mark an order stale once the published wall reaches it (rule 2006).
2. At each whole second, take the highest non-stale bid and lowest non-stale offer and test:
   both exist, each shows ≥ Lᵢ, and the spread is ≤ 90% of the band.
3. Leave out ineligible time: the first 5 minutes, exchange outages, feed gaps for the name,
   and anything after a daily loss halt.
4. Give each failed second one reason, in this order: `missing_side` → `stale` → `no_band` →
   size → `spread`. A size failure is split in two:
   - **`size_config_lot`**: the quote showed at least the lot the config implied, but that lot
     was below the official Lᵢ. A config fix would have saved this second.
   - **`size_depleted`**: the quote was below even the config lot (eaten by fills and not
     topped up yet, or held down by an order or name cap).

### Output columns (`outputs/presence_summary_<from>_<to>.csv`)

| column | meaning |
|---|---|
| `symbol` | stock |
| `official_round_lot` | required minimum Lᵢ (exchange CSV) |
| `eligible_seconds` | seconds assessed |
| `qualifying_seconds` | seconds meeting the obligation |
| `presence_pct` | overall presence |
| `sessions_below_85pct` | how many sessions fell below 85% (dates in `sessions_below_85pct_dates`; the per-session file has a True/False per day) |
| `size_failure_seconds` | seconds lost to insufficient size |
| `size_failure_pct_eligible` | that as a % of eligible time |
| `dominant_failure_reason` | the main identified failure |
| `historical_lot_used` | the lot the local strategy config implied (most common over the period) |
| `presence_pct_if_size_ok` | presence if only the size failures had been fixed |
| `median_shown_shares`, `config_target_shares`, `lost_*_s`, `note` | diagnostics |

Open **`report_*.html`** in a browser for the readable version: colour-coded presence per day
(green ok, amber A, orange B, red C), sortable columns and a filter box. It is rebuilt after
every `analyse`, or on its own with `python -m roundlot report`.

There is also `presence_by_session_*.csv` (one row per name-day, with the rule 4004 band) and
`summary_*.md` (desk totals, the names whose config lot is below Lᵢ, and suggested
`[quoting.size_overrides]` lines).

## Run it

Needs Python 3.11+ and git.

```powershell
# Windows PowerShell, in VS Code's terminal
git clone https://github.com/deborahbabatope06-collab/qte-roundlot-research
cd qte-roundlot-research
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[fetch,dev]"

python -m roundlot demo          # made-up data, no token: checks everything works
pytest                           # unit tests

copy .env.example .env           # then paste the team token into .env

python -m roundlot fetch   --team "YOUR TEAM NAME" --from 2026-11-02 --to 2026-11-04
python -m roundlot analyse --from 2026-11-02 --to 2026-11-04 --desk-toml path\to\your\desk.toml
```

(macOS/Linux: `python3 -m venv .venv`, `source .venv/bin/activate`, `cp` for `copy`.)

`--desk-toml` points at the strategy config that was live, wherever it is kept; it is only
read, never copied. The tool reads the keys `[quoting] lot_by_price`, `size_lots` and
`size_overrides`, and `[stocks.<SYMBOL>] size_lots`. Without the flag it assumes the rulebook
price table with no overrides. If the config changed during the week, give the one for each
day: `--desk-toml-for 2026-11-04=path\to\desk-wed.toml`.

## Layout

```
src/roundlot/
  instruments.py   official round lots from the exchange CSV
  deskconfig.py    the lot a price-table strategy config implies
  fetch.py         downloads sessions with the QTE SDK's history client
  store.py         the local data format (gzipped CSV / JSON lines)
  presence.py      own-order book rebuild + per-second rule 4002 test
  report.py        roll-ups, rule 4004 bands, CSV and markdown output
  html.py          the self-contained HTML report
  synthetic.py     a made-up desk for the demo and tests
tests/             pytest
```

## Limits

- This is a reconstruction, not the exchange's count. Book times are the end of each 100 ms
  publication interval, so a stale transition can show up to 100 ms late. Check headline
  names against the console's obligations panel.
- A penalty-suspended session is inferred, not read: it follows a sub-50% session and has no
  order reports for the name.
- Downloaded data, results, tokens and strategy configs are private. `data/`, `outputs/`,
  `.env` and `config/desk*.toml` are git-ignored; keep it that way.
