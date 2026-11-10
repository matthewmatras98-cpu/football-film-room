import { spawnSync } from 'node:child_process';
import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { gameCardSchema } from '../src/lib/game-schema';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const goldenDir = path.join(root, 'tests/fixtures/generated-cards');

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

const names = (await readdir(goldenDir)).filter((name) => name.endsWith('.json')).sort();
const goldens = await Promise.all(
  names.map(async (name) => ({
    name,
    data: JSON.parse(await readFile(path.join(goldenDir, name), 'utf8')) as Record<string, unknown>,
  })),
);
const outdoor = goldens.find((file) => file.name === '2099_03_MIN_GB.json');
if (!outdoor) {
  throw new Error('missing 2099_03_MIN_GB golden');
}

describe('generated cards', () => {
  it('parses every golden and matches the file name to game_id', () => {
    expect(goldens.length).toBe(4);
    for (const file of goldens) {
      const parsed = gameCardSchema.safeParse(file.data);
      expect(parsed.success, file.name).toBe(true);
      expect(file.name).toBe(`${String(file.data.game_id)}.json`);
    }
    const pending = goldens.find((file) => file.name === '2099_03_KC_DEN.json');
    expect(pending?.data.injuries_status).toBe('pending');
  });

  it('matches a fresh run of the card generator', () => {
    const result = spawnSync('python3', ['scripts/check_generated_cards.py', '--check'], {
      cwd: root,
      encoding: 'utf8',
      env: {
        ...process.env,
        PATH: `${process.env.HOME ?? ''}/.local/bin:${process.env.PATH ?? ''}`,
      },
    });
    expect(result.status, `${result.stderr ?? ''}\n${result.stdout ?? ''}`).toBe(0);
  }, 120_000);

  it('rejects an open line that is not the first point', () => {
    const data = clone(outdoor.data);
    const open = data.line_open as { home_spread: number };
    open.home_spread = open.home_spread === 0 ? 1 : 0;
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects a descending line history', () => {
    const data = clone(outdoor.data);
    const history = data.line_history as unknown[];
    history.reverse();
    data.line_open = history[0];
    data.line_latest = history[history.length - 1];
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects a week that does not match game_id', () => {
    const data = clone(outdoor.data);
    data.week = 9;
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects a rate above 1', () => {
    const data = clone(outdoor.data);
    const sections = data.sections as {
      pass_rate_pace: { home: { neutral_ed_pass_rate: { value: number } } };
    };
    sections.pass_rate_pace.home.neutral_ed_pass_rate.value = 1.2;
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects a sack rate sourced from ngs', () => {
    const data = clone(outdoor.data);
    const sections = data.sections as {
      sacks: { home: { sack_rate_for: { source: string } } };
    };
    sections.sacks.home.sack_rate_for.source = 'ngs_via_nflverse';
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects time_to_throw sourced from pbp', () => {
    const data = clone(outdoor.data);
    const sections = data.sections as {
      qb: { home: { time_to_throw: { source: string } } };
    };
    sections.qb.home.time_to_throw.source = 'nflverse_pbp';
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects outdoor weather sourced from n/a', () => {
    const data = clone(outdoor.data);
    const sections = data.sections as { weather: { temp_f: { source: string } } };
    sections.weather.temp_f.source = 'n/a';
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it.each(['Cost is $5', 'See draftkings', 'This is a parlay'])('rejects a prompt containing %s', (body) => {
    const data = clone(outdoor.data);
    const sections = data.sections as { story_prompts: Array<{ body: string }> };
    const prompt = sections.story_prompts[0];
    if (!prompt) {
      throw new Error('missing prompt');
    }
    prompt.body = body;
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });
});
