---
name: market-scan
description: Flow C — scan many candidates across districts and produce a rated shortlist (Buy/Neutral/Avoid each, strongest first). Use when the user asks to find condos in an area, pastes a results-page URL, or wants a comparison across districts/budgets.
---

# Market Scan (Flow C)

**Altitude**: a comparable shortlist. Every shortlisted listing gets a
Buy/Neutral/Avoid rating + confidence. **"No buys in this batch" is a valid
outcome** — don't manufacture a winner.

**Purpose check first**: metrics assume investment (5–7yr hold). If the request
hints at own-stay or is ambiguous, ask the user before rating
(see `docs/evaluation-rubric.md` → Purpose check).

## Steps

1. **Gather** (pick one):
   ```bash
   python invest.py --discover-districts                  # see district rankings
   python invest.py --auto --beds 2,3 --top 20            # auto-pick districts
   python invest.py --districts 3,5,14,15 --beds 2,3      # specific districts
   python invest.py --url "<PG search-results URL>"       # from a results page
   ```
   Creates `output/run_NNN/raw_analysis.json`.

2. **Recall per candidate** as you go:
   ```bash
   python invest.py --recall "<condo name>"
   ```

**Forward-signal priors (v3.4 — backtested; full list in the rubric).** Lead with
VALUE, not trailing appreciation. Strongest robust signals: **cheap-vs-district-peers**
(`relative_value.premium_vs_age_adjusted_median_pct`, `psf_premium_vs_ura_median_pct`)
and **region** (realized fwd OCR≈+4.0% ≥ RCR≈+3.5% ≫ CCR≈+0.7% — the old "CCR leads"
prior was inverted). Trailing `appreciation.annual_rate_pct`/`momentum` ≈0 forward power
(story/catalyst only); freehold is **not** a forward edge; absolute low PSF is mostly the
region effect. Model explains <10% of forward variance → small `score_1000` gaps are
noise; reserve **high** confidence for decisive evidence, near-ties → Neutral.

3. **Research & evaluate**:
   - Cover ALL shortlisted listings, not just the algo's top ranks — the
     ordering is an input, not the shortlist
   - Note: the quick filter drops listings with very incomplete scraped data;
     mention in `agent_methodology_notes` if coverage looked thin
   - Web search per project: reviews, issues, transactions; area trends and
     upcoming supply once per district
   - **Stack profiles**: `factual_data.stack_profile` carries the matched
     stack/facing/known-issues. Only `status: not_researched` for a **genuine
     contender** (one you'd rate Buy/Neutral) is worth a `/research-development`
     pass — don't research every candidate in a broad scan.
   - Rubric: `docs/evaluation-rubric.md`

4. **Fill `agent_evaluation`** per listing (`rating`, `confidence`,
   `rating_rationale` mandatory). Executive summary ordered by rating strength.

5. **Report** (auto-saves evaluation memory; commit `evaluations/`):
   ```bash
   python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
   ```

## Keeping the sheet fresh (optional)

```bash
python invest.py --update-db --districts 3,5,14,15 --beds 2,3   # refresh only, no report
python invest.py --list-db                                       # stats + price drops
```
