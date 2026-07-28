---
name: realsmart-data
description: Pull clean per-project market data from realsmart.sg (owner-preferred source) — transaction stats, % profitable, holding-period distribution, rental psf/yield, unit-size mix, profitability by block. Use during the Gather step of any analyze/scan flow when a project needs transaction-history context, profitability stats, or a rental-yield sanity check beyond the URA cache.
---

# Realsmart project data (Gather-step source)

The owner rates realsmart.sg above the agent/news sites for per-project data quality.
Treat it as a **Gather-step input** alongside (not instead of) URA prints — it aggregates
lodged transactions, so cross-check any load-bearing number against `--raw` URA data.

## Fetch

**Default — the public project page. No login, no browser, one plain HTTP fetch:**

```bash
python3 /Users/bytedance/Sideproject/personal-data-store/.claude/skills/web-extract/scripts/fetch.py \
  "https://realsmart.sg/p/<slug>"
```

Slug is the project name lowercased, non-alphanumerics collapsed to hyphens
(`JadeScape` → `jadescape`, `The Continuum` → `the-continuum`). It carries
REALSCORE, the % Profitable badge, annual returns, transaction stats, unit mix,
MRT/schools and project facts — verified 2026-07-28. If a slug 404s, try the
name's other spellings; don't fall through to the SPA just for a score.

**Only if you need something the `/p/` page genuinely lacks** (e.g. per-block
profitability), use the login-gated map SPA via the Patchright reader:

```bash
python3 /Users/bytedance/Sideproject/personal-data-store/.claude/skills/web-extract/scripts/reader.py \
  "https://realsmart.sg/map?id=<PROJECT%20NAME%20UPPERCASE>&mode=c" --wait 8 --max 15000
```

Project name is URL-encoded uppercase as URA spells it (e.g. `UNION%20SQUARE%20RESIDENCES`).

## What a project page returns

- **REALSCORE** (0–5 profitability rank) + avg annualized profit past 1y
- **Transactions**: count, total value, first/last transacted, % sold at launch,
  % profitable, profitable/unprofitable split, >6% annualized count, avg holding
- **Holding-period distribution** (counts per bucket — resale-timing evidence)
- **Rental**: avg psf rental, number of rentals, est. gross yield
- **Unit distribution**: count + size range per bedroom type
- **Profitability by block**, nearest MRT with distance, tenure/completion facts

## How to read REALSCORE

Their 0–5 profitability rank: a high score means few or no resales sold at a loss.
Read it as **downside evidence** — *has this project ever lost owners money?* — not as
an appreciation forecast. It is backward-looking, so the repo's forward-signal priors
apply: like trailing CAGR, it is story rather than measured forward edge.

**Always pair it with the transaction count.** 4.8 across 300 resales is a real
statement about how this project has treated sellers; 5.0 across 6 is noise wearing a
number. A *low* score on a deep history is the more actionable signal of the two, and
the one worth acting on.

## Caveats

- **psf is raw lodged psf** — apply the GFA-harmonisation adjustment when comparing
  pre-Jun-2023 projects against harmonised ones (gross old-comp psf ÷0.95).
- Uncompleted projects show `N.A.` for profitability/rental — expected, not an error.
- Login: Google session persists in `~/.web-extract/browser-profile`. If output is the
  "Get Superpowered Now" login shell, the session expired — the owner must re-login once
  via `--headed --wait 150`.
- Their ToS: personal use only. One page per project, no bulk crawling — this is a
  low-volume research source, never a pipeline data feed.
