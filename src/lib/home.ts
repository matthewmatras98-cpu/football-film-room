import { formatUnits } from './format-picks';

/** Compact season line, using the same units formatter as the ledger chip. */
export function formatSeasonLine(summary: { w: number; l: number; p: number; units: number }): string {
  return `${summary.w}-${summary.l}-${summary.p} · ${formatUnits(summary.units)}`;
}

/**
 * Home-page note while the season has no picks and no published posts.
 * Hidden as soon as either exists. Mention the cards only when they are on the page.
 */
export function homeEmptyNote(
  pickCount: number,
  publishedWeekCount: number,
  gameCardCount: number,
): string | null {
  if (pickCount > 0 || publishedWeekCount > 0) {
    return null;
  }
  if (gameCardCount > 0) {
    return "No picks or posts yet this season. This week's games are below.";
  }
  return 'No picks or posts yet this season.';
}
