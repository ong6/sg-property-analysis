# Full-System Audit — v3.9 (2026-06-12)

Six parallel deep audits (MMR math, benchmark/comps, backtest methodology,
yield/ROI, serving layer, ingestion). Findings verified empirically — traced
through the live scorer, measured against the 6,360-listing DB and the 28
district CSVs; statutory rates web-verified. This file is the prioritized fix
queue. History/context: `docs/RELEASES.md`.

## STATUS (2026-06-12, v3.10 fix wave — same day)

**FIXED** — every P0/P1 (#1–#14) and the listed P2s landed as v3.10 (see
`docs/RELEASES.md` v3.10 for the change list; 324 tests, DB rescored, norm
center recalibrated 1516→1508). One regression found and fixed in-wave: the
stale-window fix initially disarmed v3.8 PES protection for stale-cohort
projects (Coco Palms 624sf hit #1 at 901); stale same-size prints are now
**time-indexed** by a district price ratio and used as low-trust comps
(half blend weight, thin cohort, raised flag thresholds) → 901→784.

## STATUS (2026-06-14, v3.10b lever wave)

**FIXED in v3.10b** (all measurement-gated — see RELEASES.md v3.10b):
`future`→0, `cost`→halved, `dev_size` kept (re-measured significant), NEW
region term for data-rich listings (funded by `price_band` cap 15→11),
structural-floor gate corrected (fires on suspect, caps only unvalidated
future/cost, never the validated dev_size), vacancy-by-format priors, poll
detail enrichment wired, EC comp join implemented (graceful-when-absent),
repeat-sales outcome cross-check (conclusions robust). Norm center →1505.

**STILL OPEN (genuinely blocked, not deferrable-by-choice):**
- **Holdout validation** — waits for post-2026-06 data (~mid-2027); infra +
  registered-holdout protocol are in place.
- **EC/strata prints fetch** — the join is live but inert until the data is
  fetched: `python fetch_ura_districts.py --property-types ec` drives a
  non-headless browser with a manual-CAPTCHA step, so it must be **user-run**
  (can't be done autonomously). Strata-landed likewise.
- **~616 stale pre-prior evaluations** (133 stale Buys, the ones that can
  mislead) — being re-judged in batches; see eval-rejudge progress.
- Dashboard ANALYZE scoped `--allowedTools` — extend if a headless run stalls.

**Cross-cutting headline:** the v3.6–v3.9 trust layer works where it engages,
but (a) it silently disarms for ~15% of the DB via a name-join miss, (b) the
yield channel was never brought under it, (c) ingestion validates nothing —
every artifact class the scorer fights is still entering unflagged, and
(d) the headline backtest claim ("weights generalize") doesn't survive an
apples-to-apples re-test. None of this invalidates the cross-sectional value
signal; it does mean confidence language and several mechanics need fixing.

## P0 — fix before trusting affected outputs

1. **No temporal out-of-sample anywhere; "beats in-sample optimal +0.241,
   weights generalize" is a sample-composition artifact** — `backtest_ext.py:364-408`.
   Config scored all 1,460 pooled rows (incl. 392 partial); OLS fit on the
   1,068 complete-case rows. Same-rows comparison: config ρ **+0.225 < +0.241**.
   The 3 splits are 0.25yr apart with 2yr forward windows (~87.5% overlap;
   74% of 563 projects appear in all three) → effective n ≈ 563, not 1,460,
   and the weights were tuned on this same panel. ρ ≈ +0.29 stands only as an
   **in-sample, single-regime association** (project-cluster bootstrap CI
   [+0.22, +0.36]). *Fix: lock v3.9 weights now; treat post-2026-06 URA data
   as a registered holdout; ≥2yr split spacing for any in-panel claim.*

2. **`calibrate_forward.py` will be invalid at first use (mid-2027)** —
   `calibrate_forward.py:57,84-123`. The 2026-06 cohort blends scores from
   five config versions (same-listing drift median 178 pts, 93% tier-changing)
   and `mmr_history.csv` has **no config-version column**; the name join
   matches only 58% of keys. *Fix NOW (cheap): stamp `config_version` (config
   hash + norm center) on every scored row; alias-table the join; forbid
   baseline/outcome window overlap.* Same stamp fixes the UI score-vintage
   mixing (P1-12).

3. **Listings never expire** — `listings_db.py:214` sets `status="active"`;
   nothing ever writes `stale`, yet `search()`/UI/cohort stats all filter on
   it. All 6,360 records active; 6,307 seen exactly once. Sold/withdrawn units
   rank forever; bait asks can't age out; the price-drop signal is
   structurally dead (2 events ever; relists get fresh IDs + fresh
   `first_seen`, orphaning history — 18.5% of rows are same-unit duplicates
   that also distort cohort percentiles). *Fix: end-of-poll staleness sweep
   (≥2 consecutive misses in covered scope ⇒ stale) + `unit_key` relist
   linking that inherits price history.*

## P1 — likely bugs, verified

4. **Tight-comps name join misses 14.7% of the DB (932 listings), silently
   disarming v3.8/v3.9 exactly where they matter** — `full_scorer.py:127,1546`
   joins prints by raw exact-upper string while the benchmark uses fuzzy
   lookup. Traced: **Suites @ Katong** (the v3.6.2 artifact project) reads
   −23.6% "cheap" with zero print-contradiction protection ('SUITES @ KATONG'
   vs URA 'SUITES@ KATONG'). 106 listings recoverable with the `_pu_normalize`
   helper already in the file (verified zero collisions). Plus structural
   blind spots: **250 EC listings** (URA fetch excludes Executive Condominium)
   and strata-landed (12 cluster houses) have no prints at all — the v3.6
   strata-villa artifact is still open at ingest. *Fix: one shared normalizer
   for all URA joins; fetch the EC dataset; emit explicit `no_ura_prints` flag.*

5. **Tight-comp "recent" window anchors to the project's last print, not
   today** — `full_scorer.py:1556-1562`. 15% of projects (D05/D14/D15/D18
   sample) have latest print >24mo old; <2 in-window prints silently falls
   back to all-time. A fairly-priced 2026 ask vs 2021 prints reads +20% →
   fires `ask_above_own_stack_prints` + suspect damp — the exact stale-print
   failure v3.9's Archipelago lesson documented, reproduced in the
   implementation. *Fix: anchor at today; no comps (neutral) when stale;
   export print age.*

6. **The tight blend erases the floor normalization at n_tight ≥ 5** —
   `full_scorer.py:1604-1622`. Floor factor is applied to the band median,
   then `median_psf = w·tight_med + (1−w)·median_psf` with w=1 at n≥5 —
   tight comps are size-matched but floor-mixed. Traced: fairly-priced
   floor-15 unit reads +6.7% dear AND trips the >+5 suspect damp. Contradicts
   the v3.5 floor doctrine for exactly the listings with the best comp data.
   *Fix: floor-adjust each tight print by its own tier (prints carry Floor
   Level) or restrict to the listing's tier.*

7. **Rent-sqft cap (v3.6.2) bypassed by the `ura_project` branch** —
   `rental_estimator.py:122-125` caps only bed-matched sources. Live trace: a
   1,086sf "1BR" (PARC SEABREEZE) takes project-pooled psf × full strata sqft
   → fake $5,137/mo, yield +11.98 MMR at conf 0.8 — the artifact class
   v3.6.2 shipped to kill, resurrected at *higher* confidence on exactly the
   older projects where bed-matched contracts are rare. *Fix: serve the
   cache's bed-matched `monthly_rent` directly for `ura_project_bed` (it's
   already stored, fixes the genuine-large-unit distortion too) and cap every
   psf×sqft path whenever beds is known.*

8. **The yield channel ignores all suspect flags** — `mmr.py:301-306`. The
   v3.6 damp covers psf_value/age_value only; a flagged mis-scrape keeps up to
   ~+19 yield pts from the same untrusted price (this is how The Vision still
   scored 465). *Fix: one line — damp positive yield by
   `MMR_SUSPECT_VALUE_FACTOR` when suspect.*

9. **`cost` component penalizes missing data −5 (doctrine violation)** —
   `mmr.py:355` recentres a 0–10 score whose sub-scores award 0 for *absent*
   inputs; a bare listing gets −5 raw ≈ −43 display pts. The neutrality test
   doesn't cover cost. Related: missing district → buyer_pool default
   "moderate" = +1.5 (`full_scorer.py:1211`). *Fix: per-sub-score neutral
   centring; None → 0.*

10. **Fuzzy profile join attaches the wrong condo's profile** —
    `profile_memory.py:237-256` (SequenceMatcher ≥0.6). Verified: "Kovan
    Residences" → **Avant Residences** (0.88); 100+ DB projects would
    wrongly match it. Wrong `known_issues`/facings join into factual_data and
    `not_researched` is suppressed. *Fix: require district equality + ratio
    ≥~0.9 (or exact-normalized) in the automated path; keep loose fuzzy for
    the human CLI only.*

11. **ROI never subtracts entry costs from the return** — `roi.py:159-170`.
    BSD/ABSD/legal inflate the denominator but aren't deducted from
    `total_return` (exit costs are — the asymmetry proves it's a bug).
    Overstates 5yr ROI ~3.7pp (first property) to **~19pp** (SC 2nd property,
    20% ABSD). `tests/test_roi.py:130-142` enshrines the bug. *Fix: subtract
    `bsd+absd+legal_buy` from total_return; update tests.*

12. **DB lock is stealable while legitimately held; poller holds it through
    the whole scoring loop** — `listings_db.py:114-150` + `poller.py:81-110`.
    30s timeout ⇒ steal ⇒ two writers race; the original holder's `finally`
    then unlinks the *thief's* lock (no ownership token) ⇒ third unlocked
    writer. Degraded path: a transient load error makes every key look new →
    full-DB rescore under the lock (minutes). *Fix: `fcntl.flock` or
    PID/uuid-owned lock; score outside the lock, merge under it.*

13. **Dashboard `/analyze` is CSRF-reachable and spawns
    `claude -p … --permission-mode bypassPermissions`** — `dashboard.py:269-286`.
    Form-POST, no Origin/token check: any webpage in the user's browser can
    trigger a permission-bypassed agent with 80 chars of caller-chosen prompt
    text (argv-exec, so no shell injection — but prompt injection is open).
    `POST /poll` (:8642) CSRF-able too. *Fix: per-process token header +
    Origin check; scoped `--allowedTools` instead of bypassPermissions.*

14. **Zero validation at upsert; no schema sanity gate on scrape output** —
    `listings_db.py:179-227`, `scrapers/propertyguru.py:906-1241`. In today's
    DB: 481 listings (7.6%) outside sane sqft-per-bed bounds (112 are
    literal v3.6-class ">700sf 1BRs"), a $6,250-psf mis-scrape, 12 cluster
    houses unflagged. The 4-strategy scraper cascade degrades silently — one
    PG redesign from poisoning the DB with no alarm. *Fix: ingest-time
    `ingest_flags` (annotate, don't drop) + pre-upsert batch invariants
    failing loudly into poll_state.error.*

## P2 — material, fix opportunistically

- **Backtest evidence quality** (full detail in the audit transcripts):
  region magnitudes are construction-sensitive (URA index says CCR *beat* OCR
  over the panel's own first window — the de-inversion direction holds
  long-run, the "≫" doesn't); "OCR won every regime" is bucket-aggregation
  only (CCR won 20/81 windows, incl. the most recent independent one); the
  v3.7 0–5yr-cohort penalty rests on ≤5 projects of one vintage; MRT std_β
  −0.157 is inflated by a 2026 station file (TEL4/5 opened inside the forward
  windows); no standard errors anywhere — treat |std_β| < 0.1 as
  undetermined. Trailing-CAGR "≈0" is stale: current panel shows ρ +0.127
  (de-weight still defensible, evidence line isn't).
- **n_tight ∈ {5..7} corridor** escapes both the thin-cohort damp (≥5) and
  the below-distribution rule (needs ≥8) — a bait ask 20% under 6 prints gets
  full credit (`full_scorer.py:1452` vs `mmr.py:215`).
- **Quick-filter survivorship quantified**: 997/6,360 rejected; all 907 soft
  rejects lack `total_units`; rejects skew freehold (45% vs 26%) and older —
  and the project-units backfill that would fix them runs *after* the gate
  (`full_scorer.py:1782` vs `:664`). *Fix: backfill before the gate.*
- **relative_value drops ~93% of freehold peers** (no `lease_start`,
  `relative_value.py:146`) → freehold subjects read ~5% structurally
  expensive in freehold-heavy districts; subject included in its own peer
  median.
- **Cohort overwrite**: 2-3 tight prints demote a deep band cohort to "thin"
  (487 listings) even when the benchmark stays 60% band-based
  (`full_scorer.py:1618-1622`).
- **New Sale + multi-unit rows pollute tight comps** (`full_scorer.py:107-132`):
  developer pricing in a resale's "own prints"; bulk rows can land in the p10.
- **Rent confidence ignores contract depth and cache age** — 3 contracts =
  190 contracts = conf 0.95; `rental_cache.json` `built` never checked.
- **Rental income tax absent** from net carry (overstates after-tax rent
  10–24%). Statutory rates otherwise verified current (BSD/ABSD/SSD-Jul-2025/
  property tax all correct).
- **Agent-override appreciation is the last uncapped positive channel**
  (`mmr.py:418`; URA rates clamp to [−5,15], overrides don't), and override
  cohort-damping differs between the two code paths (`mmr.py:174` vs `:417`).
- **psf_value slope hardcoded −0.8** desynced from `MMR_RELVALUE_SLOPE`
  (`mmr.py:220`) — config tuning retunes only half the value signal.
- **Score-vintage mixing in the UI** — no config version on records; fresh
  poll-scored rows rank against stale-config bulk after any recalibration
  (masked today by the Jun-11 full rescore). Fixed by the P0-2 stamp.
- **v3.9 dedup**: merged row takes the *newest* copy's `first_seen` →
  multi-agent units permanently look NEW; key ignores floor (false merges)
  and exact price (cross-agent drops invisible). *Fix: min(first_seen), union
  price history, add floor/banded-sqft to key.*
- **Livability**: unguarded `float()` on malformed `mrt_info` 500s the whole
  fresh view (`livability.py:40` + no per-row guard in `ui.load_fresh`);
  present-and-typical fields earn +7 vs sparse listings (sorting partly ranks
  data completeness — centre typical bands at 0); "Mid Floor" emits no
  component at all.
- **Scraper field waste**: `listing_date`, `description` (where "PES"/"loft"/
  "auction" live), `tags`, `land_area_sqft` are parsed then dropped
  (`listings_db.py:52-58`) — v3.8/v3.9 had to infer from price geometry what
  the scrape already said in words; poll enrichment is off (`enrich_top=0`)
  so floor_level coverage is 0%.
- **Misc verified**: windows-1252 regression in `fetch_ura_districts.py:164`
  (fetch-layer re-read, D11-bug cousin); DOM-vs-JSON price semantics differ
  (min vs midpoint ⇒ phantom price changes); rent-URL leak via `--url`;
  project_units join misses are 64% new-launches (correlated blind spot for
  dev_size/MRT); `mmr_history.csv` appended without a lock from two
  processes; dashboard hardcodes "v3.7" + hand-copied weights; `esc()`
  doesn't escape `"` inside double-quoted attributes.

## Test-coverage gap (flagged independently by three audits)

The only MMR trust-rule "test" greps the source for a string
(`tests/test_stack_comps.py:92-99`). No behavioral test constructs a listing
with `ask_below_stack_prints`/`stack_premium_pct>5`/`bedroom_sqft_mismatch`
and asserts the 25% retention; nothing tests `_knee_discount`, the blend
math, the v3.6.2 cap, cost/buyer_pool neutrality, or the ROI identity
(test_roi enshrines bug #11). **Build the artifact-taxonomy regression
suite**: PES stack, loft bait, mis-scrape, fairly-priced high-floor,
stale-print project, missing-data sweep across every component.

## Recommended sequence

1. **Now (hours):** config-version stamp on every scored row (#2) ·
   `_pu_normalize` the prints join + `no_ura_prints` flag (#4) · yield
   suspect-damp one-liner (#8) · cost/buyer_pool neutrality (#9) ·
   ROI entry-cost fix (#11) · livability guard rails.
2. **This week:** stale-print window anchor (#5) · floor-aware tight comps
   (#6) · bed-matched rent serving (#7) · profile-join tightening (#10) ·
   staleness sweep + unit_key relist linking (#3) · ingest flags + batch
   gate (#14) · fcntl lock (#12) · dashboard token (#13) · quick-filter
   reorder · artifact-taxonomy test suite.
3. **Structural (this month):** lock weights + registered post-Jun-2026
   holdout; clustered bootstrap CIs in backtest_ext; era-block regime
   analysis; EC dataset fetch; poll-cycle detail enrichment (floor/format
   words); leveraged-ROI model; net-yield surfacing.
4. **Docs:** confidence language updated (done in this pass — RELEASES.md
   annotates the v3.5b generalization claim; CLAUDE.md qualifies ρ +0.288 as
   in-sample single-regime).
