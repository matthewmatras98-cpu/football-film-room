import { formatSpreadTeam, formatTotalLine } from './format-stat';
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

/** Home team's spread and the total. A missing line stays the unavailable string. */
export function slateLine(point: LinePoint | null, home: string): string {
  if (!point || (point.home_spread === null && point.total === null)) {
    return 'Line n/a (source unavailable)';
  }
  return `${formatSpreadTeam(home, point.home_spread)} · Total ${formatTotalLine(point.total)}`;
}
