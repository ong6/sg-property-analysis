"""Multi-scorer lens registry — several independent ways to nominate a listing.

WHY THIS EXISTS. Until Jul 2026 exactly one number opened the AI research gate:
`score_1000 >= 650` (weekly.py). shortlist.py's own header documents what that
costs: across the researched batch score_1000 correlates about -0.7 with the
share of a project's resales that sold at a profit — MMR rewards "cheap versus
district peers", and a project is often cheap precisely because the market has
learned it underperforms. A condo with a superb exit record but a fair price
could therefore NEVER be researched: the only gate that existed was the one
lens that structurally dislikes it. That is a TOPOLOGY problem, not a weighting
problem — re-tuning MMR's components still leaves one consensus number deciding
everything. The fix is several independent LENSES, each able to nominate
candidates over its own bar; weekly.select_candidates ORs them.

Deliberately NOT rank aggregation. No Borda counts, no reciprocal-rank fusion,
no averaging lenses back into one composite: averaging is the same operation
that caused the problem — a 95th-percentile exit record on a 40th-percentile
MMR listing averages to mediocre and dies at a single gate, which is exactly
the outlier a second lens exists to catch.

A lens is (name, score fn, version fn, gate_percentile):
  * ``score(scored, record)`` takes the enriched ScoredListing plus its
    listings-DB record and returns {"score": float|None, "components": dict}.
    Missing inputs degrade to a None score (= "this lens cannot judge this
    listing"), never an exception that sinks the scoring pass.
  * ``version()`` stamps every stored lens score so vintages from different
    calibrations are never pooled — the same discipline config.score_version()
    enforces for MMR and calibrate_forward.py cohorts on.
  * ``gate_percentile`` is where the lens's nomination bar sits on the scored
    book (0.85 = "top 15%"). weekly.py computes the actual bar from the book —
    never from config.py (see VERSIONING below).

VERSIONING — read this before reaching for config.py. config.score_version()
sha-hashes config.py, and that fingerprint stamps every stored score: the
registered v3.12 cohort is 9,029 listings whose forward-calibration clock
first reads ~2027-08 (calibrate_forward.py, VERSION_ALIASES). Adding ANY
non-comment line to config.py — even one constant for a new lens — restamps
the whole book and resets that clock. So a lens's calibration lives in the
LENS'S OWN module, and module_fingerprint() hashes that module exactly the way
score_version() hashes config.py (full-line comments and blank lines stripped,
so prose fixes stay free). The "mmr" lens is the one exception: its
calibration genuinely lives in config.py, so its version IS
config.score_version().

STORAGE. Lens scores persist on the DB record under a nested ``lens_scores``
field, {lens: {score, rank_norm, version, scored_at}}, merged KEY-BY-KEY
(merge_lens_scores) so a pass that ran only some lenses never clobbers the
others — the same merge-only-present idiom as models.three_score_fields and
poller._score_keys. An absent lens key means "not scored by that lens yet".
The flat mmr / score_1000 / scored_at / score_version fields stay exactly as
they are — SHEET_COLUMNS, ui.py, weekly.py and shortlist.py all read them —
and lens_scores["mmr"] merely mirrors score_1000 for uniform gate reads.

HISTORY. Per-lens snapshots append to data/lens_history.csv (append_history),
which carries a ``lens`` column. Never data/mmr_history.csv: calibrate_forward
cohorts off that file, and mixing lens rows in would pollute the registered
cohorts it is waiting ~2027-08 to read.
"""

from __future__ import annotations

import csv
import hashlib
import os
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

import config
from scoring.mmr import compute_mmr

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LENS_HISTORY_CSV = os.path.join(_BASE, "data", "lens_history.csv")

# A lens that has scored fewer book listings than this cannot hold a
# percentile bar or a rank — a percentile over a handful of scores is noise,
# and a noisy bar would let a brand-new lens nominate almost anything.
MIN_BOOK_FOR_RANK = 20


def module_fingerprint(path: str, prefix: str) -> str:
    """`prefix` + a hash of the module file's calibration lines.

    Mirrors config.score_version(): full-line comments and blank lines are
    stripped before hashing, so documenting a constant never restamps a lens's
    stored scores — only a real calibration edit moves the digest. `prefix`
    plays CONFIG_VERSION's role: bump it on an intentional recalibration so
    the change is legible without diffing hashes. Lens authors pass their own
    ``__file__``.
    """
    try:
        with open(path) as f:
            calibration = "\n".join(
                ln for ln in f.read().splitlines()
                if ln.strip() and not ln.lstrip().startswith("#"))
        digest = hashlib.sha1(calibration.encode()).hexdigest()[:8]
    except (OSError, TypeError):
        digest = "unknown"
    return f"{prefix}+{digest}"


@dataclass(frozen=True)
class Lens:
    """One independent nominator. See the module docstring for the contract."""
    name: str
    score: Callable[[Any, Optional[dict]], dict]
    version: Callable[[], str]
    gate_percentile: float


def _mmr_lens(scored: Any, record: Optional[dict] = None) -> dict:
    """compute_mmr, unchanged, as a lens.

    A pure pass-through — the wrapper adds NOTHING (tests assert bit-identical
    output against compute_mmr), because the mmr lens must behave exactly like
    the pre-lens pipeline. Its headline score is score_1000, the scale the
    gate has always operated on (650 = recommended tier); the raw Elo-style
    value rides along as "mmr".
    """
    result = compute_mmr(scored)
    return {"score": result["score_1000"],
            "components": result["components"],
            "mmr": result["mmr"]}


# Registration order matters: it is the snake-draft order in weekly's budget
# allocator. Keep the incumbent first.
LENSES: dict[str, Lens] = {}


def register(lens: Lens) -> Lens:
    LENSES[lens.name] = lens
    return lens


# gate_percentile 0.85 records where the absolute 650 bar sat on the scored
# book when lenses landed (measured 2026-07-31: 84.6% of the 9,029 scored
# active listings are below 650). It is documentation-plus-symmetry: weekly.py
# keeps mmr's bar ABSOLUTE at MIN_SCORE_FOR_AI / --min-score, so the mmr-only
# gate stays bit-identical to the pre-lens one; only lenses without a tier
# tradition get percentile-derived bars.
register(Lens(name="mmr", score=_mmr_lens,
              version=config.score_version, gate_percentile=0.85))


# --------------------------------------------------------------------------- #
# Scoring + persistence helpers (shared by poller._score_keys and
# invest --score-db so the two write identical shapes)
# --------------------------------------------------------------------------- #
def score_record(scored: Any, record: Optional[dict] = None,
                 today: Optional[str] = None) -> dict[str, dict]:
    """Run every registered lens over one enriched ScoredListing.

    Returns {lens: {"score", "version", "scored_at"}} in storage shape —
    rank_norm is added by the caller once the book is known. A lens that
    returns no score (missing inputs) or raises is OMITTED: absent means "not
    scored by that lens yet", and because the DB merge is key-by-key an
    omitted lens never clobbers an earlier score.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    out: dict[str, dict] = {}
    for name, lens in LENSES.items():
        try:
            res = lens.score(scored, record)
        except Exception:  # noqa: BLE001 — one broken lens must not sink the pass
            continue
        if not isinstance(res, dict) or res.get("score") is None:
            continue
        out[name] = {"score": res["score"], "version": lens.version(),
                     "scored_at": today}
    return out


def lens_score_of(record: dict, name: str):
    """A DB record's stored score under one lens, or None.

    The mmr lens's canonical store is the FLAT score_1000 field — that is what
    SHEET_COLUMNS, ui.py, weekly.py and shortlist.py read, and it has full
    book coverage. Every other lens lives only under lens_scores.
    """
    if name == "mmr":
        return record.get("score_1000")
    entry = (record.get("lens_scores") or {}).get(name)
    return entry.get("score") if isinstance(entry, dict) else None


def book_scores(records) -> dict[str, list[float]]:
    """Per-lens sorted score books over the active scored DB.

    The book is what percentile bars and rank_norm are measured against; stale
    records are excluded for the same reason cohort stats exclude them — a
    withdrawn listing's score is not part of the live market.
    """
    books: dict[str, list[float]] = {name: [] for name in LENSES}
    for r in records:
        if r.get("status") == "stale":
            continue
        for name in LENSES:
            v = lens_score_of(r, name)
            if v is not None:
                books[name].append(v)
    for b in books.values():
        b.sort()
    return books


def rank_norm(score, sorted_book: list) -> Optional[float]:
    """Share of the scored book at or below `score` (0..1; 0.88 = top 12%).

    Stored beside each lens score so viewers (shortlist rows, the UI) can read
    "how high did this lens rank it" without re-loading the whole book. None
    when the book is thinner than MIN_BOOK_FOR_RANK — a rank over a handful of
    scores would be noise wearing a percentile's clothes.
    """
    if score is None or len(sorted_book) < MIN_BOOK_FOR_RANK:
        return None
    return round(bisect_right(sorted_book, score) / len(sorted_book), 4)


def merge_lens_scores(record: dict, new: Optional[dict]) -> None:
    """Merge lens entries into a DB record KEY-BY-KEY, never wholesale.

    Only lenses present in `new` are updated; a pass that did not run a lens
    leaves that lens's stored score untouched (the merge-only-present idiom of
    models.three_score_fields / poller._score_keys). Replacing the whole
    lens_scores dict here would silently erase every other lens's history of
    ever having scored the record.
    """
    if not new:
        return
    store = record.setdefault("lens_scores", {})
    for name, entry in new.items():
        store[name] = entry


def append_history(rows: list[dict], path: Optional[str] = None) -> None:
    """Append per-lens snapshots to data/lens_history.csv (its OWN file).

    NEVER write these into data/mmr_history.csv: calibrate_forward.py cohorts
    off that file's (score_version, month) key, and lens rows mixed in would
    pollute the registered cohorts whose first valid forward read is ~2027-08.
    The `lens` column is what keeps this file's cohorts separable per lens
    when a forward-calibration harness eventually reads it too.

    `rows` are dicts with: scored_at, lens, id, project_name, district, beds,
    price, psf, score, rank_norm, lens_version. Locked like mmr_history.csv —
    poller and invest append from different processes.
    """
    if not rows:
        return
    import listings_db  # late import: keep scoring importable without the DB layer

    path = path or LENS_HISTORY_CSV
    fields = ["scored_at", "lens", "id", "project_name", "district", "beds",
              "price", "psf", "score", "rank_norm", "lens_version"]
    with listings_db.file_lock(path + ".lock"):
        write_header = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(fields)
            for row in rows:
                writer.writerow([row.get(k) for k in fields])
