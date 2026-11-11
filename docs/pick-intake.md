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
| `line` | blank uses the latest pre-kick snapshot; otherwise a `.0` or `.5` number in range |
| `confidence` | blank, or `1`, `2`, or `3` |
| `writeup` | markdown body, at most 50000 characters |
| `server_received_at` | ISO-8601 UTC timestamp ending in `Z` |
| `void_pick_id` | the active pick id when `action` is `void`, suffix included; blank on a pick |

A caller does not send a pick id. Intake assigns `{game_id}-{market}` for the first pick on that game and market. A pick id is active when it has a pick row and no void row. At most one active pick per game and market. A new pick is accepted only when no active pick exists; otherwise the job fails with `active pick exists: {pick_id}`, naming the active id.

After that void, the next id is `{game_id}-{market}-r{n}`. `n` is the number of existing pick rows for that game and market, plus one, so the replacements are `-r2` through `-r9`. A 10th version fails. The id matches `{game_id}-(spread|total)(-r[2-9])?`.

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
