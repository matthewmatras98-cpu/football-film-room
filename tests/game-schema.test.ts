import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  CARD_SOURCES,
  ROOF_VALUES,
  WEATHER_MODES,
  gameCardSchema,
} from '../src/lib/game-schema';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixtureDir = path.join(root, 'tests/fixtures/games');

async function loadFixtures() {
  const names = (await readdir(fixtureDir)).filter((name) => name.endsWith('.json')).sort();
  const files = await Promise.all(
    names.map(async (name) => ({
      name,
      data: JSON.parse(await readFile(path.join(fixtureDir, name), 'utf8')) as Record<string, unknown>,
    })),
  );
  return files;
}

const files = await loadFixtures();
const validFiles = files.filter((file) => file.name !== 'malformed.json');
const malformed = files.find((file) => file.name === 'malformed.json');
const base = validFiles[0]?.data;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

describe('game card schema', () => {
  it('accepts every valid fixture and rejects the malformed one', () => {
    expect(validFiles.length).toBeGreaterThanOrEqual(2);
    expect(malformed).toBeDefined();
    for (const file of validFiles) {
      const parsed = gameCardSchema.safeParse(file.data);
      expect(parsed.success, file.name).toBe(true);
    }
    expect(gameCardSchema.safeParse(malformed?.data).success).toBe(false);
  });

  it('covers every roof, every weather mode, empty and full lines, and injuries', () => {
    const cards = validFiles.flatMap((file) => {
      const parsed = gameCardSchema.safeParse(file.data);
      return parsed.success ? [parsed.data] : [];
    });
    expect(new Set(cards.map((card) => card.roof))).toEqual(new Set(ROOF_VALUES));
    expect(new Set(cards.map((card) => card.sections.weather.mode))).toEqual(new Set(WEATHER_MODES));
    expect(cards.some((card) => card.line_history.length > 0)).toBe(true);
    expect(cards.some((card) => card.line_history.length === 0 && card.line_open === null && card.line_latest === null)).toBe(
      true,
    );
    expect(cards.some((card) => card.sections.injuries.home.length + card.sections.injuries.away.length > 0)).toBe(true);
    expect(cards.some((card) => card.sections.injuries.home.length === 0 && card.sections.injuries.away.length === 0)).toBe(
      true,
    );
    expect(new Set(cards.map((card) => card.injuries_status))).toEqual(new Set(['reported', 'pending']));
    expect(cards.every((card) => card.schema_version === 1)).toBe(true);
    expect(cards.every((card) => card.sections.story_prompts.length === 3)).toBe(true);
    expect(cards.every((card) => card.sources.every((source) => CARD_SOURCES.includes(source)))).toBe(true);
  });

  it.each([
    'schema_version',
    'game_id',
    'season',
    'week',
    'kickoff_utc',
    'home',
    'away',
    'venue',
    'roof',
    'surface',
    'home_rest',
    'away_rest',
    'line_open',
    'line_latest',
    'line_history',
    'generated_at',
    'data_through_week',
    'sources',
    'injuries_status',
    'sections',
  ])('rejects a card missing %s', (field) => {
    const data = clone(base);
    delete data[field];
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it.each([
    'epa',
    'pass_rate_pace',
    'explosives',
    'qb',
    'sacks',
    'red_zone_downs',
    'injuries',
    'weather',
    'story_prompts',
  ])('rejects a card missing sections.%s', (field) => {
    const data = clone(base);
    const sections = data.sections as Record<string, unknown>;
    delete sections[field];
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects a StatNumber without source', () => {
    const data = clone(base);
    const sections = data.sections as {
      epa: { home: { off_epa_season: Record<string, unknown> } };
    };
    delete sections.epa.home.off_epa_season.source;
    expect(gameCardSchema.safeParse(data).success).toBe(false);
    sections.epa.home.off_epa_season.source = 'ftn';
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects a story prompt list that is not length 3', () => {
    const data = clone(base);
    const sections = data.sections as { story_prompts: unknown[] };
    const [first] = sections.story_prompts;
    sections.story_prompts = sections.story_prompts.slice(0, 2);
    expect(gameCardSchema.safeParse(data).success).toBe(false);
    sections.story_prompts = [...sections.story_prompts, first, first];
    expect(gameCardSchema.safeParse(data).success).toBe(false);
  });

  it('rejects an FTN token in sources', () => {
    for (const token of ['ftn', 'FTN', 'ftn_charting']) {
      const data = clone(base);
      data.sources = [token];
      expect(gameCardSchema.safeParse(data).success, token).toBe(false);
    }
  });

  it('rejects a bad game id', () => {
    for (const gameId of ['DET-PHI', '2099_2_NE_MIA', '2099_02_ne_mia', '2099_02_N_MIA']) {
      const data = clone(base);
      data.game_id = gameId;
      expect(gameCardSchema.safeParse(data).success, gameId).toBe(false);
    }
  });

  it('rejects a missing injuries_status, a bad value, and pending with rows', () => {
    const missing = clone(base);
    delete missing.injuries_status;
    expect(gameCardSchema.safeParse(missing).success).toBe(false);

    const bad = clone(base);
    bad.injuries_status = 'posted';
    expect(gameCardSchema.safeParse(bad).success).toBe(false);

    const pending = clone(base);
    pending.injuries_status = 'pending';
    const injuries = (pending.sections as { injuries: { home: unknown[]; away: unknown[] } }).injuries;
    injuries.home = [{ player: 'A. Player', position: 'QB', status: 'Out', detail: null }];
    injuries.away = [];
    expect(gameCardSchema.safeParse(pending).success).toBe(false);
  });

  it('rejects indoor weather numbers and outdoor nulls', () => {
    const indoor = validFiles.find((file) => file.name.includes('GB_CHI'));
    const outdoor = validFiles.find((file) => file.name.includes('NE_MIA'));
    if (!indoor || !outdoor) {
      throw new Error('missing indoor or outdoor fixture');
    }
    const indoorData = clone(indoor.data);
    const indoorWeather = (indoorData.sections as { weather: Record<string, unknown> }).weather;
    indoorWeather.temp_f = { value: 70, source: 'nws', sample: null, range: null };
    expect(gameCardSchema.safeParse(indoorData).success).toBe(false);

    const outdoorData = clone(outdoor.data);
    const outdoorWeather = (outdoorData.sections as { weather: Record<string, unknown> }).weather;
    outdoorWeather.temp_f = null;
    expect(gameCardSchema.safeParse(outdoorData).success).toBe(false);
  });
});
