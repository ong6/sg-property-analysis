# Condo profiles — the "perfect info" layer

One JSON per development holding its **stable physical facts**: stacks, facings,
views, layouts, site-plan notes. These are researched once (web-searched and
screened by a Claude instance via `/research-development`) and reused, because
unlike price they barely change.

This is **deliberately separate from `evaluations/`**:

| | `evaluations/` | `profiles/` (here) |
|---|---|---|
| Holds | Buy/Neutral/Avoid judgements | physical facts (stacks/facings/layouts) |
| Lifecycle | go stale fast (price-dependent) | stable; re-research only on AEI/en-bloc |
| Shape | append-only `history[]` | single current record (`revision` bumps) |

## Rules
- **Never invent a stack or facing.** An absent stack means *"not yet researched"*,
  not *"doesn't exist"*. Every profile carries `sources[]` and a `confidence`.
- `unknown`/omitted is always a valid value for any field.
- Source of truth is the per-condo files; `index.json` is rebuilt from them.

## Use
```bash
python invest.py --profile "The Luxurie"     # show a profile (fuzzy)
python invest.py --list-profiles             # all researched developments
python invest.py --save-profile path.json    # store a researched profile
```
At evaluation time the analyze/scan flows auto-join a listing to its stack/layout
(by sqft + beds + facing) and surface it as `factual_data.stack_profile`.

Schema: see the module docstring in `profile_memory.py` (`PROFILE_TEMPLATE`).
