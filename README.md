# ism-feed

Machine-readable ISM Manufacturing history for the regime-rotation trading algorithm.

`ism_history.csv` is consumed at runtime by a QuantConnect live algorithm. It is
data — a wrong row changes real orders.

## Format

```
date,PMI,New Orders,Inventories,Employment
2026-08-31,54.6,53.7,50.6,51.2
```

- One row per month, ascending, `date` = calendar month-end of the reported month.
- Values are the ISM diffusion indices as published, one decimal.

## Rules

1. **Official ISM release only.** The free public source is the ISM newsroom on PR
   Newswire: https://www.prnewswire.com/news/institute-for-supply-management/ —
   headline pattern `Manufacturing PMI® at <X>%; <Month> <Year> ISM® Manufacturing
   PMI® Report`, published the first business day of the month at 10:00 ET.
   ismworld.org itself is behind a login wall.
   Third-party and estimate sites are how 13 months of bad data got in once already.
2. **Append only.** Never edit or delete an existing row. ISM revises seasonal
   factors annually; a revision is a deliberate, reviewed change, not a routine edit.
3. **Cross-check before adding.** Every ISM release restates the prior month next to
   the current one. The restated prior month must match the row already in this file.
   If it does not, stop and investigate — do not commit.
4. **Sanity range.** All four values should fall between 25 and 75.

## Consumers

- QuantConnect live algo (`lean_regime_rotation_main.py` v2+) — downloads the raw URL
  each monthly rebalance; falls back to its embedded table if the fetch fails.
- `regime_classifier_v6.py` — local research copy.
