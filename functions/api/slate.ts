import type { PagesEnv } from '../../src/lib/admin/env';
import { handleSlate } from '../../src/lib/admin/handlers';

export function onRequestGet(context: { request: Request; env: PagesEnv }): Promise<Response> {
  return handleSlate(context.request, context.env);
}
