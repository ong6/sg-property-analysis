#!/usr/bin/env python3
"""Shortlist UI — the researched buy-list, one page, on 127.0.0.1:8644.

    python shortlist_ui.py            # then open http://127.0.0.1:8644

A viewer, not a pipeline: it reads what the scans already produced (eval memory
+ realsmart cache + listings DB via shortlist.collect) and renders it. Nothing
here scrapes, scores or spends an agent run, so it is safe to leave open and
refresh.

THE ONE THING THIS PAGE EXISTS TO SAY
-------------------------------------
Across this batch the algo grade (score_1000) and the share of a project's
resales that actually sold at a profit run OPPOSITE ways (r ~ -0.7). MMR rewards
"cheap versus district peers", and a project is often cheap precisely because the
market has learned it underperforms. So a high algo score sitting next to a poor
profitability record is a VALUE TRAP, and that pairing is what the buyer needs to
see without doing arithmetic.

Putting the two columns side by side (the first cut) still left the reading to
the human. So the pairing is now *computed* into one READ chip per row — TRAP /
HOLDS / WEAK / UNPROVEN — using the same rule that draws the quadrants in the
scatter, and the page opens with the two or three projects actually worth driving
to this weekend. Everything else here is in service of that shortlist.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import statistics
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import bed_bands
import realsmart
import shortlist

DEFAULT_PORT = 8644
DEFAULT_SINCE = "2026-07-28"

# Below this many resales a profit percentage is decoration, not evidence.
THIN = shortlist.MIN_RESALES_FOR_TRUST

# Record bands. 95% is the "clean exit book" line the shortlist already used;
# under 90% roughly one owner in ten took a loss, which no algo score buys back.
CLEAN_PCT = 95.0
MIXED_PCT = 90.0

# The buyer's underwriting window (5-7yr hold). A project whose owners
# historically needed materially longer to get out is a liquidity warning.
HOLD_YEARS_PLAN = 7.0

_VERDICT_CLASS = {"strong buy": "buy", "buy": "buy",
                  "neutral": "neutral", "avoid": "avoid"}

_READ_LABEL = {"trap": "VALUE TRAP", "holds": "RECORD HOLDS",
               "weak": "WEAK RECORD", "unproven": "UNPROVEN"}


# ---------------------------------------------------------------- derivations

def record_band(r: dict) -> str:
    """clean | mixed | poor | thin | none — what the exit record is worth.

    `thin` and `none` are deliberately NOT folded into `poor`: "we don't know"
    and "we know it's bad" lead to different next actions.
    """
    pct = r.get("pct_profitable")
    n = r.get("resale_txns") or 0
    if pct is None:
        return "none"
    if n < THIN:
        return "thin"
    if pct >= CLEAN_PCT:
        return "clean"
    if pct >= MIXED_PCT:
        return "mixed"
    return "poor"


def algo_cutoff(rows: list[dict]) -> float:
    """The line between "the algo likes it" and "it doesn't".

    Deliberately the batch median, not a fixed number: every row here already
    clears the repo's own 650 "recommended tier", so an absolute threshold would
    call all fourteen algo favourites and split nothing. The median is printed on
    the page so the split is never mysterious.
    """
    scores = [r["score_1000"] for r in rows if r.get("score_1000")]
    return float(statistics.median(scores)) if scores else 0.0


def classify(r: dict, cut: float) -> str:
    """trap | holds | weak | unproven — the algo/record pairing, decided.

    The whole point of the page: `trap` is a project the algo puts in the top
    half of the batch while its owners' actual exits say otherwise.
    """
    band = record_band(r)
    if band == "clean":
        return "holds"
    if band in ("poor", "mixed"):
        return "trap" if (r.get("score_1000") or 0) >= cut else "weak"
    return "unproven"


def read_blurb(r: dict, cut: float) -> str:
    """One plain sentence explaining a READ chip — used as its tooltip."""
    kind = classify(r, cut)
    pct, n, algo = r.get("pct_profitable"), r.get("resale_txns"), r.get("score_1000")
    if kind == "trap":
        return (f"Value trap: algo {algo} is in the top half of this batch "
                f"(median {cut:.0f}) but only {_pct(pct)}% of {n} resales sold at a "
                f"profit. Cheap versus peers because the market has learned it "
                f"underperforms.")
    if kind == "holds":
        return (f"The exit record holds up: {_pct(pct)}% of {n} resales sold at a "
                f"profit. Algo {algo} — worth a viewing whatever the algo says.")
    if kind == "weak":
        return (f"Weak record: only {_pct(pct)}% of {n} resales sold at a profit, and "
                f"algo {algo} is below this batch's median too — nothing here "
                f"disagrees.")
    if pct is not None:
        return (f"Unproven: {_pct(pct)}% looks clean but rests on only {n} resales; "
                f"under {THIN} the number is noise.")
    return "Unproven: no resale profitability record for this project at all."


def bed_check(r: dict) -> dict:
    """Per-project bedroom sanity, guarded — a viewer must not die on bad data."""
    try:
        return bed_bands.check(r.get("condo"), r.get("beds"), r.get("sqft")) or {}
    except Exception:  # noqa: BLE001
        return {}


def divergence(rows: list[dict]) -> dict[str, int]:
    """rank-by-record minus rank-by-algo, per project.

    The scale-free version of the thesis: +11 means the algo's 1st pick is the
    record's 12th. Only computed over rows with a trustworthy record — ranking an
    unknown against a known would manufacture a number.
    """
    tr = [r for r in rows if record_band(r) in ("clean", "mixed", "poor")
          and r.get("score_1000")]
    by_algo = sorted(tr, key=lambda r: -(r.get("score_1000") or 0))
    by_rec = sorted(tr, key=lambda r: (-(r.get("pct_profitable") or 0),
                                       -(r.get("resale_txns") or 0)))
    ai = {r["condo"]: i for i, r in enumerate(by_algo)}
    ri = {r["condo"]: i for i, r in enumerate(by_rec)}
    return {r["condo"]: ri[r["condo"]] - ai[r["condo"]] for r in tr}


def pearson(xs, ys):
    """Correlation, or None when there is not enough spread to claim one."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx <= 0 or sy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy) ** 0.5


def visit_list(rows: list[dict], limit: int = 3) -> list[dict]:
    """The projects actually worth driving to — the page's reason to exist.

    Gate, in order: not an Avoid; a trustworthy CLEAN exit record; and the
    bedroom count survives the per-project band check (a "3BR" that measures as a
    2BR fails the buyer's screen outright, however good the project is).
    """
    out = [r for r in rows
           if (r.get("rating") or "").lower() != "avoid"
           and record_band(r) == "clean"
           and bed_check(r).get("verdict") != "mismatch"]
    out.sort(key=lambda r: (-(r.get("pct_profitable") or 0),
                            -(r.get("resale_txns") or 0)))
    return out[:limit]


def blocked_by_beds(rows: list[dict]) -> list[dict]:
    """Clean record, not an Avoid — but the bedroom count fails its own band."""
    return [r for r in rows
            if record_band(r) == "clean"
            and (r.get("rating") or "").lower() != "avoid"
            and bed_check(r).get("verdict") == "mismatch"]


MANDATE_LABEL = {"his": "HIS · 3BR ≤$1.8M", "hers": "HERS · 3-4BR ≤$2.5M"}


def mandate_coverage(rows: list[dict], picks: list[dict], cut: float) -> list[dict]:
    """Per buyer: how many researched, how many survive to a viewing, and why not.

    Two buyers share one search, so a combined shortlist can quietly leave one of
    them with nothing to do on Saturday. Stating it per mandate is the difference
    between "we have three to see" and "he has none".
    """
    out = []
    for m in ("his", "hers"):
        in_m = [r for r in rows if r.get("mandate") == m]
        if not in_m:
            continue
        got = [r for r in picks if r.get("mandate") == m]
        chosen = {id(r) for r in got}
        reasons = {}
        for r in in_m:
            if id(r) in chosen:
                continue
            if bed_check(r).get("verdict") == "mismatch" and record_band(r) == "clean":
                key = "bed-count mislabel"
            elif (r.get("rating") or "").lower() == "avoid":
                key = "rated Avoid"
            else:
                key = {"trap": "value trap", "weak": "weak record",
                       "unproven": "unproven"}.get(classify(r, cut), "screened out")
            reasons[key] = reasons.get(key, 0) + 1
        out.append({"mandate": m, "label": MANDATE_LABEL[m], "researched": len(in_m),
                    "picks": got, "reasons": reasons})
    return out


# --------------------------------------------------------------- html helpers

def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _money(v) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v:,.0f}"


def _pct(v) -> str:
    """100.0 -> '100', 98.6 -> '98.6' — trailing .0 is noise in a dense table."""
    return f"{v:g}" if isinstance(v, (int, float)) else "?"


def _psf(r: dict):
    p, s = r.get("price"), r.get("sqft")
    if isinstance(p, (int, float)) and isinstance(s, (int, float)) and s:
        return p / s
    return None


def _flag_html(f) -> str:
    """Bold the lead clause of a red flag so fourteen of them can be skimmed.

    Split only — never rewritten. Most flags are already written head-first
    ("realsmart: only 71.5% profitable — 235 of 819 exits sold at a LOSS"), so
    promoting the head turns a wall of prose into a list without touching words.
    """
    s = str(f).strip()
    head, rest = s, ""
    for sep in (" — ", " – ", " - ", ": "):
        h, found, t = s.partition(sep)
        if found and t and 8 <= len(h) <= 110:
            head, rest = h, t
            break
    if rest:
        return (f"<li><b>{_e(head)}</b> "
                f'<span class="dim">{_e(rest)}</span></li>')
    # No lead clause to promote. A short flag still reads as a headline; a long
    # one set entirely in bold would be a wall, which is what we came to fix.
    return (f"<li><b>{_e(s)}</b></li>" if len(s) <= 90 else f"<li>{_e(s)}</li>")


def _profit_cell(r: dict) -> str:
    """The exit record: rate, sample depth, and the loss count in bodies."""
    pct, n = r.get("pct_profitable"), r.get("resale_txns")
    if pct is None:
        return ('<span class="dim">no realsmart record</span>'
                '<div class="dim sub">nothing to trust or distrust</div>')
    band = record_band(r)
    # The meter is anchored at 60-100%, not 0-100: every project sits inside that
    # band, so a full-range bar renders them all as near-identical full bars and
    # hides the only difference that matters.
    fill = max(0.0, min(1.0, (pct - 60) / 40)) * 100
    cls = {"clean": "good", "mixed": "mid", "thin": "thin"}.get(band, "bad")
    lost = round((n or 0) * (100 - pct) / 100)
    depth = "deep" if (n or 0) >= 500 else ("ok" if (n or 0) >= THIN else "thin")
    return (f'<div class="meter"><i class="{cls}" style="width:{fill:.0f}%"></i></div>'
            f'<div class="pct">{_pct(pct)}%'
            f'<span class="dim"> of {n if n is not None else "?"} resales</span>'
            f'<span class="depth {depth}">{depth.upper()}</span></div>'
            f'<div class="dim sub">'
            + (f'{lost:,} owner{"s" if lost != 1 else ""} sold at a loss'
               if n else "sample size unknown")
            + "</div>")


def _bed_chip(r: dict) -> str:
    chk = bed_check(r)
    v, why = chk.get("verdict"), _e(chk.get("reason") or "")
    if v == "mismatch":
        return (f'<span class="chip bad" title="{why}">'
                f'⚠ really a {_e(chk.get("looks_like"))}BR</span>')
    if v == "oversize":
        return f'<span class="chip dimchip" title="{why}">oversize stack</span>'
    if v == "undersize":
        return f'<span class="chip warn" title="{why}">undersized</span>'
    return ""


def _facts(r: dict) -> str:
    """The numbers a viewing decision needs but a column can't afford."""
    bits = []
    psf = _psf(r)
    if psf:
        bits.append(("psf", f"${psf:,.0f}"))
    if r.get("realscore") is not None:
        bits.append(("REALSCORE", f'{r["realscore"]}/5'))
    if r.get("resale_txns"):
        bits.append(("resales measured", f'{r["resale_txns"]:,}'))
    if r.get("avg_holding_yrs"):
        bits.append(("avg holding", f'{r["avg_holding_yrs"]} yrs'))
    if r.get("evaluated_at"):
        bits.append(("evaluated", str(r["evaluated_at"])))
    out = "".join(f'<span class="fact"><b>{_e(v)}</b> {_e(k)}</span>' for k, v in bits)

    hold = r.get("avg_holding_yrs")
    if isinstance(hold, (int, float)) and hold > HOLD_YEARS_PLAN:
        out += (f'<span class="fact warnfact">owners needed <b>{hold} yrs</b> on '
                f"average to get out — the plan underwrites 5-7</span>")
    band = bed_check(r).get("band")
    if band:
        out += (f'<span class="fact">URA rental filings put this project\'s '
                f'{_e(r.get("beds"))}BR at <b>{band["lo"]}-{band["hi"]} sqft</b> '
                f'({band["contracts"]} contracts)</span>')
    return f'<div class="facts">{out}</div>' if out else ""


# -------------------------------------------------------------------- the row

def _row_html(r: dict, i: int, cut: float, gaps: dict) -> str:
    v = (r.get("rating") or "?").strip()
    vcls = _VERDICT_CLASS.get(v.lower(), "neutral")
    kind = classify(r, cut)
    rs_url = realsmart.url_for(r.get("condo") or "")[0]
    flags = list(r.get("red_flags") or [])
    band = record_band(r)
    mismatch = bed_check(r).get("verdict") == "mismatch"
    psf = _psf(r)

    detail = ""
    if r.get("summary") or r.get("rationale") or flags:
        head = "".join(_flag_html(f) for f in flags[:4])
        tail = "".join(_flag_html(f) for f in flags[4:])
        detail = (
            f'<tr class="detail" id="d{i}"><td colspan="10"><div class="dbox">'
            + _facts(r)
            + '<div class="dcols">'
            + (f'<div><h4>The read</h4><p class="sum">{_e(r["summary"])}</p></div>'
               if r.get("summary") else "")
            + (f'<div><h4>Why this rating</h4><p>{_e(r["rationale"])}</p></div>'
               if r.get("rationale") else "")
            + "</div>"
            + ((f'<h4>Red flags <span class="dim">({len(flags)})</span></h4>'
                f'<ul class="flags">{head}</ul>')
               + (f"<details><summary>{len(flags) - 4} more red flags</summary>"
                  f'<ul class="flags">{tail}</ul></details>' if tail else "")
               if flags else "")
            + "</div></td></tr>")

    links = (f'<a href="{_e(r.get("url"))}" target="_blank" rel="noopener">listing ↗</a>'
             if r.get("url") else '<span class="dim">no listing</span>')
    links += f'<a href="{_e(rs_url)}" target="_blank" rel="noopener">realsmart ↗</a>'

    algo_note = "top half" if (r.get("score_1000") or 0) >= cut else "bottom half"
    gap = gaps.get(r.get("condo"))
    pct = r.get("pct_profitable")

    return (
        f'<tr class="r read-{kind}" id="r{i}" data-i="{i}" data-rank="{i}" '
        f'data-mandate="{_e(r.get("mandate") or "")}" data-verdict="{vcls}" '
        f'data-read="{kind}" data-district="{_e(r.get("district") or "")}" '
        f'data-price="{r.get("price") or 0}" data-algo="{r.get("score_1000") or 0}" '
        f'data-pct="{pct if pct is not None else -1}" '
        f'data-txns="{r.get("resale_txns") or 0}" '
        f'data-gap="{gap if gap is not None else -99}" '
        f'data-thin="{1 if band in ("thin", "none") else 0}" '
        f'data-bedflag="{1 if mismatch else 0}" onclick="tog({i})">'
        f'<td class="idx">{i + 1}</td>'
        f'<td><span class="read {kind}" title="{_e(read_blurb(r, cut))}">'
        f"{_READ_LABEL[kind]}</span></td>"
        f'<td><span class="verdict {vcls}">{_e(v)}</span>'
        f'<div class="dim sub">{_e(r.get("confidence") or "?")} confidence</div></td>'
        f'<td><span class="tag {"his" if r.get("mandate") == "his" else "hers"}">'
        f'{_e((r.get("mandate") or "—").upper())}</span></td>'
        f'<td class="name">{_e(r.get("condo"))} {_bed_chip(r)}'
        f'<div class="dim sub">{_e(r.get("district") or "")} · '
        f'{_e(r.get("beds") or "?")}BR · '
        f'{int(r["sqft"]) if r.get("sqft") else "?"} sqft'
        + (f" · ${psf:,.0f} psf" if psf else "") + "</div></td>"
        f'<td class="num">{_money(r.get("price"))}</td>'
        f'<td class="num algo">{_e(r.get("score_1000") or "—")}'
        f'<div class="dim sub">{algo_note}</div></td>'
        f'<td class="prof">{_profit_cell(r)}</td>'
        f'<td class="links" onclick="event.stopPropagation()">{links}</td>'
        f'<td class="dim chev">▾</td></tr>' + detail)


# ---------------------------------------------------------------- the scatter

def scatter_svg(rows: list[dict], cut: float) -> str:
    """Algo score vs actual exit record, with the trap quadrant drawn.

    This chart earns its place because it is the only artefact that *proves* the
    page's claim instead of asserting it: the cloud slopes down-right, and the
    projects the algo ranks highest sit inside the red box. Dots are numbered to
    match the table's # column, which also dodges label-collision games.
    """
    pts = [(i, r) for i, r in enumerate(rows)
           if record_band(r) in ("clean", "mixed", "poor") and r.get("score_1000")]
    if len(pts) < 4:
        return ""

    W, H, L, R, T, B = 760, 340, 54, 18, 20, 44
    xs = [float(r["score_1000"]) for _, r in pts]
    ys = [float(r["pct_profitable"]) for _, r in pts]
    x0, x1 = min(xs) - 16, max(xs) + 16
    # Headroom above the highest point, not a round 100: the band captions
    # live in that strip and must never be read through a dot.
    y0, y1 = min(min(ys) - 4, 88.0), max(max(ys) + 5, 101.5)

    def px(v):
        return L + (v - x0) / (x1 - x0) * (W - L - R)

    def py(v):
        return T + (1 - (v - y0) / (y1 - y0)) * (H - T - B)

    g = [f'<svg viewBox="0 0 {W} {H}" class="scat" role="img" '
         f'aria-label="Algo score versus share of resales sold at a profit">']

    # Quadrant fills use the identical rule as classify(), so the chip and the
    # chart can never disagree: green = clean record (at any algo score),
    # red = algo in the batch's top half on a record that is not clean.
    g.append(f'<rect x="{L}" y="{py(y1):.1f}" width="{W - L - R}" '
             f'height="{py(CLEAN_PCT) - py(y1):.1f}" fill="rgba(63,185,80,.09)"/>')
    g.append(f'<rect x="{px(cut):.1f}" y="{py(CLEAN_PCT):.1f}" '
             f'width="{W - R - px(cut):.1f}" height="{py(y0) - py(CLEAN_PCT):.1f}" '
             f'fill="rgba(248,81,73,.10)"/>')

    for t in range(70, 101, 5):
        if y0 <= t <= y1:
            g.append(f'<line x1="{L}" y1="{py(t):.1f}" x2="{W - R}" '
                     f'y2="{py(t):.1f}" stroke="#2b3543" stroke-width="1"/>')
            g.append(f'<text x="{L - 8}" y="{py(t) + 4:.1f}" class="ax" '
                     f'text-anchor="end">{t}%</text>')
    t = int(x0 // 25 + 1) * 25
    while t <= x1:
        g.append(f'<text x="{px(t):.1f}" y="{H - B + 18}" class="ax" '
                 f'text-anchor="middle">{t}</text>')
        t += 25

    g.append(f'<line x1="{px(cut):.1f}" y1="{py(y1):.1f}" x2="{px(cut):.1f}" '
             f'y2="{py(y0):.1f}" stroke="#8b98a5" stroke-dasharray="4 4"/>')
    g.append(f'<line x1="{L}" y1="{py(CLEAN_PCT):.1f}" x2="{W - R}" '
             f'y2="{py(CLEAN_PCT):.1f}" stroke="#3fb950" stroke-dasharray="4 4"/>')

    g.append(f'<text x="{L + 8}" y="{py(y1) + 15:.1f}" class="qlab good">'
             f"RECORD HOLDS &#8212; go and look</text>")
    g.append(f'<text x="{W - R - 8}" y="{py(y0) - 10:.1f}" class="qlab bad" '
             f'text-anchor="end">VALUE TRAP &#8212; algo loves it, owners '
             f"didn&#8217;t</text>")
    g.append(f'<text x="{L + 8}" y="{py(y0) - 10:.1f}" class="qlab dimlab">'
             f"weak record, and no algo hype either</text>")
    g.append(f'<text x="{px(cut) + 5:.1f}" y="{py(y1) + 15:.1f}" class="qlab dimlab">'
             f"batch median algo {cut:.0f} &#8594;</text>")

    for i, r in pts:
        kind = classify(r, cut)
        cx, cy = px(float(r["score_1000"])), py(float(r["pct_profitable"]))
        g.append(
            f'<g id="p{i}" class="pt {kind}" onclick="focusRow({i})">'
            f'<title>{_e(r.get("condo"))} — algo {r.get("score_1000")}, '
            f'{_pct(r.get("pct_profitable"))}% of {r.get("resale_txns")} resales '
            f"profitable</title>"
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="10"/>'
            f'<text x="{cx:.1f}" y="{cy + 3.5:.1f}" text-anchor="middle" '
            f'class="pn">{i + 1}</text></g>')

    g.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 6}" class="ax" '
             f'text-anchor="middle">ALGO SCORE (MMR) &#8212; further right = the '
             f"algo likes it more</text>")
    g.append(f'<text transform="translate(14,{(T + H - B) / 2:.0f}) rotate(-90)" '
             f'class="ax" text-anchor="middle">% OF RESALES SOLD AT A PROFIT</text>')
    g.append("</svg>")
    return "".join(g)


# ------------------------------------------------------------------- the page

_CSS = """
:root { --bg:#0f1419; --panel:#1a2129; --line:#2b3543; --text:#dce3ea;
        --dim:#8b98a5; --gold:#e3b341; --green:#3fb950; --red:#f85149; --blue:#58a6ff; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:14px/1.45 -apple-system,"Segoe UI",Helvetica,Arial,sans-serif; }
a { color:var(--blue); }
header { padding:20px 24px 0; }
h1 { margin:0 0 4px; font-size:20px; letter-spacing:-.01em; }
h2 { margin:0 0 10px; font-size:12px; text-transform:uppercase; letter-spacing:.08em;
     color:var(--dim); font-weight:600; }
h4 { margin:14px 0 6px; font-size:11px; text-transform:uppercase; letter-spacing:.07em;
     color:var(--dim); font-weight:600; }
.sub, .dim { color:var(--dim); }
.sub { font-size:11.5px; }
.note { margin:6px 0 0; color:var(--dim); font-size:12.5px; max-width:92ch; }
.note b { color:var(--text); }

/* the call ---------------------------------------------------------------- */
.call { margin:16px 24px 0; background:var(--panel); border:1px solid var(--line);
        border-left:3px solid var(--gold); border-radius:8px; padding:14px 18px; }
.call.hasbuy { border-left-color:var(--green); }
.callh { font-size:17px; font-weight:600; margin:0 0 4px; }
.callp { margin:0; color:var(--dim); font-size:13px; max-width:96ch; }
.callp b { color:var(--text); }
.cards { display:flex; gap:10px; flex-wrap:wrap; }
.card { flex:1 1 260px; background:#141b23; border:1px solid var(--line);
        border-left:3px solid var(--green); border-radius:6px; padding:10px 12px;
        cursor:pointer; }
.card:hover { background:#18212b; }
.cardh { font-weight:600; display:flex; justify-content:space-between; gap:8px; }
.cardn { color:var(--dim); font-variant-numeric:tabular-nums; }
.cardm { font-size:12.5px; margin-top:4px; color:var(--green); }
.covs { margin:12px 0 0; display:flex; flex-direction:column; gap:5px; }
.cov { font-size:12px; color:var(--dim); border-left:2px solid var(--line);
       padding:1px 0 1px 9px; }
.cov.has { border-left-color:var(--green); }
.cov.none { border-left-color:var(--red); }
.cov b { color:var(--text); }
.covgot { color:var(--green); }
.covnone { color:var(--red); font-weight:600; }
.blocked { margin:12px 0 0; font-size:12.5px; color:var(--dim); max-width:110ch;
           border-top:1px dashed var(--line); padding-top:10px; }
.blocked b { color:var(--red); }

/* kpis -------------------------------------------------------------------- */
.kpis { display:flex; gap:10px; flex-wrap:wrap; margin:14px 24px 0; }
.kpi { background:var(--panel); border:1px solid var(--line); border-radius:8px;
       padding:10px 14px; min-width:150px; }
.kv { font-size:21px; font-weight:600; font-variant-numeric:tabular-nums; }
.kl { font-size:12.5px; }
.ks { font-size:11px; color:var(--dim); margin-top:2px; }
.kpi.warn .kv { color:var(--red); }
.kpi.ok .kv { color:var(--green); }

/* scatter ----------------------------------------------------------------- */
.chartwrap { margin:16px 24px 0; background:var(--panel); border:1px solid var(--line);
             border-radius:8px; padding:14px 16px 10px; }
.scat { width:100%; height:auto; max-width:900px; display:block; }
.ax { fill:#8b98a5; font-size:10px; }
.qlab { font-size:10.5px; font-weight:600; letter-spacing:.05em;
        stroke:#0f1419; stroke-width:3px; paint-order:stroke fill; }
.qlab.good { fill:#3fb950; }
.qlab.bad { fill:#f85149; }
.qlab.dimlab { fill:#8b98a5; font-weight:400; }
.pt { cursor:pointer; }
.pt circle { stroke-width:1.5; }
.pt.trap circle { fill:rgba(248,81,73,.85); stroke:#f85149; }
.pt.holds circle { fill:rgba(63,185,80,.85); stroke:#3fb950; }
.pt.weak circle { fill:rgba(227,179,65,.75); stroke:#e3b341; }
.pt.unproven circle { fill:rgba(139,152,165,.5); stroke:#8b98a5; }
.pt .pn { fill:#0f1419; font-size:10px; font-weight:700; pointer-events:none; }
.pt:hover circle { stroke-width:3; }
.legend { display:flex; gap:16px; flex-wrap:wrap; font-size:11.5px; color:var(--dim);
          margin:8px 0 4px; }
.legend i { display:inline-block; width:9px; height:9px; border-radius:99px;
            margin-right:5px; }
.unplotted { font-size:11.5px; color:var(--dim); margin:2px 0 4px; max-width:110ch; }

/* filters ----------------------------------------------------------------- */
.bar { position:sticky; top:0; z-index:5; background:var(--bg);
       border-bottom:1px solid var(--line); margin:18px 0 0; padding:10px 24px;
       display:flex; gap:10px 14px; flex-wrap:wrap; align-items:center; }
.bar label { font-size:11px; color:var(--dim); display:flex; align-items:center;
             gap:5px; text-transform:uppercase; letter-spacing:.05em; }
.bar select { background:var(--panel); color:var(--text); border:1px solid var(--line);
              border-radius:5px; padding:4px 6px; font-size:12px; text-transform:none;
              letter-spacing:0; }
.bar button { background:var(--panel); color:var(--dim); border:1px solid var(--line);
              border-radius:5px; padding:5px 10px; font-size:12px; cursor:pointer; }
.bar button:hover { color:var(--text); }
#count { font-size:12px; color:var(--dim); margin-left:auto;
         font-variant-numeric:tabular-nums; }

/* table ------------------------------------------------------------------- */
.wrap { margin:0 24px 40px; overflow-x:auto; }
table { border-collapse:collapse; width:100%; min-width:1100px; }
th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.06em;
     color:var(--dim); font-weight:600; padding:10px 10px 8px; white-space:nowrap; }
td { border-top:1px solid var(--line); padding:11px 10px; vertical-align:top; }
tr.r { cursor:pointer; }
tr.r:hover td { background:#151c24; }
tr.r td:first-child { border-left:3px solid transparent; }
tr.r.read-trap td:first-child { border-left-color:var(--red); }
tr.r.read-holds td:first-child { border-left-color:var(--green); }
tr.r.read-weak td:first-child { border-left-color:var(--gold); }
tr.r.flash td { background:#1e2a36; }
.idx { color:var(--dim); font-variant-numeric:tabular-nums; font-size:12px;
       width:36px; padding-left:12px; }
.name { font-weight:600; }
.num { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }
.algo { color:var(--dim); }
.algo .sub { text-align:right; }
.read { display:inline-block; padding:3px 8px; border-radius:4px; font-size:10.5px;
        font-weight:700; letter-spacing:.04em; white-space:nowrap; cursor:help; }
.read.trap { background:rgba(248,81,73,.16); color:var(--red);
             box-shadow:inset 0 0 0 1px rgba(248,81,73,.45); }
.read.holds { background:rgba(63,185,80,.16); color:var(--green);
              box-shadow:inset 0 0 0 1px rgba(63,185,80,.45); }
.read.weak { background:rgba(227,179,65,.14); color:var(--gold); }
.read.unproven { background:#222b35; color:var(--dim); }
.verdict { display:inline-block; padding:2px 9px; border-radius:99px;
           font-size:11.5px; font-weight:600; }
.verdict.buy { background:rgba(63,185,80,.15); color:var(--green); }
.verdict.neutral { background:rgba(227,179,65,.14); color:var(--gold); }
.verdict.avoid { background:rgba(248,81,73,.14); color:var(--red); }
.tag { font-size:10.5px; padding:2px 7px; border-radius:4px; border:1px solid var(--line);
       color:var(--dim); }
.tag.his { border-color:#2f5d8a; color:var(--blue); }
.tag.hers { border-color:#6b4a86; color:#c08fe8; }
.chip { display:inline-block; font-size:10px; padding:1px 6px; border-radius:4px;
        margin-left:4px; font-weight:600; vertical-align:1px; cursor:help; }
.chip.bad { background:rgba(248,81,73,.16); color:var(--red); }
.chip.warn { background:rgba(227,179,65,.14); color:var(--gold); }
.chip.dimchip { background:#222b35; color:var(--dim); }
.prof { min-width:215px; }
.meter { height:5px; background:#243040; border-radius:99px; overflow:hidden;
         max-width:190px; }
.meter i { display:block; height:100%; border-radius:99px; }
.meter .good { background:var(--green); }
.meter .mid { background:var(--gold); }
.meter .bad { background:var(--red); }
.meter .thin { background:#4a5764; }
.pct { font-size:12.5px; margin-top:4px; font-variant-numeric:tabular-nums; }
.depth { font-size:9.5px; font-weight:700; letter-spacing:.05em; margin-left:6px;
         padding:1px 5px; border-radius:3px; background:#222b35; color:var(--dim); }
.depth.deep { background:rgba(63,185,80,.14); color:var(--green); }
.depth.thin { background:rgba(248,81,73,.16); color:var(--red); }
.links a { display:block; color:var(--blue); text-decoration:none; font-size:12.5px;
           white-space:nowrap; }
.links a:hover { text-decoration:underline; }
.chev { text-align:right; }

/* row detail -------------------------------------------------------------- */
tr.detail { display:none; }
tr.detail.open { display:table-row; }
.dbox { background:#141b23; border-left:2px solid var(--line); padding:14px 18px 16px;
        margin:2px 0 8px; border-radius:0 6px 6px 0; }
.dbox p { margin:0; }
.dcols { display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr));
         gap:2px 30px; }
.dcols p { max-width:80ch; color:var(--dim); font-size:13px; line-height:1.6; }
.dcols .sum { color:var(--text); }
.facts { display:flex; flex-wrap:wrap; gap:6px; }
.fact { font-size:11px; color:var(--dim); background:#1a2129; border:1px solid var(--line);
        border-radius:4px; padding:3px 8px; }
.fact b { color:var(--text); font-variant-numeric:tabular-nums; }
.warnfact { border-color:rgba(227,179,65,.4); color:var(--gold); }
.warnfact b { color:var(--gold); }
ul.flags { margin:4px 0 0; padding:0; list-style:none; }
ul.flags li { font-size:12.5px; line-height:1.55; padding:5px 0 5px 12px;
              max-width:114ch; border-left:2px solid #2b3543; margin-bottom:5px; }
ul.flags li b { color:var(--text); font-weight:600; }
details { margin-top:8px; }
details summary { cursor:pointer; color:var(--blue); font-size:12px; }
#noresults td { color:var(--dim); padding:28px 12px; }
footer { margin:0 24px 40px; color:var(--dim); font-size:12px; max-width:100ch; }
"""

_JS = """
var ROWS = [];
function tog(i) {
  var d = document.getElementById('d' + i);
  if (d) d.classList.toggle('open');
}
function focusRow(i) {
  var tr = document.getElementById('r' + i);
  if (!tr) return;
  var d = document.getElementById('d' + i);
  if (d) d.classList.add('open');
  tr.classList.add('flash');
  tr.scrollIntoView({block: 'center'});
  setTimeout(function () { tr.classList.remove('flash'); }, 1600);
}
function val(id) { var e = document.getElementById(id); return e ? e.value : ''; }
function chk(id) { var e = document.getElementById(id); return !!(e && e.checked); }
function num(v, dflt) { var x = parseFloat(v); return isNaN(x) ? dflt : x; }
function apply() {
  var m = val('f-mandate'), v = val('f-verdict'), rd = val('f-read'),
      d = val('f-district'), mp = parseFloat(val('f-price')) || 0,
      hideBed = chk('f-bed'), hideThin = chk('f-thin'), n = 0;
  for (var i = 0; i < ROWS.length; i++) {
    var tr = ROWS[i], ds = tr.dataset;
    var ok = (!m || ds.mandate === m)
          && (!v || ds.verdict === v)
          && (!rd || ds.read === rd)
          && (!d || ds.district === d)
          && (!mp || num(ds.price, 0) <= mp)
          && (!hideBed || ds.bedflag !== '1')
          && (!hideThin || ds.thin !== '1');
    tr.style.display = ok ? '' : 'none';
    var det = document.getElementById('d' + ds.i);
    if (det && !ok) det.classList.remove('open');
    var pt = document.getElementById('p' + ds.i);
    if (pt) pt.setAttribute('opacity', ok ? '1' : '0.12');
    if (ok) n++;
  }
  var c = document.getElementById('count');
  if (c) c.textContent = n + ' of ' + ROWS.length + ' shown';
  var nr = document.getElementById('noresults');
  if (nr) nr.style.display = n ? 'none' : '';
}
/* Every sort is expressed as "smaller key first", so descending measures are
   negated. Missing values get a sentinel that parks them at the bottom rather
   than at the top, where they would look like winners. */
function skey(tr, k) {
  var d = tr.dataset;
  if (k === 'pct') return -num(d.pct, -1);
  if (k === 'algo') return -num(d.algo, 0);
  if (k === 'price') return num(d.price, 0) || 1e12;
  if (k === 'txns') return -num(d.txns, 0);
  if (k === 'gap') return -num(d.gap, -99);
  return num(d.rank, 0);
}
function sortRows() {
  var k = val('f-sort'), tb = document.getElementById('tb');
  if (!tb) return;
  var arr = ROWS.slice();
  arr.sort(function (a, b) { return skey(a, k) - skey(b, k); });
  for (var i = 0; i < arr.length; i++) {
    tb.appendChild(arr[i]);
    var det = document.getElementById('d' + arr[i].dataset.i);
    if (det) tb.appendChild(det);
  }
  var nr = document.getElementById('noresults');
  if (nr) tb.appendChild(nr);
}
function resetAll() {
  var ids = ['f-mandate', 'f-verdict', 'f-read', 'f-district', 'f-price'];
  for (var i = 0; i < ids.length; i++) {
    var e = document.getElementById(ids[i]);
    if (e) e.value = '';
  }
  var s = document.getElementById('f-sort');
  if (s) s.value = 'rank';
  var b = document.getElementById('f-bed'); if (b) b.checked = false;
  var t = document.getElementById('f-thin'); if (t) t.checked = false;
  sortRows();
  apply();
}
document.addEventListener('DOMContentLoaded', function () {
  ROWS = Array.prototype.slice.call(document.querySelectorAll('#tb tr.r'));
  var ctrl = document.querySelectorAll('.bar select, .bar input');
  for (var i = 0; i < ctrl.length; i++) {
    ctrl[i].addEventListener('change', function (e) {
      if (e.target.id === 'f-sort') { sortRows(); } else { apply(); }
    });
  }
  apply();
});
"""


def call_panel(rows: list[dict], cut: float) -> str:
    """The empty state done honestly — and the actual "go see these" list.

    0-of-14-rated-Buy is a real finding about the market, not a rendering bug, so
    it gets stated in words. Underneath it sits the only thing the owner asked
    for: which two or three to physically visit, chosen on the exit record.
    """
    n_buy = sum(1 for r in rows
                if (r.get("rating") or "").lower() in ("buy", "strong buy"))
    n_avoid = sum(1 for r in rows if (r.get("rating") or "").lower() == "avoid")
    n_neutral = len(rows) - n_buy - n_avoid
    picks, blocked = visit_list(rows), blocked_by_beds(rows)

    if not rows:
        # Distinct from "nothing clears the bar": there is nothing to clear it.
        return ('<div class="call"><div class="callh">Nothing researched in this '
                'window.</div><p class="callp">No evaluation lands on or after the '
                '<code>--since</code> date, so there is nothing to rank. Widen '
                '<code>--since</code>, or run a scan first — this page only ever '
                'reads what the scans already produced.</p></div>')

    if n_buy:
        headline = f"{n_buy} listing{'s' if n_buy != 1 else ''} clears the Buy bar."
        lede = (f"{n_buy} Buy · {n_neutral} Neutral · {n_avoid} Avoid across "
                f"{len(rows)} researched in-mandate listings.")
        cls = "call hasbuy"
    else:
        headline = "Nothing clears the Buy bar."
        lede = (f"None of the {len(rows)} in-mandate listings is rated Buy — {n_neutral} "
                f"Neutral, {n_avoid} Avoid. <b>That is the finding, not a broken "
                f"page:</b> nothing in this batch is mispriced enough to be an "
                f"edge. Treat what follows as a viewing list, not a buy list, and "
                f"expect to negotiate rather than to pounce.")
        cls = "call"

    if picks:
        cards = []
        idx = {id(r): i for i, r in enumerate(rows)}
        for r in picks:
            i = idx[id(r)]
            psf = _psf(r)
            side = "above" if (r.get("score_1000") or 0) >= cut else "below"
            cards.append(
                f'<div class="card" onclick="focusRow({i})">'
                f'<div class="cardh"><span>{i + 1}. {_e(r.get("condo"))}</span>'
                f'<span class="cardn">{_money(r.get("price"))}</span></div>'
                f'<div class="sub">{_e((r.get("mandate") or "—").upper())} · '
                f'{_e(r.get("district") or "")} · {_e(r.get("beds") or "?")}BR · '
                f'{int(r["sqft"]) if r.get("sqft") else "?"} sqft'
                + (f" · ${psf:,.0f} psf" if psf else "") + "</div>"
                f'<div class="cardm">{_pct(r.get("pct_profitable"))}% of '
                f'{r.get("resale_txns")} resales sold at a profit</div>'
                f'<div class="sub">algo {r.get("score_1000")} — {side} this batch\'s '
                f"median, which is beside the point</div></div>")
        picks_html = ("<h4>If you are spending a Saturday — ranked on exit record, "
                      "not on algo score</h4>"
                      f'<div class="cards">{"".join(cards)}</div>')
    else:
        picks_html = ('<h4>Viewing shortlist</h4><p class="callp">Nothing here has '
                      "both a trustworthy clean exit record and a bedroom count that "
                      "survives its own project's size bands. Nothing is worth the "
                      "drive on this data.</p>")

    cov_rows = []
    for c in mandate_coverage(rows, picks, cut):
        why = ", ".join(f"{v} {k}" for k, v in sorted(c["reasons"].items(),
                                                      key=lambda kv: -kv[1]))
        got = len(c["picks"])
        names = ", ".join(_e(r.get("condo")) for r in c["picks"])
        cov_rows.append(
            f'<div class="cov {"has" if got else "none"}">'
            f'<b>{_e(c["label"])}</b> — '
            + (f'<span class="covgot">{got} to view: {names}</span>'
               if got else '<span class="covnone">nothing to view</span>')
            + f'<span class="dim"> · {c["researched"]} researched'
            + (f" ({why})" if why else "") + "</span></div>")
    cov_html = f'<div class="covs">{"".join(cov_rows)}</div>' if cov_rows else ""

    blocked_html = ""
    if blocked:
        items = "; ".join(
            f'<b>{_e(r.get("condo"))}</b> ({_pct(r.get("pct_profitable"))}% of '
            f'{r.get("resale_txns")}) — its "{_e(r.get("beds"))}BR" measures '
            f'{int(r["sqft"]) if r.get("sqft") else "?"} sqft, which URA rental '
            f"filings put in this project's "
            f'{_e(bed_check(r).get("looks_like"))}BR band'
            for r in blocked)
        blocked_html = (f'<p class="blocked">Kept off the shortlist despite a clean '
                        f"record: {items}. A mislabelled bedroom count fails the "
                        f"screen outright — it is the wrong product to resell into "
                        f"the family-buyer bid that is the whole exit plan.</p>")

    return (f'<div class="{cls}"><div class="callh">{headline}</div>'
            f'<p class="callp">{lede}</p>{picks_html}{cov_html}{blocked_html}</div>')


def render(rows: list[dict], since: str) -> str:
    rows = [r for r in rows if r.get("mandate")]
    rows.sort(key=shortlist.rank_key)
    cut = algo_cutoff(rows)
    gaps = divergence(rows)

    trusted = [r for r in rows if record_band(r) in ("clean", "mixed", "poor")]
    clean = sum(1 for r in trusted if record_band(r) == "clean")
    traps = [r for r in rows if classify(r, cut) == "trap"]
    corr_rows = [r for r in trusted if r.get("score_1000")]
    r_corr = pearson([float(r["score_1000"]) for r in corr_rows],
                     [float(r["pct_profitable"]) for r in corr_rows])
    n_buy = sum(1 for r in rows
                if (r.get("rating") or "").lower() in ("buy", "strong buy"))

    kpis = [
        ("", str(len(rows)), "researched, in mandate", f"evaluated since {since}"),
        ("ok" if n_buy else "warn", str(n_buy), "rated Buy",
         "nothing manufactured to fill the list"),
        ("ok" if clean else "", f"{clean}/{len(trusted)}", "clean exit record",
         f"≥{CLEAN_PCT:.0f}% profitable on ≥{THIN} resales"),
        ("warn" if traps else "", str(len(traps)), "value traps",
         "top-half algo, record says no"),
        ("", "—" if r_corr is None else f"{r_corr:+.2f}", "algo vs record (r)",
         "negative = the score points the wrong way"),
    ]
    kpi_html = "".join(
        f'<div class="kpi {c}"><div class="kv">{_e(a)}</div>'
        f'<div class="kl">{_e(b)}</div><div class="ks">{_e(d)}</div></div>'
        for c, a, b, d in kpis)

    body = "".join(_row_html(r, i, cut, gaps) for i, r in enumerate(rows))
    if not body:
        body = ('<tr><td colspan="10" class="dim" style="padding:28px">Nothing '
                "evaluated in this window. Run a scan, or widen --since.</td></tr>")
    body += ('<tr id="noresults" style="display:none"><td colspan="10">No listing '
             "matches those filters. Widen the price cap or clear the hide-boxes — "
             "an empty result here is the filter talking, not the market.</td></tr>")

    districts = sorted({r.get("district") for r in rows if r.get("district")})
    dist_opts = "".join(f'<option value="{_e(d)}">{_e(d)}</option>' for d in districts)

    chart_block = ""
    chart = scatter_svg(rows, cut)
    if chart:
        legend = "".join(
            f'<span><i style="background:{col}"></i>{lab}</span>'
            for col, lab in (("#f85149", "value trap"), ("#3fb950", "record holds"),
                             ("#e3b341", "weak record")))
        unplotted = [r for r in rows if record_band(r) in ("thin", "none")]
        un = ""
        if unplotted:
            pos = {id(r): i for i, r in enumerate(rows)}
            un = ('<div class="unplotted">Not plotted — no trustworthy resale record, '
                  "so there is no honest y-value: "
                  + ", ".join(f"#{pos[id(r)] + 1} {_e(r.get('condo'))}"
                              for r in unplotted) + ".</div>")
        chart_block = (
            f'<div class="chartwrap"><h2>Algo score vs what owners actually got out'
            f"</h2>{chart}"
            f'<div class="legend">{legend}<span>numbers match the # column · click a '
            f"dot to jump to its row</span></div>{un}</div>")

    r_txt = "" if r_corr is None else f"{r_corr:+.2f}"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>Condo shortlist — Property Finder</title>
<style>{_CSS}</style></head><body>
<header>
  <h1>Condo shortlist — which two do we go and look at?</h1>
  <div class="sub">researched verdicts · ranked by evidence of exit, not by algo score</div>
  <p class="note"><b>The algo and the record disagree, and the record wins.</b>
  ALGO is the repo's MMR grade; the EXIT RECORD is the share of that project's
  resales that actually sold at a gain. In this batch they run <i>opposite</i> ways
  (r &asymp; {r_txt}) &mdash; MMR rewards &ldquo;cheap versus district peers&rdquo;,
  and a project is often cheap precisely because the market has learned it
  underperforms. The <b>READ</b> column does that pairing for you:
  <span class="read trap">VALUE TRAP</span> means a top-half algo score sitting on a
  record that says no. A percentage measured on fewer than {THIN} resales is marked
  <b>THIN</b> and means nothing. Click any row for the full reasoning.</p>
</header>
{call_panel(rows, cut)}
<div class="kpis">{kpi_html}</div>
{chart_block}
<div class="bar">
  <label>for <select id="f-mandate"><option value="">both</option>
    <option value="his">HIS · 3BR &le;$1.8M</option>
    <option value="hers">HERS · 3-4BR &le;$2.5M</option></select></label>
  <label>verdict <select id="f-verdict"><option value="">any</option>
    <option value="buy">Buy</option><option value="neutral">Neutral</option>
    <option value="avoid">Avoid</option></select></label>
  <label>read <select id="f-read"><option value="">any</option>
    <option value="holds">record holds</option><option value="trap">value trap</option>
    <option value="weak">weak record</option>
    <option value="unproven">unproven</option></select></label>
  <label>max price <select id="f-price"><option value="">any</option>
    <option value="1200000">$1.2M</option><option value="1400000">$1.4M</option>
    <option value="1600000">$1.6M</option><option value="1800000">$1.8M</option>
    <option value="2100000">$2.1M</option><option value="2500000">$2.5M</option>
    </select></label>
  <label>district <select id="f-district"><option value="">any</option>
    {dist_opts}</select></label>
  <label>sort <select id="f-sort">
    <option value="rank">evidence of exit (default)</option>
    <option value="pct">% profitable</option>
    <option value="txns">deepest sample</option>
    <option value="gap">algo-vs-record divergence</option>
    <option value="algo">algo score</option>
    <option value="price">price, low first</option></select></label>
  <label><input type="checkbox" id="f-bed"> hide bed mislabels</label>
  <label><input type="checkbox" id="f-thin"> hide thin (&lt;{THIN} resales)</label>
  <button onclick="resetAll()">reset</button>
  <span id="count"></span>
</div>
<div class="wrap"><table>
<thead><tr><th>#</th><th>Read</th><th>Verdict</th><th>For</th><th>Project</th>
<th class="num">Price</th><th class="num">Algo</th><th>Exit record</th>
<th>Links</th><th></th></tr></thead>
<tbody id="tb">{body}</tbody></table></div>
<footer>Viewer only &mdash; reads eval memory, the realsmart cache and the listings DB;
it never scrapes, scores or spawns a run. Bedroom-count checks come from URA rental
contracts via <code>bed_bands</code>, which is why they are per project rather than a
global sqft table. Refresh after a scan to pick up new verdicts.
JSON at <a href="/api/shortlist">/api/shortlist</a>.</footer>
<script>{_JS}</script>
</body></html>"""


# ----------------------------------------------------------------------- serve

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
