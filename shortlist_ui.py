#!/usr/bin/env python3
"""Shortlist UI — the researched buy-list, one page, on 127.0.0.1:8644.

    python shortlist_ui.py            # then open http://127.0.0.1:8644

A viewer, not a pipeline: it reads what the scans already produced (eval memory
+ realsmart cache + listings DB via shortlist.collect) and renders it. Nothing
here scrapes, scores or spends an agent run, so it is safe to leave open and
refresh.

The ordering is the opinion. Rows sort by evidence of EXIT — the verdict first,
then the share of a project's resales that sold at a profit — with the algo
grade shown but ranked last. Across this batch score_1000 correlates about -0.7
with % profitable, so presenting the algo order as the buy order would invert
the thing the owner actually cares about. The columns sit side by side
deliberately: seeing 855 next to "71.5% of 819" is the point.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import realsmart
import shortlist

DEFAULT_PORT = 8644
DEFAULT_SINCE = "2026-07-28"

# Below this many resales a profit percentage is decoration, not evidence.
THIN = shortlist.MIN_RESALES_FOR_TRUST

_VERDICT_CLASS = {"strong buy": "buy", "buy": "buy",
                  "neutral": "neutral", "avoid": "avoid"}


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def _profit_cell(r: dict) -> str:
    pct, n = r.get("pct_profitable"), r.get("resale_txns")
    if pct is None:
        return '<span class="dim">no data</span>'
    thin = (n or 0) < THIN
    # Meter is anchored at 60-100%, not 0-100: every project sits in that band,
    # so a full-range bar would render them all as near-identical full bars and
    # hide the only difference that matters.
    fill = max(0.0, min(1.0, (pct - 60) / 40)) * 100
    cls = "good" if pct >= 95 else ("mid" if pct >= 85 else "bad")
    lost = f"{round((n or 0) * (100 - pct) / 100):,} lost money" if n else ""
    return (f'<div class="meter"><i class="{cls}" style="width:{fill:.0f}%"></i></div>'
            f'<div class="pct">{pct}%<span class="dim"> of {n or "?"}'
            f'{" ⚠thin" if thin else ""}</span></div>'
            f'<div class="dim sub">{_e(lost)}</div>')


def _row_html(r: dict, i: int) -> str:
    v = (r.get("rating") or "?").strip()
    cls = _VERDICT_CLASS.get(v.lower(), "neutral")
    rs_url = realsmart.url_for(r.get("condo") or "")[0]
    flags = r.get("red_flags") or []
    detail = ""
    if r.get("summary") or r.get("rationale") or flags:
        detail = (
            f'<tr class="detail" id="d{i}"><td colspan="9"><div class="dbox">'
            + (f'<p class="sum">{_e(r["summary"])}</p>' if r.get("summary") else "")
            + (f'<p><b>Why:</b> {_e(r["rationale"])}</p>' if r.get("rationale") else "")
            + ("<p><b>Red flags:</b></p><ul>"
               + "".join(f"<li>{_e(f)}</li>" for f in flags) + "</ul>" if flags else "")
            + "</div></td></tr>")

    return (
        f'<tr class="r" onclick="tog({i})">'
        f'<td><span class="verdict {cls}">{_e(v)}</span>'
        f'<div class="dim sub">{_e(r.get("confidence") or "")}</div></td>'
        f'<td><span class="tag {"his" if r.get("mandate")=="his" else "hers"}">'
        f'{_e((r.get("mandate") or "—").upper())}</span></td>'
        f'<td class="name">{_e(r.get("condo"))}'
        f'<div class="dim sub">{_e(r.get("district") or "")} · '
        f'{_e(r.get("beds") or "?")}BR '
        f'{int(r["sqft"]) if r.get("sqft") else "?"} sqft</div></td>'
        f'<td class="num">{_money(r.get("price"))}</td>'
        f'<td class="num algo">{_e(r.get("score_1000") or "—")}</td>'
        f'<td class="num">{_e(r.get("realscore") or "—")}</td>'
        f'<td class="prof">{_profit_cell(r)}</td>'
        f'<td class="links" onclick="event.stopPropagation()">'
        + (f'<a href="{_e(r.get("url"))}" target="_blank" rel="noopener">listing ↗</a>'
           if r.get("url") else '<span class="dim">—</span>')
        + f'<a href="{_e(rs_url)}" target="_blank" rel="noopener">realsmart ↗</a>'
        f'</td>'
        f'<td class="dim chev">▾</td></tr>' + detail)


def render(rows: list[dict], since: str) -> str:
    rows = [r for r in rows if r.get("mandate")]
    rows.sort(key=shortlist.rank_key)
    n_buy = sum(1 for r in rows if (r.get("rating") or "").lower() in ("buy", "strong buy"))
    scored = [r for r in rows if r.get("pct_profitable") is not None
              and (r.get("resale_txns") or 0) >= THIN]
    clean = sum(1 for r in scored if r["pct_profitable"] >= 95)

    kpis = [
        (str(len(rows)), "researched", f"since {since}"),
        (str(n_buy), "rated Buy", "nothing manufactured"),
        (f"{clean}/{len(scored)}", "clean exit record", f"≥95% profitable, ≥{THIN} resales"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kv">{_e(a)}</div>'
        f'<div class="kl">{_e(b)}</div><div class="ks">{_e(c)}</div></div>'
        for a, b, c in kpis)

    body = "".join(_row_html(r, i) for i, r in enumerate(rows)) or \
        '<tr><td colspan="9" class="dim" style="padding:28px">Nothing evaluated yet.</td></tr>'

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Condo shortlist — Property Finder</title>
<style>
:root {{ --bg:#0f1419; --panel:#1a2129; --line:#2b3543; --text:#dce3ea;
        --dim:#8b98a5; --gold:#e3b341; --green:#3fb950; --red:#f85149; --blue:#58a6ff; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
       font:14px/1.45 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif; }}
header {{ padding:20px 24px 0; }}
h1 {{ margin:0 0 4px; font-size:20px; }}
.sub, .dim {{ color:var(--dim); }}
.sub {{ font-size:11.5px; }}
.note {{ margin:6px 0 0; color:var(--dim); font-size:12.5px; max-width:76ch; }}
.kpis {{ display:flex; gap:10px; flex-wrap:wrap; margin:14px 24px 0; }}
.kpi {{ background:var(--panel); border:1px solid var(--line); border-radius:8px;
       padding:10px 14px; min-width:150px; }}
.kv {{ font-size:21px; font-weight:600; }}
.kl {{ font-size:12.5px; }}
.ks {{ font-size:11px; color:var(--dim); margin-top:2px; }}
.wrap {{ margin:16px 24px 40px; overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; min-width:940px; }}
th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.06em;
     color:var(--dim); font-weight:600; padding:0 10px 8px; white-space:nowrap; }}
td {{ border-top:1px solid var(--line); padding:11px 10px; vertical-align:top; }}
tr.r {{ cursor:pointer; }}
tr.r:hover td {{ background:#151c24; }}
.name {{ font-weight:600; }}
.num {{ text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.algo {{ color:var(--dim); }}
.verdict {{ display:inline-block; padding:2px 9px; border-radius:99px;
           font-size:11.5px; font-weight:600; }}
.verdict.buy {{ background:rgba(63,185,80,.15); color:var(--green); }}
.verdict.neutral {{ background:rgba(227,179,65,.14); color:var(--gold); }}
.verdict.avoid {{ background:rgba(248,81,73,.14); color:var(--red); }}
.tag {{ font-size:10.5px; padding:2px 7px; border-radius:4px; border:1px solid var(--line);
       color:var(--dim); }}
.tag.his {{ border-color:#2f5d8a; color:var(--blue); }}
.tag.hers {{ border-color:#6b4a86; color:#c08fe8; }}
.prof {{ min-width:170px; }}
.meter {{ height:5px; background:#243040; border-radius:99px; overflow:hidden; }}
.meter i {{ display:block; height:100%; border-radius:99px; }}
.meter .good {{ background:var(--green); }}
.meter .mid {{ background:var(--gold); }}
.meter .bad {{ background:var(--red); }}
.pct {{ font-size:12.5px; margin-top:4px; font-variant-numeric:tabular-nums; }}
.links a {{ display:block; color:var(--blue); text-decoration:none; font-size:12.5px;
           white-space:nowrap; }}
.links a:hover {{ text-decoration:underline; }}
.chev {{ text-align:right; }}
tr.detail {{ display:none; }}
tr.detail.open {{ display:table-row; }}
.dbox {{ background:#141b23; border-left:2px solid var(--line); padding:12px 16px;
        margin:2px 0 6px; border-radius:0 6px 6px 0; max-width:100ch; }}
.dbox p {{ margin:0 0 8px; }}
.dbox .sum {{ color:var(--text); }}
.dbox ul {{ margin:4px 0 0 18px; padding:0; color:var(--dim); }}
.dbox li {{ margin-bottom:3px; }}
footer {{ margin:0 24px 40px; color:var(--dim); font-size:12px; }}
</style></head><body>
<header>
  <h1>Condo shortlist</h1>
  <div class="sub">researched verdicts · ranked by evidence of exit, not by algo score</div>
  <p class="note"><b>Read the two score columns together.</b> ALGO is the repo's
  MMR grade; PROFITABLE is the share of that project's resales that actually sold
  at a gain. In this batch they run <i>opposite</i> ways (r &asymp; &minus;0.7) &mdash; MMR
  rewards &ldquo;cheap versus district peers&rdquo;, and a project is often cheap precisely
  because the market has learned it underperforms. A percentage on fewer than
  {THIN} resales is marked thin and means little. Click any row for the reasoning.</p>
</header>
<div class="kpis">{kpi_html}</div>
<div class="wrap"><table>
<thead><tr><th>Verdict</th><th>For</th><th>Project</th><th class="num">Price</th>
<th class="num">Algo</th><th class="num">REALSCORE</th><th>Profitable (resales)</th>
<th>Links</th><th></th></tr></thead>
<tbody>{body}</tbody></table></div>
<footer>Viewer only &mdash; reads eval memory, the realsmart cache and the listings DB.
Refresh after a scan to pick up new verdicts.</footer>
<script>
function tog(i) {{
  var d = document.getElementById('d' + i);
  if (d) d.classList.toggle('open');
}}
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    since = DEFAULT_SINCE

    def do_GET(self):
        if self.path.startswith("/api/shortlist"):
            rows = shortlist.collect(self.since)
            rows.sort(key=shortlist.rank_key)
            payload = json.dumps(rows, ensure_ascii=False, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            page = render(shortlist.collect(self.since), self.since).encode()
        except Exception as e:  # noqa: BLE001 — a viewer must not 500 on bad data
            page = (f"<pre>shortlist failed: {html.escape(type(e).__name__)}: "
                    f"{html.escape(str(e))}</pre>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args):
        pass          # keep the console clean


def main() -> int:
    ap = argparse.ArgumentParser(description="Local shortlist viewer")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help="only show evaluations on/after this date")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    Handler.since = args.since
    url = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Shortlist UI: {url}  (evaluations since {args.since}; Ctrl-C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
