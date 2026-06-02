# Evaluation Memory

This folder is the **git-tracked memory of past condo evaluations**. Each time the AI
agent finishes an evaluation (via `invest.py --from-review`), the per-condo verdicts are
appended here so future analyses can reference what was concluded before.

## ⚠ Past evaluations may be wrong or stale

These are **point-in-time judgements**, not facts:

- **Prices move.** The `as_of` price/PSF in each entry was true on `evaluated_at` and is
  probably out of date now.
- **Conditions change.** Interest rates, supply (new launches nearby), government plans,
  and MRT timelines all shift the picture.
- **The evaluation could simply have been wrong.** It was one analysis with the
  information available at the time.

**Always re-verify the current price and re-research before relying on a stored rating.**
A fresh evaluation is *appended* to the condo's history — the old one is kept for
context, not overwritten.

## Layout

```
evaluations/
├── index.json        # fast lookup: slug -> {condo, district, latest_rating, latest_date}
├── <condo-slug>.json # one condo, append-only `history` of evaluations
└── README.md         # this file
```

## Using it

```bash
python invest.py --recall "The Continuum"   # past evaluations for a condo (fuzzy match)
python invest.py --list-evals               # everything evaluated so far
```

Commit this folder so the memory persists across machines and sessions.
