"""Turn per-session results into the tables: one row per symbol over the period, one row per
symbol-session, and a short markdown summary."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .presence import REASONS, SessionResult

OBLIGATION = 0.85

LABELS = {
    "missing_side": "missing side",
    "stale": "stale quote",
    "no_band": "no band",
    "size_config_lot": "size: config lot below official",
    "size_depleted": "size: shown below lot (fills/caps)",
    "spread": "spread too wide",
}

SUMMARY_COLUMNS = [
    # the columns asked for, in order
    "symbol",
    "official_round_lot",
    "eligible_seconds",
    "qualifying_seconds",
    "presence_pct",
    "sessions_below_85pct",
    "size_failure_seconds",
    "size_failure_pct_eligible",
    "dominant_failure_reason",
    "historical_lot_used",
    # extra diagnostics
    "sessions_assessed",
    "sessions_below_85pct_dates",
    "presence_pct_if_size_ok",
    "historical_lot_values",
    "config_size_lots",
    "config_target_shares",
    "median_shown_shares",
    "lot_change_vs_28oct",
    *[f"lost_{k}_s" for k in REASONS],
    "note",
]

SESSION_COLUMNS = [
    "date",
    "symbol",
    "official_round_lot",
    "eligible_seconds",
    "qualifying_seconds",
    "presence_pct",
    "below_85pct",
    "rule_4004_band",
    "size_failure_seconds",
    "size_failure_pct_eligible",
    "presence_pct_if_size_ok",
    "dominant_failure_reason",
    "historical_lot_used",
    "median_shown_shares",
    *[f"lost_{k}_s" for k in REASONS],
    "note",
]


def pct(num: float, den: float) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def ladder_band(p: float | None) -> str:
    """Rule 4004 band for one instrument-session (before the third-B escalation)."""
    if p is None:
        return ""
    if p >= 0.85:
        return "ok"
    if p >= 0.70:
        return "A"
    if p >= 0.50:
        return "B"
    return "C"


def dominant(lost: dict[str, int]) -> str:
    if not any(lost.values()):
        return "none"
    return max(REASONS, key=lambda k: lost[k])


def dominant_label(lost: dict[str, int], hist: int | None, official: int) -> str:
    key = dominant(lost)
    if key == "none":
        return "none"
    if key == "size_config_lot" and hist is not None:
        return f"size: config lot {hist} < official {official}"
    return LABELS[key]


@dataclass
class SymbolRollup:
    symbol: str
    official_lot: int
    sessions: list[SessionResult] = field(default_factory=list)
    suspended: list[str] = field(default_factory=list)  # dates inferred as penalty suspensions
    notes: list[str] = field(default_factory=list)


def infer_suspensions(results: dict[str, list[SessionResult]]) -> dict[str, list[str]]:
    """Rule 4004: a C (presence < 50%, or the third B in the term) suspends the name for the
    next session. A session is treated as suspended only when it follows a C *and* we have no
    order reports in the name that day (a suspension stops us quoting it). Those sessions are
    flagged and left out of the period totals, because the exchange scores them zero rather
    than measuring presence. Only sessions in the analysed range are seen, so a B before the
    range is not counted."""
    out: dict[str, list[str]] = {}
    for sym, sessions in results.items():
        b_count = 0
        suspend_next = False
        for r in sorted(sessions, key=lambda s: s.date):
            if suspend_next and r.report_events == 0:
                out.setdefault(sym, []).append(r.date)
                suspend_next = False
                continue
            suspend_next = False
            band = ladder_band(r.presence)
            if band == "B":
                b_count += 1
            if band == "C" or (band == "B" and b_count >= 3):
                suspend_next = True
    return out


def session_rows(results: dict[str, list[SessionResult]], suspended: dict[str, list[str]], lot_change: dict[str, str]) -> list[dict]:
    rows = []
    for sym in sorted(results):
        for r in sorted(results[sym], key=lambda s: s.date):
            note = []
            if r.date in suspended.get(sym, []):
                note.append("likely penalty suspension (previous session C); excluded from totals")
            if r.report_events == 0:
                note.append("no order reports for this name")
            rows.append(
                {
                    "date": r.date,
                    "symbol": sym,
                    "official_round_lot": r.official_lot,
                    "eligible_seconds": r.eligible_s,
                    "qualifying_seconds": r.qualifying_s,
                    "presence_pct": pct(r.qualifying_s, r.eligible_s),
                    "below_85pct": (r.presence is not None and r.presence < OBLIGATION),
                    "rule_4004_band": ladder_band(r.presence),
                    "size_failure_seconds": r.size_failure_s,
                    "size_failure_pct_eligible": pct(r.size_failure_s, r.eligible_s),
                    "presence_pct_if_size_ok": pct(r.qualifying_s + r.size_only_s, r.eligible_s),
                    "dominant_failure_reason": dominant_label(r.lost, r.historical_lot, r.official_lot),
                    "historical_lot_used": r.historical_lot,
                    "median_shown_shares": r.median_shown,
                    **{f"lost_{k}_s": r.lost[k] for k in REASONS},
                    "note": "; ".join(note),
                }
            )
    return rows


def summary_rows(
    results: dict[str, list[SessionResult]],
    suspended: dict[str, list[str]],
    lot_change: dict[str, str],
    size_lots: dict[str, int],
) -> list[dict]:
    rows = []
    for sym in sorted(results):
        sessions = [r for r in results[sym] if r.date not in suspended.get(sym, [])]
        official = results[sym][0].official_lot
        eligible = sum(r.eligible_s for r in sessions)
        qualifying = sum(r.qualifying_s for r in sessions)
        size_only = sum(r.size_only_s for r in sessions)
        lost = {k: sum(r.lost[k] for r in sessions) for k in REASONS}
        size_fail = lost["size_config_lot"] + lost["size_depleted"]
        lots: Counter = Counter()
        shown: list[int] = []
        for r in sessions:
            lots.update(r.hist_lots)
            shown.extend(r.shown_min)
        hist = lots.most_common(1)[0][0] if lots else None
        below = sorted(r.date for r in sessions if r.presence is not None and r.presence < OBLIGATION)
        notes = []
        if suspended.get(sym):
            notes.append("excluded as likely suspension: " + ", ".join(suspended[sym]))
        if hist is not None and hist < official:
            notes.append(f"desk.toml lot {hist} is below the official {official}: add {sym} = {official} to [quoting.size_overrides]")
        target = hist * size_lots.get(sym, 2) if hist is not None else None
        shown.sort()
        rows.append(
            {
                "symbol": sym,
                "official_round_lot": official,
                "eligible_seconds": eligible,
                "qualifying_seconds": qualifying,
                "presence_pct": pct(qualifying, eligible),
                "sessions_below_85pct": len(below),
                "size_failure_seconds": size_fail,
                "size_failure_pct_eligible": pct(size_fail, eligible),
                "dominant_failure_reason": dominant_label(lost, hist, official),
                "historical_lot_used": hist,
                "sessions_assessed": len(sessions),
                "sessions_below_85pct_dates": " ".join(below),
                "presence_pct_if_size_ok": pct(qualifying + size_only, eligible),
                "historical_lot_values": "/".join(str(k) for k, _ in lots.most_common()),
                "config_size_lots": size_lots.get(sym),
                "config_target_shares": target,
                "median_shown_shares": shown[(len(shown) - 1) // 2] if shown else None,
                "lot_change_vs_28oct": lot_change.get(sym, ""),
                **{f"lost_{k}_s": lost[k] for k in REASONS},
                "note": "; ".join(notes),
            }
        )
    rows.sort(key=lambda r: (-(r["size_failure_seconds"] or 0), r["symbol"]))
    return rows


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})


def markdown(summary: list[dict], sessions: list[dict], dates: list[str], desk: str) -> str:
    eligible = sum(r["eligible_seconds"] for r in summary) or 1
    qualifying = sum(r["qualifying_seconds"] for r in summary)
    size_fail = sum(r["size_failure_seconds"] for r in summary)
    lost_total = {k: sum(r[f"lost_{k}_s"] for r in summary) for k in REASONS}
    total_lost = sum(lost_total.values()) or 1
    by_day: dict[str, list[dict]] = {}
    for s in sessions:
        by_day.setdefault(s["date"], []).append(s)

    lines = [
        f"# Round-lot presence check: {desk} desk, {dates[0]} to {dates[-1]}",
        "",
        f"Desk-wide presence {100 * qualifying / eligible:.1f}% over {len(summary)} names. "
        f"Seconds lost to size: {size_fail:,} ({100 * size_fail / eligible:.1f}% of eligible time, "
        f"{100 * size_fail / total_lost:.0f}% of all lost time).",
        "",
        "## Lost time by reason",
        "",
        "| reason | seconds | share of lost |",
        "|---|---:|---:|",
    ]
    for k in sorted(REASONS, key=lambda k: -lost_total[k]):
        lines.append(f"| {LABELS[k]} | {lost_total[k]:,} | {100 * lost_total[k] / total_lost:.1f}% |")
    lines += ["", "## By session", "", "| date | names | below 85% | A | B | C | mean presence |", "|---|---:|---:|---:|---:|---:|---:|"]
    for day in sorted(by_day):
        rows = [r for r in by_day[day] if r["presence_pct"] is not None]
        bands = Counter(r["rule_4004_band"] for r in rows)
        mean = sum(r["presence_pct"] for r in rows) / len(rows) if rows else 0
        lines.append(
            f"| {day} | {len(rows)} | {sum(r['below_85pct'] for r in rows)} | {bands['A']} | {bands['B']} | {bands['C']} | {mean:.1f}% |"
        )
    mismatch = [r for r in summary if r["historical_lot_used"] is not None and r["historical_lot_used"] < r["official_round_lot"]]
    lines += ["", "## Names where desk.toml's lot is below the official round lot", ""]
    if mismatch:
        lines += ["| symbol | official | desk.toml lot | presence | size-lost | presence if size ok |", "|---|---:|---:|---:|---:|---:|"]
        for r in mismatch:
            lines.append(
                f"| {r['symbol']} | {r['official_round_lot']} | {r['historical_lot_used']} | {r['presence_pct']}% | "
                f"{r['size_failure_pct_eligible']}% | {r['presence_pct_if_size_ok']}% |"
            )
        lines += ["", "Suggested `[quoting.size_overrides]` lines:", "", "```toml"]
        lines += [f"{r['symbol']} = {r['official_round_lot']}" for r in mismatch]
        lines += ["```"]
    else:
        lines.append("None.")
    worst = [r for r in summary if r["size_failure_seconds"]][:15]
    lines += ["", "## Worst 15 names by size-lost seconds", ""]
    if worst:
        lines += ["| symbol | Li | presence | size-lost | if size ok | dominant reason | median shown |", "|---|---:|---:|---:|---:|---|---:|"]
        for r in worst:
            lines.append(
                f"| {r['symbol']} | {r['official_round_lot']} | {r['presence_pct']}% | {r['size_failure_pct_eligible']}% | "
                f"{r['presence_pct_if_size_ok']}% | {r['dominant_failure_reason']} | {r['median_shown_shares']} |"
            )
    else:
        lines.append("No size failures.")
    lines += [
        "",
        "Method: our own order reports from the history service rebuilt per second against the published wall "
        "(rule 4002). A reconstruction, not the exchange's own count; check headline names against the console.",
        "",
    ]
    return "\n".join(lines)
