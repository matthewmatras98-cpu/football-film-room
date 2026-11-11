import { handleLogin } from '../../src/lib/admin/handlers';
import type { PagesEnv } from '../../src/lib/admin/env';

export function onRequestGet(context: { request: Request; env: PagesEnv }): Promise<Response> {
  return handleLogin(context.request, context.env);
}
