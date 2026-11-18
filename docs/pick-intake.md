# Pick intake

`pick-intake` runs only from `workflow_dispatch`. Dispatch it against `main`. `pick-ledger` starts on a push to `main` that changes `data/picks.csv`, and on no other branch. A dispatch against any other ref does not start the ledger.

The runner fetches `origin/main`, checks that out, and installs pipeline dependencies before it validates or appends, so the row lands on current `main`.

## Inputs

All ten inputs are strings. GitHub does not require them. The intake command rejects a missing or invalid value and leaves the tree untouched.

| Input | Rule |
| --- | --- |
| `action` | `pick` or `void`. Surrounding whitespace is stripped before the branch |
| `request_id` | lowercase UUID v4. Surrounding whitespace is stripped; the commit body uses that value |
| `game_id` | open-week game id, `YYYY_WW_AWAY_HOME` |
| `market` | `spread` or `total` |
| `side` | team abbreviation on a spread; `over` or `under` on a total |
| `line` | blank uses the latest pre-kick `line` from `data/lines`; otherwise a `.0` or `.5` number in range |
| `confidence` | blank, or `1`, `2`, or `3` |
| `writeup` | markdown body, at most 50000 characters |
| `server_received_at` | ISO-8601 UTC timestamp ending in `Z` |
| `void_pick_id` | the active pick id when `action` is `void`, suffix included; blank on a pick |

A caller does not send a pick id. Intake assigns `{game_id}-{market}` for the first pick on that game and market. A pick id is active when it has a pick row and no void row. At most one active pick per game and market. A new pick is accepted only when no active pick exists; otherwise the job fails with `active pick exists: {pick_id}`, naming the active id.

After that void, the next id is `{game_id}-{market}-r{n}`. `n` is the number of existing pick rows for that game and market, plus one, so the replacements are `-r2` through `-r9`. There is no `-r1` suffix. A 10th version fails. The id matches `{game_id}-(spread|total)(-r[2-9])?`.

`validate` rejects any other pick id. The error is `pick_id must be {game_id}-{market} or {game_id}-{market}-r2..-r9`.

The grader counts a replacement only when every earlier pick id for that same game and market has a void that itself counted (`picked_at` and the ledger push time are both before kickoff). A void on only the immediately previous id is not enough. Otherwise the replacement is `rejected_late`. A hand-edited `-r3` is rejected when the original void counted and `-r2`'s did not, and also when `-r2`'s void counted and the original's did not. The row `picks_view.json` emits for that void, and the slate array a consumer builds from it, are under [Voided picks](#voided-picks-picks_viewjson).

A blank `line` reads `data/lines/{season}_w{NN}.csv`, whose columns are `snapshot_ts,game_id,market,line,n_books`. An older file may also have `min` and `max`. Intake uses `line` and does not read those columns.

`void_pick_id` must be that active id. Voiding a superseded or already-voided id fails.

A pick commit contains `data/picks.csv` and `src/content/picks/{pick_id}.md`. A void commit contains only `data/picks.csv`. An existing row is not rewritten. A blank `action` cell stays blank. The commit body is the validated `request_id`.

## Dry run

Dispatch against `main` only. Do not print secrets.

1. Dispatch `pick` for a real open-week game. `request_id` is a lowercase UUID v4. `server_received_at` is a few minutes behind the runner. The write-up can include a quote, a colon, and a `---` line. The commit touches only the two paths above. `picked_at` equals `server_received_at`.
2. `pick-ledger` starts on that push and appends one `(pick_id, pick, row_sha, first_pushed_at)` row. It does not rewrite the header or any other row. `first_pushed_at` is the GitHub activity timestamp.
3. Dispatch `void` for that pick, still more than 10 minutes before kickoff. The commit adds a second CSV row and does not change the markdown. The ledger gains a second row with a different SHA.
4. Dispatch `pick` again for the same game and market. The new id is `{game_id}-{market}-r2`, with its own write-up. Voiding the original id fails. Void the `-r2` id, then another pick is `{game_id}-{market}-r3`.
5. These fail the job and add no commit: `server_received_at` exactly at kickoff minus 10 minutes; a pick while one is still active for that game and market; a `request_id` that is not a lowercase UUID v4; `action=void` for a missing, superseded, or already-voided id.
6. Two dispatches a few seconds apart either both land, or the second fails with no partial push. A retry of the failed run succeeds against current `main`.

## Voided picks (picks_view.json)

`grade.py` writes `src/data/picks_view.json` from `picks_view()`. One object per pick row (`action` `pick`, or a blank action). A `void` row in `data/picks.csv` is not its own object. When that void counts, the matching pick's `status` becomes `void`.

`/api/slate` (`handleSlate`) does not return voids yet. When it does, it reads this file only. It does not re-derive them from `data/picks.csv` or `data/pick_ledger.csv`.

### Fields

`_view_row` emits the fields below. Keys are sorted. A decimal is quantized to thousandths and trailing zeros are stripped, so `1.0` is JSON `1` and `2.5` stays `2.5`. Whole minutes of `close_age_min` are integers.

| Field | JSON | Null |
| --- | --- | --- |
| `pick_id` | string | never |
| `game_id` | string | never |
| `season` | integer | never |
| `week` | integer | never |
| `market` | string, `spread` or `total` | never |
| `side` | string | never |
| `line_at_pick` | number | never |
| `picked_at` | string, the pick row's cell, unchanged | never |
| `units` | number | never |
| `confidence` | integer 1–3 | blank cell |
| `tags` | string | never; a blank cell is `""` |
| `note` | string | never; a blank cell is `""` |
| `closing_line` | number | no close |
| `close_source` | string: `odds_api`, `odds_api_thin`, `odds_api_stale`, or `nflverse` | no close |
| `close_ts` | string | no timestamp. A snapshot close is UTC `Z` via `format_ts`. An nflverse close is `""` on the grade and `null` here |
| `close_books` | integer | no book count |
| `close_age_min` | number | no age |
| `final_home` | integer | no final score |
| `final_away` | integer | no final score |
| `result` | string `W`, `L`, or `P` | unless `status` is `graded`. Always `null` on a void |
| `units_pl` | number. A win is `0.909`, a loss is `-1`, a push is `0` | unless `status` is `graded`. Always `null` on a void |
| `clv_pts` | number | unless `status` is `graded`. Always `null` on a void |
| `status` | string: `graded`, `pending`, `ungraded`, `rejected_late`, or `void` | never |
| `first_pushed_at` | string, UTC `Z`, second precision | the pick has no ledger row |
| `voided_at` | string, UTC `Z`, second precision | the pick is not a counted void |

A counted void is a row with `status` `"void"`. `voided_at` is `format_ts` of the counting void row's `picked_at`: UTC, seconds, suffix `Z` (`YYYY-MM-DDTHH:MM:SSZ`). It is not the ledger push time. `picked_at` on that same object is still the original pick. `_grade_one` runs first. The void overlay replaces only `status`, `result`, `units_pl`, `clv_pts`, and `voided_at`. The close, the score, `first_pushed_at`, and the pick cells stay.

When more than one void row for that `pick_id` counts, `_counting_void` keeps the earliest `picked_at`.

### Counting rule

A void counts only when the game has a kickoff and both times are strictly before that kickoff. A time equal to kickoff does not count.

- the void row's own `picked_at`
- that void row's ledger `first_pushed_at`, looked up by `pick_id` and the void row's own `row_sha`

A missing ledger time does not count. A void that fails either check is ignored. The original pick keeps the status `_grade_one` already gave it (`graded`, `pending`, `ungraded`, or `rejected_late`) and `voided_at` stays null.

`grade --no-fetch` adds one case. Intake republishes the view in the same push, before `pick-ledger` appends, so a void that is not in the ledger yet is treated as pushed at its own `picked_at`. `_counting_void` still drops it when that time is not before kickoff. Weekly grading does not do this. It requires the ledger row.

### Re-pick

`pick_version` numbers `{game_id}-{market}` as version 1. There is no `-r1` suffix. Versions 2–9 are `{game_id}-{market}-r2` through `{game_id}-{market}-r9`. `previous_pick_id` steps `-r2` back to the unsuffixed id, and `-rN` back to `-r{N-1}`. The original of a re-pick is that previous id. `game_id` and `market` stay the same; only the suffix changes.

A re-pick is graded only when every earlier id has a counted void. If any earlier id does not, that re-pick is `rejected_late`, with `result`, `units_pl`, and `clv_pts` cleared and `voided_at` null. A counted void on the immediately previous id is not enough when an older id was missed.

### W/L, units, CLV, summary

`summary_document` adds w, l, p, units, `win_pct`, `avg_clv`, and `pct_clv_pos` from rows with `status == "graded"` only. A void is left out of those totals, and out of `rejected_count` and `ungraded_count`. Its `result`, `units_pl`, and `clv_pts` are null, so they cannot be summed. `summary.json` still has `void_count`, the number of view rows with `status` `void`. The void row itself stays in `picks_view.json`.

### Slate shape

Build `voids` from `picks_view.json` only, then group the array by `week`:

```json
{
  "game_id": "2099_01_DET_PHI",
  "week": 1,
  "pick": {
    "side": "DET",
    "market": "spread",
    "line_at_pick": 2.5,
    "picked_at": "2099-09-13T15:00:00Z"
  },
  "voided_at": "2099-09-13T16:00:00Z",
  "repick": {
    "pick_id": "2099_01_DET_PHI-spread-r2",
    "side": "DET",
    "line_at_pick": 2.5,
    "picked_at": "2099-09-13T15:30:00Z"
  }
}
```

`repick` is `null` when the void has no later version. Steps:

1. Index the view by `pick_id`.
2. Keep rows whose `status` is `void`.
3. The next id is `{game_id}-{market}-r2` for the unsuffixed id, and `{game_id}-{market}-r{N+1}` for `-rN` when N is 2–8. `-r9` has no next id.
4. When that id is in the view and its `status` is not `rejected_late`, copy its `pick_id`, `side`, `line_at_pick`, and `picked_at` into `repick`. Otherwise `repick` is null.
5. Group the entries by `week`.

`pick` on the void entry is the voided row (`side`, `market`, `line_at_pick`, `picked_at`), not the void CSV row.

### Tests already pinning this

`pipeline/tests/test_voids_contract.py` pins the emitted void row, the `-r2` link and week grouping, a late void, and a file with no voids. These already pin the same behavior:

- `pipeline/tests/test_ledger.py`: `test_void_before_kickoff_is_excluded_and_a_late_void_is_ignored` (counted void versus a late `picked_at` and a late ledger push; `voided_at`; summary exclusion), `test_grader_counts_the_replacement_and_the_void_only` (`-r2` graded beside the void; view keys), `test_late_void_rejects_the_replacement`, `test_replacement_requires_the_immediate_previous_void`, and `test_hand_edited_r3_needs_every_earlier_void` (every earlier id, and there is no `-r1`).
- `pipeline/tests/test_grade_fixture.py`: `test_fixture_grades_and_summary` grades the season-2099 fixture, which has no void action. `void_count` is 0 and the W-L, units, and CLV totals are the hand calculation.
- `pipeline/tests/test_grade_offline.py`: `test_pending_pick_keeps_summary_counts_and_a_void_updates_the_view` pins `grade --no-fetch` for a void that is not in the ledger yet.
