# Pick intake

`pick-intake` runs only from `workflow_dispatch`. Dispatch it against `main`. `pick-ledger` starts on a push to `main` that changes `data/picks.csv`, and on no other branch. A dispatch against any other ref does not start the ledger.

The runner fetches `origin/main` and checks that out before it validates or appends, so the row lands on current `main`.

## Inputs

All ten inputs are strings. GitHub does not require them. The intake command rejects a missing or invalid value and leaves the tree untouched.

| Input | Rule |
| --- | --- |
| `action` | `pick` or `void`. Surrounding whitespace is stripped before the branch |
| `request_id` | lowercase UUID v4 |
| `game_id` | open-week game id, `YYYY_WW_AWAY_HOME` |
| `market` | `spread` or `total` |
| `side` | team abbreviation on a spread; `over` or `under` on a total |
| `line` | blank uses the latest pre-kick snapshot; otherwise a `.0` or `.5` number in range |
| `confidence` | blank, or `1`, `2`, or `3` |
| `writeup` | markdown body, at most 50000 characters |
| `server_received_at` | ISO-8601 UTC timestamp ending in `Z` |
| `void_pick_id` | existing pick id when `action` is `void`; blank on a pick |

A pick commit contains `data/picks.csv` and `src/content/picks/{pick_id}.md`. A void commit contains only `data/picks.csv`. An existing row is not rewritten. A blank `action` cell stays blank.

## Dry run

Dispatch against `main` only. Do not print secrets.

1. Dispatch `pick` for a real open-week game. `request_id` is a lowercase UUID v4. `server_received_at` is a few minutes behind the runner. The write-up can include a quote, a colon, and a `---` line. The commit touches only the two paths above. `picked_at` equals `server_received_at`.
2. `pick-ledger` starts on that push and appends one `(pick_id, pick, row_sha, first_pushed_at)` row. It does not rewrite the header or any other row. `first_pushed_at` is the GitHub activity timestamp.
3. Dispatch `void` for that pick, still more than 10 minutes before kickoff. The commit adds a second CSV row and does not change the markdown. The ledger gains a second row with a different SHA.
4. These fail the job and add no commit: `server_received_at` exactly at kickoff minus 10 minutes; a duplicate pick; a `request_id` that is not a lowercase UUID v4; `action=void` for a missing id.
5. Two dispatches a few seconds apart either both land, or the second fails with no partial push. A retry of the failed run succeeds against current `main`.
