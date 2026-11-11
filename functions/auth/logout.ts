import type { PagesEnv } from '../../src/lib/admin/env';
import { handleLogout } from '../../src/lib/admin/handlers';

export function onRequestGet(context: { request: Request; env: PagesEnv }): Response {
  return handleLogout(context.request);
}
