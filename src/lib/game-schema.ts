import { z } from 'astro/zod';
import { GAME_ID_PATTERN, isIsoWithOffset } from './schemas';

/**
 * Game card JSON contract v1.
 * `schema_version` is the integer 1. Key names, enums, and nullability match
 * the locked contract. A card that fails this schema fails the build.
 */

export const SCHEMA_VERSION = 1;

export const CARD_SOURCES = [
  'nflverse_pbp',
  'nflverse_schedules',
  'nflverse_injuries',
  'ngs_via_nflverse',
  'odds_api',
  'nws',
] as const;

export const ROOF_VALUES = ['outdoors', 'dome', 'retractable', 'unknown'] as const;

export const WEATHER_MODES = ['outdoor', 'indoor', 'retractable', 'unavailable'] as const;

const GAME_ID_PARTS = /^(\d{4})_(\d{2})_([A-Z]{2,3})_([A-Z]{2,3})$/;

const utcTimestamp = z
  .string()
  .refine((value) => value.endsWith('Z') && isIsoWithOffset(value), 'must be a UTC timestamp ending in Z');

const kickoffTimestamp = z
  .string()
  .refine(isIsoWithOffset, 'kickoff_utc must be an ISO-8601 timestamp with an offset');

export const statNumberSchema = z
  .object({
    value: z.number().nullable(),
    source: z.string().min(1),
    sample: z.number().int().nullable(),
    range: z.string().nullable(),
  })
  .strict()
  .refine((stat) => !/ftn/i.test(stat.source), 'FTN is not a public card source');

export type StatNumber = z.infer<typeof statNumberSchema>;

function rateStat() {
  return statNumberSchema.refine(
    (stat) => stat.value === null || (stat.value >= 0 && stat.value <= 1),
    'rate must be a fraction from 0 to 1',
  );
}

function secondsStat() {
  return statNumberSchema.refine(
    (stat) => stat.value === null || stat.value >= 0,
    'seconds must be zero or positive',
  );
}

const timeToThrowStat = secondsStat().refine(
  (stat) => stat.source === 'ngs_via_nflverse',
  'time_to_throw source must be ngs_via_nflverse',
);

const sackStat = rateStat().refine(
  (stat) => stat.source === 'nflverse_pbp',
  'sack rates are nflverse_pbp only',
);

const nwsStat = statNumberSchema.refine((stat) => stat.source === 'nws', 'weather source must be nws');

const nwsPrecipStat = nwsStat.refine(
  (stat) => stat.value === null || (stat.value >= 0 && stat.value <= 100),
  'precip_prob_pct is a percent from 0 to 100',
);

const nwsNullStat = nwsStat.refine((stat) => stat.value === null, 'unavailable weather values must be null');

export const linePointSchema = z
  .object({
    ts: utcTimestamp,
    home_spread: z.number().nullable(),
    total: z.number().nullable(),
    n_books: z.number().int().nonnegative(),
  })
  .strict();

export type LinePoint = z.infer<typeof linePointSchema>;

export const injurySchema = z
  .object({
    player: z.string().min(1),
    position: z.string().min(1),
    status: z.string().min(1),
    detail: z.string().nullable(),
  })
  .strict();

export type Injury = z.infer<typeof injurySchema>;

export const storyPromptSchema = z
  .object({
    title: z.string().min(1),
    body: z.string().min(1),
  })
  .strict();

function teamSides<T extends z.ZodType>(side: T) {
  return z
    .object({
      home: side,
      away: side,
    })
    .strict();
}

export const epaSideSchema = z
  .object({
    off_epa_season: statNumberSchema,
    off_epa_l4: statNumberSchema,
    def_epa_season: statNumberSchema,
    def_epa_l4: statNumberSchema,
    off_sr_season: rateStat(),
    def_sr_season: rateStat(),
  })
  .strict();

export const epaSchema = teamSides(epaSideSchema);

export const passRatePaceSideSchema = z
  .object({
    neutral_ed_pass_rate: rateStat(),
    pace_sec_per_play: secondsStat(),
  })
  .strict();

export const passRatePaceSchema = teamSides(passRatePaceSideSchema);

export const explosivesSideSchema = z
  .object({
    explosive_rate_for: rateStat(),
    explosive_rate_allowed: rateStat(),
  })
  .strict();

export const explosivesSchema = teamSides(explosivesSideSchema);

export const qbSideSchema = z
  .object({
    player: z.string().nullable(),
    epa_per_db: statNumberSchema,
    cpoe: statNumberSchema,
    time_to_throw: timeToThrowStat,
  })
  .strict();

export const qbSchema = teamSides(qbSideSchema);

export const sacksSideSchema = z
  .object({
    sack_rate_for: sackStat,
    sack_rate_allowed: sackStat,
  })
  .strict();

export const sacksSchema = teamSides(sacksSideSchema);

export const redZoneDownsSideSchema = z
  .object({
    rz_td_rate_for: rateStat(),
    rz_td_rate_allowed: rateStat(),
    third_down_rate_for: rateStat(),
    third_down_rate_allowed: rateStat(),
    fourth_down_rate_for: rateStat(),
    fourth_down_rate_allowed: rateStat(),
  })
  .strict();

export const redZoneDownsSchema = teamSides(redZoneDownsSideSchema);

export const injuriesSchema = z
  .object({
    home: z.array(injurySchema),
    away: z.array(injurySchema),
  })
  .strict();

const outdoorWeatherSchema = z
  .object({
    mode: z.literal('outdoor'),
    temp_f: nwsStat,
    wind_mph: nwsStat,
    precip_prob_pct: nwsPrecipStat,
    summary: z.string().nullable(),
  })
  .strict();

const retractableWeatherSchema = z
  .object({
    mode: z.literal('retractable'),
    temp_f: nwsStat,
    wind_mph: nwsStat,
    precip_prob_pct: nwsPrecipStat,
    summary: z.string().nullable(),
  })
  .strict();

const indoorWeatherSchema = z
  .object({
    mode: z.literal('indoor'),
    temp_f: z.null(),
    wind_mph: z.null(),
    precip_prob_pct: z.null(),
    summary: z.null(),
  })
  .strict();

const unavailableWeatherSchema = z
  .object({
    mode: z.literal('unavailable'),
    temp_f: nwsNullStat,
    wind_mph: nwsNullStat,
    precip_prob_pct: nwsNullStat,
    summary: z.string().nullable(),
  })
  .strict();

export const weatherSchema = z.discriminatedUnion('mode', [
  outdoorWeatherSchema,
  indoorWeatherSchema,
  retractableWeatherSchema,
  unavailableWeatherSchema,
]);

export const sectionsSchema = z
  .object({
    epa: epaSchema,
    pass_rate_pace: passRatePaceSchema,
    explosives: explosivesSchema,
    qb: qbSchema,
    sacks: sacksSchema,
    red_zone_downs: redZoneDownsSchema,
    injuries: injuriesSchema,
    weather: weatherSchema,
    story_prompts: z.array(storyPromptSchema).length(3),
  })
  .strict();

const BOOK_OR_DOLLAR =
  /\$|draftkings|fanduel|betmgm|betrivers|bovada|betonline|lowvig|mybookie|betus/i;

const WAGERING_LANGUAGE =
  /\b(?:lock|locks|locked|guaranteed|wager|wagers|wagering|parlay|parlays|sportsbook|sportsbooks)\b/i;

function collectStrings(value: unknown): string[] {
  if (typeof value === 'string') {
    return [value];
  }
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectStrings(item));
  }
  if (value && typeof value === 'object') {
    return Object.values(value).flatMap((item) => collectStrings(item));
  }
  return [];
}

function samePoint(left: LinePoint, right: LinePoint): boolean {
  return (
    left.ts === right.ts &&
    left.home_spread === right.home_spread &&
    left.total === right.total &&
    left.n_books === right.n_books
  );
}

export const gameCardSchema = z
  .object({
    schema_version: z.literal(SCHEMA_VERSION),
    game_id: z.string().regex(GAME_ID_PATTERN),
    season: z.number().int(),
    week: z.number().int(),
    kickoff_utc: kickoffTimestamp,
    home: z.string().min(1),
    away: z.string().min(1),
    venue: z.string().nullable(),
    roof: z.enum(ROOF_VALUES),
    surface: z.string().nullable(),
    home_rest: z.number().int().nullable(),
    away_rest: z.number().int().nullable(),
    line_open: linePointSchema.nullable(),
    line_latest: linePointSchema.nullable(),
    line_history: z.array(linePointSchema),
    generated_at: utcTimestamp,
    data_through_week: z.number().int(),
    sources: z.array(z.enum(CARD_SOURCES)),
    injuries_status: z.enum(['reported', 'pending']),
    sections: sectionsSchema,
  })
  .strict()
  .superRefine((card, ctx) => {
    const seen = new Set<string>();
    for (const source of card.sources) {
      if (seen.has(source)) {
        ctx.addIssue({
          code: 'custom',
          message: 'sources must be a subset with no duplicates',
          path: ['sources'],
        });
        break;
      }
      seen.add(source);
    }

    const id = GAME_ID_PARTS.exec(card.game_id);
    if (id) {
      if (card.season !== Number(id[1])) {
        ctx.addIssue({ code: 'custom', message: 'season must match game_id', path: ['season'] });
      }
      if (card.week !== Number(id[2])) {
        ctx.addIssue({ code: 'custom', message: 'week must match game_id', path: ['week'] });
      }
      if (card.away !== id[3]) {
        ctx.addIssue({ code: 'custom', message: 'away must match game_id', path: ['away'] });
      }
      if (card.home !== id[4]) {
        ctx.addIssue({ code: 'custom', message: 'home must match game_id', path: ['home'] });
      }
    }

    const history = card.line_history;
    for (let index = 1; index < history.length; index += 1) {
      const previous = history[index - 1];
      const current = history[index];
      if (!previous || !current || !(Date.parse(current.ts) > Date.parse(previous.ts))) {
        ctx.addIssue({
          code: 'custom',
          message: 'line_history must be strictly ascending by ts',
          path: ['line_history'],
        });
        break;
      }
    }

    if (history.length === 0) {
      if (card.line_open !== null) {
        ctx.addIssue({
          code: 'custom',
          message: 'line_open is null when line_history is empty',
          path: ['line_open'],
        });
      }
      if (card.line_latest !== null) {
        ctx.addIssue({
          code: 'custom',
          message: 'line_latest is null when line_history is empty',
          path: ['line_latest'],
        });
      }
    } else {
      const first = history[0];
      const last = history[history.length - 1];
      if (!first || !card.line_open || !samePoint(card.line_open, first)) {
        ctx.addIssue({
          code: 'custom',
          message: 'line_open is the first line_history point',
          path: ['line_open'],
        });
      }
      if (!last || !card.line_latest || !samePoint(card.line_latest, last)) {
        ctx.addIssue({
          code: 'custom',
          message: 'line_latest is the last line_history point',
          path: ['line_latest'],
        });
      }
    }

    for (const text of collectStrings(card)) {
      if (BOOK_OR_DOLLAR.test(text)) {
        ctx.addIssue({
          code: 'custom',
          message: 'card text must not contain a dollar sign or a sportsbook name',
        });
        break;
      }
    }

    if (
      card.injuries_status === 'pending' &&
      (card.sections.injuries.home.length > 0 || card.sections.injuries.away.length > 0)
    ) {
      ctx.addIssue({
        code: 'custom',
        message: 'pending injury status requires empty home and away lists',
        path: ['injuries_status'],
      });
    }

    for (const prompt of card.sections.story_prompts) {
      if (WAGERING_LANGUAGE.test(prompt.title) || WAGERING_LANGUAGE.test(prompt.body) || /ftn/i.test(`${prompt.title} ${prompt.body}`)) {
        ctx.addIssue({
          code: 'custom',
          message: 'story prompts must not use wagering language or an FTN credit',
          path: ['sections', 'story_prompts'],
        });
        break;
      }
    }
  });

export type GameCard = z.infer<typeof gameCardSchema>;
export type WeatherMode = (typeof WEATHER_MODES)[number];
export type RoofValue = (typeof ROOF_VALUES)[number];
