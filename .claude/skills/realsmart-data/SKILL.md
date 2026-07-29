---
name: realsmart-data
description: Pull clean per-project market data from realsmart.sg (owner-preferred source) — transaction stats, % profitable, holding-period distribution, rental psf/yield, unit-size mix, profitability by block. Use during the Gather step of any analyze/scan flow when a project needs transaction-history context, profitability stats, or a rental-yield sanity check beyond the URA cache.
---

# Realsmart project data (Gather-step source)

The owner rates realsmart.sg above the agent/news sites for per-project data quality.
Treat it as a **Gather-step input** alongside (not instead of) URA prints — it aggregates
lodged transactions, so cross-check any load-bearing number against `--raw` URA data.

## Fetch

**First choice — let the repo do it.** `realsmart_cache.py` already wraps every step
below (slug resolution, plain fetch, the `/map` escalation, parsing) and caches the
result per project for 45 days, so a repeat lookup costs nothing:

```bash
python realsmart_cache.py --show "Regentville"          # one project, JSON out
python realsmart_cache.py --from-db --districts 16,18,19 --min-score 600   # warm a scope
```

Reach for the raw fetches below only when you need a field the cache doesn't parse.

**Resolve the slug — do not guess it.** PropertyGuru abbreviates where realsmart
spells out ("West Bay Condo" vs `west-bay-condominium`), so a lowercase-and-hyphenate
guess 404s often enough to matter, and a guess that *happens* to 200 can attach a
different project's REALSCORE. `realsmart.py` resolves against realsmart's own sitemap
(cached slug index, one sitemap request a month):

```bash
python realsmart.py --refresh              # build/refresh the slug index
python realsmart.py "West Bay Condo"       # -> https://realsmart.sg/p/west-bay-condominium
```

If it returns no slug, say "not found" and leave the field null. That is the correct
answer — never fall back to a guessed URL.

**Then fetch the public project page. No login, no browser, one plain HTTP fetch:**

```bash
python3 /Users/bytedance/Sideproject/personal-data-store/.claude/skills/web-extract/scripts/fetch.py \
  "https://realsmart.sg/p/<slug>"
```

It carries REALSCORE, the % Profitable badge, annual returns, transaction stats, unit
mix, MRT/schools and project facts — verified 2026-07-28.

**Escalate to the map SPA in two cases**, via the Patchright reader (login-gated):

1. You need something the `/p/` page genuinely lacks (e.g. per-block profitability).
2. **The `/p/` page has no REALSCORE at all.** realsmart serves two `/p` templates —
   a full one (JadeScape, Regentville, FLO Residence) and a *lite* one (Palm Gardens)
   with no score at any depth. Re-fetching the lite page never helps; `/map` has the
   numbers for those same projects. This is an escalation for a missing score, not a
   retry for a flaky one.

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

- **Reading the page by eye/regex is a known trap** (all handled by
  `realsmart_cache.parse_project_page`, so prefer it). The page mixes two layouts:
  stat tiles are VALUE-then-LABEL ("71.5%" / "% Profitable"), while the two header
  stats are LABEL-then-SUBTITLE-then-VALUE ("Annual Returns" / "Avg annualized profit
  (past 1y)" / "4.7"). The subtitle contains a digit, so "first number after the label"
  yields **1.0** for a 4.7% figure. A highlights badge reading "100%" / "Profitable"
  also sits *above* the real "584" / "Profitable" count tile — take the count, not the
  badge. Wrong numbers arrive silently; nothing errors.
- **The denominator for % profitable is profitable + unprofitable resales** — that
  count is what makes the percentage meaningful, so always carry it.
- **psf is raw lodged psf** — apply the GFA-harmonisation adjustment when comparing
  pre-Jun-2023 projects against harmonised ones (gross old-comp psf ÷0.95).
- Uncompleted projects show `N.A.` for profitability/rental — expected, not an error.
- Login: Google session persists in `~/.web-extract/browser-profile`. If output is the
  "Get Superpowered Now" login shell, the session expired — the owner must re-login once
  via `--headed --wait 150`.
- Their ToS: personal use only. One page per project, no bulk crawling — this is a
  low-volume research source, never a pipeline data feed.
