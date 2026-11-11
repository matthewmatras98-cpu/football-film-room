export const DISPATCH_URL =
  'https://api.github.com/repos/matthewmatras98-cpu/football-film-room/actions/workflows/pick-intake.yml/dispatches';

export const RUNS_URL =
  'https://api.github.com/repos/matthewmatras98-cpu/football-film-room/actions/workflows/pick-intake.yml/runs?per_page=100';

export const DISPATCH_KEYS = [
  'action',
  'request_id',
  'game_id',
  'market',
  'side',
  'line',
  'confidence',
  'writeup',
  'server_received_at',
  'void_pick_id',
] as const;

export type DispatchKey = (typeof DISPATCH_KEYS)[number];
export type DispatchInputs = Record<DispatchKey, string>;

export const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export function isUuidV4(value: string): boolean {
  return UUID_V4.test(value);
}

export type RunState = 'queued' | 'in_progress' | 'success' | 'failure';

export interface WorkflowRun {
  name?: string;
  display_title?: string;
  status?: string;
  conclusion?: string | null;
}

function blankInputs(): DispatchInputs {
  return {
    action: '',
    request_id: '',
    game_id: '',
    market: '',
    side: '',
    line: '',
    confidence: '',
    writeup: '',
    server_received_at: '',
    void_pick_id: '',
  };
}

export function pickInputs(
  fields: {
    game_id: string;
    market: string;
    side: string;
    line: string;
    confidence: string;
    writeup: string;
  },
  meta: { requestId: string; receivedAt: string },
): DispatchInputs {
  return {
    action: 'pick',
    request_id: meta.requestId,
    game_id: fields.game_id,
    market: fields.market,
    side: fields.side,
    line: fields.line,
    confidence: fields.confidence,
    writeup: fields.writeup,
    server_received_at: meta.receivedAt,
    void_pick_id: '',
  };
}

export function voidInputs(pickId: string, meta: { requestId: string; receivedAt: string }): DispatchInputs {
  return {
    ...blankInputs(),
    action: 'void',
    request_id: meta.requestId,
    server_received_at: meta.receivedAt,
    void_pick_id: pickId,
  };
}

export function githubHeaders(token: string): Record<string, string> {
  return {
    accept: 'application/vnd.github+json',
    authorization: `Bearer ${token}`,
    'content-type': 'application/json',
    'user-agent': 'football-film-room',
    'x-github-api-version': '2022-11-28',
  };
}

export async function sendDispatch(
  token: string,
  inputs: DispatchInputs,
  fetchImpl: typeof fetch,
): Promise<{ ok: true } | { ok: false }> {
  try {
    const response = await fetchImpl(DISPATCH_URL, {
      method: 'POST',
      redirect: 'manual',
      headers: githubHeaders(token),
      body: JSON.stringify({ ref: 'main', inputs }),
    });
    if (response.status < 200 || response.status >= 300) {
      return { ok: false };
    }
    return { ok: true };
  } catch {
    return { ok: false };
  }
}

export function runTitle(action: 'pick' | 'void', requestId: string): string {
  return `pick-intake ${action} ${requestId}`;
}

export function mapRunState(status: string | undefined, conclusion: string | null | undefined): RunState {
  if (status === 'in_progress') {
    return 'in_progress';
  }
  if (status === 'completed') {
    return conclusion === 'success' ? 'success' : 'failure';
  }
  return 'queued';
}

/**
 * GitHub puts `run-name` on `name` or `display_title` depending on the API field.
 * A run that has not appeared yet is still queued.
 */
export function stateFromRuns(runs: readonly WorkflowRun[], requestId: string): RunState {
  const wanted = new Set([runTitle('pick', requestId), runTitle('void', requestId)]);
  const match = runs.find((run) => wanted.has(run.name ?? '') || wanted.has(run.display_title ?? ''));
  if (!match) {
    return 'queued';
  }
  return mapRunState(match.status, match.conclusion);
}
