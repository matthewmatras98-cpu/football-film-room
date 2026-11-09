import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { findAssetViolations } from '../scripts/check-no-logos.mjs';

const dirs: string[] = [];

async function tempDir(): Promise<string> {
  const dir = await mkdtemp(path.join(tmpdir(), 'no-logos-'));
  dirs.push(dir);
  return dir;
}

afterEach(async () => {
  await Promise.all(dirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

describe('check-no-logos', () => {
  it('passes a clean directory, including abbreviations and a text favicon', async () => {
    const dir = await tempDir();
    await writeFile(
      path.join(dir, 'favicon.svg'),
      '<svg xmlns="http://www.w3.org/2000/svg"><text>MN</text></svg>',
    );
    await writeFile(path.join(dir, 'phi.svg'), '<svg xmlns="http://www.w3.org/2000/svg"></svg>');
    await writeFile(path.join(dir, 'notes.txt'), 'text only');

    await expect(findAssetViolations([dir])).resolves.toEqual([]);
  });

  it('fails on a png', async () => {
    const dir = await tempDir();
    await writeFile(path.join(dir, 'photo.png'), 'not really a png');

    const violations = await findAssetViolations([dir]);
    expect(violations.length).toBeGreaterThan(0);
    expect(violations.join('\n')).toMatch(/raster/i);
  });

  it('fails on a filename containing logo', async () => {
    const dir = await tempDir();
    await writeFile(path.join(dir, 'logo.svg'), '<svg xmlns="http://www.w3.org/2000/svg"></svg>');

    const violations = await findAssetViolations([dir]);
    expect(violations.join('\n')).toMatch(/logo/i);
  });

  it('fails on a team-nickname filename', async () => {
    const dir = await tempDir();
    await writeFile(path.join(dir, 'packers.svg'), '<svg xmlns="http://www.w3.org/2000/svg"></svg>');

    const violations = await findAssetViolations([dir]);
    expect(violations.join('\n')).toMatch(/packers/i);
  });

  it('fails on an svg with an embedded image', async () => {
    const dir = await tempDir();
    await writeFile(
      path.join(dir, 'mark.svg'),
      '<svg xmlns="http://www.w3.org/2000/svg"><image href="photo.png" /></svg>',
    );

    const violations = await findAssetViolations([dir]);
    expect(violations.join('\n')).toMatch(/embedded <image>/i);
  });

  it('does not treat an embedded image in favicon.svg as a violation', async () => {
    const dir = await tempDir();
    await writeFile(
      path.join(dir, 'favicon.svg'),
      '<svg xmlns="http://www.w3.org/2000/svg"><image href="photo.png" /></svg>',
    );

    await expect(findAssetViolations([dir])).resolves.toEqual([]);
  });
});
