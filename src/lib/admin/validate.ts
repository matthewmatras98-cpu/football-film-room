import { WRITEUP_MAX, sanitizeWriteup } from '../writeup';
import { openWeekGames, type AdminData, type AdminGame } from './admin-data';
import { isBeforeLock } from './lock';

const GAME_TEAMS = /^(\d{4})_(\d{2})_([A-Z]{2,3})_([A-Z]{2,3})$/;
const LINE_RE = /^[+-]?\d+(?:\.\d+)?$/;

export interface PickCommand {
  game_id: string;
  market: 'spread' | 'total';
  side: string;
  line: string;
  confidence: string;
  writeup: string;
}

export type ValidateResult<T> = { ok: true; value: T } | { ok: false; status: 409 | 422; error: string };

function reject(status: 409 | 422, error: string): ValidateResult<never> {
  return { ok: false, status, error };
}

function teamsFromGameId(gameId: string): { away: string; home: string } | null {
  const match = GAME_TEAMS.exec(gameId);
  if (!match) {
    return null;
  }
  return { away: match[3] ?? '', home: match[4] ?? '' };
}

function knownKickoff(game: AdminGame): boolean {
  return game.kickoff_utc.length > 0 && Number.isFinite(Date.parse(game.kickoff_utc));
}

function isHalfStep(value: number): boolean {
  if (!Number.isFinite(value)) {
    return false;
  }
  const scaled = value * 2;
  return Math.abs(scaled - Math.round(scaled)) < 1e-6;
}

function formatLine(value: number): string {
  const rounded = Math.round(value * 2) / 2;
  if (Object.is(rounded, -0)) {
    return '0';
  }
  return String(rounded);
}

function parseLine(raw: unknown, market: 'spread' | 'total'): string | ValidateResult<never> {
  if (raw === null || raw === undefined || raw === '') {
    return '';
  }
  const text = typeof raw === 'number' ? String(raw) : typeof raw === 'string' ? raw.trim() : null;
  if (text === null) {
    return reject(422, 'Line must be blank or a number in 0.5 steps.');
  }
  if (text === '') {
    return '';
  }
  if (!LINE_RE.test(text)) {
    return reject(422, 'Line must be blank or a number in 0.5 steps.');
  }
  const value = Number(text);
  if (!isHalfStep(value)) {
    return reject(422, 'Line must be blank or a number in 0.5 steps.');
  }
  if (market === 'spread' && Math.abs(value) > 30) {
    return reject(422, 'Spread line must be between -30 and 30.');
  }
  if (market === 'total' && (value < 20 || value > 80)) {
    return reject(422, 'Total line must be between 20 and 80.');
  }
  return formatLine(value);
}

function parseConfidence(raw: unknown): string | null {
  if (raw === null || raw === undefined || raw === '') {
    return '';
  }
  if (raw === 1 || raw === 2 || raw === 3) {
    return String(raw);
  }
  if (raw === '1' || raw === '2' || raw === '3') {
    return raw;
  }
  return null;
}

export function validatePick(body: unknown, data: AdminData, now: Date): ValidateResult<PickCommand> {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return reject(422, 'Body must be a JSON object.');
  }
  const record = body as Record<string, unknown>;
  if (typeof record.game_id !== 'string' || record.game_id.length === 0) {
    return reject(422, 'game_id is required.');
  }
  if (record.market !== 'spread' && record.market !== 'total') {
    return reject(422, 'Market must be spread or total.');
  }
  const market = record.market;
  const game = openWeekGames(data).find((row) => row.game_id === record.game_id);
  if (!game) {
    return reject(422, 'That game is not on the open week.');
  }
  if (!knownKickoff(game)) {
    return reject(422, 'Kickoff time is unknown for that game.');
  }
  const teams = teamsFromGameId(game.game_id);
  if (!teams) {
    return reject(422, 'That game id is not a schedule id.');
  }
  if (typeof record.side !== 'string') {
    return reject(422, 'Side is required.');
  }
  const side = record.side;
  if (market === 'spread' && side !== teams.away && side !== teams.home) {
    return reject(422, 'Side must be the away or home abbreviation on the game id.');
  }
  if (market === 'total' && side !== 'over' && side !== 'under') {
    return reject(422, 'Side must be over or under.');
  }
  const line = parseLine(record.line, market);
  if (typeof line !== 'string') {
    return line;
  }
  const confidence = parseConfidence(record.confidence);
  if (confidence === null) {
    return reject(422, 'Confidence must be blank, 1, 2, or 3.');
  }
  if (record.writeup !== undefined && record.writeup !== null && typeof record.writeup !== 'string') {
    return reject(422, 'Write-up must be text.');
  }
  const writeup = sanitizeWriteup(typeof record.writeup === 'string' ? record.writeup : '');
  if (writeup.length > WRITEUP_MAX) {
    return reject(422, 'Write-up must be 4000 characters or fewer.');
  }
  if (data.picks.some((pick) => pick.game_id === game.game_id && pick.market === market)) {
    return reject(409, 'A pick already exists for this game and market.');
  }
  if (!isBeforeLock(now.getTime(), game.kickoff_utc)) {
    return reject(409, 'Too late. Picks close 10 minutes before kickoff.');
  }
  return {
    ok: true,
    value: { game_id: game.game_id, market, side, line, confidence, writeup },
  };
}

export function validateVoid(body: unknown, data: AdminData, now: Date): ValidateResult<{ pick_id: string }> {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return reject(422, 'Body must be a JSON object.');
  }
  const pickId = (body as Record<string, unknown>).pick_id;
  if (typeof pickId !== 'string' || pickId.length === 0) {
    return reject(422, 'pick_id is required.');
  }
  const pick = data.picks.find((row) => row.pick_id === pickId);
  if (!pick) {
    return reject(422, 'That pick is not on the ledger.');
  }
  if (pick.status === 'void') {
    return reject(409, 'That pick is already void.');
  }
  if (!pick.kickoff_utc || !Number.isFinite(Date.parse(pick.kickoff_utc))) {
    return reject(422, 'Kickoff time is unknown for that pick.');
  }
  if (!isBeforeLock(now.getTime(), pick.kickoff_utc)) {
    return reject(409, 'Too late. Voids close 10 minutes before kickoff.');
  }
  return { ok: true, value: { pick_id: pick.pick_id } };
}
