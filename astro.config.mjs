import mdx from '@astrojs/mdx';
import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';
import { siteUrl } from './src/data/site-url.mjs';

const site = siteUrl();

export default defineConfig({
  site,
  output: 'static',
  integrations: [
    mdx(),
    sitemap({
      filter(page) {
        const pathname = new URL(page).pathname.replace(/\/+$/, '');
        return pathname !== '/404' && pathname !== '/404.html';
      },
    }),
  ],
});
