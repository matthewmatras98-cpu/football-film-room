import type { PagesEnv } from '../../src/lib/admin/env';
import { handleVoid } from '../../src/lib/admin/handlers';

export function onRequestPost(context: { request: Request; env: PagesEnv }): Promise<Response> {
  return handleVoid(context.request, context.env);
}
