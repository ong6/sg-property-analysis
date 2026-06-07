---
name: arena-cycle
description: Run the full condo arena cycle — refresh/score the listings DB, run the bracketed tournament, referee the results (mandatory), and surface champions worth a deep evaluation. Use when the user wants updated rankings, says "run the arena/fight", or after new listings/URA data land.
---

# Arena Cycle (score → fight → referee → deep-dive)

The arena is purely algorithmic. **The cycle is not done until the AI referee
has verified the results** — that's the agent flow, not an optional extra.

## Steps

1. **(Optional) refresh data** — only if the user wants new listings or the
   DB is stale:
   ```bash
   python invest.py --update-db --districts <D,D,...> --beds 2,3
   python fetch_ura_districts.py --districts <D,...>      # if URA gaps exist
   ```

2. **Score the backlog**:
   ```bash
   python invest.py --score-db
   ```
   If mean MMR in the output drifts more than ~5 points from
   `MMR_NORM_CENTER` (config.py), recalibrate the center and rescore.

3. **Fight** (brackets: 2BR vs 2BR, 3BR vs 3BR… + open division):
   ```bash
   python invest.py --fight
   ```
   Writes `output/arena_latest.md` (+ timestamped archive),
   `output/arena_referee_packet.json`, and appends `data/arena_results.csv`.
   Past agent evaluations are auto-joined onto contenders.

4. **REFEREE (mandatory)** — read `output/arena_referee_packet.json`:
   - Work every `auto_flags` entry: verify the stats behind it (thin
     transactions, fallback rents, extreme value components, odd sqft).
     Web-check anything extreme.
   - Watch for **stacked artifacts** — one medium flag is a caveat; two or
     three on the same contender means demote it explicitly.
   - Check contenders whose `agent_rating` (past evaluation) disagrees with
     their rank — investigate which is right; conditions may have changed.
   - If you find a SYSTEMATIC artifact (a whole class mis-scored), fix the
     metric in `scoring/mmr.py` + add a test, rescore and re-fight — don't
     referee the same artifact forever.
   - Append a **"## Referee verdict"** section to `output/arena_latest.md`:
     confirmed champions, demotions + reasons, run confidence. Do this AFTER
     the final fight of the session (each fight overwrites the file).

5. **Evaluator subagents (when top contenders lack agent evals)** — the
   `agent_rating` join only works if evaluations exist. For unevaluated top
   contenders, spawn evaluation subagents (e.g., one per 2 brackets), each:
   - given its contenders' dossier (stats from `arena_referee_packet.json`)
     and the rubric (`docs/evaluation-rubric.md`)
   - researches each condo on the web (reviews, transactions, defects, area)
   - writes a reviewed JSON (`{"listings": [{project_name, district, price,
     psf, beds, sqft, url, tenure, agent_evaluation: {rating, confidence,
     summary, rating_rationale, red_flags, catalysts}}]}`) and persists it:
     ```bash
     python invest.py --save-eval <reviewed.json>
     ```
   Then RE-RUN `--fight` so the fresh evals join the rankings.

6. **Surface the result** — give the user the refereed bracket champions and
   real contenders (post-demotion), each with price/PSF/age-adjusted premium
   and their agent eval. Offer `/analyze-development` on any champion they
   care about; remind them the UI is at `python ui.py` → http://127.0.0.1:8642.

## Don'ts

- Don't present unrefereed rankings as conclusions.
- Don't let a contender with stacked data artifacts stay "champion" in your
  summary just because the algorithm ranked it #1.
- Don't forget brackets are the headline (like-for-like); the open division
  is secondary.
