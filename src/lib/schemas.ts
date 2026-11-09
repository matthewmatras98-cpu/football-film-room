import { z } from 'astro/zod';

/** nflverse game id, for example 2026_11_DET_PHI. */
export const GAME_ID_PATTERN = /^\d{4}_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}$/;

const ISO_WITH_OFFSET =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/;

export function isIsoWithOffset(value: string): boolean {
  const match = ISO_WITH_OFFSET.exec(value);
  if (!match) {
    return false;
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  if (hour > 23 || minute > 59 || second > 59) {
    return false;
  }

  const probe = new Date(Date.UTC(year, month - 1, day));
  if (
    probe.getUTCFullYear() !== year ||
    probe.getUTCMonth() !== month - 1 ||
    probe.getUTCDate() !== day
  ) {
    return false;
  }

  const offset = match[7] ?? '';
  if (offset !== 'Z') {
    const offsetHours = Number(offset.slice(1, 3));
    const offsetMinutes = Number(offset.slice(4, 6));
    if (offsetHours > 23 || offsetMinutes > 59) {
      return false;
    }
  }

  return Number.isFinite(Date.parse(value));
}

/**
 * Astro's YAML parser turns an unquoted ISO timestamp into a Date before the
 * schema runs. Normalize that to a string, then require an offset.
 */
const publishedAtSchema = z.preprocess((value) => {
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return value.toISOString();
  }
  return value;
}, z.string().refine(isIsoWithOffset, 'published_at must be an ISO-8601 datetime with a timezone offset'));

export const weekSchema = z.object({
  title: z.string().min(1),
  season: z.number().int(),
  week: z.number().int(),
  published_at: publishedAtSchema,
  summary: z.string().min(1),
  picks: z.array(z.string().regex(GAME_ID_PATTERN)),
  tags: z.array(z.string()),
  draft: z.boolean(),
});

export type WeekFrontmatter = z.infer<typeof weekSchema>;
