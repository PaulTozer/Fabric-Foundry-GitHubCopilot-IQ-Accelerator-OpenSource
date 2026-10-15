import { expect, test, type Page } from '@playwright/test';

declare global {
  interface Window {
    queryStream?: {
      send: (event: string, payload: Record<string, unknown>) => void;
      close: () => void;
      request: Record<string, unknown>;
    };
  }
}

async function prepare(page: Page) {
  await page.route('**/api/me', route => route.fulfill({ json: { demo: true, configOwner: 'progress-test' } }));
  await page.route('**/api/agent/config', route => route.fulfill({ json: {
    origins: [], authReferences: [], defaultModel: 'offline-model', fabricConfigured: false, demo: true,
    defaultSystemPrompt: 'You are an enterprise consultancy analyst.',
  } }));
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (input, options) => {
      if (String(input) !== '/api/chat/stream') return original(input, options);
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      const encoder = new TextEncoder();
      const abort = () => controller.error(new DOMException('Aborted', 'AbortError'));
      const body = new ReadableStream<Uint8Array>({
        start(value) {
          controller = value;
          options?.signal?.addEventListener('abort', abort, { once: true });
        },
      });
      window.queryStream = {
        request: JSON.parse(String(options?.body)),
        send(event, payload) {
          controller.enqueue(encoder.encode(`event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`));
        },
        close() {
          options?.signal?.removeEventListener('abort', abort);
          controller.close();
        },
      };
      return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
    };
  });
  await page.goto('/');
  await expect(page.locator('textarea')).toBeEnabled();
  await page.locator('textarea').fill('Review the evidence.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Connecting' })).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.queryStream))).toBe(true);
}

async function send(page: Page, event: string, payload: Record<string, unknown> = {}) {
  await page.evaluate(({ event, payload }) => window.queryStream!.send(event, payload), { event, payload });
}

const requestId = 'cadf660b-e251-42ca-a827-4bf2d2650443';

test('SDK progress is live, accessible and safe from first line through failed tool and actual fallback', async ({ page }) => {
  await prepare(page);
  await send(page, 'start', { method: 'M4', model: 'offline-model', grounding: 'tools', authorization: 'never-display',
    requestId, ontologyEnabled: true, ontologyConfigured: true, ontologyToolAvailable: true });
  const status = page.getByRole('status').filter({ hasText: 'Starting Copilot SDK...' });
  await expect(status).toBeVisible();
  await expect(status).toHaveAttribute('aria-live', 'polite');
  await expect(status).toHaveAttribute('aria-atomic', 'true');
  await page.getByText('Activity details', { exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Copilot SDK session started.', { exact: false })).toBeVisible();
  await expect(page.getByText(`Trace: ${requestId}`, { exact: false })).toBeVisible();
  await expect(page.getByText(/\bM4\b|never-display/)).toHaveCount(0);
  await send(page, 'assistant_turn', { iteration: 1 });
  await expect(page.getByRole('status').filter({ hasText: 'Working on your question...' })).toBeVisible();
  await send(page, 'assistant_activity', { intent: 'never-display' });
  await expect(page.getByRole('status').filter({ hasText: 'Working on the next step...' })).toBeVisible();
  await send(page, 'tool_start', { name: 'query_ontology', callId: 'ontology' });
  await expect(page.getByRole('status').filter({ hasText: 'Ontology query in progress...' })).toBeVisible();
  await send(page, 'tool_done', { name: 'query_ontology', callId: 'ontology', success: false, error: 'never-display' });
  await expect(page.getByRole('status').filter({ hasText: 'Ontology query failed.' })).toBeVisible();
  await expect(page.getByText(/retrying/i)).toHaveCount(0);
  await send(page, 'assistant_retry', { reason: 'never-display' });
  await expect(page.getByRole('status').filter({ hasText: 'Retrying the model request...' })).toBeVisible();
  await send(page, 'tool_start', { name: 'query_lakehouse', callId: 'sql' });
  await expect(page.getByRole('status').filter({ hasText: 'Lakehouse query in progress...' })).toBeVisible();
  await send(page, 'tool_done', { name: 'query_lakehouse', callId: 'sql', success: true });
  await expect(page.getByRole('status').filter({ hasText: 'Lakehouse query completed.' })).toBeVisible();
  await send(page, 'assistant_turn', { iteration: 2 });
  await expect(page.getByRole('status').filter({ hasText: 'Continuing with the next model step...' })).toBeVisible();
  await send(page, 'delta', { content: 'Evidence ' });
  await send(page, 'delta', { content: 'summary.' });
  await expect(page.getByLabel('Response in progress')).toHaveText('Evidence summary.');
  await expect(page.getByRole('status').filter({ hasText: 'Writing the response...' })).toBeVisible();
  await expect(page.getByText(/\bM4\b|never-display/)).toHaveCount(0);
  await send(page, 'complete');
  await send(page, 'result', { method: 'M4', answer: 'Evidence summary.', model: 'offline-model', error: '', requestId });
  await page.evaluate(() => window.queryStream!.close());
  await expect(page.getByText('Evidence summary.', { exact: true })).toBeVisible();
  await expect(page.getByText('Model: offline-model', { exact: true })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Response complete.' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Cancel response' })).toHaveCount(0);
  await page.getByText('Response details', { exact: true }).click();
  await expect(page.getByText(`Trace: ${requestId}`, { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.queryStream!.request.method)).toBe('M4');
});

for (const ending of ['error', 'interrupted', 'cancelled', 'server-cancelled']) {
  test(`ungrounded SDK ${ending} preserves partial answer and never claims tool activity`, async ({ page }) => {
    await prepare(page);
    await send(page, 'start', { method: 'M4', grounding: 'ungrounded', requestId });
    await send(page, 'assistant_turn', { iteration: 1 });
    await send(page, 'delta', { content: 'Partial evidence summary.' });
    await expect(page.getByLabel('Response in progress')).toContainText('Partial evidence summary.');
    await expect(page.getByText(/query in progress|query completed/i)).toHaveCount(0);
    if (ending === 'cancelled') {
      await page.getByRole('button', { name: 'Cancel response' }).click();
    } else {
      if (ending === 'error') await send(page, 'error', { message: 'Safe runtime diagnostic.' });
      if (ending === 'server-cancelled') await send(page, 'cancelled');
      await page.evaluate(() => window.queryStream!.close());
    }
    await expect(page.getByText('Partial answer — interrupted', { exact: false })).toBeVisible();
    await expect(page.getByText('Partial evidence summary.', { exact: true })).toBeVisible();
    await expect(page.getByRole('status').filter({
      hasText: ending.includes('cancelled') ? 'Response cancelled.' : 'The response could not be completed.',
    })).toBeVisible();
    await expect(page.getByText(/\bM4\b/)).toHaveCount(0);
    await expect(page.locator('textarea')).toBeEnabled();
    await page.getByText('Response details', { exact: true }).click();
    await expect(page.getByText(`Trace: ${requestId}`, { exact: true })).toBeVisible();
  });
}
