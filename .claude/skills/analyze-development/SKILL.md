---
name: analyze-development
description: Flow A — evaluate a whole condo development by name or project URL. Produces a development-level verdict plus best/worst stacks, facings and floors, with a Buy/Neutral/Avoid rating per unit type. Use when the user names a condo ("analyze The Continuum", "is Grand Dunman good?") or gives a PropertyGuru project page.
---

# Analyze a Development (Flow A)

**Altitude**: the question is whether the DEVELOPMENT is worth buying into, and
which stacks/facings/floors to target or avoid. Rate each unit type too.

**Purpose check first**: metrics assume investment (5–7yr hold). If the request
hints at own-stay or is ambiguous, ask the user before rating
(see `docs/evaluation-rubric.md` → Purpose check).

## Steps

1. **Recall** — past evaluations are references, not truth; re-verify prices:
   ```bash
   python invest.py --recall "<condo name>"
   python invest.py --search-db "<condo name>"
   ```

2. **Gather**:
   ```bash
   python invest.py --condo "<name>" --beds 1,2,3            # by name
   python invest.py --url "<propertyguru project URL>"        # or by project page
   ```
   Creates `output/run_NNN/raw_analysis.json` (one entry per unit type).

3. **Research & evaluate** (form your own view BEFORE looking at algo_reference):
   - Web search: `"<name> Singapore review"` (build quality, developer, defects),
     `"<name> price trend transactions"` (resale PSF direction),
     `"<name> floor plan site plan facing"` (stack/facing/floor quality)
   - Read `factual_data` per unit type; rubric: `docs/evaluation-rubric.md`
   - Optional sanity-check: `python invest.py --score '{...}'` — remember it
     takes your inputs at face value; it is not independent confirmation.

4. **Fill `agent_evaluation`** for each unit type in `raw_analysis.json`. Put
   stack/facing/floor guidance in `stack_notes`; overall development verdict in
   `report_level.agent_executive_summary`. Always set `rating`, `confidence`,
   `rating_rationale`.

5. **Report** (auto-saves evaluation memory; commit `evaluations/`):
   ```bash
   python invest.py --from-review output/run_NNN/raw_analysis.json --output output/run_NNN/final
   ```
