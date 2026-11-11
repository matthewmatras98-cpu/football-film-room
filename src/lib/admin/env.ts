export interface PagesEnv {
  GITHUB_OAUTH_CLIENT_ID?: string;
  GITHUB_OAUTH_CLIENT_SECRET?: string;
  GH_DISPATCH_TOKEN?: string;
  SESSION_SECRET?: string;
  ASSETS?: { fetch(input: Request | URL | string): Promise<Response> };
}

export interface AppConfig {
  clientId: string;
  clientSecret: string;
  dispatchToken: string;
  sessionSecret: string;
}

function present(value: string | undefined): value is string {
  return typeof value === 'string' && value.length > 0;
}

/** All four secrets are required. Empty strings count as missing. Values are never logged. */
export function readConfig(env: PagesEnv): AppConfig | null {
  if (
    !present(env.GITHUB_OAUTH_CLIENT_ID) ||
    !present(env.GITHUB_OAUTH_CLIENT_SECRET) ||
    !present(env.GH_DISPATCH_TOKEN) ||
    !present(env.SESSION_SECRET)
  ) {
    return null;
  }
  return {
    clientId: env.GITHUB_OAUTH_CLIENT_ID,
    clientSecret: env.GITHUB_OAUTH_CLIENT_SECRET,
    dispatchToken: env.GH_DISPATCH_TOKEN,
    sessionSecret: env.SESSION_SECRET,
  };
}
