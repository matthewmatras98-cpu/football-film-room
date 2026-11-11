export interface PickedLine {
  market: string;
  side: string;
  home: string;
  away: string;
  homeSpread: number | null;
  awaySpread?: number | null;
  total: number | null;
}

/** `-0` is `0`. Other numbers stay as written. */
export function formatPickedLine(value: number): string {
  if (!Number.isFinite(value)) {
    return '';
  }
  if (value === 0 || Object.is(value, -0)) {
    return '0';
  }
  return String(value);
}

/**
 * Spread lines are from the picked side. Home keeps `home_spread`.
 * Away uses `away_spread` when admin data has it, otherwise the negation of `home_spread`.
 * Totals are the snapshot total.
 */
export function prefillLine(source: PickedLine): string {
  if (source.market === 'total') {
    return source.total === null ? '' : formatPickedLine(source.total);
  }
  if (source.market !== 'spread') {
    return '';
  }
  if (source.side === source.home) {
    return source.homeSpread === null ? '' : formatPickedLine(source.homeSpread);
  }
  if (source.side !== source.away) {
    return '';
  }
  if (typeof source.awaySpread === 'number' && Number.isFinite(source.awaySpread)) {
    return formatPickedLine(source.awaySpread);
  }
  if (source.homeSpread === null) {
    return '';
  }
  return formatPickedLine(-source.homeSpread);
}
