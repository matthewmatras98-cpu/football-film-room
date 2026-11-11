import { isBeforeLock } from '../lib/admin/lock';

function selectedOption(select: HTMLSelectElement): HTMLOptionElement | null {
  return select.selectedOptions[0] ?? null;
}

function fillSides(sideSelect: HTMLSelectElement, sides: string[], previous: string): void {
  sideSelect.replaceChildren();
  for (const side of sides) {
    const option = document.createElement('option');
    option.value = side;
    option.textContent = side;
    sideSelect.append(option);
  }
  if (sides.includes(previous)) {
    sideSelect.value = previous;
  }
}

function syncLine(market: string, option: HTMLOptionElement | null, lineInput: HTMLInputElement): void {
  if (!option) {
    lineInput.value = '';
    return;
  }
  lineInput.value = market === 'total' ? (option.dataset.total ?? '') : (option.dataset.spread ?? '');
}

async function readSession(): Promise<{ login: string; csrf: string } | null> {
  const response = await fetch('/api/session', { headers: { accept: 'application/json' } });
  if (!response.ok) {
    return null;
  }
  const body = (await response.json()) as { login?: unknown; csrf?: unknown };
  if (typeof body.csrf !== 'string' || body.csrf.length === 0) {
    return null;
  }
  return { login: typeof body.login === 'string' ? body.login : '', csrf: body.csrf };
}

async function poll(requestId: string, message: HTMLElement, detail: HTMLElement, successText: string): Promise<void> {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    const response = await fetch('/api/status?request_id=' + encodeURIComponent(requestId), {
      headers: { accept: 'application/json' },
    });
    let body: { state?: string; error?: string } = {};
    try {
      body = (await response.json()) as { state?: string; error?: string };
    } catch {
      body = {};
    }
    if (!response.ok) {
      message.textContent = body.error || 'Status check failed.';
      return;
    }
    if (body.state === 'success') {
      message.textContent = successText;
      detail.textContent = 'success';
      return;
    }
    if (body.state === 'failure') {
      message.textContent = 'Dispatch failed.';
      detail.textContent = 'failure';
      return;
    }
    message.textContent = successText;
    detail.textContent = body.state || 'queued';
    await new Promise((resolve) => {
      window.setTimeout(resolve, 2000);
    });
  }
}

export function bootAdminPick(): void {
  const form = document.querySelector<HTMLFormElement>('#pick-form');
  const gameSelect = document.querySelector<HTMLSelectElement>('#game_id');
  const marketSelect = document.querySelector<HTMLSelectElement>('#market');
  const sideSelect = document.querySelector<HTMLSelectElement>('#side');
  const lineInput = document.querySelector<HTMLInputElement>('#line');
  const confidenceSelect = document.querySelector<HTMLSelectElement>('#confidence');
  const writeup = document.querySelector<HTMLTextAreaElement>('#writeup');
  const counter = document.querySelector<HTMLElement>('#writeup-count');
  const message = document.querySelector<HTMLElement>('#pick-message');
  const detail = document.querySelector<HTMLElement>('#pick-poll');
  const signedIn = document.querySelector<HTMLElement>('#signed-in');
  if (!message || !detail) {
    return;
  }

  const formReady = Boolean(form && gameSelect && marketSelect && sideSelect && lineInput && confidenceSelect && writeup);

  const refreshSides = (keepSide: boolean) => {
    if (!gameSelect || !marketSelect || !sideSelect || !lineInput) {
      return;
    }
    const option = selectedOption(gameSelect);
    const previous = sideSelect.value;
    const sides =
      marketSelect.value === 'total'
        ? ['over', 'under']
        : [option?.dataset.away ?? '', option?.dataset.home ?? ''].filter((side) => side.length > 0);
    fillSides(sideSelect, sides, keepSide ? previous : '');
    syncLine(marketSelect.value, option, lineInput);
  };

  if (formReady && gameSelect && marketSelect && writeup) {
    gameSelect.addEventListener('change', () => refreshSides(false));
    marketSelect.addEventListener('change', () => refreshSides(false));
    refreshSides(true);
    const updateCount = () => {
      if (counter) {
        counter.textContent = String(writeup.value.length);
      }
    };
    writeup.addEventListener('input', updateCount);
    updateCount();
  }

  let csrf = '';
  void readSession().then((session) => {
    if (!session) {
      message.textContent = 'Sign in required.';
      return;
    }
    csrf = session.csrf;
    if (signedIn) {
      signedIn.textContent = session.login ? 'Signed in as ' + session.login : 'Signed in';
    }
  });

  const now = Date.now();
  const rows = document.querySelectorAll<HTMLLIElement>('#open-picks li');
  rows.forEach((row) => {
    const kickoff = row.dataset.kickoff ?? '';
    const kickoffMs = Date.parse(kickoff);
    if (!kickoff || !Number.isFinite(kickoffMs) || now >= kickoffMs) {
      row.remove();
      return;
    }
    const button = row.querySelector<HTMLButtonElement>('button');
    if (button) {
      button.disabled = !isBeforeLock(now, kickoff);
    }
  });
  const list = document.querySelector<HTMLUListElement>('#open-picks');
  const empty = document.querySelector<HTMLElement>('#open-empty');
  if (list && empty) {
    const remaining = list.querySelectorAll('li').length;
    empty.hidden = remaining > 0;
    list.hidden = remaining === 0;
  }

  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!formReady || !gameSelect || !marketSelect || !sideSelect || !lineInput || !confidenceSelect || !writeup) {
      return;
    }
    void (async () => {
      if (!csrf) {
        const session = await readSession();
        if (!session) {
          message.textContent = 'Sign in required.';
          return;
        }
        csrf = session.csrf;
      }
      message.textContent = '';
      detail.textContent = '';
      const response = await fetch('/api/pick', {
        method: 'POST',
        headers: {
          accept: 'application/json',
          'content-type': 'application/json',
          'x-csrf-token': csrf,
        },
        body: JSON.stringify({
          game_id: gameSelect.value,
          market: marketSelect.value,
          side: sideSelect.value,
          line: lineInput.value.trim(),
          confidence: confidenceSelect.value,
          writeup: writeup.value,
        }),
      });
      let body: { request_id?: string; error?: string } = {};
      try {
        body = (await response.json()) as { request_id?: string; error?: string };
      } catch {
        body = {};
      }
      if (!response.ok || !body.request_id) {
        message.textContent = body.error || 'The pick was not saved.';
        return;
      }
      message.textContent = 'Saved, live in a few minutes';
      await poll(body.request_id, message, detail, 'Saved, live in a few minutes');
    })();
  });

  document.querySelectorAll<HTMLButtonElement>('[data-void]').forEach((button) => {
    button.addEventListener('click', () => {
      void (async () => {
        if (button.disabled) {
          return;
        }
        const pickId = button.dataset.void ?? '';
        if (!window.confirm('Void this pick?')) {
          return;
        }
        if (!csrf) {
          const session = await readSession();
          if (!session) {
            message.textContent = 'Sign in required.';
            return;
          }
          csrf = session.csrf;
        }
        const response = await fetch('/api/void', {
          method: 'POST',
          headers: {
            accept: 'application/json',
            'content-type': 'application/json',
            'x-csrf-token': csrf,
          },
          body: JSON.stringify({ pick_id: pickId }),
        });
        let body: { request_id?: string; error?: string } = {};
        try {
          body = (await response.json()) as { request_id?: string; error?: string };
        } catch {
          body = {};
        }
        if (!response.ok || !body.request_id) {
          message.textContent = body.error || 'The void was not saved.';
          return;
        }
        message.textContent = 'Void saved, live in a few minutes';
        await poll(body.request_id, message, detail, 'Void saved, live in a few minutes');
      })();
    });
  });
}

void bootAdminPick();
