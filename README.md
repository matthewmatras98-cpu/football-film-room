# football-film-room

Personal football matchup analysis, weekly picks, and film notes.

The site is a static Astro build. Node 22 is required (`.nvmrc`, and `engines` in `package.json`).

## Publish a week post

Add an MDX file at `src/content/weeks/2026-wXX.mdx`. Every frontmatter field is required. There are no defaults.

```yaml
title: Week 11 notes
season: 2026
week: 11
published_at: 2026-11-14T18:00:00-07:00
summary: One or two sentences.
picks:
  - 2026_11_DET_PHI
tags:
  - rest
draft: false
```

- `published_at` is an ISO-8601 datetime with a timezone offset, as in the example. An unquoted value is fine.
- `picks` lists nflverse game ids: `YYYY_WW_AWAY_HOME`, with 2- or 3-letter abbreviations.
- `draft: false` publishes the post at `/weeks/2026-wXX/`. `draft: true` is left out of the build.
- A draft stays in this public git repo even though the site does not render it.

The two files already in `src/content/weeks/` are drafts. Do not publish anything labeled sample.

## Local checks

```sh
npm ci
npm run check
npm test
npm run lint:assets
npm run build
```

`npm run build` writes the site to `dist/`.

## Cloudflare Pages

When the Pages project exists, use these settings and nothing else from this repo:

- Build command: `npm run build`
- Output directory: `dist`
- Node.js version: 22

`SITE_URL` is optional. Set it to the `pages.dev` origin once that hostname exists. The build does not call the network and does not read any secrets.
