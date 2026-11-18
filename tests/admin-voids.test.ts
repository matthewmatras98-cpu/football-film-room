import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { buildAdminData, parseAdminData, type AdminData } from '../src/lib/admin/admin-data';
import type { PagesEnv } from '../src/lib/admin/env';
import type { HandlerDeps } from '../src/lib/admin/handlers';
import { handleSlate } from '../src/lib/admin/handlers';
import { issueSession } from '../src/lib/admin/session';
import { OWNER_GITHUB_ID, OWNER_LOGIN } from '../src/lib/admin/owner';
import { selectableGames } from '../src/lib/admin/open-week';
import { picks } from '../src/lib/picks';
import type { PickView } from '../src/lib/picks-schema';
import { previousPickId, voidsByWeek } from '../src/lib/admin/voids';

const SECRET = 'session-secret';
const env: PagesEnv = {
  GITHUB_OAUTH_CLIENT_ID: 'client-id',
  GITHUB_OAUTH_CLIENT_SECRET: 'client-secret',
  GH_DISPATCH_TOKEN: 'dispatch-token',
  SESSION_SECRET: SECRET,
};

function fixtureView(): PickView[] {
  return JSON.parse(readFileSync(new URL('./fixtures/picks_view.json', import.meta.url), 'utf8')) as PickView[];
}

function schedule(): AdminData {
  return {
    open_week: { season: 2026, week: 12 },
    games: [
      {
        game_id: '2026_12_AWY_HOM',
        season: 2026,
        week: 12,
        away: 'AWY',
        home: 'HOM',
        kickoff_utc: '2026-11-22T18:00:00.000Z',
        home_spread: -3.5,
        away_spread: null,
        total: 45.5,
        final: false,
      },
    ],
    picks: [],
  };
}

describe('voids from picks_view.json', () => {
  it('links -r2 through previous_pick_id and leaves every other status out', () => {
    const view = fixtureView();
    expect(previousPickId('2099_01_DET_PHI-spread-r2', '2099_01_DET_PHI', 'spread')).toBe('2099_01_DET_PHI-spread');
    expect(previousPickId('2099_01_DET_PHI-spread', '2099_01_DET_PHI', 'spread')).toBeNull();
    expect(previousPickId('2099_01_DET_PHI-spread-r1', '2099_01_DET_PHI', 'spread')).toBeNull();

    const voids = voidsByWeek(view);
    expect(Object.keys(voids)).toEqual(['1']);
    expect(voids['1']).toEqual([
      {
        game_id: '2099_01_ARI_LAR',
        week: 1,
        pick: {
          side: 'LAR',
          market: 'spread',
          line_at_pick: -3,
          picked_at: '2099-09-13T15:00:00Z',
        },
        voided_at: '2099-09-13T16:00:00Z',
        repick: null,
      },
      {
        game_id: '2099_01_DET_PHI',
        week: 1,
        pick: {
          side: 'DET',
          market: 'spread',
          line_at_pick: 2.5,
          picked_at: '2099-09-13T15:00:00Z',
        },
        voided_at: '2099-09-13T16:00:00Z',
        repick: {
          pick_id: '2099_01_DET_PHI-spread-r2',
          side: 'DET',
          line_at_pick: 2.5,
          picked_at: '2099-09-13T15:30:00Z',
        },
      },
    ]);

    const attached = voids['1']?.flatMap((row) => [row.game_id, row.repick?.pick_id ?? '']) ?? [];
    expect(attached).not.toContain('2099_01_KC_BUF-spread');
    expect(attached).not.toContain('2099_01_BAL_PIT-spread');
    expect(attached).not.toContain('2099_01_DET_PHI-total');
  });

  it('does not attach a rejected_late re-pick or a non-adjacent -r3', () => {
    const view = fixtureView();
    const rejected = view.map((row) =>
      row.pick_id === '2099_01_DET_PHI-spread-r2' ? { ...row, status: 'rejected_late' as const } : row,
    );
    expect(voidsByWeek(rejected)['1']?.[1]?.repick).toBeNull();

    const skipped = view.flatMap((row) => {
      if (row.pick_id !== '2099_01_DET_PHI-spread-r2') {
        return [row];
      }
      return [{ ...row, pick_id: '2099_01_DET_PHI-spread-r3' }];
    });
    expect(previousPickId('2099_01_DET_PHI-spread-r3', '2099_01_DET_PHI', 'spread')).toBe('2099_01_DET_PHI-spread-r2');
    expect(voidsByWeek(skipped)['1']?.[1]?.repick).toBeNull();
  });

  it('reads the published view and adds voids without changing games', async () => {
    const baked = buildAdminData([], picks);
    expect(baked.voids).toEqual(voidsByWeek(picks));
    expect(parseAdminData(JSON.parse(JSON.stringify(baked)))?.voids).toEqual(baked.voids);
    const now = Date.parse('2026-11-22T17:49:00.000Z');
    const data = schedule();
    const issued = await issueSession(SECRET, OWNER_GITHUB_ID, OWNER_LOGIN, new Date(now));
    const deps: HandlerDeps = {
      fetch: globalThis.fetch,
      now: () => new Date(now),
      uuid: () => crypto.randomUUID(),
      loadAdminData: async () => data,
      loadPicksView: () => fixtureView(),
    };
    const response = await handleSlate(
      new Request('https://film.example/api/slate', {
        headers: { cookie: `ffr_session=${encodeURIComponent(issued.token)}` },
      }),
      env,
      deps,
    );
    expect(response.status).toBe(200);
    const body = (await response.json()) as { games: unknown; voids: unknown };
    expect(Object.keys(body).sort()).toEqual(['games', 'voids']);
    expect(body.games).toEqual(selectableGames(data.games, new Date(now)));
    expect(body.voids).toEqual(voidsByWeek(fixtureView()));
  });
});
