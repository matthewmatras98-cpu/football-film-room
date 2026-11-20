# Film fact-check

Matthew writes a Friday or Saturday MDX post and asks Film to check the stats. Film runs `agent-factcheck` against the read-only DuckDB and replies with one row per extracted claim. The reply lists each stat as confirmed, corrected, or unverifiable, each with a source.

The database path and the build are in [agent-data.md](agent-data.md). On the shared box the file is `/workspace/football-app/data/football.duckdb`. This command reads that file. It does not build it.

```bash
cd pipeline && uv run --frozen python -m football_pipeline agent-factcheck --db /workspace/football-app/data/football.duckdb --mdx path/to/draft.mdx
```

`--mdx` is the draft. The command prints a JSON list and exits 0. Film turns that list into a table with these columns: text, status, claimed, actual, source, sample, range, reason. Each JSON object also has `team` and `stat`.

## Status

`confirmed` means the claim matches the database value. `actual` is that value. `reason` is null.

`corrected` means a row was found and the claim does not match. `actual` is the database value. `reason` is null. The reply uses `actual`.

`unverifiable` means the line was extracted and could not be checked. `actual` is null. `source` is `no matching view`. `reason` says why. The reply leaves the number blank.

A view can return one row whose value is null. `_close_enough` rejects a null, so the current code marks that claim `corrected` with `actual` null, and it keeps the view's source. Sample and range are null. That row has no replacement number. Leave it blank. KC sack rate on side `for` is one of these rows in the 2099 fixture database.

## Tolerance

`_close_enough` rounds the stored value to 6 decimal places and compares that with the claim, also rounded to 6 decimal places. The two rounded values have to be equal.

- A plain number matches that rounded value.
- A `±` number matches either sign. The `claimed` field stores the absolute value.
- A percent matches the written number, or that number divided by 100, both at 6 decimals. `33.3333%` matches a stored sack rate of `0.333333`. The unscaled number is tried as well, so `6%` also matches a stored CPOE of `6` (CPOE is already in percentage points).
- A null stored value does not match.
- A value that cannot be read as a float does not match.

`actual` uses the same 6-decimal display rounding as the cards. A rounded whole number is an integer, so `6.0` is `6`. `claimed` uses that rounding too. A whole number is an integer, except a whole percent, which stays a float: `75%` is claimed as `75.0`.

The graded record does not use `_close_enough`. The first `W-L` or `W-L-P` token on the line must equal `{wins}-{losses}-{pushes}` from `v_record`. The stored form always includes pushes, so `2-1` and `2-1-1` are different strings.

## What is recognized

A line is checked when any of these is true:

- It is a record line.
- It has a known phrase, and also a team or a number.
- It has a team and a number.

Any other line is omitted. The first number on a stat line is the claim. Later numbers on that line are ignored, including a sample size written first.

Team codes are the nflverse abbreviations (`CHI`, `GB`, `LAC`, and the rest). Matching is case-sensitive and whole-word. Longer codes are tried first, and the first code on the line is the team.

Scope is `l4` when the line contains `last 4`, `last4`, `last four`, or `l4`. Otherwise the scope is `season`. On `v_qb`, `l4` is the team's last four played games, not the quarterback's last four starts. Say it that way, and cite the row's `range`. The definition is the same one in [agent-data.md](agent-data.md).

For a sided stat, the side is `allowed` when the line contains `allowed`, `allowing`, or `against`. Otherwise the side is `for`. A phrase that is not sided ignores those words.

Phrases are case-insensitive substrings, tried in this order. The first hit wins.

| Phrase in the line | View | Metric | Sided |
|---|---|---|---|
| time to throw | `v_qb` | `time_to_throw` | no |
| neutral pass rate | `v_pass_rate_pace` | `neutral_ed_pass_rate` | no |
| epa per play | `v_team_epa` | `epa_per_play` | yes |
| success rate | `v_team_epa` | `success_rate` | yes |
| sack rate | `v_pass_rush` | `sack_rate` | yes |
| explosive | `v_explosives` | `explosive_rate` | yes |
| red zone | `v_red_zone_downs` | `rz_td_rate` | yes |
| third down | `v_red_zone_downs` | `third_down_rate` | yes |
| cpoe | `v_qb` | `cpoe` | no |

Rates in those views are fractions from 0 to 1. CPOE is percentage points. Time to throw is seconds, and its source is `ngs_via_nflverse`. The other phrases use `nflverse_pbp`.

A record line contains the word `record` or `w-l`, and a token like `2-1` or `2-1-1`. The whole line is checked as that record against the single `v_record` row. That row is the graded pick record for the season, source `graded.csv`. `team` is null and `stat` is `record`.

## What is unverifiable

These lines are extracted and come back unverifiable. `source` is `no matching view`.

| Situation | `reason` | `stat` |
|---|---|---|
| A team and a number, and no known phrase | `no mapped stat phrase` | null |
| A known phrase and no number | `no number` | null |
| A known phrase and a number, but no team, or the view does not have exactly one row | `no row for this team` | the metric |
| The record query does not return exactly one row | `no row for this team` | `record` |

A missing team uses the same reason as a team with no row: `no row for this team`.

Phrases that are not in the table stay unmapped. That includes pace, fourth-down rate, EPA per dropback, pressure, yards per play, and a team win-loss. It also includes a spread or a total. `v_lines` is not read. `CHI closed at -2.5` stays unverifiable when `-2.5` is a stored book line. Leave it unverifiable.

A number with no team and no known phrase is omitted. It is not a confirmed row.

## What is skipped

Skipped text never becomes a row.

- A leading byte-order mark.
- Frontmatter. A file that starts with `---` drops everything through the next line that is `---`. Numbers in the title, the season, the week, or the summary are ignored.
- A fenced block, from an opening fence of three backticks through the matching closing fence. A stat inside the fence is ignored. It is not marked unverifiable.
- A blank line.
- A line that starts with `import ` or `export `.
- A JSX line whose text starts a tag, such as `<SpreadChart />`.

## Guardrails

Film follows these on every fact-check reply.

1. Open the database read-only. The checker connects with `read_only=True`. Leave the file as it is. The box file is mode `444`, as in [agent-data.md](agent-data.md).
2. No Odds API and no D1. This command does not import those clients and does not call them. A spread, a total, or a book count is not confirmed from `v_lines`.
3. No betting advice and no fantasy advice. The reply is the table. No bet size, no bankroll, no sportsbook name, no wagering link, and no start, sit, or lineup talk.
4. Never invent a number. `confirmed` and `corrected` use `actual` from the row. `unverifiable` has no actual. A `corrected` row whose `actual` is null is missing data.
5. Keep the citation. Pass `source`, `sample`, and `range` through. A null sample or a null range stays null.
6. Leave the MDX, `data/picks.csv`, and the graded file unchanged.
