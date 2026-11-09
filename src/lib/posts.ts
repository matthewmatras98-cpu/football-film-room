import { getCollection } from 'astro:content';

export function formatPublishedAt(iso: string): string {
  const formatted = new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'America/Phoenix',
  }).format(new Date(iso));
  return `${formatted} MST`;
}

/** Published weeks only, newest published_at first. Every route goes through this. */
export async function getPublishedWeeks() {
  const weeks = await getCollection('weeks');
  return weeks
    .filter((week) => week.data.draft === false)
    .sort(
      (a, b) => Date.parse(b.data.published_at) - Date.parse(a.data.published_at),
    );
}
