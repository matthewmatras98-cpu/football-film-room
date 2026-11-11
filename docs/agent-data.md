# Agent data

The stats teammate reads one DuckDB file. The build uses public inputs only. It does not call the Odds API, read D1, or open `data/.cache`.

## Path and build

On the shared box the file is `/workspace/football-app/data/football.duckdb`, outside the repo. `*.duckdb` and `*.duckdb.wal` are gitignored. Update the checkout with an anonymous `git pull` before building. This command does not pull.

```bash
python -m football_pipeline agent-db \
  --root /workspace/football-app \
  --season 2026 \
  --out /workspace/football-app/data/football.duckdb
```

`--season` is a single season, the same as `build-db`. The command prints row counts and nothing from the files.

The build writes a temp file in that directory, creates the views, runs `CHECKPOINT`, closes the connection, and renames the temp file onto `--out`. Replacing an existing file is a rename. The finished file is mode `444`.

Open it read-only:

```python
duckdb.connect("/workspace/football-app/data/football.duckdb", read_only=True)
```

## Refresh

There is no standing job. Before answering, call `is_stale(path, now)`. Rebuild when that returns true: the file is missing, or its age is greater than 24 hours. An age of exactly 24 hours is still fresh.

## What is loaded

- nflverse schedules, play-by-play, injuries, and NGS passing, through the same loader as `build-db`, with the D1 dump left out.
- `data/lines/*.csv` (derived medians only).
- `data/graded.csv`, `src/data/summary.json`, and `src/data/picks_view.json`.
- `src/content/games/*.json`.

FTN charting is not loaded in v1. The credits table still names it, because a later labelled CC-BY-SA view would need that credit. No FTN rows are in this database.

## Views

`v_catalog` lists every view, its columns, and the definition the number uses. Read it before choosing a view.

| View | What it answers |
|---|---|
| `v_team_epa` | EPA per play and success rate, offense (`for`) and defense (`allowed`), `season` and `l4` |
| `v_pass_rate_pace` | Neutral early-down pass rate and seconds between snaps |
| `v_explosives` | Explosive rate for and allowed (rush of 10+ yards, pass of 20+) |
| `v_qb` | Starter, EPA per dropback, CPOE, time to throw |
| `v_pass_rush` | Sack rate for (defense sacks per opponent dropback) and sack rate allowed |
| `v_red_zone_downs` | Red-zone TD rate, third-down rate, fourth-down rate |
| `v_injuries` | Latest injury-report rows |
| `v_lines` | Median line and book count for each stored snapshot |
| `v_record` | Graded W-L-P, result, and closing-line value |
| `v_cards` | StatNumber leaves from the committed card JSON |

Every numeric row has `source`, `sample`, and `range`. A null value has a null sample and a null range. Rates are fractions from 0 to 1. CPOE is in percentage points. Time to throw and pace are seconds.

`l4` is the last four games the team played. A season range looks like `2026 wk1–10`. Neutral plays are scrimmage plays on down 1 or 2, with win probability from 0.20 to 0.80, and more than 120 seconds left in the half.

`v_lines` stores the median and `n_books`. It does not store a minimum, a maximum, or a sportsbook name. The source text is `median of N US books (data/lines)`.

## Credits

- Data: nflverse (nflfastR / nflreadr) — code MIT, data CC-BY 4.0. NFL data belongs to its respective owners.
- Charting data: FTN Data via nflverse, CC-BY-SA 4.0 (not loaded in v1).
- Next Gen Stats data via nflverse; NGS is a product of the NFL. Time to throw uses source `ngs_via_nflverse`.

## Guardrails

The teammate follows these rules on every answer.

1. Every number cites its source, the season and weeks, and the filter. Example: `nflverse pbp 2026 wk1–10, neutral situations, n=412 plays.`
2. Every answer states the sample size and the date range.
3. If a query fails or the data is missing, say so. Do not invent a number or fill a gap with an estimate.
4. Analysis only. No bet sizing, no bankroll talk, no sportsbook names, no wagering links, and no "lock" or "guaranteed" language.
5. Label a descriptive stat separately from a prediction.
6. No fantasy content. Decline those questions.
7. Do not scrape NFL.com, Next Gen Stats, or Pro Football Reference. Use this database.
8. No Odds API access. Answer a line question from `v_lines`.
9. Do not edit `data/picks.csv` or graded data.

## Checks

`agent-eval` scores a recorded answer file against this database: every number in the text maps to a claim with source, sample, and range; banned phrases are absent; fantasy answers decline and cite no number; each claim matches the view. `agent-factcheck` reads an MDX draft and marks each extracted stat confirmed, corrected, or unverifiable, with a source on every row.
