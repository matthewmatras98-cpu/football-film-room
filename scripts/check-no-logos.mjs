import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export const RASTER_EXTENSIONS = new Set([
  '.png',
  '.jpg',
  '.jpeg',
  '.gif',
  '.webp',
  '.avif',
  '.bmp',
  '.ico',
]);

/** Nicknames only. Abbreviations are omitted on purpose; they false-positive. */
export const TEAM_NICKNAMES = [
  'cardinals',
  'falcons',
  'ravens',
  'bills',
  'panthers',
  'bears',
  'bengals',
  'browns',
  'cowboys',
  'broncos',
  'lions',
  'packers',
  'texans',
  'colts',
  'jaguars',
  'chiefs',
  'raiders',
  'chargers',
  'rams',
  'dolphins',
  'vikings',
  'patriots',
  'saints',
  'giants',
  'jets',
  'eagles',
  'steelers',
  '49ers',
  'seahawks',
  'buccaneers',
  'titans',
  'commanders',
];

async function walkFiles(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (error && error.code === 'ENOENT') {
      return [];
    }
    throw error;
  }

  const files = [];
  for (const entry of entries) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await walkFiles(fullPath)));
    } else if (entry.isFile()) {
      files.push(fullPath);
    }
  }
  return files;
}

export async function findAssetViolations(roots) {
  const violations = [];

  for (const root of roots) {
    const files = await walkFiles(root);
    for (const filePath of files) {
      const filename = path.basename(filePath);
      const lowerName = filename.toLowerCase();
      const extension = path.extname(lowerName);
      const relativePath = path.relative(root, filePath);

      if (RASTER_EXTENSIONS.has(extension)) {
        violations.push(`${relativePath}: raster image (${extension})`);
      }

      if (lowerName.includes('logo')) {
        violations.push(`${relativePath}: filename contains "logo"`);
      }

      for (const nickname of TEAM_NICKNAMES) {
        if (lowerName.includes(nickname)) {
          violations.push(`${relativePath}: filename contains team nickname "${nickname}"`);
          break;
        }
      }

      if (extension === '.svg' && lowerName !== 'favicon.svg') {
        const text = await readFile(filePath, 'utf8');
        if (/<image\b/i.test(text)) {
          violations.push(`${relativePath}: svg contains an embedded <image>`);
        }
      }
    }
  }

  return violations;
}

const invokedDirectly =
  process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedDirectly) {
  const roots = process.argv.slice(2);
  const targets = roots.length > 0 ? roots : ['public', 'src/assets'];
  const violations = await findAssetViolations(targets);
  if (violations.length > 0) {
    console.error(violations.join('\n'));
    process.exit(1);
  }
}
