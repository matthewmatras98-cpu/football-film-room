import { formatPickLine, formatSignedLine } from './format-picks';
import type { StatNumber } from './game-schema';

/** Exact missing-data string. A null stat is never rendered as 0 or left blank. */
export const SOURCE_UNAVAILABLE = 'n/a (source unavailable)';

export const STAT_KINDS = [
  'epa',
  'rate',
  'cpoe',
  'seconds',
  'number',
  'precip',
  'temp',
  'wind',
] as const;

export type StatKind = (typeof STAT_KINDS)[number];

function assertReal(text: string): string {
  if (text === SOURCE_UNAVAILABLE) {
    throw new Error('a real stat formatted as unavailable');
  }
  return text;
}

function signed(value: number, digits: number, suffix = ''): string {
  const magnitude = Math.abs(value).toFixed(digits);
  if (value > 0) {
    return assertReal(`+${magnitude}${suffix}`);
  }
  if (value < 0) {
    return assertReal(`\u2212${magnitude}${suffix}`);
  }
  return assertReal(`${(0).toFixed(digits)}${suffix}`);
}

function percentFromFraction(value: number): string {
  return assertReal(`${(value * 100).toFixed(1)}%`);
}

function alreadyPercent(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  const text = Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1);
  return assertReal(`${text}%`);
}

/**
 * Raw StatNumber display. Null is the unavailable string. Zero is the character
 * "0" only when the value is actually 0.
 */
export function formatStat(stat: { value: number | null } | null | undefined): string {
  if (stat == null || stat.value === null) {
    return SOURCE_UNAVAILABLE;
  }
  if (stat.value === 0) {
    return '0';
  }
  const text = Number.isInteger(stat.value) ? String(stat.value) : String(Number(stat.value.toFixed(3)));
  return assertReal(text);
}

/** Signed EPA to three decimals. Negatives use a real minus, the same as a line. */
export function formatEpaValue(value: number): string {
  return formatStatKind({ value }, 'epa');
}

const EPA_DECIMAL = /-?\d+\.\d+/g;

/**
 * Rounds EPA figures inside a story sentence. Other prompts stay as written.
 * The stored card is not changed.
 */
export function formatEpaStory(title: string, body: string): string {
  if (!/epa/i.test(title)) {
    return body;
  }
  return body.replace(EPA_DECIMAL, (token) => formatEpaValue(Number(token)));
}

export function formatStatKind(stat: { value: number | null } | null | undefined, kind: StatKind): string {
  if (stat == null || stat.value === null) {
    return SOURCE_UNAVAILABLE;
  }
  const value = stat.value;
  switch (kind) {
    case 'epa':
      return signed(value, 3);
    case 'rate':
      return percentFromFraction(value);
    case 'cpoe':
      return signed(value, 1, ' pp');
    case 'seconds':
      return assertReal(`${value.toFixed(2)} s`);
    case 'precip':
      return alreadyPercent(value);
    case 'temp': {
      const rounded = Math.round(value);
      const body = rounded < 0 ? `\u2212${Math.abs(rounded)}` : String(rounded);
      return assertReal(`${body}\u00b0F`);
    }
    case 'wind': {
      const rounded = Math.round(value * 10) / 10;
      const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
      return assertReal(`${text} mph`);
    }
    case 'number':
      return formatStat(stat);
    default: {
      const exhaustive: never = kind;
      return exhaustive;
    }
  }
}

export function statFootnote(stat: Pick<StatNumber, 'source' | 'sample' | 'range'>): string {
  const parts = [stat.source];
  if (stat.sample !== null) {
    parts.push(`n=${stat.sample}`);
  }
  if (stat.range) {
    parts.push(stat.range);
  }
  return parts.join(' \u00b7 ');
}

export function formatHomeLine(value: number | null): string {
  if (value === null) {
    return SOURCE_UNAVAILABLE;
  }
  return formatSignedLine(value);
}

export function formatTotalLine(value: number | null): string {
  if (value === null) {
    return SOURCE_UNAVAILABLE;
  }
  return formatPickLine('total', value);
}

export function formatRest(days: number | null): string {
  if (days === null) {
    return SOURCE_UNAVAILABLE;
  }
  return days === 1 ? '1 day' : `${days} days`;
}

export function formatText(value: string | null): string {
  if (value === null || value.trim() === '') {
    return SOURCE_UNAVAILABLE;
  }
  return value;
}

export function formatPlayer(player: string | null): string {
  if (player === null || player.trim() === '') {
    return SOURCE_UNAVAILABLE;
  }
  return player;
}
