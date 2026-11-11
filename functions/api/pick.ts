import type { PagesEnv } from '../../src/lib/admin/env';
import { handlePick } from '../../src/lib/admin/handlers';

export function onRequestPost(context: { request: Request; env: PagesEnv }): Promise<Response> {
  return handlePick(context.request, context.env);
}
