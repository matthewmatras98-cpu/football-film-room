import { basename, extname } from 'node:path';
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { weekSchema } from './lib/schemas';

const weeks = defineCollection({
  loader: glob({
    pattern: 'src/content/weeks/*.mdx',
    base: '.',
    generateId: ({ entry }) => basename(entry, extname(entry)).toLowerCase(),
  }),
  schema: weekSchema,
});

export const collections = { weeks };
