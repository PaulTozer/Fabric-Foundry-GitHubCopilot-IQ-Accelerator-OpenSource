import { test, expect } from '@playwright/test';
import { samplePdf } from '../pdf-fixture';
import { sampleDocx } from '../docx-fixture';

test('demo MCP discovery, safe Markdown dialog, keyboard focus and upload cleanup', async ({ page }) => {
  test.setTimeout(60000);
  await page.goto('/');
  await expect(page.getByText('DEMO sources:', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Agent Manager', exact: true }).first().click();
  const manager = page.getByRole('dialog', { name: 'Agent Manager' });
  await manager.getByRole('button', { name: 'MCP Servers', exact: true }).click();
  await manager.getByText('Operator-managed MCP servers', { exact: true }).click();
  await manager.getByRole('button', { name: 'Refresh tools and health' }).click();
  for (const name of ['fabric_iq_query_portfolio', 'foundry_iq_search_knowledge', 'work_iq_search']) {
    await expect(manager.getByText(name, { exact: true })).toBeVisible();
  }
  await page.keyboard.press('Escape');
  await expect(manager).not.toBeVisible();
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/upload'));
  await page.locator('input[type=file]').setInputFiles({
    name: 'example.md', mimeType: 'text/markdown',
    buffer: Buffer.from('# Example document\n\nSafe text.\n<script>window.unsafeExecuted = true</script>'),
  });
  const { documentUrl } = await (await uploaded).json();
  const link = page.getByRole('button', { name: 'example.md', exact: true });
  await link.click();
  await expect(page.getByRole('dialog', { name: 'Document viewer' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Example document', exact: true })).toBeVisible();
  expect(await page.evaluate(() => Reflect.get(window, 'unsafeExecuted'))).toBeUndefined();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(link).toBeFocused();
  const deleted = page.waitForResponse(response => response.url().endsWith(documentUrl)
    && response.request().method() === 'DELETE');
  await page.getByRole('button', { name: 'Remove example.md' }).click();
  expect((await deleted).status()).toBe(200);
  expect((await page.request.get(documentUrl)).status()).toBe(404);
});

test('PDF citation opens the actual viewer on page two and renders canvas', async ({ page }) => {
  await page.goto('/');
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/upload'));
  await page.locator('input[type=file]').setInputFiles({
    name: 'example.pdf', mimeType: 'application/pdf', buffer: samplePdf(),
  });

  const uploadResponse = await uploaded;
  expect(uploadResponse.status()).toBe(200);
  const { documentUrl } = await uploadResponse.json();
  await page.route('**/api/chat/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: 'event: start\ndata: {"method":"M4"}\n\nevent: delta\ndata: {"content":"Partial text"}\n\n'
      + `event: result\ndata: ${JSON.stringify({ answer: `[PDF source](${documentUrl}#page=2)`, method: 'M4', error: '' })}\n\n`,
  }));
  await page.locator('textarea').fill('Show the source citation.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page.getByRole('button', { name: 'PDF source', exact: true }).click();
  await expect(page.getByText('Page 2 / 2', { exact: true })).toBeVisible();
  await expect.poll(() => page.getByRole('img', { name: 'PDF page 2' })
    .evaluate(node => (node as HTMLCanvasElement).width)).toBeGreaterThan(300);
  await page.getByRole('button', { name: 'Previous page' }).click();
  await expect(page.getByText('Page 1 / 2', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  expect((await page.request.delete(documentUrl)).status()).toBe(200);
});

test('DOCX citation preserves extracted text, renders inertly, rejects bad XML and cleans up', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('textarea')).toBeEnabled();
  const uploaded = page.waitForResponse(response => response.url().endsWith('/api/upload'));
  await page.locator('input[type=file]').setInputFiles({
    name: 'evidence.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    buffer: sampleDocx('<w:p><w:r><w:t>DOCX evidence</w:t><w:tab/><w:t>&lt;script&gt;window.unsafeExecuted=true&lt;/script&gt;</w:t></w:r></w:p>'),
  });
  const response = await uploaded;
  expect(response.status()).toBe(200);
  const { documentUrl, text } = await response.json();
  expect(text).toBe('DOCX evidence\t<script>window.unsafeExecuted=true</script>\n\n');
  await page.route('**/api/chat/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: `event: result\ndata: ${JSON.stringify({ answer: `[DOCX source](${documentUrl})`, method: 'M4', error: '' })}\n\n`,
  }));
  await page.locator('textarea').fill('Show the DOCX citation.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await page.getByRole('button', { name: 'DOCX source', exact: true }).click();
  const viewer = page.getByRole('dialog', { name: 'Document viewer' });
  await expect(viewer.getByText(/DOCX evidence/)).toBeVisible();
  const bounds = await viewer.boundingBox();
  const viewport = page.viewportSize();
  expect(bounds).not.toBeNull();
  expect(viewport).not.toBeNull();
  if (!bounds || !viewport) throw new Error('Expected a visible native dialog and configured browser viewport.');
  expect(Math.abs(bounds.x + bounds.width / 2 - viewport.width / 2)).toBeLessThanOrEqual(1);
  expect(Math.abs(bounds.y + bounds.height / 2 - viewport.height / 2)).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => Reflect.get(window, 'unsafeExecuted'))).toBeUndefined();
  await page.keyboard.press('Escape');
  expect((await page.request.delete(documentUrl)).status()).toBe(200);
  expect((await page.request.get(documentUrl)).status()).toBe(404);
  const rejected = await page.request.post('/api/upload', { multipart: { file: {
    name: 'malformed.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    buffer: sampleDocx('<w:p>'),
  } } });
  expect(rejected.status()).toBe(415);
  expect((await rejected.json()).error).toMatch(/DOCX/);
});

test('stream without a terminal event preserves partial text with an interruption notice', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('DEMO sources:', { exact: false })).toBeVisible();
  await page.route('**/api/chat/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: 'event: start\ndata: {"method":"M4"}\n\nevent: delta\ndata: {"content":"Useful partial answer"}\n\n',
  }));
  await page.locator('textarea').fill('Show interrupted streaming.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('Useful partial answer', { exact: false })).toBeVisible();
  await expect(page.getByText('Partial answer — interrupted:', { exact: false })).toBeVisible();
  await expect(page.locator('textarea')).toBeEnabled();
});

test('missing BYOK settings produce an explicit safe error through the real stream', async ({ page }) => {
  await page.goto('/');
  await page.locator('textarea').fill('Check provider configuration.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText(/Set COPILOT_MODEL and COPILOT_BASE_URL/)).toBeVisible();
  await expect(page.locator('textarea')).toBeEnabled();
});
