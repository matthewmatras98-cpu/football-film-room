import type { APIRoute } from 'astro';
import { buildAdminData } from '../lib/admin/admin-data';
import { getAllGames } from '../lib/games';
import { picks } from '../lib/picks';

export const prerender = true;

/** Schedule and ledger snapshot baked into dist for Pages Functions. No secrets. */
export const GET: APIRoute = async () => {
  const games = await getAllGames();
  const data = buildAdminData(games, picks);
  return new Response(JSON.stringify(data), {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
};
