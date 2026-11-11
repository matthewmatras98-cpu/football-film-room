import { handleMiddleware, type PagesContext } from '../src/lib/admin/middleware';

export function onRequest(context: PagesContext): Promise<Response> {
  return handleMiddleware(context);
}
