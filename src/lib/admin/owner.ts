/**
 * GitHub login allowed to open /admin/pick.
 * Numeric id from the public user API:
 * https://api.github.com/users/matthewmatras98-cpu
 */
export const OWNER_LOGIN = 'matthewmatras98-cpu';
export const OWNER_GITHUB_ID = 350931718;

export function isOwner(login: unknown, id: unknown): boolean {
  return login === OWNER_LOGIN && id === OWNER_GITHUB_ID;
}
