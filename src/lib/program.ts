import { formatHomeLine, formatTotalLine } from './format-stat';
import type { LinePoint } from './game-schema';

export type SlateWindow = 'early' | 'late';

/** Early is before 4:00 PM ET. Late is 4:00 PM ET and primetime. */
export function kickoffWindow(iso: string): SlateWindow {
  const hour = Number(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      hourCycle: 'h23',
    }).format(new Date(iso)),
  );
  return hour < 16 ? 'early' : 'late';
}

/** Home spread and total from a real line point. Null stays the unavailable string. */
export function slateLine(point: LinePoint | null): string {
  if (!point || (point.home_spread === null && point.total === null)) {
    return 'Line n/a (source unavailable)';
  }
  return `Home ${formatHomeLine(point.home_spread)} · Total ${formatTotalLine(point.total)}`;
}
