---
name: realsmart-data
description: Pull clean per-project market data from realsmart.sg (owner-preferred source) — transaction stats, % profitable, holding-period distribution, rental psf/yield, unit-size mix, profitability by block. Use during the Gather step of any analyze/scan flow when a project needs transaction-history context, profitability stats, or a rental-yield sanity check beyond the URA cache.
---

# Realsmart project data (Gather-step source)

The owner rates realsmart.sg above the agent/news sites for per-project data quality.
Treat it as a **Gather-step input** alongside (not instead of) URA prints — it aggregates
lodged transactions, so cross-check any load-bearing number against `--raw` URA data.

## Fetch

Rendered via the personal-data-store's Patchright reader (persistent logged-in profile):

```bash
python3 /Users/bytedance/Sideproject/personal-data-store/.claude/skills/web-extract/scripts/reader.py \
  "https://realsmart.sg/map?id=<PROJECT%20NAME%20UPPERCASE>&mode=c" --wait 8 --max 15000
```

Project name is URL-encoded uppercase as URA spells it (e.g. `UNION%20SQUARE%20RESIDENCES`).

## What a project page returns

- **REALSCORE** (their profitability rank) + avg annualized profit past 1y
- **Transactions**: count, total value, first/last transacted, % sold at launch,
  % profitable, profitable/unprofitable split, >6% annualized count, avg holding
- **Holding-period distribution** (counts per bucket — resale-timing evidence)
- **Rental**: avg psf rental, number of rentals, est. gross yield
- **Unit distribution**: count + size range per bedroom type
- **Profitability by block**, nearest MRT with distance, tenure/completion facts

## Caveats

- **psf is raw lodged psf** — apply the GFA-harmonisation adjustment when comparing
  pre-Jun-2023 projects against harmonised ones (gross old-comp psf ÷0.95).
- Uncompleted projects show `N.A.` for profitability/rental — expected, not an error.
- Login: Google session persists in `~/.web-extract/browser-profile`. If output is the
  "Get Superpowered Now" login shell, the session expired — the owner must re-login once
  via `--headed --wait 150`.
- Their ToS: personal use only. One page per project, no bulk crawling — this is a
  low-volume research source, never a pipeline data feed.
