export const WRITEUP_MAX = 4000;

export interface Writeup {
  pick_id: string;
  game_id: string;
  submitted_at: string;
  body: string;
}

/** `\r\n` becomes `\n`. Control characters other than newline and tab are removed. */
export function sanitizeWriteup(value: string): string {
  const normalized = value.replace(/\r\n/g, '\n');
  let out = '';
  for (const char of normalized) {
    const code = char.charCodeAt(0);
    if (code === 9 || code === 10) {
      out += char;
      continue;
    }
    if (code <= 31 || code === 127) {
      continue;
    }
    out += char;
  }
  return out;
}

export function writeupForPick(pickId: string, writeups: readonly Writeup[]): Writeup | null {
  return writeups.find((entry) => entry.pick_id === pickId) ?? null;
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function decodeHtml(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&gt;/g, '>')
    .replace(/&lt;/g, '<')
    .replace(/&amp;/g, '&');
}

/** http(s), mailto, and same-site relative URLs. javascript: and data: are dropped. */
export function safeHref(escapedUrl: string): string | null {
  const raw = decodeHtml(escapedUrl).trim();
  if (raw.length === 0 || raw.length > 2000) {
    return null;
  }
  const compact = raw.toLowerCase().replace(/[\u0000-\u0020]+/g, '');
  if (compact.startsWith('javascript:') || compact.startsWith('data:') || compact.startsWith('vbscript:')) {
    return null;
  }
  if (raw.startsWith('//')) {
    return null;
  }
  if (raw.startsWith('#') || raw.startsWith('/') || raw.startsWith('./') || raw.startsWith('../')) {
    if (/[\s"<>]/.test(raw)) {
      return null;
    }
    return escapeHtml(raw);
  }
  try {
    const url = new URL(raw);
    if (url.protocol === 'http:' || url.protocol === 'https:' || url.protocol === 'mailto:') {
      return escapeHtml(url.href);
    }
  } catch {
    return null;
  }
  return null;
}

function renderInline(escaped: string): string {
  const slots: string[] = [];
  const slot = (html: string) => {
    const token = `\uE000${slots.length}\uE001`;
    slots.push(html);
    return token;
  };
  let text = escaped.replace(/`([^`]+)`/g, (_match, code: string) => slot(`<code>${code}</code>`));
  text = text.replace(/\[([^\]]+)\]\(((?:[^()]|\([^()]*\))+)\)/g, (_match, label: string, url: string) => {
    const href = safeHref(url.trim());
    if (!href) {
      return label;
    }
    return slot(`<a href="${href}">${label}</a>`);
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  for (let index = 0; index < slots.length; index += 1) {
    text = text.replace(`\uE000${index}\uE001`, slots[index] ?? '');
  }
  return text;
}

/** Markdown with raw HTML escaped. Unsafe link schemes are not emitted. */
export function renderWriteup(source: string): string {
  const escaped = escapeHtml(sanitizeWriteup(source));
  const lines = escaped.split('\n');
  const blocks: string[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? '';
    if (line.trim() === '') {
      index += 1;
      continue;
    }
    if (line.startsWith('```')) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !(lines[index] ?? '').startsWith('```')) {
        code.push(lines[index] ?? '');
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(`<pre><code>${code.join('\n')}</code></pre>`);
      continue;
    }
    if (/^[-*] /.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*] /.test(lines[index] ?? '')) {
        items.push(`<li>${renderInline((lines[index] ?? '').slice(2))}</li>`);
        index += 1;
      }
      blocks.push(`<ul>${items.join('')}</ul>`);
      continue;
    }
    const paragraph: string[] = [];
    while (
      index < lines.length &&
      (lines[index] ?? '').trim() !== '' &&
      !/^[-*] /.test(lines[index] ?? '') &&
      !(lines[index] ?? '').startsWith('```')
    ) {
      paragraph.push(renderInline(lines[index] ?? ''));
      index += 1;
    }
    blocks.push(`<p>${paragraph.join('<br>\n')}</p>`);
  }
  return blocks.join('\n');
}

/**
 * Empty string when there is no write-up file. The component renders that as nothing.
 * `formatTime` is the site's existing clock formatter.
 */
export function writeupMarkup(entry: Writeup | null, formatTime: (iso: string) => string): string {
  if (!entry) {
    return '';
  }
  const when = escapeHtml(entry.submitted_at);
  const label = escapeHtml(formatTime(entry.submitted_at));
  return `<div class="writeup prose"><p class="meta">Submitted (server time) <time datetime="${when}">${label}</time></p><div class="writeup-body">${renderWriteup(entry.body)}</div></div>`;
}
