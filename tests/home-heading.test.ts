import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const indexSource = readFileSync(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles/global.css', import.meta.url), 'utf8');

function mediaBlock(source: string, header: string): string {
  const start = source.indexOf(header);
  expect(start, header).toBeGreaterThanOrEqual(0);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(open + 1, i);
    }
  }
  throw new Error(`unclosed ${header}`);
}

/** Last compound of each selector, which is the element the declarations apply to. */
function displayNoneTargets(block: string): string[] {
  const targets: string[] = [];
  const cleaned = block.replace(/\/\*[\s\S]*?\*\//g, '');
  let cursor = 0;
  while (cursor < cleaned.length) {
    const open = cleaned.indexOf('{', cursor);
    if (open < 0) break;
    const selector = cleaned.slice(cursor, open).trim();
    let depth = 0;
    let end = open;
    for (; end < cleaned.length; end += 1) {
      if (cleaned[end] === '{') depth += 1;
      else if (cleaned[end] === '}') {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    const body = cleaned.slice(open + 1, end);
    if (/display\s*:\s*none/.test(body)) {
      for (const part of selector.split(',')) {
        const pieces = part.trim().split(/\s*[>+~]\s*/);
        targets.push(pieces[pieces.length - 1] ?? '');
      }
    }
    cursor = end + 1;
  }
  return targets;
}

describe('home h1', () => {
  it('keeps one h1 and clips it on the small card layout instead of removing it', () => {
    expect(indexSource.match(/<h1\b/g)).toHaveLength(1);
    expect(indexSource).toMatch(/<h1 class:list=\{\[summary && 'visually-hidden'\]\}>/);
    expect(css).toMatch(/\.visually-hidden\s*\{[^}]*clip\s*:\s*rect\(/);

    const small = mediaBlock(css, '@media (max-width: 40rem)');
    const hidden = displayNoneTargets(small);
    expect(hidden.length).toBeGreaterThan(0);
    for (const target of hidden) {
      expect(target).not.toMatch(/^h1\b/);
      expect(target).not.toMatch(/\.home-intro(?:--summary)?$/);
    }
    expect(small).not.toMatch(/visibility\s*:\s*hidden/);
    expect(small).toMatch(/h1\.visually-hidden\s*\{[^}]*clip\s*:\s*rect\(/);
    expect(small).not.toMatch(/h1\.visually-hidden\s*\{[^}]*(?:display\s*:\s*none|visibility\s*:\s*hidden)/);
  });

  it('places the empty-season note after the games', () => {
    const gamesAt = indexSource.indexOf('class="card-list"');
    const noteAt = indexSource.indexOf('No picks yet this season.');
    expect(gamesAt).toBeGreaterThan(-1);
    expect(noteAt).toBeGreaterThan(gamesAt);
  });

  it('builds a single h1 when dist is present', () => {
    const dist = new URL('../dist/index.html', import.meta.url);
    if (!existsSync(dist)) return;
    const html = readFileSync(dist, 'utf8');
    expect(html.match(/<h1\b/g)).toHaveLength(1);
    expect(html).toMatch(/<h1 class="visually-hidden">Season record<\/h1>/);
    const gamesAt = html.indexOf('data-week-cards');
    const noteAt = html.indexOf('No picks yet this season.');
    if (noteAt !== -1) {
      expect(noteAt).toBeGreaterThan(gamesAt);
    }
  });
});
