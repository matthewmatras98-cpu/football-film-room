import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { picksViewSchema } from '../src/lib/picks-schema';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const viewFixture = path.join(root, 'tests/fixtures/picks_view.json');

describe('re-pick fixture', () => {
  it('builds a voided pick and a graded -r2 replacement through verify-dist', () => {
    const view = picksViewSchema.parse(JSON.parse(readFileSync(viewFixture, 'utf8')));
    const original = view.find((row) => row.pick_id === '2099_01_DET_PHI-spread');
    const replacement = view.find((row) => row.pick_id === '2099_01_DET_PHI-spread-r2');
    expect(original?.status).toBe('void');
    expect(original?.game_id).toBe('2099_01_DET_PHI');
    expect(replacement?.status).toBe('graded');
    expect(replacement?.result).toBe('W');
    expect(replacement?.game_id).toBe(original?.game_id);
    expect(replacement?.market).toBe(original?.market);

    const build = run('npm', ['run', 'build']);
    expect(build.status, tail(build)).toBe(0);
    const verified = run('node', ['scripts/verify-dist.mjs']);
    expect(verified.status, tail(verified)).toBe(0);
  }, 300_000);
});

function run(command: string, args: string[]) {
  return spawnSync(command, args, {
    cwd: root,
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${process.env.HOME ?? ''}/.local/bin${path.delimiter}${process.env.PATH ?? ''}`,
      ASTRO_TELEMETRY_DISABLED: '1',
    },
  });
}

function tail(result: { stdout?: string | null; stderr?: string | null }): string {
  return `${result.stderr ?? ''}\n${result.stdout ?? ''}`.slice(-4000);
}
