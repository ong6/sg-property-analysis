#!/usr/bin/env python3
"""Local rankings UI — view arena results, click through to PropertyGuru & Maps.

Zero dependencies (stdlib http.server). Reads the latest tournament from
data/arena_results.csv and joins listing details from data/listings_db.json.

Usage:
    python ui.py                 # serve http://127.0.0.1:8642 and open browser
    python ui.py --port 9000     # custom port
    python ui.py --no-browser    # don't auto-open
"""

import argparse
import csv
import html
import json
import os
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ARENA_CSV = os.path.join(DATA_DIR, "arena_results.csv")
DB_FILE = os.path.join(DATA_DIR, "listings_db.json")
DEFAULT_PORT = 8642


def load_rankings() -> dict:
    """Latest arena run joined with listing details.

    Returns {"run_date": str|None, "rows": [dict]} — rows sorted by rank.
    """
    if not os.path.exists(ARENA_CSV):
        return {"run_date": None, "rows": []}

    with open(ARENA_CSV, newline="") as f:
        all_rows = list(csv.DictReader(f))
    if not all_rows:
        return {"run_date": None, "rows": []}

    # Tolerate legacy CSVs without a run_date column (None from DictReader)
    latest = max((r.get("run_date") or "" for r in all_rows), default="")
    if not latest:
        return {"run_date": None, "rows": []}
    rows = [r for r in all_rows if r.get("run_date") == latest]

    # Join listing details by URL
    details = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f:
                db = json.load(f)
            for rec in db.get("listings", {}).values():
                if rec.get("url"):
                    details[rec["url"]] = rec
        except (json.JSONDecodeError, OSError):
            pass

    out = []
    for r in rows:
        rec = details.get(r.get("url", ""), {})
        name = r.get("project_name") or rec.get("project_name") or rec.get("title") or "?"
        maps_query = urllib.parse.quote(f"{name} condo Singapore")
        out.append({
            "bracket": r.get("bracket") or "open",
            "rank": int(r["rank"]),
            "project_name": name,
            "beds": r.get("beds") or rec.get("beds") or "",
            "elo": int(float(r["elo"])) if r.get("elo") else None,
            "record": f"{r.get('wins', '?')}-{r.get('losses', '?')}-{r.get('draws', '?')}",
            "win_rate_pct": int(float(r["win_rate_pct"])) if r.get("win_rate_pct") else 0,
            "on_frontier": r.get("on_frontier") == "1",
            "mmr": float(r["mmr"]) if r.get("mmr") else None,
            "score_1000": int(float(r["score_1000"])) if r.get("score_1000") else None,
            "price": int(float(r["price"])) if r.get("price") else None,
            "psf": float(r["psf"]) if r.get("psf") else None,
            "district": r.get("district") or rec.get("district") or "",
            "agent_rating": r.get("agent_rating") or "",
            "agent_eval_date": r.get("agent_eval_date") or "",
            "sqft": rec.get("sqft"),
            "built_year": rec.get("built_year"),
            "tenure": rec.get("tenure") or "",
            "mrt_info": rec.get("mrt_info") or "",
            "url": r.get("url") or "",
            "maps_url": f"https://www.google.com/maps/search/?api=1&query={maps_query}",
        })

    out.sort(key=lambda x: (x["bracket"], x["rank"]))
    return {"run_date": latest, "rows": out}


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Condo Arena Rankings</title>
<style>
  :root {{ --bg:#0f1419; --panel:#1a2129; --line:#2b3543; --text:#dce3ea;
           --dim:#8b98a5; --gold:#e3b341; --green:#3fb950; --red:#f85149; --blue:#58a6ff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font:14px/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }}
  header {{ padding:18px 24px 10px; }}
  h1 {{ margin:0; font-size:20px; }}
  .sub {{ color:var(--dim); font-size:12.5px; margin-top:4px; }}
  .controls {{ display:flex; gap:10px; flex-wrap:wrap; padding:10px 24px 14px; }}
  .controls input, .controls select {{
    background:var(--panel); color:var(--text); border:1px solid var(--line);
    border-radius:6px; padding:6px 10px; font-size:13px; }}
  table {{ border-collapse:collapse; width:100%; }}
  thead th {{ position:sticky; top:0; background:var(--panel); color:var(--dim);
             text-align:left; font-size:11.5px; text-transform:uppercase; letter-spacing:.04em;
             padding:8px 10px; border-bottom:1px solid var(--line); cursor:pointer;
             user-select:none; white-space:nowrap; }}
  thead th:hover {{ color:var(--text); }}
  tbody td {{ padding:7px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  tbody tr:hover {{ background:#202a35; }}
  .name {{ font-weight:600; }}
  .star {{ color:var(--gold); }}
  .chip {{ display:inline-block; min-width:42px; text-align:center; padding:2px 7px;
          border-radius:10px; font-weight:600; font-size:12.5px; }}
  .links a {{ color:var(--blue); text-decoration:none; margin-right:10px; }}
  .links a:hover {{ text-decoration:underline; }}
  .dim {{ color:var(--dim); }}
  .empty {{ padding:60px 24px; text-align:center; color:var(--dim); }}
  footer {{ padding:14px 24px 26px; color:var(--dim); font-size:12px; }}
</style>
</head>
<body>
<header>
  <h1>🥊 Condo Arena Rankings</h1>
  <div class="sub">Run {run_date} · {count} ranked entries across brackets ·
    ⭐ = Pareto frontier · brackets are like-for-like (2BR fights 2BR) ·
    technical ranking only — referee verdict &amp; full agent evaluation live in
    <code>output/arena_latest.md</code></div>
</header>
<div class="controls">
  <select id="bracket" onchange="render()" title="Weight class — like-for-like fights">{bracket_opts}</select>
  <input id="q" placeholder="Search condo…" oninput="render()">
  <select id="district" onchange="render()"><option value="">All districts</option>{district_opts}</select>
  <input id="maxprice" type="number" placeholder="Max price $" oninput="render()">
  <select id="frontier" onchange="render()">
    <option value="">All contenders</option>
    <option value="1">Pareto frontier only</option>
  </select>
</div>
{body}
<footer>Data: data/arena_results.csv + data/listings_db.json · serve with <code>python ui.py</code></footer>
<script>
const DATA = {data_json};
let sortKey = "rank", sortAsc = true;
const fmtPrice = p => p ? "$" + (p/1e6).toFixed(2) + "M" : "-";
const fmtPsf = p => p ? "$" + Math.round(p).toLocaleString() : "-";
const scoreColor = s => s >= 650 ? "background:#1c3326;color:#3fb950" :
                        s >= 450 ? "background:#332d1c;color:#e3b341" :
                                   "background:#33201c;color:#f85149";
function render() {{
  const br = document.getElementById("bracket").value;
  const q = document.getElementById("q").value.toLowerCase();
  const d = document.getElementById("district").value;
  const mp = parseFloat(document.getElementById("maxprice").value);
  const fo = document.getElementById("frontier").value;
  let rows = DATA.filter(r =>
    r.bracket === br &&
    (!q || r.project_name.toLowerCase().includes(q)) &&
    (!d || r.district === d) &&
    (!mp || (r.price && r.price <= mp)) &&
    (!fo || r.on_frontier));
  rows.sort((a, b2) => {{
    let x = a[sortKey], y = b2[sortKey];
    if (x == null) return 1; if (y == null) return -1;
    if (typeof x === "string") {{ x = x.toLowerCase(); y = String(y).toLowerCase(); }}
    return (x < y ? -1 : x > y ? 1 : 0) * (sortAsc ? 1 : -1);
  }});
  document.getElementById("rows").innerHTML = rows.map(r => `
    <tr>
      <td class="dim">${{r.rank}}</td>
      <td class="name">${{r.on_frontier ? '<span class="star">⭐</span> ' : ''}}${{esc(r.project_name)}}</td>
      <td>${{r.beds ? r.beds + "BR" : "?"}}</td>
      <td>${{r.elo ?? "-"}}</td>
      <td class="dim">${{r.record}} (${{r.win_rate_pct}}%)</td>
      <td><span class="chip" style="${{scoreColor(r.score_1000 ?? 0)}}">${{r.score_1000 ?? "-"}}</span></td>
      <td>${{fmtPrice(r.price)}}</td>
      <td>${{fmtPsf(r.psf)}}</td>
      <td>${{esc(r.district)}}</td>
      <td>${{agentBadge(r.agent_rating, r.agent_eval_date)}}</td>
      <td class="dim">${{r.built_year ?? "-"}}</td>
      <td class="dim">${{esc(r.mrt_info || "-")}}</td>
      <td class="links">
        ${{r.url ? `<a href="${{esc(r.url)}}" target="_blank" rel="noopener">Listing ↗</a>` : ""}}
        <a href="${{esc(r.maps_url)}}" target="_blank" rel="noopener">Maps 📍</a>
      </td>
    </tr>`).join("");
  document.getElementById("shown").textContent = rows.length;
}}
function esc(s) {{ const d = document.createElement("div"); d.textContent = s ?? ""; return d.innerHTML; }}
function agentBadge(rating, date) {{
  if (!rating) return '<span class="dim">-</span>';
  const r = rating.toLowerCase();
  const color = r.includes("buy") ? "background:#1c3326;color:#3fb950"
              : r.includes("avoid") ? "background:#33201c;color:#f85149"
              : "background:#332d1c;color:#e3b341";
  return `<span class="chip" style="${{color}}" title="AI evaluation ${{esc(date)}}">${{esc(rating)}}</span>`;
}}
function sortBy(k) {{
  if (sortKey === k) sortAsc = !sortAsc; else {{ sortKey = k; sortAsc = (k === "rank" || k === "project_name"); }}
  render();
}}
render();
</script>
</body>
</html>"""

_TABLE = """<div style="padding:0 24px 4px" class="dim"><span id="shown">0</span> shown</div>
<table>
  <thead><tr>
    <th onclick="sortBy('rank')">#</th>
    <th onclick="sortBy('project_name')">Condo</th>
    <th onclick="sortBy('beds')">Type</th>
    <th onclick="sortBy('elo')">Elo</th>
    <th onclick="sortBy('win_rate_pct')">Record</th>
    <th onclick="sortBy('score_1000')">MMR/1000</th>
    <th onclick="sortBy('price')">Price</th>
    <th onclick="sortBy('psf')">PSF</th>
    <th onclick="sortBy('district')">District</th>
    <th onclick="sortBy('agent_rating')">Agent eval</th>
    <th onclick="sortBy('built_year')">Built</th>
    <th>MRT</th>
    <th>Links</th>
  </tr></thead>
  <tbody id="rows"></tbody>
</table>"""

_EMPTY = """<div class="empty">No arena results yet.<br>
Run <code>python invest.py --fight</code> first, then refresh.</div>"""


def render_index(rankings: dict) -> str:
    rows = rankings["rows"]
    districts = sorted({r["district"] for r in rows if r["district"]})
    district_opts = "".join(f'<option value="{html.escape(d)}">{html.escape(d)}</option>' for d in districts)

    # Bracket selector: like-for-like weight classes first, open division last.
    # Default to 2BR (the most common purchase bracket) when present.
    order = {"1BR": 1, "2BR": 2, "3BR": 3, "4BR+": 4, "open": 9}
    brackets = sorted({r.get("bracket") or "open" for r in rows}, key=lambda b: order.get(b, 5))
    default_bracket = "2BR" if "2BR" in brackets else (brackets[0] if brackets else "open")
    bracket_opts = "".join(
        f'<option value="{html.escape(b)}"{" selected" if b == default_bracket else ""}>'
        f'{html.escape(b + (" division (all types)" if b == "open" else " bracket"))}</option>'
        for b in brackets
    )
    return _PAGE.format(
        run_date=html.escape(rankings["run_date"] or "—"),
        count=len(rows),
        district_opts=district_opts,
        bracket_opts=bracket_opts,
        body=_TABLE if rows else _EMPTY,
        # "</" must be escaped when embedding JSON in a <script> block — a
        # scraped name containing "</script>" would otherwise close the tag
        # and inject markup (standard inline-JSON hardening).
        data_json=json.dumps(rows).replace("</", "<\\/"),
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            body = render_index(load_rankings()).encode()
            self._send(200, "text/html; charset=utf-8", body)
        elif path == "/api/rankings":
            body = json.dumps(load_rankings()).encode()
            self._send(200, "application/json", body)
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # quiet
        pass


def main():
    parser = argparse.ArgumentParser(description="Local condo arena rankings UI")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Condo arena UI: {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
