import { spawn } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, readdir, rename, rm, stat, writeFile } from 'node:fs/promises';
import { register } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { siteUrl } from '../src/data/site-url.mjs';
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

register(new URL('./ts-resolver.mjs', import.meta.url));

const { formatStatKind } = await import('../src/lib/format-stat.ts');
const { formatCardPick, officialCardPicks } = await import('../src/lib/card-pick.ts');
const { gameCardSchema } = await import('../src/lib/game-schema.ts');
const { currentWeek } = await import('../src/lib/current-week.ts');
const { picksViewSchema, summarySchema } = await import('../src/lib/picks-schema.ts');

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const weeksDir = path.join(root, 'src/content/weeks');
const publishedFixture = path.join(root, 'tests/fixtures/published-post.mdx');
const publishedCopy = path.join(weeksDir, 'published-post.mdx');
const publishedSlug = 'published-post';
const deployDist = path.join(root, 'dist');
const tempDist = path.join(root, 'dist-published-check');
const picksDist = path.join(root, 'dist-picks-check');
const emptyDist = path.join(root, 'dist-empty-check');
const summaryFixture = path.join(root, 'tests/fixtures/summary.json');
const viewFixture = path.join(root, 'tests/fixtures/picks_view.json');
const summaryDest = path.join(root, 'src/data/summary.json');
const viewDest = path.join(root, 'src/data/picks_view.json');
const templatePath = path.join(root, 'docs/post-template.mdx');
const templateSentinel = 'b2-post-template-sentinel';
const gamesFixtureDir = path.join(root, 'tests/fixtures/games');
const gamesContentDir = path.join(root, 'src/content/games');
const cardLinkSlug = 'card-link-check';
const cardLinkCopy = path.join(weeksDir, `${cardLinkSlug}.mdx`);
const cardLinkUnknownId = '2026_11_DET_PHI';

const sectionMarkers = [
  'header',
  'lines',
  'epa',
  'pass-rate-pace',
  'explosives',
  'qb',
  'sacks',
  'red-zone-downs',
  'injuries',
  'weather',
  'story-prompts',
  'source-footer',
];

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

const footerCreditLine =
  'Data: nflverse (CC-BY 4.0). Charting: FTN Data via nflverse (CC-BY-SA 4.0). Next Gen Stats via nflverse. Odds: The Odds API.';

const primaryNav = ['Home', 'Picks', 'Games', 'Methodology', 'About'];

const exampleMarkers = [
  '7\u20133\u20132',
  'Example data',
  'design specimen',
  'Example Week',
  'not live results',
  '+3.363',
];

const singularWeek = 'No published week yet.';
const pluralWeeks = 'No published weeks yet.';

function countOccurrences(haystack, needle) {
  let count = 0;
  let from = 0;
  while (from <= haystack.length) {
    const at = haystack.indexOf(needle, from);
    if (at < 0) {
      return count;
    }
    count += 1;
    from = at + needle.length;
  }
  return count;
}

function gradedStamp(summary) {
  return summary.w + summary.l + summary.p === 0 ? 'Last updated' : 'Last graded';
}

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

async function listFiles(dir, suffix) {
  if (!(await exists(dir))) {
    return [];
  }
  const files = [];
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listFiles(fullPath, suffix)));
    } else if (entry.isFile() && entry.name.endsWith(suffix)) {
      files.push(fullPath);
    }
  }
  return files;
}

async function listHtml(dir) {
  return listFiles(dir, '.html');
}

function sameSeasonWeek(row, key) {
  return key !== null && row.season === key.season && row.week === key.week;
}

function weekHeading(week) {
  return `Season ${week.season}, week ${week.week}`;
}

function hasWeekHeading(html, week) {
  return new RegExp(`Season ${week.season}, week ${week.week}(?!\\d)`).test(html);
}

/** Home heading follows currentWeek at this build clock. The week number is not hard-coded. */
function assertHomeWeekHeading(home, cards, current) {
  if (!current) {
    fail('cards are present but the current week is missing');
  }
  if (!hasWeekHeading(home, current)) {
    fail(`home heading is missing ${weekHeading(current)}`);
  }
  const seen = new Set();
  for (const card of cards) {
    const key = `${card.season}-${card.week}`;
    if (seen.has(key) || sameSeasonWeek(card, current)) {
      continue;
    }
    seen.add(key);
    if (hasWeekHeading(home, card)) {
      fail(`home heading shows ${weekHeading(card)}`);
    }
  }
}

async function assertGamesWeekHeading(distDir, current) {
  const gamesIndex = await readHtml(distDir, 'games', 'index.html');
  if (!new RegExp(`<h1>\\s*Week ${current.week}(?!\\d)`).test(gamesIndex)) {
    fail(`games page heading is missing Week ${current.week}`);
  }
}

async function homePickWeek(rows) {
  const cards = await loadContentCards();
  if (cards.length > 0) {
    return currentWeek(cards);
  }
  return currentWeek(rows);
}

function attribute(tag, name) {
  const match = new RegExp(`\\s${name}\\s*=\\s*("([^"]*)"|'([^']*)')`, 'i').exec(tag);
  if (!match) {
    return null;
  }
  return match[2] ?? match[3] ?? null;
}

function linkHrefs(html, rel) {
  const tags = html.match(/<link\b[^>]*>/gi) ?? [];
  const hrefs = [];
  for (const tag of tags) {
    const value = attribute(tag, 'rel');
    if (!value || !value.toLowerCase().split(/\s+/).includes(rel)) {
      continue;
    }
    hrefs.push(attribute(tag, 'href'));
  }
  return hrefs;
}

function metaContents(html, property) {
  const tags = html.match(/<meta\b[^>]*>/gi) ?? [];
  const contents = [];
  for (const tag of tags) {
    const value = attribute(tag, 'property');
    if (!value || value.toLowerCase() !== property) {
      continue;
    }
    contents.push(attribute(tag, 'content'));
  }
  return contents;
}

function sitemapListsNotFound(xml) {
  return /<loc>[^<]*\/404(?:\.html)?\/?<\/loc>/i.test(xml);
}

function sitemapListsAdminPick(xml) {
  return /<loc>[^<]*\/admin\/pick(?:\.html)?\/?<\/loc>/i.test(xml);
}

async function assertSeo(distDir) {
  const label = path.relative(root, distDir);
  const site = new URL(siteUrl());
  const notFoundPath = path.join(distDir, '404.html');
  if (!(await exists(notFoundPath))) {
    fail(`${label}/404.html is missing`);
  }
  const notFoundHtml = await readFile(notFoundPath, 'utf8');
  if (!notFoundHtml.includes('noindex')) {
    fail(`${label}/404.html is missing noindex`);
  }

  const robotsPath = path.join(distDir, 'robots.txt');
  if (!(await exists(robotsPath))) {
    fail(`${label}/robots.txt is missing`);
  }
  const robots = await readFile(robotsPath, 'utf8');
  const sitemapLine = robots
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => /^sitemap:/i.test(line));
  if (!sitemapLine) {
    fail(`${label}/robots.txt is missing a Sitemap line`);
  }
  const expectedSitemap = new URL('/sitemap-index.xml', site).href;
  const actualSitemap = sitemapLine.replace(/^sitemap:\s*/i, '').trim();
  if (actualSitemap !== expectedSitemap) {
    fail(`${label}/robots.txt Sitemap is ${actualSitemap}, expected ${expectedSitemap}`);
  }

  if (!(await exists(path.join(distDir, 'sitemap-index.xml')))) {
    fail(`${label}/sitemap-index.xml is missing`);
  }
  const xmlFiles = await listFiles(distDir, '.xml');
  if (xmlFiles.length === 0) {
    fail(`${label} has no sitemap xml`);
  }
  for (const filePath of xmlFiles) {
    const xml = await readFile(filePath, 'utf8');
    if (sitemapListsNotFound(xml)) {
      fail(`${path.relative(root, filePath)} includes the 404 page`);
    }
    if (sitemapListsAdminPick(xml)) {
      fail(`${path.relative(root, filePath)} includes /admin/pick`);
    }
  }

  const adminPickCandidates = [
    path.join(distDir, 'admin', 'pick', 'index.html'),
    path.join(distDir, 'admin', 'pick.html'),
  ];
  let adminPick = null;
  for (const candidate of adminPickCandidates) {
    if (await exists(candidate)) {
      adminPick = candidate;
    }
  }
  if (!adminPick) {
    fail(`${label} is missing /admin/pick`);
  }
  const adminHtml = await readFile(adminPick, 'utf8');
  if (!adminHtml.includes('noindex')) {
    fail(`${label}/admin/pick is missing noindex`);
  }

  const htmlFiles = await listHtml(distDir);
  if (htmlFiles.length === 0) {
    fail(`${label} has no HTML pages`);
  }
  for (const filePath of htmlFiles) {
    const html = await readFile(filePath, 'utf8');
    const relativePath = path.relative(root, filePath);
    const canonicals = linkHrefs(html, 'canonical');
    const ogUrls = metaContents(html, 'og:url');
    if (canonicals.length !== 1) {
      fail(`${relativePath} has ${canonicals.length} canonical tags`);
    }
    if (ogUrls.length !== 1) {
      fail(`${relativePath} has ${ogUrls.length} og:url tags`);
    }
    for (const href of [canonicals[0], ogUrls[0]]) {
      if (typeof href !== 'string' || href.length === 0) {
        fail(`${relativePath} is missing a canonical or og:url value`);
      }
      let url;
      try {
        url = new URL(href);
      } catch {
        fail(`${relativePath} has a non-absolute canonical or og:url (${href})`);
      }
      if (url.origin !== site.origin) {
        fail(`${relativePath} uses origin ${url.origin}, expected ${site.origin}`);
      }
    }
  }
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

function collapsedText(html) {
  return html.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
}

function isGamesIndex(distDir, filePath) {
  return path.relative(path.join(distDir, 'games'), filePath) === 'index.html';
}

async function assertWeekEmptyCopy(distDir) {
  for (const filePath of await listHtml(distDir)) {
    const html = await readFile(filePath, 'utf8');
    if (html.includes(singularWeek) && html.includes(pluralWeeks)) {
      fail(`${path.relative(root, filePath)} contains both empty-week lines`);
    }
  }
  const home = await readHtml(distDir, 'index.html');
  const start = home.indexOf('data-dispatches');
  if (start < 0) {
    fail('home is missing the dispatches rail');
  }
  const end = home.indexOf('</aside>', start);
  const rail = home.slice(start, end === -1 ? home.length : end);
  const emptyLines = countOccurrences(rail, singularWeek) + countOccurrences(rail, pluralWeeks);
  if (emptyLines > 1) {
    fail(`home rail shows ${emptyLines} empty-state lines`);
  }
}

async function assertGameProgram(distDir, htmlFiles) {
  const label = path.relative(root, distDir);
  for (const filePath of htmlFiles) {
    const html = await readFile(filePath, 'utf8');
    const relativePath = path.relative(root, filePath);
    const lowered = html.toLowerCase();
    if (lowered.includes('fonts.googleapis.com') || lowered.includes('fonts.gstatic.com')) {
      fail(`${relativePath} references a Google Fonts host`);
    }
    for (const marker of exampleMarkers) {
      if (html.includes(marker)) {
        fail(`${relativePath} ships example data: ${marker}`);
      }
    }
    const footerStart = html.indexOf('data-footer');
    const footerEnd = html.indexOf('</footer>', footerStart);
    if (footerStart < 0 || footerEnd < 0) {
      fail(`${relativePath} is missing the site footer`);
    }
    const footerHtml = html.slice(footerStart, footerEnd);
    const footerText = collapsedText(footerHtml);
    if (!footerText.includes(footerCreditLine)) {
      fail(`${relativePath} footer is missing the credit line`);
    }
    if (!footerText.includes(disclaimer)) {
      fail(`${relativePath} footer is missing the disclaimer`);
    }
    if (!footerHtml.includes('href="/methodology/"') || !footerHtml.includes('>Full credits<')) {
      fail(`${relativePath} footer Full credits link is not /methodology/`);
    }
    if (!/class="nowrap"[^>]*>\s*1-800-GAMBLER\s*</.test(footerHtml)) {
      fail(`${relativePath} does not keep 1-800-GAMBLER on one line`);
    }
    const nav = html.match(/<nav\b[^>]*aria-label="Primary"[^>]*>([\s\S]*?)<\/nav>/i);
    if (!nav) {
      fail(`${relativePath} is missing the primary nav`);
    }
    const labels = [...nav[1].matchAll(/<a\b[^>]*>([^<]*)<\/a>/gi)].map((match) => match[1].trim());
    if (labels.join('|') !== primaryNav.join('|')) {
      fail(`${relativePath} nav is ${labels.join(', ') || 'empty'}`);
    }
  }

  for (const filePath of await listFiles(distDir, '.css')) {
    const css = (await readFile(filePath, 'utf8')).toLowerCase();
    if (css.includes('fonts.googleapis.com') || css.includes('fonts.gstatic.com')) {
      fail(`${path.relative(root, filePath)} references a Google Fonts host`);
    }
  }

  const gamesIndex = path.join(distDir, 'games', 'index.html');
  if (!(await exists(gamesIndex))) {
    fail(`${label}/games/index.html is missing`);
  }
  const xmlFiles = await listFiles(distDir, '.xml');
  const listed = [];
  for (const filePath of xmlFiles) {
    const xml = await readFile(filePath, 'utf8');
    if (/<loc>[^<]*\/games\/(?:index\.html)?<\/loc>/i.test(xml)) {
      listed.push(path.relative(root, filePath));
    }
  }
  if (listed.length === 0) {
    fail(`${label} sitemap does not list /games/`);
  }
}

async function assertPages(distDir, entries, { requirePublished }) {
  await assertSeo(distDir);
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

  await assertGameProgram(distDir, htmlFiles);
  await assertWeekEmptyCopy(distDir);

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

function rowBlock(html, pickId, label = 'picks page') {
  const marker = `id="pick-${pickId}"`;
  const start = html.indexOf(marker);
  if (start < 0) {
    fail(`${label} is missing #pick-${pickId}`);
  }
  // Each pick is its own tbody. The home page keeps the season record after that
  // table, so a slice that runs to the footer would treat "0-0-0 · 0.000u" as
  // this row's units.
  const next = html.indexOf('id="pick-', start + marker.length);
  const footer = html.indexOf('<footer', start);
  const tbodyEnd = html.indexOf('</tbody>', start);
  let end = html.length;
  if (next !== -1) {
    end = Math.min(end, next);
  }
  if (footer !== -1) {
    end = Math.min(end, footer);
  }
  if (tbodyEnd !== -1) {
    end = Math.min(end, tbodyEnd + '</tbody>'.length);
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

async function gameFixtureNeedles() {
  const names = (await readdir(gamesFixtureDir)).filter((name) => name.endsWith('.json'));
  const needles = ['b3-fixture-sentinel', 'b3-malformed-card'];
  for (const name of names) {
    const data = JSON.parse(await readFile(path.join(gamesFixtureDir, name), 'utf8'));
    if (typeof data.game_id === 'string') {
      needles.push(data.game_id);
    }
  }
  return needles;
}

function hasNullStat(value) {
  if (!value || typeof value !== 'object') {
    return false;
  }
  if (
    Object.prototype.hasOwnProperty.call(value, 'value') &&
    Object.prototype.hasOwnProperty.call(value, 'source') &&
    value.value === null
  ) {
    return true;
  }
  return Object.values(value).some((item) => hasNullStat(item));
}

function section(html, name) {
  const marker = `data-section="${name}"`;
  const start = html.indexOf(marker);
  if (start < 0) {
    fail(`missing ${marker}`);
  }
  let end = html.length;
  const next = html.indexOf('data-section="', start + marker.length);
  if (next !== -1) {
    end = next;
  }
  const siteFooter = html.indexOf('data-footer', start);
  if (siteFooter !== -1 && siteFooter < end) {
    end = siteFooter;
  }
  return html.slice(start, end);
}

function weekCardsHeading(html) {
  return /This week(?:'|&#39;|&#x27;|’)s cards/.test(html);
}

async function loadGameFixtures() {
  const names = (await readdir(gamesFixtureDir)).filter((name) => name.endsWith('.json')).sort();
  const valid = [];
  const invalid = [];
  for (const name of names) {
    const data = JSON.parse(await readFile(path.join(gamesFixtureDir, name), 'utf8'));
    const parsed = gameCardSchema.safeParse(data);
    if (parsed.success) {
      valid.push({ name, card: parsed.data });
    } else {
      invalid.push(name);
    }
  }
  if (invalid.length !== 1 || invalid[0] !== 'malformed.json') {
    fail(`expected only malformed.json to fail the card schema, got ${invalid.join(', ') || 'none'}`);
  }
  if (valid.length < 2) {
    fail('expected at least two valid card fixtures');
  }
  const cards = valid.map((entry) => entry.card);
  const modes = new Set(cards.map((card) => card.sections.weather.mode));
  for (const mode of ['outdoor', 'indoor', 'retractable', 'unavailable']) {
    if (!modes.has(mode)) {
      fail(`card fixtures are missing weather mode ${mode}`);
    }
  }
  const roofs = new Set(cards.map((card) => card.roof));
  for (const roof of ['outdoors', 'dome', 'retractable', 'unknown']) {
    if (!roofs.has(roof)) {
      fail(`card fixtures are missing roof ${roof}`);
    }
  }
  if (!cards.some((card) => card.line_history.length > 0)) {
    fail('card fixtures are missing a line history');
  }
  if (!cards.some((card) => card.line_history.length === 0 && card.line_open === null && card.line_latest === null)) {
    fail('card fixtures are missing an empty line history');
  }
  if (!cards.some((card) => card.sections.injuries.home.length === 0 && card.sections.injuries.away.length === 0)) {
    fail('card fixtures are missing empty injuries');
  }
  if (!cards.some((card) => card.sections.injuries.home.length + card.sections.injuries.away.length > 0)) {
    fail('card fixtures are missing an injury');
  }
  if (!cards.some((card) => hasNullStat(card))) {
    fail('card fixtures are missing a null StatNumber');
  }
  if (!cards.every((card) => card.sections.story_prompts.length === 3)) {
    fail('card fixtures must have three story prompts');
  }
  return valid;
}

async function assertEmptyCards(distDir) {
  const home = await readHtml(distDir, 'index.html');
  if (home.includes('data-week-cards') || weekCardsHeading(home)) {
    fail(`${path.relative(root, distDir)} shows a card section with zero cards`);
  }
  const pages = await listHtml(path.join(distDir, 'games'));
  const extras = pages.filter((filePath) => !isGamesIndex(distDir, filePath));
  if (extras.length > 0) {
    fail(`${path.relative(root, distDir)} contains game card pages with zero cards`);
  }
  if (!pages.some((filePath) => isGamesIndex(distDir, filePath))) {
    fail(`${path.relative(root, distDir)} is missing /games/index.html`);
  }
  const gamesIndex = await readHtml(distDir, 'games', 'index.html');
  if (!gamesIndex.includes('No published weeks yet.')) {
    fail(`${path.relative(root, distDir)} /games/ is missing the no-week text`);
  }
}

function gameItem(html, gameId) {
  const marker = `data-game-id="${gameId}"`;
  const start = html.indexOf(marker);
  if (start < 0) {
    fail(`home is missing data-game-id="${gameId}"`);
  }
  const end = html.indexOf('</li>', start);
  return html.slice(start, end === -1 ? html.length : end);
}

function cardLinkSource(card) {
  return `---
title: Card link check
season: ${card.season}
week: ${card.week}
published_at: "2099-09-18T12:00:00Z"
summary: A temporary post that links a known card and an unknown game id.
picks:
  - ${card.game_id}
  - ${cardLinkUnknownId}
tags: []
draft: false
---

Temporary card link check.
`;
}

async function assertFixtureCards(distDir, fixtures, picks, knownGameId) {
  const cards = fixtures.map((entry) => entry.card);
  const current = currentWeek(cards);
  const home = await readHtml(distDir, 'index.html');
  if (!home.includes('data-week-cards') || !weekCardsHeading(home)) {
    fail("home is missing This week's cards");
  }
  assertHomeWeekHeading(home, cards, current);
  await assertGamesWeekHeading(distDir, current);

  for (const card of cards) {
    const page = await readHtml(distDir, 'games', card.game_id, 'index.html');
    const relative = `games/${card.game_id}/index.html`;
    for (const marker of sectionMarkers) {
      if (!page.includes(`data-section="${marker}"`)) {
        fail(`${relative} is missing data-section="${marker}"`);
      }
    }
    if (!page.includes('n/a (source unavailable)')) {
      fail(`${relative} is missing n/a (source unavailable)`);
    }
    if (!page.includes('On defense, lower EPA is better.')) {
      fail(`${relative} is missing the defense EPA note`);
    }
    if (/<img\b/i.test(page)) {
      fail(`${relative} contains an img`);
    }
    if (/recharts|chart\.js|chartjs|highcharts|plotly|vega-embed|apexcharts|\bnivo\b/i.test(page)) {
      fail(`${relative} references a chart library`);
    }
    const sourceFooter = section(page, 'source-footer');
    if (/ftn/i.test(sourceFooter)) {
      fail(`${relative} credits FTN on the card`);
    }
    for (const source of card.sources) {
      if (!sourceFooter.includes(`data-source="${source}"`)) {
        fail(`${relative} source footer is missing ${source}`);
      }
    }
    if (!sourceFooter.includes(`Data through week ${card.data_through_week}`)) {
      fail(`${relative} is missing the data-through line`);
    }
    if (!sourceFooter.includes('MST')) {
      fail(`${relative} source footer is missing MST`);
    }
    for (const needle of ['data-footer', 'nflverse', '21+', '1-800-GAMBLER']) {
      if (!page.includes(needle)) {
        fail(`${relative} is missing the site footer marker ${needle}`);
      }
    }

    const nullStats = [
      ...page.matchAll(
        /<span class="stat" data-value="null" data-source="[^"]*">\s*<span class="stat-value">([^<]*)<\/span>/g,
      ),
    ];
    if (hasNullStat(card) && nullStats.length === 0) {
      fail(`${relative} did not render a null stat`);
    }
    for (const match of nullStats) {
      if (match[1] !== 'n/a (source unavailable)') {
        fail(`${relative} rendered a null stat as ${match[1]}`);
      }
    }

    assertSparkline(section(page, 'lines'), card, relative);

    const epa = section(page, 'epa');
    if (!epa.includes('data-chart="epa-bars"') || !epa.includes('<svg')) {
      fail(`${relative} is missing the EPA bars`);
    }
    const groups = [...epa.matchAll(/<g data-series="([^"]+)" data-value="([^"]*)">([\s\S]*?)<\/g>/g)];
    if (groups.length === 0) {
      fail(`${relative} EPA chart has no series`);
    }
    for (const group of groups) {
      const value = group[2];
      const body = group[3] ?? '';
      if (value === 'null' && body.includes('<rect')) {
        fail(`${relative} drew an EPA bar for a null value`);
      }
      if (value !== 'null' && !body.includes('<rect')) {
        fail(`${relative} omitted an EPA bar for ${group[1]}`);
      }
    }

    const weatherHtml = section(page, 'weather');
    const weather = card.sections.weather;
    if (weather.mode === 'indoor') {
      if (!weatherHtml.includes('Indoors (dome)')) {
        fail(`${relative} indoor weather is missing Indoors (dome)`);
      }
      if (
        weatherHtml.includes('Retractable roof: status not confirmed') ||
        weatherHtml.includes('data-value=') ||
        weatherHtml.includes('\u00b0') ||
        weatherHtml.includes('mph') ||
        weatherHtml.includes('%')
      ) {
        fail(`${relative} indoor weather rendered numbers`);
      }
    } else if (weather.mode === 'retractable') {
      if (!weatherHtml.includes('Retractable roof: status not confirmed')) {
        fail(`${relative} is missing the retractable roof note`);
      }
      if (!weatherHtml.includes(formatStatKind(weather.temp_f, 'temp'))) {
        fail(`${relative} retractable weather is missing the temperature`);
      }
    } else if (weather.mode === 'outdoor') {
      if (
        weatherHtml.includes('Indoors (dome)') ||
        weatherHtml.includes('Retractable roof: status not confirmed')
      ) {
        fail(`${relative} outdoor weather used an indoor label`);
      }
      const precip = formatStatKind(weather.precip_prob_pct, 'precip');
      if (!weatherHtml.includes(precip)) {
        fail(`${relative} is missing precipitation ${precip}`);
      }
      if (weatherHtml.includes('3000%') || weatherHtml.includes('3000.0%')) {
        fail(`${relative} treated precip_prob_pct as a fraction`);
      }
    } else if (weather.mode === 'unavailable') {
      if (!weatherHtml.includes('n/a (source unavailable)')) {
        fail(`${relative} unavailable weather is missing n/a`);
      }
      if (weatherHtml.includes('Indoors (dome)') || weatherHtml.includes('Retractable roof: status not confirmed')) {
        fail(`${relative} unavailable weather used a roof label`);
      }
    }

    const rate = formatStatKind(card.sections.pass_rate_pace.home.neutral_ed_pass_rate, 'rate');
    if (!page.includes(rate)) {
      fail(`${relative} is missing pass rate ${rate}`);
    }
    const cpoe = formatStatKind(card.sections.qb.home.cpoe, 'cpoe');
    if (!page.includes(cpoe)) {
      fail(`${relative} is missing CPOE ${cpoe}`);
    }
    if (!page.includes(`Roof: ${card.roof}`)) {
      fail(`${relative} is missing roof ${card.roof}`);
    }

    assertCardOnHome(home, card, current);
    if (sameSeasonWeek(card, current)) {
      const item = gameItem(home, card.game_id);
      const matches = picks.filter((row) => row.game_id === card.game_id);
      const official = officialCardPicks(matches);
      if (official.length === 0) {
        if (!item.includes('No official pick')) {
          fail(`home ${card.game_id} is missing No official pick`);
        }
        if (item.includes('pick-badge') || item.includes('<details')) {
          fail(`home ${card.game_id} rendered a pick or write-up with no official pick`);
        }
      } else if (item.includes('No official pick') || item.includes('pick-badge')) {
        fail(`home ${card.game_id} still uses the empty-pick treatment`);
      } else {
        for (const row of official) {
          const text = formatCardPick(row);
          if (!item.includes(text)) {
            fail(`home is missing ${text} for ${row.pick_id}`);
          }
        }
        if (item.includes('<details')) {
          fail(`home ${card.game_id} rendered a write-up without a write-up file`);
        }
      }
    }

    const injuries = section(page, 'injuries');
    if (card.injuries_status === 'pending') {
      if (!injuries.includes('Injury report not posted yet')) {
        fail(`${relative} pending injury report is missing the not-posted line`);
      }
      if (injuries.includes('None listed.')) {
        fail(`${relative} pending injury report rendered an empty list`);
      }
    } else if (injuries.includes('Injury report not posted yet')) {
      fail(`${relative} reported injury list used the pending line`);
    } else if (card.sections.injuries.home.length === 0 && card.sections.injuries.away.length === 0) {
      const listed = injuries.split('None listed.').length - 1;
      if (listed < 2) {
        fail(`${relative} did not show an empty injury list for both teams`);
      }
    }
  }

  const linkedWeek = await weekPage(distDir, cardLinkSlug);
  if (!linkedWeek) {
    fail('card link week page is missing');
  }
  const weekHtml = await readFile(linkedWeek, 'utf8');
  if (!weekHtml.includes(`/games/${knownGameId}/`)) {
    fail('week post did not link to the known card');
  }
  if (!weekHtml.includes(cardLinkUnknownId)) {
    fail('unknown game id was dropped from the week post');
  }
  if (weekHtml.includes(`/games/${cardLinkUnknownId}`)) {
    fail('unknown game id was linked to a card');
  }
  if (weekHtml.includes(`#pick-${cardLinkUnknownId}`)) {
    fail('unknown game id was linked to a pick row');
  }
  for (const row of picks.filter((row) => row.game_id === knownGameId)) {
    if (!weekHtml.includes(`/picks/#pick-${row.pick_id}`)) {
      fail(`week post dropped the pick link for ${row.pick_id}`);
    }
  }
}

async function assertNoFixtureContent(distDir) {
  const needles = [
    ...(await fixtureNeedles()),
    ...(await gameFixtureNeedles()),
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

function schemaIssues(error) {
  const issues = error && Array.isArray(error.issues) ? error.issues : [];
  if (issues.length === 0) {
    return 'invalid';
  }
  return issues.map((issue) => `${issue.path.join('.') || '(root)'}: ${issue.message}`).join('; ');
}

function assertSparkline(lines, card, relative) {
  const drawable = card.line_history.some((point) => point.home_spread !== null || point.total !== null);
  if (!drawable) {
    if (lines.includes('<svg') || lines.includes('data-chart="sparkline"')) {
      fail(`${relative} drew a sparkline for an empty line history`);
    }
    if (!lines.includes('n/a (source unavailable)')) {
      fail(`${relative} empty line history is missing the unavailable note`);
    }
    return;
  }
  if (!lines.includes('<svg') || !lines.includes('data-chart="sparkline"')) {
    fail(`${relative} is missing the line sparkline`);
  }
}

function weekCardsSection(home) {
  const start = home.indexOf('data-week-cards');
  if (start < 0) {
    return '';
  }
  const end = home.indexOf('</section>', start);
  return end === -1 ? home.slice(start) : home.slice(start, end);
}

function assertCardOnHome(home, card, current) {
  // Judge the card list, not every /games/ link. A pick from an earlier week
  // still links to its game page while this week's cards are a later week.
  const onHome = weekCardsSection(home).includes(`data-game-id="${card.game_id}"`);
  if (sameSeasonWeek(card, current) && !onHome) {
    fail(`home is missing current-week card ${card.game_id}`);
  }
  if (!sameSeasonWeek(card, current) && onHome) {
    fail(`home fell back to older card ${card.game_id}`);
  }
}

async function readJsonIfPresent(filePath) {
  if (!(await exists(filePath))) {
    return null;
  }
  return JSON.parse(await readFile(filePath, 'utf8'));
}

function parseSummaryFile(data) {
  const parsed = summarySchema.safeParse(data);
  if (!parsed.success) {
    fail(`src/data/summary.json does not match the grader contract: ${schemaIssues(parsed.error)}`);
  }
  return parsed.data;
}

function parseViewFile(data) {
  const parsed = picksViewSchema.safeParse(data);
  if (!parsed.success) {
    fail(`src/data/picks_view.json does not match the grader contract: ${schemaIssues(parsed.error)}`);
  }
  return parsed.data;
}

async function loadContentCards() {
  const names = (await readdir(gamesContentDir)).filter((name) => name.endsWith('.json')).sort();
  const cards = [];
  for (const name of names) {
    const data = JSON.parse(await readFile(path.join(gamesContentDir, name), 'utf8'));
    const parsed = gameCardSchema.safeParse(data);
    if (!parsed.success) {
      fail(`src/content/games/${name} does not match the card schema: ${schemaIssues(parsed.error)}`);
    }
    const expectedId = name.replace(/\.json$/i, '');
    if (parsed.data.game_id !== expectedId) {
      fail(`src/content/games/${name} declares game_id "${parsed.data.game_id}"`);
    }
    cards.push(parsed.data);
  }
  return cards;
}

function gamePageId(distDir, filePath) {
  const relative = path.relative(path.join(distDir, 'games'), filePath);
  const parsed = path.parse(relative);
  if (parsed.base === 'index.html') {
    return parsed.dir.split(path.sep)[0];
  }
  return parsed.name;
}

async function assertPresentCards(distDir, cards) {
  const home = await readHtml(distDir, 'index.html');
  const current = currentWeek(cards);
  if (!home.includes('data-week-cards') || !weekCardsHeading(home)) {
    fail("home is missing This week's cards");
  }
  assertHomeWeekHeading(home, cards, current);
  await assertGamesWeekHeading(distDir, current);
  const pages = (await listHtml(path.join(distDir, 'games'))).filter(
    (filePath) => !isGamesIndex(distDir, filePath),
  );
  if (!(await exists(path.join(distDir, 'games', 'index.html')))) {
    fail(`${path.relative(root, distDir)} is missing /games/index.html`);
  }
  const pageIds = pages.map((filePath) => gamePageId(distDir, filePath));
  if (pages.length !== cards.length) {
    fail(
      `${path.relative(root, distDir)} has ${pages.length} game card page(s), source has ${cards.length} card(s)`,
    );
  }
  for (const card of cards) {
    if (!pageIds.includes(card.game_id)) {
      fail(`missing /games/${card.game_id}/`);
    }
    const relative = `games/${card.game_id}/index.html`;
    const page = await readHtml(distDir, 'games', card.game_id, 'index.html');
    assertSparkline(section(page, 'lines'), card, relative);
    assertCardOnHome(home, card, current);
  }
}

function assertRenderedPick(html, row, where) {
  const block = rowBlock(html, row.pick_id, where);
  assertPickRow(block, row);
  const units = formatUnits(row.units_pl);
  if (!block.includes(units)) {
    fail(`${where} ${row.pick_id} is missing units ${units}`);
  }
  if (units.includes('$') || block.includes('$') || block.includes('&#36;') || block.includes('&dollar;')) {
    fail(`${where} ${row.pick_id} renders units as dollars`);
  }
}

async function assertPresentPicks(distDir, summary, view) {
  const home = await readHtml(distDir, 'index.html');
  const picksPage = await readHtml(distDir, 'picks', 'index.html');
  if (!picksPage.includes('href="/methodology/"')) {
    fail('/picks/ is missing the methodology link');
  }
  if (summary === null) {
    for (const [label, html] of [
      ['/', home],
      ['/picks/', picksPage],
    ]) {
      if (html.includes('data-season-summary')) {
        fail(`${label} renders a season summary without summary.json`);
      }
    }
  } else {
    assertSummary(picksPage, summary, '/picks/');
    assertSummary(home, summary, '/');
    const stamp = gradedStamp(summary);
    if (!picksPage.includes(stamp) || !picksPage.includes(formatMst(summary.generated_at))) {
      fail(`/picks/ is missing the ${stamp} timestamp`);
    }
    const units = formatUnits(summary.units);
    if (!home.includes(units) || !picksPage.includes(units)) {
      fail(`season units ${units} are missing`);
    }
  }

  if (view.length === 0) {
    for (const [label, html] of [
      ['/', home],
      ['/picks/', picksPage],
    ]) {
      if (!html.includes('No picks yet this season.')) {
        fail(`${label} is missing the empty-list text`);
      }
      if (html.includes('id="pick-')) {
        fail(`${label} renders a pick row when picks_view.json has no rows`);
      }
    }
    return;
  }

  if (home.includes('No picks yet this season.') || picksPage.includes('No picks yet this season.')) {
    fail('pick pages show the empty state while picks_view.json has rows');
  }
  assertPicksPageCards(picksPage, view);
  const current = await homePickWeek(view);
  for (const row of view) {
    const anchor = `id="pick-${row.pick_id}"`;
    if (sameSeasonWeek(row, current)) {
      if (!home.includes(anchor)) {
        fail(`home is missing current-week pick ${row.pick_id}`);
      }
      assertRenderedPick(home, row, '/');
    } else if (home.includes(row.pick_id)) {
      fail(`home fell back to older pick ${row.pick_id}`);
    }
  }
}

/**
 * The site reads summary.json and picks_view.json with import.meta.glob, and
 * game cards from src/content/games. Missing grader files are the empty state.
 * A present picks file with zero rows still renders the summary and the
 * empty-list line. Cards follow the same split.
 */
async function assertSourceState(distDir) {
  const summaryData = await readJsonIfPresent(summaryDest);
  const viewData = await readJsonIfPresent(viewDest);
  if (summaryData === null && viewData === null) {
    await assertEmptyPicks(distDir);
  } else {
    await assertPresentPicks(
      distDir,
      summaryData === null ? null : parseSummaryFile(summaryData),
      viewData === null ? [] : parseViewFile(viewData),
    );
  }
  const cards = await loadContentCards();
  if (cards.length === 0) {
    await assertEmptyCards(distDir);
  } else {
    await assertPresentCards(distDir, cards);
  }
}

async function parkLiveData(parked) {
  const files = [];
  if (await exists(summaryDest)) {
    files.push(summaryDest);
  }
  if (await exists(viewDest)) {
    files.push(viewDest);
  }
  const gameNames = (await readdir(gamesContentDir)).filter((name) => name.endsWith('.json')).sort();
  for (const name of gameNames) {
    files.push(path.join(gamesContentDir, name));
  }
  if (files.length === 0) {
    return;
  }
  const dir = await mkdtemp(path.join(os.tmpdir(), 'verify-dist-'));
  for (const filePath of files) {
    const dest = path.join(dir, `${parked.length}-${path.basename(filePath)}`);
    await rename(filePath, dest);
    parked.push({ from: dest, to: filePath });
  }
  console.log(`parked ${parked.length} live grader file(s) and game card(s) in ${dir}`);
}

async function restoreParked(parked) {
  for (const entry of [...parked].reverse()) {
    await mkdir(path.dirname(entry.to), { recursive: true });
    if (await exists(entry.to)) {
      await rm(entry.to, { force: true });
    }
    await rename(entry.from, entry.to);
  }
  if (parked.length > 0) {
    console.log(`restored ${parked.length} live grader file(s) and game card(s)`);
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

function cardLineText(row, line) {
  return formatCardPick({ ...row, line_at_pick: line, result: null });
}

/** /picks/ is one program card per pick row. Home still uses the ledger table. */
function assertPicksPageCards(html, view) {
  if (/<table\b[^>]*\bclass="picks"/.test(html)) {
    fail('/picks/ still renders the ledger table');
  }
  const cards = html.match(/<li\b[^>]*\bclass="game-card"/g) ?? [];
  if (cards.length !== view.length) {
    fail(`/picks/ has ${cards.length} cards, expected ${view.length}`);
  }
  const anchors = html.match(/id="pick-[^"]+"/g) ?? [];
  if (anchors.length !== view.length) {
    fail(`/picks/ has ${anchors.length} pick anchors, expected ${view.length}`);
  }
  for (const row of view) {
    const block = rowBlock(html, row.pick_id, '/picks/');
    if (!block.includes('class="game-card"')) {
      fail(`/picks/ #pick-${row.pick_id} is not a card`);
    }
    if (!block.includes(matchupLabel(row.game_id))) {
      fail(`/picks/ ${row.pick_id} is missing its matchup`);
    }
    if (!block.includes(formatMst(row.picked_at))) {
      fail(`/picks/ ${row.pick_id} is missing picked_at in MST`);
    }
    if (!block.includes(`Status ${row.status}`)) {
      fail(`/picks/ ${row.pick_id} is missing status ${row.status}`);
    }
    if (row.closing_line !== null) {
      const closed = cardLineText(row, row.closing_line);
      if (!block.includes(`Closed ${closed}`)) {
        fail(`/picks/ ${row.pick_id} is missing close ${closed}`);
      }
      const marker = row.close_source ? CLOSE_MARKER[row.close_source] : undefined;
      if (marker && !block.includes(marker.text)) {
        fail(`/picks/ ${row.pick_id} is missing the ${marker.text} marker`);
      }
      if (!marker) {
        for (const text of Object.values(CLOSE_MARKER).map((item) => item.text)) {
          if (block.includes(text)) {
            fail(`/picks/ ${row.pick_id} shows ${text} for close source ${row.close_source}`);
          }
        }
      }
    }
    if (row.clv_pts !== null && !block.includes(`CLV ${formatClv(row.clv_pts)}`)) {
      fail(`/picks/ ${row.pick_id} is missing CLV ${formatClv(row.clv_pts)}`);
    }
    if (row.result) {
      const word = row.result === 'P' ? 'Push' : row.result;
      if (!block.includes(`Result ${word}`)) {
        fail(`/picks/ ${row.pick_id} is missing result ${word}`);
      }
    }
    if (row.status === 'void' || row.status === 'rejected_late') {
      const taken = cardLineText(row, row.line_at_pick);
      if (!block.includes(`Pick ${taken}`)) {
        fail(`/picks/ ${row.pick_id} is missing the original pick ${taken}`);
      }
      const label = row.status === 'void' ? STATUS_LABEL.void : STATUS_LABEL.rejected_late;
      if (!block.includes(label)) {
        fail(`/picks/ ${row.pick_id} badge is missing ${label}`);
      }
    } else if (!block.includes(formatCardPick(row))) {
      fail(`/picks/ ${row.pick_id} is missing ${formatCardPick(row)}`);
    }
    const links = block.match(/<a\b[^>]*>[\s\S]*?<\/a>/gi) ?? [];
    for (const link of links) {
      if (link.includes('<details')) {
        fail(`/picks/ ${row.pick_id} nests a write-up inside a link`);
      }
    }
    if (block.includes('$') || block.includes('&#36;') || block.includes('&dollar;')) {
      fail(`/picks/ ${row.pick_id} renders dollars`);
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
  if (!block.includes('Locked at')) {
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
  const stamp = gradedStamp(summary);
  if (!picksHtml.includes(stamp) || !picksHtml.includes(formatMst(summary.generated_at))) {
    fail(`/picks/ is missing the ${stamp} timestamp`);
  }
  if (picksHtml.includes('No picks yet this season.') || homeHtml.includes('No picks yet this season.')) {
    fail('fixture build shows the empty state');
  }

  const rejected = view.filter((row) => row.status === 'rejected_late');
  const rejectedLabels = picksHtml.split(STATUS_LABEL.rejected_late).length - 1;
  if (rejectedLabels !== rejected.length) {
    fail(`expected ${rejected.length} "Rejected – late" labels, found ${rejectedLabels}`);
  }

  assertPicksPageCards(picksHtml, view);

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

  const current = await homePickWeek(view);
  for (const row of view) {
    const present = homeHtml.includes(row.pick_id);
    if (sameSeasonWeek(row, current) && !present) {
      fail(`home is missing current-week pick ${row.pick_id}`);
    }
    if (!sameSeasonWeek(row, current) && present) {
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
  await assertSourceState(deployDist);
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
    await assertSourceState(tempDist);
    await assertNoTemplate(tempDist);
    await assertNoFixtureContent(tempDist);
    await assertNoDollar(tempDist);
    await assertNoSportsbooks(tempDist);
    console.log(`temporary build: /weeks/${publishedSlug}/ is present and drafts are absent`);
  } finally {
    await rm(publishedCopy, { force: true });
    await rm(tempDist, { recursive: true, force: true });
  }

  const parked = [];
  try {
    await parkLiveData(parked);
    if (parked.length > 0) {
      await rm(emptyDist, { recursive: true, force: true });
      try {
        await runAstroBuild(emptyDist);
        await assertPages(emptyDist, committed, { requirePublished: false });
        await assertSourceState(emptyDist);
        await assertNoTemplate(emptyDist);
        await assertNoFixtureContent(emptyDist);
        await assertNoPublishedFixture(emptyDist);
        await assertNoDollar(emptyDist);
        await assertNoSportsbooks(emptyDist);
        console.log('empty build: grader files and game cards were moved aside');
      } finally {
        await rm(emptyDist, { recursive: true, force: true });
      }
    }

    if ((await exists(summaryDest)) || (await exists(viewDest))) {
      fail('refusing to overwrite src/data/summary.json or src/data/picks_view.json');
    }
    if (await exists(cardLinkCopy)) {
      fail(`refusing to overwrite ${path.relative(root, cardLinkCopy)}`);
    }
    const preexistingGames = (await readdir(gamesContentDir)).filter((name) => name.endsWith('.json'));
    if (preexistingGames.length > 0) {
      fail(`refusing to overwrite src/content/games/${preexistingGames[0]}`);
    }
    const gameFixtures = await loadGameFixtures();
    const picksForCards = JSON.parse(await readFile(viewFixture, 'utf8'));
    const currentCardWeek = currentWeek(gameFixtures.map((entry) => entry.card));
    const knownCard =
      gameFixtures.find(
        (entry) =>
          sameSeasonWeek(entry.card, currentCardWeek) &&
          picksForCards.some((row) => row.game_id === entry.card.game_id),
      ) ?? gameFixtures.find((entry) => sameSeasonWeek(entry.card, currentCardWeek));
    if (!knownCard) {
      fail('card fixtures have no current-week game');
    }
    const copiedGames = [];
    await copyFile(summaryFixture, summaryDest);
    await copyFile(viewFixture, viewDest);
    try {
      for (const entry of gameFixtures) {
        const dest = path.join(gamesContentDir, entry.name);
        await copyFile(path.join(gamesFixtureDir, entry.name), dest);
        copiedGames.push(dest);
      }
      await writeFile(cardLinkCopy, cardLinkSource(knownCard.card));
      await rm(picksDist, { recursive: true, force: true });
      await runAstroBuild(picksDist);
      await assertPages(picksDist, committed, { requirePublished: false });
      await assertFixturePicks(picksDist);
      await assertFixtureCards(picksDist, gameFixtures, picksForCards, knownCard.card.game_id);
      await assertNoTemplate(picksDist);
      await assertNoDollar(picksDist);
      await assertNoSportsbooks(picksDist);
      console.log('fixture build: /picks/ matches the grader summary and shows one card per pick');
      console.log('fixture build: game cards render every section and stay out of the empty dist');
    } finally {
      await rm(summaryDest, { force: true });
      await rm(viewDest, { force: true });
      await rm(cardLinkCopy, { force: true });
      for (const dest of copiedGames) {
        await rm(dest, { force: true });
      }
      await rm(picksDist, { recursive: true, force: true });
    }
  } finally {
    await restoreParked(parked);
  }
}

await main();
