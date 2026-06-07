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

3. **Research & evaluate**:
   - Cover ALL shortlisted listings, not just the algo's top ranks — the
     ordering is an input, not the shortlist
   - Note: the quick filter drops listings with very incomplete scraped data;
     mention in `agent_methodology_notes` if coverage looked thin
   - Web search per project: reviews, issues, transactions; area trends and
     upcoming supply once per district
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
