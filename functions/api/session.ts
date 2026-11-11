import type { PagesEnv } from '../../src/lib/admin/env';
import { handleSession } from '../../src/lib/admin/handlers';

export function onRequestGet(context: { request: Request; env: PagesEnv }): Promise<Response> {
  return handleSession(context.request, context.env);
}
