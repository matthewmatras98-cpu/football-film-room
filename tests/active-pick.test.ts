import { describe, expect, it } from 'vitest';
import { activePickId, activePicks, type ActivePickRef } from '../src/lib/admin/active-pick';

const gameId = '2026_12_AWY_HOM';
const market = 'spread';

function row(pickId: string, status: string): ActivePickRef {
  return { pick_id: pickId, game_id: gameId, market, status };
}

describe('active pick', () => {
  it('allows none, names an active id, and lists only that id for void', () => {
    const base = `${gameId}-${market}`;
    const replacement = `${base}-r2`;
    expect(activePickId([], gameId, market)).toBeNull();
    expect(activePickId([row(base, 'pending')], gameId, market)).toBe(base);
    expect(activePickId([row(base, 'void')], gameId, market)).toBeNull();
    const history = [row(base, 'void'), row(replacement, 'pending')];
    expect(activePickId(history, gameId, market)).toBe(replacement);
    const open = activePicks(history);
    expect(open.map((pick) => pick.pick_id)).toEqual([replacement]);
  });
});
