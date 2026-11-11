import { readFile } from 'node:fs/promises';
import { getCollection } from 'astro:content';
import { writeupForPick, type Writeup } from './writeup';

export { writeupForPick };

function stripFrontmatter(source: string): string {
  return source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
}

/** One markdown file per pick. An empty collection is an empty list. */
export async function getWriteups(): Promise<Writeup[]> {
  const entries = await getCollection('picks');
  const writeups: Writeup[] = [];
  for (const entry of entries) {
    const record = entry as {
      id: string;
      body?: string;
      filePath?: string;
      data: { pick_id: string; game_id: string; submitted_at: string };
    };
    if (record.id !== record.data.pick_id) {
      throw new Error(
        `src/content/picks/${record.id}.md declares pick_id "${record.data.pick_id}". The file name must match pick_id.`,
      );
    }
    let body = record.body ?? '';
    if (body.length === 0 && record.filePath) {
      body = stripFrontmatter(await readFile(record.filePath, 'utf8'));
    }
    writeups.push({
      pick_id: record.data.pick_id,
      game_id: record.data.game_id,
      submitted_at: record.data.submitted_at,
      body,
    });
  }
  return writeups;
}
