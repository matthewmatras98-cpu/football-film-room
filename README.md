# football-film-room

Personal football matchup analysis, weekly picks, and film notes.

The site is a static Astro build. Node 22 is required (`.nvmrc`, and `engines` in `package.json`).

## Publish a week post

Add an MDX file at `src/content/weeks/2026-wXX.mdx`. Every frontmatter field is required. There are no defaults.

```yaml
title: Week 11 notes
season: 2026
week: 11
published_at: "2026-11-14T18:00:00-07:00"
summary: One or two sentences.
picks:
  - 2026_11_DET_PHI
tags:
  - rest
draft: false
```

- `published_at` must be a quoted ISO-8601 datetime with a timezone offset, as in the example. Leave the quotes in place so the timestamp stays a string.
- `picks` lists nflverse game ids: `YYYY_WW_AWAY_HOME`, with 2- or 3-letter abbreviations.
- `draft: false` publishes the post at `/weeks/2026-wXX/`. `draft: true` is left out of the build.
- A draft stays in this public git repo even though the site does not render it.

The two files already in `src/content/weeks/` are drafts. Do not publish anything labeled sample.

`published_at` stays quoted in the file. `docs/post-template.mdx` is the same frontmatter, kept outside `src/content` so the build does not publish it. Copy that file to `src/content/weeks/` when a post is ready.

## Add a pick

Picks are entered on `/admin/pick` (owner-only sign-in). The server records the submission time and rejects anything at or after 10 minutes before kickoff. The pick is then committed to the public git history and graded against the closing line as before.

Do not hand-edit `data/picks.csv`. Agents do not edit that file. Do not hand-edit `src/data/summary.json` or `src/data/picks_view.json`. The grader writes them. Until those files exist, `/` and `/picks/` build with an empty state.

### Lock rule

The server records the submission time and rejects anything at or after 10 minutes before kickoff. A client-set commit time does not lock a pick. The pick is then committed to the public git history and graded against the closing line as before. Rejected rows stay on `/picks/` and are left out of the W-L-P, units, and CLV.

## Local checks

```sh
npm ci
npm run check
npm test
npm run lint:assets
npm run build
```

`npm test` checks `tests/fixtures/summary.json` and `tests/fixtures/picks_view.json` against a fresh grader run. That step needs [uv](https://docs.astral.sh/uv/) 0.13.7, the version pinned in `.github/workflows/ci.yml`. The numbers come from `python -m football_pipeline grade` on `pipeline/tests/fixtures/`, plus one rejected row in `tests/fixtures/b2/` so a stale close is on the page. After a grader change, regenerate the two JSON files and commit them:

```sh
python3 scripts/grade_ui_fixtures.py --write
```

`npm run build` writes the site to `dist/`. The pick JSON files are not required for that build. After that, `node scripts/verify-dist.mjs` checks `dist/` against the paths the build reads: `src/data/summary.json`, `src/data/picks_view.json`, and `src/content/games/*.json`.

When both grader files are missing, `/` and `/picks/` must show "No picks yet this season." and must not render a season summary or a `pick-{pick_id}` anchor. When either file is present, the season summary has to match `summary.json`, every row in `picks_view.json` has to appear on `/picks/` with its anchor and a units figure (never a dollar amount), and `/` shows only the picks from the current `(season, week)`. An empty `picks_view.json` still shows the no-picks line, because the list itself is empty, and it still shows the season summary when `summary.json` is present.

When `src/content/games/` has no card JSON, the home page has no card section and `dist/games/` is empty. When cards are present, each `game_id` has a page at `/games/{game_id}/`, a sparkline is drawn when `line_history` contains a spread or a total, and the home page links only to the cards in the current `(season, week)`.

If those files are already in the tree, verify-dist moves them to a temporary directory, rebuilds into `dist-empty-check/`, and runs the empty-state checks. It puts the files back, then builds the fixture picks and cards. That covers both states on a tree that already has grader output. With the files absent, `dist/` is the empty-state check and the fixture build is the data-present check.

## Cloudflare Pages

When the Pages project exists, use these settings and nothing else from this repo:

- Build command: `npm run build`
- Output directory: `dist`
- Node.js version: 22

`SITE_URL` is optional. When it is unset, the canonical origin is `https://football-film-room.pages.dev`. A trailing slash on `SITE_URL` is removed. The build does not call the network and does not read any secrets.

Pages Functions in `functions/` serve `/auth/*`, `/admin/*`, and `/api/*`. Set `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `GH_DISPATCH_TOKEN`, and `SESSION_SECRET` on the Pages project. The GitHub OAuth callback URL is `https://<site>/auth/callback`. The static build does not read those values. If any of them is missing, those routes respond with 503 and the text `not configured`.
