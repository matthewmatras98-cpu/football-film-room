/** Display helpers for the public pick record. Units only, and every clock time is MST. */

export const EM_DASH = '\u2014';

export const STATUS_LABEL = {
  graded: 'Final',
  pending: 'Pending',
  ungraded: 'Ungraded',
  rejected_late: 'Rejected \u2013 late',
  void: 'Void',
} as const;

export type StatusLabel = keyof typeof STATUS_LABEL;

/** Visible marker text. `odds_api` and a null source have no marker. */
export const CLOSE_MARKER: Record<string, { text: string; title: string }> = {
  odds_api_thin: {
    text: 'thin close',
    title: '1-2 books. The lower-middle line of those books is used.',
  },
  odds_api_stale: {
    text: 'stale close',
    title: 'The snapshot is more than 180 minutes before kickoff.',
  },
  nflverse: {
    text: 'nflverse line',
    title: 'No books, so the nflverse line is used.',
  },
};

export function closeMarker(source: string | null): { text: string; title: string } | null {
  if (!source) {
    return null;
  }
  return CLOSE_MARKER[source] ?? null;
}

/** +0.909u, −1.000u, 0.000u. Null stays an em dash and is never shown as zero. */
export function formatUnits(value: number | null): string {
  if (value === null) {
    return EM_DASH;
  }
  const fixed = value.toFixed(3);
  if (value > 0) {
    return `+${fixed}u`;
  }
  if (value < 0) {
    return `\u2212${fixed.slice(1)}u`;
  }
  return '0.000u';
}

/** Signed CLV with three decimal places. Null stays an em dash. */
export function formatClv(value: number | null): string {
  if (value === null) {
    return EM_DASH;
  }
  const fixed = value.toFixed(3);
  if (value > 0) {
    return `+${fixed}`;
  }
  if (value < 0) {
    return `\u2212${fixed.slice(1)}`;
  }
  return '0.000';
}

/** A ratio from summary.json, such as 0.429, shown as 42.9%. Null stays an em dash. */
export function formatPct(value: number | null): string {
  if (value === null) {
    return EM_DASH;
  }
  return `${(value * 100).toFixed(1)}%`;
}

/** Win % is the summary figure. This does not recompute W / (W + L). */
export function formatWinPct(summary: { win_pct: number | null }): string {
  return formatPct(summary.win_pct);
}

/** Spread line from the picked side, with an explicit sign. */
export function formatSignedLine(value: number | null): string {
  if (value === null) {
    return EM_DASH;
  }
  const magnitude = formatMagnitude(Math.abs(value));
  if (value > 0) {
    return `+${magnitude}`;
  }
  if (value < 0) {
    return `\u2212${magnitude}`;
  }
  return magnitude;
}

export function formatPickLine(market: 'spread' | 'total', value: number | null): string {
  if (value === null) {
    return EM_DASH;
  }
  if (market === 'total') {
    return formatMagnitude(value);
  }
  return formatSignedLine(value);
}

export function formatSideLine(market: 'spread' | 'total', side: string, line: number | null): string {
  return `${side} ${formatPickLine(market, line)}`;
}

export function formatMst(iso: string | null): string {
  if (!iso) {
    return EM_DASH;
  }
  const formatted = new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'America/Phoenix',
  }).format(new Date(iso));
  return `${formatted.replace(/[\u00a0\u202f]/g, ' ')} MST`;
}

const GAME_TEAMS = /^(\d{4})_(\d{2})_([A-Z]{2,3})_([A-Z]{2,3})$/;

export function matchupTeams(gameId: string): { away: string; home: string } | null {
  const match = GAME_TEAMS.exec(gameId);
  const away = match?.[3];
  const home = match?.[4];
  if (!away || !home) {
    return null;
  }
  return { away, home };
}

export function matchupLabel(gameId: string): string {
  const teams = matchupTeams(gameId);
  if (!teams) {
    return gameId;
  }
  return `${teams.away} at ${teams.home}`;
}

export function formatFinal(gameId: string, finalAway: number | null, finalHome: number | null): string {
  if (finalAway === null || finalHome === null) {
    return EM_DASH;
  }
  const match = GAME_TEAMS.exec(gameId);
  if (!match) {
    return `${finalAway}\u2013${finalHome}`;
  }
  const away = match[3] ?? '';
  const home = match[4] ?? '';
  return `${away} ${finalAway}, ${home} ${finalHome}`;
}

function formatMagnitude(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  if (Number.isInteger(rounded)) {
    return rounded.toFixed(1);
  }
  const text = rounded.toFixed(2);
  return text.endsWith('0') ? text.slice(0, -1) : text;
}
