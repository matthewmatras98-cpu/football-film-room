# Game card JSON contract

File: `src/content/games/{game_id}.json`. This is the v1 contract (Tape, Nov 9 2026) rewritten so every rule matches `src/lib/game-schema.ts`. Where the Nov 9 text and the schema differ, the schema wins. `schema_version` is the integer `1`.

The file name is not part of the Zod schema. `src/lib/games.ts` rejects a card whose file name is not `game_id`.

## Top level

| Field | Rule |
| --- | --- |
| `schema_version` | integer literal `1` |
| `game_id` | `YYYY_WW_AWAY_HOME`, away and home 2 or 3 uppercase letters |
| `season`, `week` | integers. `season` equals the year in `game_id`. `week` equals the week in `game_id` |
| `kickoff_utc` | ISO-8601 with a numeric offset or `Z` |
| `home`, `away` | non-empty strings, equal to the home and away abbreviations in `game_id` |
| `venue`, `surface` | `string \| null`. `venue` is the nflverse stadium name |
| `roof` | `outdoors \| dome \| retractable \| unknown` |
| `home_rest`, `away_rest` | `int \| null` |
| `line_open`, `line_latest` | a line point, or `null` |
| `line_history` | array of line points |
| `generated_at` | UTC timestamp ending in `Z` |
| `data_through_week` | integer |
| `sources` | a deduped subset of `nflverse_pbp`, `nflverse_schedules`, `nflverse_injuries`, `ngs_via_nflverse`, `odds_api`, `nws`, in that order when this repo writes the card. List only sources that were actually used |
| `injuries_status` | `reported \| pending`. `pending` requires `sections.injuries.home` and `sections.injuries.away` both empty |
| `sections` | the object below |

A fixed roof, including a fixed canopy, is `dome`. The card never says whether a retractable roof is open or closed. An unknown `stadium_id` is `roof: unknown`.

`injuries_status` is `reported` when the home or away team has at least one row on that season and week's injury report. Otherwise it is `pending` and both arrays are empty. The generator does not substitute an older week's report. One team can have an empty array while the other is listed; that card is still `reported`.

## Line point

`{ ts, home_spread, total, n_books }`

- `ts` is ISO-8601 and ends in `Z`.
- `home_spread` and `total` are `number \| null`.
- `n_books` is an integer `>= 0`.
- One point per snapshot. Spread and total rows join on `(game_id, snapshot_ts)`. A market missing at that timestamp is `null`.
- `n_books` is the minimum of the two markets when both are present, otherwise the present market's value.
- `line_history` is strictly ascending by `ts`. Equal timestamps are not allowed.
- `line_open` deep-equals the first point and `line_latest` deep-equals the last point. Both are `null` when the history is empty.
- `min`, `max`, prices, and book names are never emitted.

The derived file the generator reads is `data/lines/{season}_w{NN}.csv`. `lines-export` writes market `spread` (home spread) and `total`. The reader also accepts `spreads` and `totals`. It requires `snapshot_ts`, `game_id`, `market`, `line`, and `n_books`. A file that still has `min` and `max` is accepted, and those columns are not read.

## StatNumber

`{ value, source, sample, range }`

- `value` is `number \| null`. Missing input is `null`, never `0`.
- `source` is a non-empty string and must not contain `ftn` in any case.
- `sample` is `int \| null`. It is the play, dropback, or attempt count.
- `range` is `string \| null`. Week spans use an en dash, for example `2026 wk1–10` or `2026 wk7–10`.

Units:

- Rates are fractions from 0 to 1. The page formats them as percents.
- EPA is the raw per-play or per-dropback value.
- `cpoe` is in percentage points, as nflverse publishes it. It is not a 0–1 rate.
- `time_to_throw` and `pace_sec_per_play` are seconds, and a non-null value is `>= 0`.
- `time_to_throw.source` is exactly `ngs_via_nflverse`.
- Sack rates are 0–1 and `source` is exactly `nflverse_pbp`.

## sections

Each stat section is `{ home, away }` with the same keys on both sides. Every leaf is a StatNumber unless marked otherwise.

### epa

`off_epa_season`, `off_epa_l4`, `def_epa_season`, `def_epa_l4`, `off_sr_season`, `def_sr_season`.

Defense values are EPA and success rate allowed, so lower is better. The success rates are 0–1. Season is weeks 1 through `data_through_week`. Last 4 is that team's last four played games inside that span.

Neutral early-down passes are down 1 or 2, win probability 0.20 through 0.80, and `half_seconds_remaining > 120`. Pace is the mean seconds between consecutive offensive snaps in those situations.

### pass_rate_pace

`neutral_ed_pass_rate` (0–1) and `pace_sec_per_play` (seconds).

### explosives

`explosive_rate_for` and `explosive_rate_allowed`, both 0–1. An explosive play is a run of at least 10 yards or a pass of at least 20 yards.

### qb

`player` is `string \| null`, the expected starter: the dropback leader in the team's most recent played game. `epa_per_db` and `cpoe` come from nflverse play-by-play. `time_to_throw` is NGS `avg_time_to_throw` (`ngs_via_nflverse`). No quarterback means `player: null` and null StatNumbers, with `time_to_throw.source` still `ngs_via_nflverse`.

### sacks

`sack_rate_for` is defense sacks per opponent dropback. `sack_rate_allowed` is sacks taken per own dropback. Both are `nflverse_pbp` only.

### red_zone_downs

`rz_td_rate_for` and `rz_td_rate_allowed` are touchdowns per red-zone drive (a drive with a scrimmage play at `yardline_100` from 1 through 20). `third_down_rate_*` and `fourth_down_rate_*` are conversions per attempt. All six are 0–1.

### injuries

`{ home: Injury[], away: Injury[] }`. `Injury` is `{ player, position, status, detail }`. `player`, `position`, and `status` are non-empty strings. `status` is the nflverse report status. `detail` is the primary injury, or `null`.

### weather

`{ mode, temp_f, wind_mph, precip_prob_pct, summary }`. `mode` is `outdoor \| indoor \| retractable \| unavailable`.

- `indoor` (dome or fixed roof): `temp_f`, `wind_mph`, `precip_prob_pct`, and `summary` are JSON `null`, not StatNumbers. The page renders "Indoors (dome)".
- `outdoor` and `retractable`: the three measurements are StatNumbers with `source: "nws"`. `range` is the NWS hourly period that covers kickoff, `start/end` in the forecast's own ISO text. The value is `null` when that period does not exist yet. `precip_prob_pct` is 0–100, the one field that is a percent rather than a fraction. `summary` is the NWS `shortForecast`, or `null`. A retractable roof renders "Retractable roof: status not confirmed" plus the numbers.
- `unavailable` (not in the US, unknown roof, or no coordinates): the three measurements are StatNumbers with `source: "nws"` and `value: null`. The page renders n/a.

`nws` is listed in `sources` only when a number or a summary string actually came back from NWS.

### story_prompts

Exactly three `{ title, body }` objects. Both strings are non-empty.

Every string in the card is scanned for `$` and for `draftkings`, `fanduel`, `betmgm`, `betrivers`, `bovada`, `betonline`, `lowvig`, `mybookie`, and `betus` (any case). Prompts are also rejected by the schema when they contain `ftn` or the words lock, locked, guaranteed, wager, wagering, parlay, or sportsbook. The generator also refuses to emit bet, cover, units, odds, or pick.

The 2026 nflverse stadium names do not contain those tokens, so `venue` is scanned with the rest of the card.

## Spec corrections (T3)

`football-v1-spec.md` is not in this repo. These two corrections are recorded here. Tape mirrors them into the box copy of the spec.

### §12 S7 lines CSV

`lines-export` writes `data/lines/{season}_w{NN}.csv` as exactly:

`snapshot_ts,game_id,market,line,n_books`

`line` is the single lower-middle book line, element `floor((n - 1) / 2)`. There is no second line column. `market` is `spread` (the home spread) or `total`. `min` and `max` are not written. With one or two books those columns were raw book quotes, and the Odds API terms bar redistributing that as a downloadable file.

Files committed before this change still end in `min,max`. They stay in git history as they are. The next `lines-export` rewrites the week files without those columns. The card loader, `build-db`, and `agent-db` read a file with or without `min` and `max`, and they do not use those values.

### §8 budget skips

A budget skip writes the shot row, prints `::notice::`, appends that same line to the step summary, and exits 0. HTTP errors, D1 errors, and team-mapping errors still exit non-zero. Kind L, the one extra close shot for a window whose latest kickoff is more than 10 minutes after its anchor, is skipped below 150, the same floor as a fixed shot. A window of 10 minutes or less, including one kickoff, has no L. Three or more distinct kickoffs still get one L, keyed to the latest.

### §8 close-shot timing

A stays (40, 75] minutes before the anchor K and B stays (5, 40]. The late slot stays (0, 5] when B is untaken. L still uses B's clock from K_late. Credit gates, catch-up (a close shot taken more than 30 minutes after its window opened), and one row per shot key are unchanged. Fixed shots stay Tuesday 14:07, Wednesday 16:07, and Saturday 16:07 UTC, each due for 30 minutes.

The scheduled job passes `--wait-max-minutes 45`. It aims an untaken A at K-60, an untaken B at K-15, and an untaken L at K_late-15. If that aim is already behind the clock and the shot is still inside its window, the job takes it on this tick. If the aim is still ahead and inside the remaining cap, the job sleeps until it, even when the window is already open, then re-reads D1 shots and credits and runs the normal dispatch. It repeats while another aim fits under the same cap. A due shot whose aim is farther than a fresh cap is taken now. One whose aim fits a fresh cap but not the time left in this run is left for the next run, which is what keeps L on its own aim instead of folding it into B. A dry run does not wait. GitHub may delay or drop a cron tick, so the wait is what lands the shot inside the 35-minute window.

Game-day crons also fire every 10 minutes at 3, 13, 23, 33, 43, and 53 past the hour. Thursday night is Thu 23 and Fri 00-01 UTC. Sunday is Sun 12-23 and Mon 00-01 (London about 13:30 UTC, early 18:00, late 21:05/21:25, SNF 01:20). Monday night is Mon 23 and Tue 00-01. Saturday late season is Sat 18-23 and Sun 00-01. Phoenix does not observe DST, so the late Sunday kickoffs stay at 21:05 and 21:25 UTC. The half-hour crons stay. The job times out at 60 minutes. The `odds-dispatch` concurrency group still does not cancel, so a run queued behind a sleeper sees the shot already taken.
