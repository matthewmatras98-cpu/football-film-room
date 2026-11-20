# Weekly grade

Phoenix does not observe DST. Times below are MST, which is Phoenix local time (UTC−7).

`weekly.yml` runs the full chain on Tuesday at 13:07 UTC (6:07 AM) and Thursday at 11:07 UTC (4:07 AM): refresh, pull-snapshots, grade, lines-export, build-db, then cards. It commits `data/graded.csv`, `src/data/summary.json`, `src/data/picks_view.json`, `data/lines`, and the game cards. The commit step skips when the staged diff is only `generated_at`. That field is on `summary.json` and each game card. `graded.csv`, `picks_view.json`, and the lines CSVs do not have it. Lines columns stay `snapshot_ts`, `game_id`, `market`, `line`, `n_books`.

`grade-only.yml` runs Friday at 11:07 UTC and Monday at 11:07 UTC, both 4:07 AM. It uses the same `weekly-grade` concurrency group, so it never overlaps a full run. It runs refresh, pull-snapshots, and grade, and it commits only `data/graded.csv`, `src/data/summary.json`, and `src/data/picks_view.json`. It uses the same generated_at guard as Tuesday and Thursday.

pull-snapshots reads D1. Neither run calls the Odds API. The grade-only job has no dispatch step and no `ODDS_API_KEY`, so it makes 0 Odds API requests.

`grade` rewrites `data/graded.csv` for every pick, including rows whose status is `void`. That is already what the Tuesday and Thursday grade step does. `grade --no-fetch`, which pick-ledger uses, leaves `graded.csv` as the last full grade wrote it.

A second grade with the same inputs rewrites the same bytes. The commit step then logs `no grader changes` and does not commit, so a result is not counted twice against a later Tuesday or Thursday run. `generated_at` in `summary.json` is included, not excluded. It stays put when `--as-of` is unchanged. The workflow passes the current UTC time, so a later run whose grades did not change moves only `generated_at`. That timestamp is not committed. Tuesday and Thursday do the same for game cards. `scripts/only_generated_at_changed.py` compares the index to HEAD, ignores `generated_at` keys and a `generated_at` CSV column, and the workflow restores those files and logs `no grader changes`.

Monday at 4:07 AM is before Monday Night Football. Tuesday's run is unchanged and still picks up MNF.

## Final whistle to /picks/

Approximate. Assumes nflverse has the final posted, a game lasts about 3 hours 15 minutes, and the Pages build after the commit takes a few minutes. Kickoffs below are Eastern Daylight Time, 3 hours ahead of Phoenix. From early November, Eastern Standard Time makes each whistle about an hour later in Phoenix and each lag about an hour shorter. The old schedule's next runs are Tuesday 6:07 AM and Thursday 4:07 AM. The new schedule adds Monday 4:07 AM and Friday 4:07 AM.

| Slate | Approx final whistle (Phoenix) | Old next run | Old lag | New next run | New lag |
| --- | --- | --- | --- | --- | --- |
| Sunday early (1:00 PM ET) | 1:15 PM Sunday | Tuesday 6:07 AM | about 41h | Monday 4:07 AM | about 15h |
| Sunday late (4:25 PM ET) | 4:40 PM Sunday | Tuesday 6:07 AM | about 38h | Monday 4:07 AM | about 12h |
| SNF (8:20 PM ET) | 8:35 PM Sunday | Tuesday 6:07 AM | about 34h | Monday 4:07 AM | about 8h |
| MNF (8:15 PM ET) | 8:30 PM Monday | Tuesday 6:07 AM | about 10h | Tuesday 6:07 AM | about 10h |
| TNF (8:15 PM ET) | 8:30 PM Thursday | Tuesday 6:07 AM | about 4.4 days | Friday 4:07 AM | about 8h |

Earlier rounding of these crons was about 40h, 37h, 32h, 9h, and 4.4 days. A Sunday-night whistle near 10 PM Phoenix is the 32h figure; 8:35 PM is about 34h. MNF stays on Tuesday either way.
