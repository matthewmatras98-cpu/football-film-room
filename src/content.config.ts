import { basename, extname } from 'node:path';
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';
import { gameCardSchema } from './lib/game-schema';
import { GAME_ID_PATTERN, isIsoWithOffset, weekSchema } from './lib/schemas';

const weeks = defineCollection({
  loader: glob({
    pattern: 'src/content/weeks/*.mdx',
    base: '.',
    generateId: ({ entry }) => basename(entry, extname(entry)).toLowerCase(),
  }),
  schema: weekSchema,
});

// An empty directory is a valid build. Astro warns that the collection is empty until T3 commits cards.
const games = defineCollection({
  loader: glob({
    pattern: '*.json',
    base: './src/content/games',
    generateId: ({ entry }) => basename(entry, extname(entry)),
  }),
  schema: gameCardSchema,
});

const submittedAt = z.preprocess((value) => {
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    return value.toISOString();
  }
  return value;
}, z.string().refine((value) => value.endsWith('Z') && isIsoWithOffset(value), 'submitted_at must be a UTC timestamp ending in Z'));

// An empty directory is a valid build. Write-ups land as src/content/picks/{pick_id}.md.
const picks = defineCollection({
  loader: glob({
    pattern: '*.md',
    base: './src/content/picks',
    generateId: ({ entry }) => basename(entry, extname(entry)),
  }),
  schema: z
    .object({
      pick_id: z.string().min(1),
      game_id: z.string().regex(GAME_ID_PATTERN),
      submitted_at: submittedAt,
    })
    .strict(),
});

export const collections = { weeks, games, picks };
