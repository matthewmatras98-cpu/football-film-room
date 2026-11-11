import type { PagesEnv } from '../../src/lib/admin/env';
import { handleStatus } from '../../src/lib/admin/handlers';

export function onRequestGet(context: { request: Request; env: PagesEnv }): Promise<Response> {
  return handleStatus(context.request, context.env);
}