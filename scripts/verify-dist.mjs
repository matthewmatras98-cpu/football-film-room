import { spawn } from 'node:child_process';
import { copyFile, readFile, readdir, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CLOSE_MARKER,
  STATUS_LABEL,
  formatClv,
  formatFinal,
  formatMst,
  formatPct,
  formatPickLine,
  formatSideLine,
  formatUnits,
  formatWinPct,
  matchupLabel,
} from '../src/lib/format-picks.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const weeksDir = path.join(root, 'src/content/weeks');
const publishedFixture = path.join(root, 'tests/fixtures/published-post.mdx');
const publishedCopy = path.join(weeksDir, 'published-post.mdx');
const publishedSlug = 'published-post';
const deployDist = path.join(root, 'dist');
const tempDist = path.join(root, 'dist-published-check');
const picksDist = path.join(root, 'dist-picks-check');
const summaryFixture = path.join(root, 'tests/fixtures/summary.json');
const viewFixture = path.join(root, 'tests/fixtures/picks_view.json');
const summaryDest = path.join(root, 'src/data/summary.json');
const viewDest = path.join(root, 'src/data/picks_view.json');
const templatePath = path.join(root, 'docs/post-template.mdx');
const templateSentinel = 'b2-post-template-sentinel';

const sportsbookNames = [
  'draftkings',
  'fanduel',
  'betmgm',
  'betrivers',
  'bovada',
  'betonline',
  'lowvig',
  'mybookie',
  'betus',
];

const fullCredits = [
  'Data: nflverse (nflfastR / nflreadr), licensed CC-BY 4.0. NFL data belongs to its respective owners.',
  'Charting data: FTN Data via nflverse, licensed CC-BY-SA 4.0. Any published table derived from FTN data is also CC-BY-SA. Label those tables so readers can see it.',
  'Next Gen Stats data via nflverse; NGS is a product of the NFL.',
  'Odds data: The Odds API. Lines shown are the lower-middle line of N US books.',
];

const closingLinePhrases = [
  'floor((n-1)/2)',
  'lower-middle',
  'never an average',
  'Thin: 1-2 books',
  'Stale: the snapshot is more than 180 minutes before kickoff',
  'nflverse: no books, so the nflverse line is used',
  'Ungraded: no line from either source',
];

const disclaimer =
  'For entertainment and analysis only. Not betting advice. No sportsbook affiliation. 21+. If you or someone you know has a gambling problem, call 1-800-GAMBLER.';

function fail(message) {
  console.error(message);
  process.exitCode = 1;
  throw new Error(message);
}

async function exists(filePath) {
  try {
    await stat(filePath);
    return true;
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return false;
    }
    throw error;
  }
}

async function listHtml(dir) {
  if (!(await exists(dir))) {
    return [];
  }
  const files = [];
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listHtml(fullPath)));
    } else if (entry.isFile() && entry.name.endsWith('.html')) {
      files.push(fullPath);
    }
  }
  return files;
}

function slugFromFilename(filename) {
  return filename.replace(/\.mdx$/i, '').toLowerCase();
}

async function readWeekEntries() {
  const names = (await readdir(weeksDir)).filter((name) => name.endsWith('.mdx')).sort();
  const entries = [];
  for (const name of names) {
    const source = await readFile(path.join(weeksDir, name), 'utf8');
    const match = source.match(/^draft:\s*(true|false)\s*$/m);
    if (!match) {
      fail(`${name} is missing a draft field`);
    }
    entries.push({
      name,
      slug: slugFromFilename(name),
      draft: match[1] === 'true',
    });
  }
  return entries;
}

async function weekPage(distDir, slug) {
  const candidates = [
    path.join(distDir, 'weeks', slug, 'index.html'),
    path.join(distDir, 'weeks', `${slug}.html`),
  ];
  for (const candidate of candidates) {
    if (await exists(candidate)) {
      return candidate;
    }
  }
  return null;
}

async function assertPages(distDir, entries, { requirePublished }) {
  const htmlFiles = await listHtml(distDir);
  if (htmlFiles.length === 0) {
    fail(`${path.relative(root, distDir)} has no HTML pages`);
  }

  for (const filePath of htmlFiles) {
    const html = await readFile(filePath, 'utf8');
    const relativePath = path.relative(root, filePath);
    for (const needle of ['data-footer', '1-800-GAMBLER', 'nflverse']) {
      if (!html.includes(needle)) {
        fail(`${relativePath} is missing ${needle}`);
      }
    }
    if (/\bsample\b/i.test(html) || relativePath.toLowerCase().includes('sample')) {
      fail(`${relativePath} ships something labeled sample`);
    }
  }

  for (const entry of entries) {
    const page = await weekPage(distDir, entry.slug);
    if (entry.draft && page) {
      fail(`draft slug ${entry.slug} is present at ${path.relative(root, page)}`);
    }
    if (!entry.draft && !page) {
      fail(`published slug ${entry.slug} is missing from ${path.relative(root, distDir)}`);
    }
  }

  const methodology = path.join(distDir, 'methodology', 'index.html');
  if (!(await exists(methodology))) {
    fail('methodology/index.html is missing');
  }
  const methodologyHtml = await readFile(methodology, 'utf8');
  for (const line of [...fullCredits, disclaimer, ...closingLinePhrases]) {
    if (!methodologyHtml.includes(line)) {
      fail(`methodology is missing the full text: ${line}`);
    }
  }
  if (/exact median|quarter[- ]point|\bmedian\b/i.test(methodologyHtml)) {
    fail('methodology still describes a median or a quarter-point close');
  }

  if (requirePublished) {
    const publishedPage = await weekPage(distDir, publishedSlug);
    if (!publishedPage) {
      fail(`published slug ${publishedSlug} is missing from the temporary build`);
    }
  }
}

async function readHtml(distDir, ...parts) {
  const filePath = path.join(distDir, ...parts);
  if (!(await exists(filePath))) {
    fail(`${path.relative(root, filePath)} is missing`);
  }
  return readFile(filePath, 'utf8');
}

function rowBlock(html, pickId) {
  const marker = `id="pick-${pickId}"`;
  const start = html.indexOf(marker);
  if (start < 0) {
    fail(`picks page is missing #pick-${pickId}`);
  }
  const next = html.indexOf('id="pick-', start + marker.length);
  const footer = html.indexOf('<footer', start);
  let end = html.length;
  if (next !== -1) {
    end = Math.min(end, next);
  }
  if (footer !== -1) {
    end = Math.min(end, footer);
  }
  return html.slice(start, end);
}

function attr(html, name) {
  const match = new RegExp(`data-${name}="([^"]*)"`).exec(html);
  if (!match) {
    fail(`missing data-${name}`);
  }
  return match[1];
}

function sameNumber(actual, expected) {
  if (expected === null) {
    return actual === 'null';
  }
  return Number(actual) === expected;
}

async function assertNoDollar(distDir) {
  for (const filePath of await listHtml(distDir)) {
    const html = await readFile(filePath, 'utf8');
    if (html.includes('$') || html.includes('&#36;') || html.includes('&dollar;')) {
      fail(`${path.relative(root, filePath)} contains a dollar sign`);
    }
  }
}

async function assertNoSportsbooks(distDir) {
  for (const filePath of await listHtml(distDir)) {
    const html = (await readFile(filePath, 'utf8')).toLowerCase();
    for (const name of sportsbookNames) {
      if (html.includes(name)) {
        fail(`${path.relative(root, filePath)} names a sportsbook (${name})`);
      }
    }
  }
}

async function templateBodyLines() {
  const text = await readFile(templatePath, 'utf8');
  const match = text.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n([\s\S]*)$/);
  if (!match) {
    fail('docs/post-template.mdx is missing frontmatter');
  }
  const lines = match[1]
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  if (!lines.includes(templateSentinel)) {
    fail(`docs/post-template.mdx is missing ${templateSentinel}`);
  }
  return lines;
}

async function assertNoTemplate(distDir) {
  const lines = await templateBodyLines();
  for (const filePath of await listHtml(distDir)) {
    const html = await readFile(filePath, 'utf8');
    for (const line of lines) {
      if (html.includes(line)) {
        fail(`${path.relative(root, filePath)} contains post-template text: ${line}`);
      }
    }
  }
}

async function fixtureNeedles() {
  const view = JSON.parse(await readFile(viewFixture, 'utf8'));
  const needles = ['2099-09-20T00:00:00Z'];
  for (const row of view) {
    needles.push(row.pick_id);
    if (typeof row.note === 'string' && row.note.length >= 4) {
      needles.push(row.note);
    }
  }
  return needles;
}

async function assertNoPublishedFixture(distDir) {
  const text = await readFile(path.join(root, 'tests/fixtures/published-post.mdx'), 'utf8');
  const body = text.split(/\n---\n/).pop() ?? '';
  const lines = body
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length >= 20);
  if (lines.length === 0) {
    fail('published-post fixture has no body to check');
  }
  for (const filePath of await listHtml(distDir)) {
    const html = await readFile(filePath, 'utf8');
    for (const line of lines) {
      if (html.includes(line)) {
        fail(`${path.relative(root, filePath)} contains the published-post fixture: ${line}`);
      }
    }
  }
}

async function assertNoFixtureContent(distDir) {
  const needles = [
    ...(await fixtureNeedles()),
    'This file omits summary and must fail the build.',
  ];
  for (const filePath of await listHtml(distDir)) {
    const html = await readFile(filePath, 'utf8');
    for (const needle of needles) {
      if (html.includes(needle)) {
        fail(`${path.relative(root, filePath)} contains test fixture content: ${needle}`);
      }
    }
  }
}

async function assertEmptyPicks(distDir) {
  const home = await readHtml(distDir, 'index.html');
  const picksPage = await readHtml(distDir, 'picks', 'index.html');
  for (const [label, html] of [
    ['/', home],
    ['/picks/', picksPage],
  ]) {
    if (!html.includes('No picks yet this season.')) {
      fail(`${label} is missing the empty-state text`);
    }
    if (html.includes('data-season-summary') || html.includes('id="pick-')) {
      fail(`${label} renders pick data when the grader files are absent`);
    }
  }
}

function assertSummary(html, summary, label) {
  const record = `${summary.w}-${summary.l}-${summary.p}`;
  if (!html.includes(record)) {
    fail(`${label} is missing W-L-P ${record}`);
  }
  if (!html.includes(formatUnits(summary.units))) {
    fail(`${label} is missing units ${formatUnits(summary.units)}`);
  }
  if (!html.includes(formatClv(summary.avg_clv))) {
    fail(`${label} is missing average CLV ${formatClv(summary.avg_clv)}`);
  }
  if (!html.includes(formatWinPct(summary))) {
    fail(`${label} is missing win % ${formatWinPct(summary)}`);
  }
  if (!html.includes(formatPct(summary.pct_clv_pos))) {
    fail(`${label} is missing beating-the-close ${formatPct(summary.pct_clv_pos)}`);
  }
  if (!html.includes('pushes excluded')) {
    fail(`${label} is missing the pushes-excluded note`);
  }
  const fields = {
    w: summary.w,
    l: summary.l,
    p: summary.p,
    units: summary.units,
    'avg-clv': summary.avg_clv,
    'win-pct': summary.win_pct,
    'pct-clv': summary.pct_clv_pos,
    rejected: summary.rejected_count,
    ungraded: summary.ungraded_count,
  };
  for (const [name, expected] of Object.entries(fields)) {
    const actual = attr(html, name);
    if (!sameNumber(actual, expected)) {
      fail(`${label} data-${name} is ${actual}, summary has ${expected}`);
    }
  }
}

function assertPickRow(block, row) {
  if (!block.includes(matchupLabel(row.game_id))) {
    fail(`${row.pick_id} is missing its matchup`);
  }
  if (!block.includes(formatSideLine(row.market, row.side, row.line_at_pick))) {
    fail(`${row.pick_id} is missing the picked side and line`);
  }
  if (!block.includes(formatPickLine(row.market, row.closing_line))) {
    fail(`${row.pick_id} is missing its close`);
  }
  if (!block.includes(formatFinal(row.game_id, row.final_away, row.final_home))) {
    fail(`${row.pick_id} is missing the final`);
  }
  if (!block.includes(formatUnits(row.units_pl))) {
    fail(`${row.pick_id} units rendered as something other than ${formatUnits(row.units_pl)}`);
  }
  if (!block.includes(formatClv(row.clv_pts))) {
    fail(`${row.pick_id} CLV rendered as something other than ${formatClv(row.clv_pts)}`);
  }
  if (row.units_pl === null && (block.includes('0.000u') || block.includes('+0.000u'))) {
    fail(`${row.pick_id} shows zero units for a null result`);
  }
  const label = STATUS_LABEL[row.status];
  if (!label || !block.includes(label)) {
    fail(`${row.pick_id} is missing status ${label}`);
  }
  const marker = row.close_source ? CLOSE_MARKER[row.close_source] : undefined;
  if (marker) {
    if (!block.includes(marker.text)) {
      fail(`${row.pick_id} is missing the ${marker.text} marker`);
    }
  } else {
    for (const text of Object.values(CLOSE_MARKER).map((item) => item.text)) {
      if (block.includes(text)) {
        fail(`${row.pick_id} shows ${text} for close source ${row.close_source}`);
      }
    }
  }
  if (row.note && !block.includes(row.note)) {
    fail(`${row.pick_id} is missing its note`);
  }
  if (!block.includes('Locked (GitHub push time)')) {
    fail(`${row.pick_id} is missing the lock label`);
  }
  if (!block.includes(formatMst(row.picked_at))) {
    fail(`${row.pick_id} is missing picked_at in MST`);
  }
  if (!block.includes(formatMst(row.first_pushed_at))) {
    fail(`${row.pick_id} is missing first_pushed_at in MST`);
  }
  if (!block.includes(' MST')) {
    fail(`${row.pick_id} is missing the MST label`);
  }
  if (attr(block, 'units-pl') && !sameNumber(attr(block, 'units-pl'), row.units_pl)) {
    fail(`${row.pick_id} data-units-pl does not match the fixture`);
  }
  if (!sameNumber(attr(block, 'clv'), row.clv_pts)) {
    fail(`${row.pick_id} data-clv does not match the fixture`);
  }
  if (!sameNumber(attr(block, 'close'), row.closing_line)) {
    fail(`${row.pick_id} data-close does not match the fixture`);
  }
  if (attr(block, 'status') !== row.status) {
    fail(`${row.pick_id} data-status does not match the fixture`);
  }
  if (attr(block, 'close-source') !== (row.close_source ?? 'null')) {
    fail(`${row.pick_id} data-close-source does not match the fixture`);
  }
}

async function assertFixturePicks(distDir) {
  const summary = JSON.parse(await readFile(summaryFixture, 'utf8'));
  const view = JSON.parse(await readFile(viewFixture, 'utf8'));
  const picksHtml = await readHtml(distDir, 'picks', 'index.html');
  const homeHtml = await readHtml(distDir, 'index.html');

  assertSummary(picksHtml, summary, '/picks/');
  assertSummary(homeHtml, summary, '/');

  if (!picksHtml.includes('href="/methodology/"')) {
    fail('/picks/ is missing the methodology link');
  }
  if (!picksHtml.includes('Last graded') || !picksHtml.includes(formatMst(summary.generated_at))) {
    fail('/picks/ is missing the last-graded timestamp');
  }
  if (picksHtml.includes('No picks yet this season.') || homeHtml.includes('No picks yet this season.')) {
    fail('fixture build shows the empty state');
  }

  const rejected = view.filter((row) => row.status === 'rejected_late');
  const rejectedLabels = picksHtml.split(STATUS_LABEL.rejected_late).length - 1;
  if (rejectedLabels !== rejected.length) {
    fail(`expected ${rejected.length} "Rejected – late" labels, found ${rejectedLabels}`);
  }

  for (const row of view) {
    assertPickRow(rowBlock(picksHtml, row.pick_id), row);
  }

  const seenWeeks = [];
  for (const row of view) {
    const key = `${row.season}-${row.week}`;
    if (!seenWeeks.some((group) => group.key === key)) {
      seenWeeks.push({ key, season: row.season, week: row.week });
    }
  }
  seenWeeks.sort((a, b) => b.season - a.season || b.week - a.week);
  let weekAt = -1;
  for (const group of seenWeeks) {
    const at = picksHtml.indexOf(`Season ${group.season}, week ${group.week}`);
    if (at < 0) {
      fail(`picks page is missing season ${group.season} week ${group.week}`);
    }
    if (at < weekAt) {
      fail('picks are not grouped newest week first');
    }
    weekAt = at;
  }

  const maxWeek = Math.max(...view.map((row) => row.week));
  for (const row of view) {
    const present = homeHtml.includes(row.pick_id);
    if (row.week === maxWeek && !present) {
      fail(`home is missing current-week pick ${row.pick_id}`);
    }
    if (row.week !== maxWeek && present) {
      fail(`home fell back to older pick ${row.pick_id}`);
    }
  }
}

async function assertPublishedPickLink(distDir) {
  const page = await weekPage(distDir, publishedSlug);
  if (!page) {
    fail('published week page is missing');
  }
  const html = await readFile(page, 'utf8');
  const heading =
    html.includes("This week's picks") || html.includes('This week&#39;s picks');
  if (!html.includes('week-picks') || !heading) {
    fail("published post is missing This week's picks");
  }
  if (!html.includes('2026_11_DET_PHI')) {
    fail('unknown game id was dropped from the published post');
  }
  if (html.includes('#pick-2026_11_DET_PHI')) {
    fail('unknown game id was linked to a pick row');
  }
}

function runAstroBuild(outDir) {
  return new Promise((resolve, reject) => {
    const child = spawn('npm', ['run', 'build', '--', '--force', '--outDir', outDir], {
      cwd: root,
      stdio: 'inherit',
      env: { ...process.env, ASTRO_TELEMETRY_DISABLED: '1' },
    });
    child.on('error', reject);
    child.on('exit', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`astro build exited ${code}`));
      }
    });
  });
}

async function main() {
  if (!(await exists(deployDist))) {
    fail('dist/ is missing. Run npm run build first.');
  }

  const committed = await readWeekEntries();
  if (committed.some((entry) => !entry.draft)) {
    fail('src/content/weeks must contain drafts only. The published post stays in tests/fixtures.');
  }
  if (committed.length === 0) {
    fail('src/content/weeks has no posts');
  }

  await assertPages(deployDist, committed, { requirePublished: false });
  await assertEmptyPicks(deployDist);
  await assertNoTemplate(deployDist);
  await assertNoFixtureContent(deployDist);
  await assertNoPublishedFixture(deployDist);
  await assertNoDollar(deployDist);
  await assertNoSportsbooks(deployDist);
  console.log(`deploy dist: ${committed.length} draft slug(s) absent, footer present on every page`);

  if (await exists(publishedCopy)) {
    fail('refusing to overwrite src/content/weeks/published-post.mdx');
  }

  await copyFile(publishedFixture, publishedCopy);
  try {
    await rm(tempDist, { recursive: true, force: true });
    await runAstroBuild(tempDist);
    const withFixture = await readWeekEntries();
    const published = withFixture.find((entry) => entry.slug === publishedSlug);
    if (!published || published.draft) {
      fail('temporary published fixture was not copied as draft: false');
    }
    await assertPages(tempDist, withFixture, { requirePublished: true });
    await assertPublishedPickLink(tempDist);
    await assertNoTemplate(tempDist);
    await assertNoFixtureContent(tempDist);
    await assertNoDollar(tempDist);
    await assertNoSportsbooks(tempDist);
    console.log(`temporary build: /weeks/${publishedSlug}/ is present and drafts are absent`);
  } finally {
    await rm(publishedCopy, { force: true });
    await rm(tempDist, { recursive: true, force: true });
  }

  if ((await exists(summaryDest)) || (await exists(viewDest))) {
    fail('refusing to overwrite src/data/summary.json or src/data/picks_view.json');
  }
  await copyFile(summaryFixture, summaryDest);
  await copyFile(viewFixture, viewDest);
  try {
    await rm(picksDist, { recursive: true, force: true });
    await runAstroBuild(picksDist);
    await assertPages(picksDist, committed, { requirePublished: false });
    await assertFixturePicks(picksDist);
    await assertNoTemplate(picksDist);
    await assertNoDollar(picksDist);
    await assertNoSportsbooks(picksDist);
    console.log('fixture build: /picks/ matches the grader summary and shows every row');
  } finally {
    await rm(summaryDest, { force: true });
    await rm(viewDest, { force: true });
    await rm(picksDist, { recursive: true, force: true });
  }
}

await main();
