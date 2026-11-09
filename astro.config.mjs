import mdx from '@astrojs/mdx';
import { defineConfig } from 'astro/config';

const site = process.env.SITE_URL?.trim();

export default defineConfig({
  output: 'static',
  integrations: [mdx()],
  ...(site ? { site } : {}),
});
