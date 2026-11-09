import { readFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { CLOSE_SOURCES, PICK_STATUSES, pickViewSchema, picksViewSchema, summarySchema } from '../src/lib/picks-schema';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const summaryText = readFileSync(path.join(root, 'tests/fixtures/summary.json'), 'utf8');
const viewText = readFileSync(path.join(root, 'tests/fixtures/picks_view.json'), 'utf8');
const summary = JSON.parse(summaryText) as Record<string, unknown>;
const view = JSON.parse(viewText) as Record<string, unknown>[];

describe('grader fixtures', () => {
  it('validates summary.json and picks_view.json', () => {
    expect(summarySchema.safeParse(summary).success).toBe(true);
    const parsed = picksViewSchema.safeParse(view);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data).toHaveLength(view.length);
    }
  });

  it('keeps three decimal places on summary figures', () => {
    for (const key of ['win_pct', 'units', 'avg_clv', 'pct_clv_pos']) {
      expect(summaryText).toMatch(new RegExp(`"${key}": (null|-?\\d+\\.\\d{3})`));
    }
    expect(summaryText.trimEnd().endsWith('}')).toBe(true);
    expect(summary.generated_at).toMatch(/Z$/);
  });

  it.each(Object.keys(summary))('rejects a summary missing %s', (field) => {
    const data: Record<string, unknown> = { ...summary };
    delete data[field];
    expect(summarySchema.safeParse(data).success).toBe(false);
  });

  it.each(Object.keys(view[0] ?? {}))('rejects a pick missing %s', (field) => {
    const row: Record<string, unknown> = { ...(view[0] ?? {}) };
    delete row[field];
    expect(pickViewSchema.safeParse(row).success).toBe(false);
  });

  it('rejects a bad status and a bad close source', () => {
    const row = view[0];
    expect(pickViewSchema.safeParse({ ...row, status: 'late' }).success).toBe(false);
    expect(pickViewSchema.safeParse({ ...row, status: 'Rejected – late' }).success).toBe(false);
    expect(pickViewSchema.safeParse({ ...row, close_source: 'manual' }).success).toBe(false);
    expect(pickViewSchema.safeParse({ ...row, close_source: '' }).success).toBe(false);
  });

  it('rejects an extra field', () => {
    expect(summarySchema.safeParse({ ...summary, extra: 1 }).success).toBe(false);
    expect(pickViewSchema.safeParse({ ...view[0], extra: true }).success).toBe(false);
  });

  it('covers every status, close source, market side, push, and CLV sign', () => {
    const statuses = new Set(view.map((row) => row.status));
    const sources = new Set(view.map((row) => row.close_source));
    expect([...statuses].sort()).toEqual([...PICK_STATUSES].sort());
    expect(sources).toEqual(new Set<unknown>([...CLOSE_SOURCES, null]));
    expect(new Set(view.map((row) => row.market))).toEqual(new Set(['spread', 'total']));
    const sides = new Set(view.map((row) => row.side));
    expect(sides.has('over')).toBe(true);
    expect(sides.has('under')).toBe(true);
    expect(view.some((row) => row.result === 'P')).toBe(true);
    const clvs = view.map((row) => row.clv_pts).filter((value): value is number => typeof value === 'number');
    expect(clvs.some((value) => value > 0)).toBe(true);
    expect(clvs.some((value) => value < 0)).toBe(true);
  });

  it('matches a fresh run of the T1 grader', () => {
    const result = spawnSync('python3', ['scripts/grade_ui_fixtures.py', '--check'], {
      cwd: root,
      encoding: 'utf8',
      env: {
        ...process.env,
        PATH: `${process.env.HOME ?? ''}/.local/bin:${process.env.PATH ?? ''}`,
      },
    });
    expect(`${result.stderr ?? ''}${result.stdout ?? ''}`, result.stderr ?? result.stdout).not.toMatch(
      /pick fixtures are stale|B2 stale row changed|uv is required|expected one late/,
    );
    expect(result.status, `${result.stderr ?? ''}\n${result.stdout ?? ''}`).toBe(0);
  }, 300_000);
});
