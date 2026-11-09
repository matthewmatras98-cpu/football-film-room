import { spawn } from 'node:child_process';
import { copyFile, readFile, readdir, rm, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const weeksDir = path.join(root, 'src/content/weeks');
const publishedFixture = path.join(root, 'tests/fixtures/published-post.mdx');
const publishedCopy = path.join(weeksDir, 'published-post.mdx');
const publishedSlug = 'published-post';
const deployDist = path.join(root, 'dist');
const tempDist = path.join(root, 'dist-published-check');

const fullCredits = [
  'Data: nflverse (nflfastR / nflreadr), licensed CC-BY 4.0. NFL data belongs to its respective owners.',
  'Charting data: FTN Data via nflverse, licensed CC-BY-SA 4.0. Any published table derived from FTN data is also CC-BY-SA. Label those tables so readers can see it.',
  'Next Gen Stats data via nflverse; NGS is a product of the NFL.',
  'Odds data: The Odds API. Lines shown are medians of N US books.',
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
  for (const line of [...fullCredits, disclaimer]) {
    if (!methodologyHtml.includes(line)) {
      fail(`methodology is missing the full text: ${line}`);
    }
  }

  if (requirePublished) {
    const publishedPage = await weekPage(distDir, publishedSlug);
    if (!publishedPage) {
      fail(`published slug ${publishedSlug} is missing from the temporary build`);
    }
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
    console.log(`temporary build: /weeks/${publishedSlug}/ is present and drafts are absent`);
  } finally {
    await rm(publishedCopy, { force: true });
    await rm(tempDist, { recursive: true, force: true });
  }
}

await main();
