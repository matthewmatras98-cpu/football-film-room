import { describe, expect, it } from 'vitest';
import { formatMst } from '../src/lib/format-picks';
import { renderWriteup, writeupForPick, writeupMarkup, type Writeup } from '../src/lib/writeup';

const spread: Writeup = {
  pick_id: '2026_12_AWY_HOM-spread',
  game_id: '2026_12_AWY_HOM',
  submitted_at: '2026-11-20T18:00:00.000Z',
  body: 'Cover the number',
};

function row(pickId: string, writeups: readonly Writeup[]): string {
  return `<tbody id="pick-${pickId}">${writeupMarkup(writeupForPick(pickId, writeups), formatMst)}</tbody>`;
}

describe('write-up placement', () => {
  it('renders under the matching pick and renders nothing when the file is missing', () => {
    const html = `${row(spread.pick_id, [spread])}${row('2026_12_AWY_HOM-total', [spread])}`;
    expect(html).toContain('id="pick-2026_12_AWY_HOM-spread"');
    expect(row(spread.pick_id, [spread])).toContain('Cover the number');
    expect(row(spread.pick_id, [spread])).toContain('Submitted (server time)');
    expect(row(spread.pick_id, [spread])).toContain('MST');
    expect(row('2026_12_AWY_HOM-total', [spread])).toBe('<tbody id="pick-2026_12_AWY_HOM-total"></tbody>');
    expect(writeupMarkup(null, formatMst)).toBe('');
  });
});

describe('write-up escaping', () => {
  it('keeps script, onerror, and javascript links inert', () => {
    const html = renderWriteup(
      '<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n[x](javascript:alert(1))\n\n[y](data:text/html,alert(1))',
    );
    const tags = html.match(/<[^>]+>/g) ?? [];
    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/<img/i);
    expect(html).toContain('&lt;script');
    expect(html).toContain('&lt;img');
    expect(html).not.toContain('javascript:');
    expect(html).not.toContain('data:');
    for (const tag of tags) {
      expect(tag).not.toMatch(/\son[a-z]+\s*=/i);
      expect(tag).not.toMatch(/javascript:/i);
      expect(tag).not.toMatch(/data:/i);
    }
    expect(html).toContain('>x<');
    expect(html).not.toMatch(/<a\b[^>]*>x<\/a>/);
  });

  it('keeps an ordinary https link', () => {
    const html = renderWriteup('[notes](https://example.com/a)');
    expect(html).toContain('href="https://example.com/a"');
    expect(html).toContain('>notes</a>');
  });
});
