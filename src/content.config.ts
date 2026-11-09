import { basename, extname } from 'node:path';
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { gameCardSchema } from './lib/game-schema';
import { weekSchema } from './lib/schemas';

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

export const collections = { weeks, games };
