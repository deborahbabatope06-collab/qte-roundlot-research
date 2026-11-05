"""A readable, self-contained HTML report from the two output CSVs.

    python -m roundlot report --from 2026-11-02 --to 2026-11-04

Opens in any browser, works offline (no external files), and is written next to the CSVs as
outputs/report_<from>_<to>.html. Table columns sort on click; the search box filters names.
"""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path

REASON_LABELS = {
    "missing_side": "Missing side",
    "stale": "Stale quote",
    "size_depleted": "Size: below lot after fills/caps",
    "size_config_lot": "Size: config lot below official",
    "spread": "Spread too wide",
    "no_band": "No band",
}


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _short_note(note: str, sym: str, lot: str) -> str:
    """The CSV's notes, shortened for a table cell."""
    parts = []
    for part in (p.strip() for p in note.split(";") if p.strip()):
        if "is below the official" in part:
            parts.append(f"Fix: {sym} = {lot} in size_overrides")
        else:
            parts.append(part)
    return "; ".join(parts)


def build(summary_csv: Path, sessions_csv: Path, out: Path, desk: str = "") -> Path:
    summary = list(csv.DictReader(summary_csv.open(encoding="utf-8")))
    sessions = list(csv.DictReader(sessions_csv.open(encoding="utf-8")))
    dates = sorted({s["date"] for s in sessions})

    by_name: dict[str, dict] = {}
    for s in sessions:
        by_name.setdefault(s["symbol"], {})[s["date"]] = {"p": _num(s["presence_pct"]), "band": s["rule_4004_band"]}

    rows = []
    for r in summary:
        elig = _num(r["eligible_seconds"]) or 0
        def share(key: str) -> float | None:
            return round(100 * (_num(r[key]) or 0) / elig, 2) if elig else None
        rows.append(
            {
                "sym": r["symbol"],
                "lot": int(_num(r["official_round_lot"]) or 0),
                "cfg": r["historical_lot_used"],
                "days": [by_name.get(r["symbol"], {}).get(d, {"p": None, "band": ""}) for d in dates],
                "p": _num(r["presence_pct"]),
                "pfix": _num(r["presence_pct_if_size_ok"]),
                "size": _num(r["size_failure_pct_eligible"]),
                "miss": share("lost_missing_side_s"),
                "stale": share("lost_stale_s"),
                "shown": _num(r["median_shown_shares"]),
                "why": r["dominant_failure_reason"],
                "note": _short_note(r["note"], r["symbol"], r["official_round_lot"]),
            }
        )

    total_elig = sum(_num(r["eligible_seconds"]) or 0 for r in summary) or 1
    total_q = sum(_num(r["qualifying_seconds"]) or 0 for r in summary)
    lost = {k: sum(_num(r[f"lost_{k}_s"]) or 0 for r in summary) for k in REASON_LABELS}
    lost_total = sum(lost.values()) or 1
    day_stats = []
    for d in dates:
        ps = [_num(s["presence_pct"]) for s in sessions if s["date"] == d and _num(s["presence_pct"]) is not None]
        bands = [s["rule_4004_band"] for s in sessions if s["date"] == d]
        day_stats.append(
            {
                "date": d,
                "mean": round(sum(ps) / len(ps), 1) if ps else None,
                "below": sum(p < 85 for p in ps),
                "n": len(ps),
                "A": bands.count("A"),
                "B": bands.count("B"),
                "C": bands.count("C"),
            }
        )
    data = {
        "desk": desk,
        "dates": dates,
        "presence": round(100 * total_q / total_elig, 1),
        "names": len(summary),
        "lost": [{"label": REASON_LABELS[k], "s": int(v), "share": round(100 * v / lost_total, 1)} for k, v in sorted(lost.items(), key=lambda kv: -kv[1])],
        "size_share": round(100 * (lost["size_depleted"] + lost["size_config_lot"]) / lost_total, 1),
        "days": day_stats,
        "rows": rows,
    }
    title = f"Presence report {dates[0]} to {dates[-1]}" if dates else "Presence report"
    page = TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    out.write_text(page, encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fafaf9;--panel:#fff;--ink:#1c1917;--muted:#78716c;--line:#e7e5e4;--ok:#15803d;--okbg:#dcfce7;--a:#a16207;--abg:#fef3c7;--b:#c2410c;--bbg:#ffedd5;--c:#b91c1c;--cbg:#fee2e2;--bar:#57534e;--accent:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--panel:#292524;--ink:#f5f5f4;--muted:#a8a29e;--line:#44403c;--ok:#86efac;--okbg:#14532d;--a:#fde68a;--abg:#713f12;--b:#fdba74;--bbg:#7c2d12;--c:#fca5a5;--cbg:#7f1d1d;--bar:#d6d3d1;--accent:#93c5fd}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.sub{color:var(--muted);margin:0 0 20px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}.big{font-size:26px;font-weight:650;font-variant-numeric:tabular-nums}.lbl{color:var(--muted);font-size:12px}
.reason{display:grid;grid-template-columns:minmax(150px,260px) 1fr 160px;gap:10px;align-items:center;margin:6px 0}.track{height:10px;background:var(--line);border-radius:5px;overflow:hidden}.fill{height:100%;background:var(--bar)}
.num{text-align:right;font-variant-numeric:tabular-nums}table{border-collapse:collapse;width:100%;background:var(--panel);font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}th,td{padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
th{position:sticky;top:0;background:var(--panel);text-align:left;cursor:pointer;user-select:none;font-size:12px;color:var(--muted)}th:hover{color:var(--ink)}
td.note{white-space:normal;min-width:160px;max-width:260px;color:var(--muted);font-size:12px}
.pill{display:inline-block;min-width:58px;text-align:center;padding:2px 6px;border-radius:6px;font-weight:600}
.ok{color:var(--ok);background:var(--okbg)}.A{color:var(--a);background:var(--abg)}.B{color:var(--b);background:var(--bbg)}.C{color:var(--c);background:var(--cbg)}
.controls{display:flex;gap:12px;flex-wrap:wrap;align-items:center;margin-bottom:10px}input[type=search]{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);min-width:200px}
.legend span{margin-right:10px}.flag{color:var(--c);font-weight:600}
</style></head><body><main>
<h1 id="h"></h1><p class="sub" id="sub"></p>
<div class="grid" id="kpis"></div>
<h2>Where the lost time went</h2><div class="card" id="reasons"></div>
<h2>By session</h2><div class="wrap"><table id="days"></table></div>
<h2>By name</h2>
<div class="controls"><input type="search" id="q" placeholder="Filter by symbol or reason"><label><input type="checkbox" id="below"> Only names below 85% on some day</label>
<span class="legend"><span class="pill ok">ok ≥85</span><span class="pill A">A 70–85</span><span class="pill B">B 50–70</span><span class="pill C">C &lt;50</span></span></div>
<div class="wrap"><table id="names"></table></div>
<p class="sub" style="margin-top:16px">Rule 4002 rebuilt per second from our own order reports against the published wall. A reconstruction, not the exchange's own count. "If size ok" = presence had every size failure been fixed.</p>
</main>
<script>
const D=__DATA__;
const fmt=(v,d=1)=>v==null||v===""?"–":Number(v).toFixed(d);
const band=p=>p==null?"":p>=85?"ok":p>=70?"A":p>=50?"B":"C";
document.getElementById("h").textContent=(D.desk?D.desk+" desk: ":"")+"quoting presence";
document.getElementById("sub").textContent=D.dates.join(", ")+" · "+D.names+" names";
const k=document.getElementById("kpis");
const kpi=(v,l)=>`<div class="card"><div class="big">${v}</div><div class="lbl">${l}</div></div>`;
k.innerHTML=kpi(D.presence+"%","desk presence (85% required)")+D.days.map(d=>kpi(`${d.below}/${d.n}`,`names below 85% on ${d.date}`)).join("")+kpi(D.size_share+"%","of lost time due to size");
document.getElementById("reasons").innerHTML=D.lost.map(r=>`<div class="reason"><div>${r.label}</div><div class="track"><div class="fill" style="width:${r.share}%"></div></div><div class="num">${r.share}% · ${r.s.toLocaleString()} s</div></div>`).join("");
document.getElementById("days").innerHTML="<tr><th>Date</th><th class=num>Mean presence</th><th class=num>Below 85%</th><th class=num>A</th><th class=num>B</th><th class=num>C</th></tr>"+D.days.map(d=>`<tr><td>${d.date}</td><td class=num>${fmt(d.mean)}%</td><td class=num>${d.below}</td><td class=num>${d.A}</td><td class=num>${d.B}</td><td class=num>${d.C}</td></tr>`).join("");
const cols=[["sym","Symbol"],["lot","Lot","num"],["cfg","Config lot","num"],...D.dates.map((d,i)=>["d"+i,d.slice(5),"num"]),["p","Overall %","num"],["pfix","If size ok %","num"],["size","Size lost %","num"],["miss","Missing %","num"],["stale","Stale %","num"],["shown","Median shown","num"],["why","Main reason"],["note","Note"]];
let sortKey="p",asc=true;
const val=(r,key)=>key.startsWith("d")&&key.length<4?(r.days[+key.slice(1)]||{}).p:r[key];
function render(){
 const q=document.getElementById("q").value.toLowerCase(),only=document.getElementById("below").checked;
 let rows=D.rows.filter(r=>(!q||r.sym.toLowerCase().includes(q)||r.why.toLowerCase().includes(q))&&(!only||r.days.some(d=>d.p!=null&&d.p<85)));
 rows.sort((a,b)=>{let x=val(a,sortKey),y=val(b,sortKey);if(x==null)return 1;if(y==null)return -1;if(typeof x==="string"&&isNaN(+x))return asc?x.localeCompare(y):y.localeCompare(x);return asc?x-y:y-x});
 const head="<tr>"+cols.map(([key,l,c])=>`<th class="${c||""}" data-k="${key}">${l}${key===sortKey?(asc?" ▲":" ▼"):""}</th>`).join("")+"</tr>";
 const body=rows.map(r=>{
  const mismatch=r.cfg!==""&&+r.cfg<r.lot;
  return "<tr>"+`<td><b>${r.sym}</b></td><td class=num>${r.lot}</td><td class="num ${mismatch?"flag":""}">${r.cfg||"–"}</td>`+
  r.days.map(d=>`<td class=num><span class="pill ${band(d.p)}">${fmt(d.p)}</span></td>`).join("")+
  `<td class=num><span class="pill ${band(r.p)}">${fmt(r.p)}</span></td><td class=num>${fmt(r.pfix)}</td><td class=num>${fmt(r.size,2)}</td><td class=num>${fmt(r.miss,1)}</td><td class=num>${fmt(r.stale,1)}</td><td class=num>${fmt(r.shown,0)}</td><td>${r.why}</td><td class=note>${r.note||""}</td></tr>`}).join("");
 const t=document.getElementById("names");t.innerHTML=head+body;
 t.querySelectorAll("th").forEach(th=>th.onclick=()=>{const key=th.dataset.k;if(key===sortKey)asc=!asc;else{sortKey=key;asc=key==="sym"||key==="why"||key==="p"||key.startsWith("d")}render()});
}
document.getElementById("q").oninput=render;document.getElementById("below").onchange=render;render();
</script></body></html>
"""
