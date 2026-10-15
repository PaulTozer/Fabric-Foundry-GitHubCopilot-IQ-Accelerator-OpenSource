import { test, expect, type Page } from '@playwright/test';

test('removed stage controls/preferences do not alter general settings or outbound questions', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('enterpriseiq:settings:v1', JSON.stringify({
      method: 'M4', ontologyEnabled: false, workIQEnabled: true, ribaEnabledStages: [2],
    }));
    localStorage.setItem('saved-general-chat', 'saved conversation');
  });
  await page.goto('/');
  await expect(page.getByText('DEMO sources:', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByText('RIBA Plan of Work', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/^Stage\s+[0-7]$/)).toHaveCount(0);
  await expect(page.getByRole('switch').nth(0)).not.toBeChecked();
  await expect(page.getByRole('switch').nth(1)).toBeChecked();
  await expect.poll(() => page.evaluate(() => JSON.parse(
    localStorage.getItem('enterpriseiq:settings:v1')!,
  ))).toEqual({ method: 'M4', ontologyEnabled: false, workIQEnabled: true });
  expect(await page.evaluate(() => localStorage.getItem('saved-general-chat'))).toBe('saved conversation');
  await page.getByRole('button', { name: 'Close settings' }).click();
  let payload: Record<string, unknown> | undefined;
  await page.route('**/api/chat/stream', route => {
    payload = route.request().postDataJSON();
    return route.fulfill({
      contentType: 'text/event-stream',
      body: 'event: result\ndata: {"answer":"Unframed answer","method":"M4","error":""}\n\n',
    });
  });
  const question = '/stage 2 Explain this project brief.';
  await page.locator('textarea').fill(question);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('Unframed answer', { exact: true })).toBeVisible();
  await expect(page.getByRole('main').getByText(question, { exact: true })).toBeVisible();
  expect(payload?.message).toBe(question);
  expect(Object.keys(payload ?? {}).some(key => key.toLowerCase().includes('riba'))).toBe(false);
  expect(payload?.ontologyEnabled).toBe(false);
  expect(payload?.workIQEnabled).toBe(true);
});

const ownerPreferences = {
  agents: [{
    id: 'reviewer', name: 'Portfolio reviewer', systemPrompt: 'Explain uncertainty.',
    skills: [{ id: 'evidence', name: 'Evidence', description: '', prompt: 'Use evidence.', enabled: true }],
    servers: [{ id: 'docs', type: 'http', url: 'https://approved.example/mcp',
      enabled: false, authRef: 'public', tools: ['search_docs'] }],
  }],
  selectedAgent: 'reviewer', selectedModel: 'deployment-b',
};
const otherPreferences = {
  agents: [{ id: 'other', name: 'Other persona', systemPrompt: '', skills: [], servers: [] }],
  selectedAgent: 'other', selectedModel: 'deployment-a',
};

async function configureAccount(page: Page, owner: string) {
  await page.route('**/api/me', route => route.fulfill({ json: { demo: true, configOwner: owner } }));
  await page.route('**/api/agent/config', route => route.fulfill({ json: {
    origins: [], authReferences: [], endpoint: 'https://model.example/openai/v1/',
    authentication: 'Operator configured', defaultModel: 'deployment-a', fabricConfigured: false, demo: true,
    defaultSystemPrompt: 'You are an enterprise consultancy analyst.',
  } }));
  await page.route('**/api/models*', route => route.fulfill({ json: { models: [
    { id: 'deployment-a', model: 'chat-model', version: '1' },
    { id: 'deployment-b', model: 'chat-model', version: '2' },
  ] } }));
}

for (const flag of [true, false, undefined]) {
  test(`saved M7 migrates to SDK preserving Work IQ ${String(flag)}, account persona/model and server opt-outs`, async ({ page }) => {
    await page.addInitScript(({ flag, ownerPreferences, otherPreferences }) => {
      if (localStorage.getItem('enterpriseiq:settings:v1')) return;
      localStorage.setItem('enterpriseiq:settings:v1', JSON.stringify({
        method: 'M7', ontologyEnabled: false, workIQEnabled: flag,
      }));
      localStorage.setItem('enterpriseiq:agents:v1:account-one', JSON.stringify(ownerPreferences));
      localStorage.setItem('enterpriseiq:agents:v1:account-two', JSON.stringify(otherPreferences));
    }, { flag, ownerPreferences, otherPreferences });
    await configureAccount(page, 'account-one');
    await page.route('**/api/mcp', route => route.fulfill({ json: { servers: [{
      id: 'work-iq', transport: 'stdio', enabled: true, demo: true, error: '',
      tools: [{ name: 'work_iq_search_collaboration', description: 'Synthetic collaboration' }],
    }] } }));
    await page.goto('/');
    await expect(page.locator('textarea')).toBeEnabled();
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page.getByRole('button', { name: /M7/ })).toHaveCount(0);
    await expect(page.getByRole('radio', { name: /M7/ })).toHaveCount(0);
    await expect(page.getByRole('option', { name: /M7/ })).toHaveCount(0);
    await expect(page.getByText('Work IQ (preview)', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Current · Copilot SDK · BYOK', { exact: true })).toBeVisible();
    await expect(page.getByLabel('Model deployment', { exact: true })).toHaveValue('deployment-b');
    const toggle = page.getByRole('switch', { name: 'Work IQ', exact: true });
    await expect(toggle).toBeEnabled();
    await expect(toggle).toHaveAttribute('aria-checked', String(flag === true));
    await expect.poll(() => page.evaluate(() => JSON.parse(
      localStorage.getItem('enterpriseiq:settings:v1')!,
    ))).toEqual({ method: 'M4', ontologyEnabled: false, workIQEnabled: flag === true });
    await page.getByRole('button', { name: 'Close settings' }).click();

    await page.getByRole('button', { name: 'Agent Manager', exact: true }).first().click();
    const manager = page.getByRole('dialog', { name: 'Agent Manager' });
    await expect(manager.getByLabel('Agent persona')).toHaveValue('reviewer');
    await manager.getByRole('button', { name: 'MCP Servers', exact: true }).click();
    await expect(manager.getByLabel('Enable docs', { exact: true })).not.toBeChecked();
    await manager.getByText('Operator-managed MCP servers', { exact: true }).click();
    await manager.getByRole('button', { name: 'Refresh tools and health' }).click();
    await manager.getByRole('checkbox', { name: /work-iq/ }).uncheck();
    await page.keyboard.press('Escape');

    const requests: Record<string, unknown>[] = [];
    await page.route('**/api/chat/stream', route => {
      requests.push(route.request().postDataJSON());
      return route.fulfill({ contentType: 'text/event-stream',
        body: `event: result\ndata: ${JSON.stringify({ answer: `SDK answer ${requests.length}`, method: 'M4', error: '' })}\n\n` });
    });
    for (const enabled of [flag === true, true, false]) {
      await page.getByRole('button', { name: 'Settings', exact: true }).click();
      if (await toggle.getAttribute('aria-checked') !== String(enabled)) {
        await toggle.focus();
        await page.keyboard.press('Space');
      }
      await expect(toggle).toHaveAttribute('aria-checked', String(enabled));
      await page.getByRole('button', { name: 'Close settings' }).click();
      const turn = requests.length + 1;
      await page.locator('textarea').fill(`Review evidence ${turn}.`);
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await expect(page.getByText(`SDK answer ${turn}`, { exact: true })).toBeVisible();
      expect(requests.at(-1)).toMatchObject({
        method: 'M4', workIQEnabled: enabled, ontologyEnabled: false,
        selectedModel: 'deployment-b', agent: ownerPreferences.agents[0],
        disabledMcpServers: ['work-iq'],
      });
    }
    expect(await page.evaluate(() => JSON.parse(
      localStorage.getItem('enterpriseiq:agents:v1:account-one')!,
    ))).toEqual(ownerPreferences);
    expect(await page.evaluate(() => JSON.parse(
      localStorage.getItem('enterpriseiq:agents:v1:account-two')!,
    ))).toEqual(otherPreferences);
    await configureAccount(page, 'account-two');
    await page.reload();
    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page.getByLabel('Model deployment', { exact: true })).toHaveValue('deployment-a');
    await page.getByRole('button', { name: 'Close settings' }).click();
    await page.getByRole('button', { name: 'Agent Manager', exact: true }).first().click();
    await expect(manager.getByLabel('Agent persona')).toHaveValue('other');
  });
}

test('Council and pending turns cannot enable Work IQ; returning to SDK retains the preference', async ({ page }) => {
  await configureAccount(page, 'account-one');
  await page.goto('/');
  await expect(page.locator('textarea')).toBeEnabled();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const toggle = page.getByRole('switch', { name: 'Work IQ', exact: true });
  await toggle.click();
  await page.getByRole('button', { name: /M6 · Council/ }).click();
  await expect(toggle).toBeDisabled();
  await expect(toggle).not.toBeChecked();
  await page.getByRole('button', { name: 'Close settings' }).click();
  let submitted: Record<string, unknown> | undefined;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/chat/stream', async route => {
    submitted = route.request().postDataJSON();
    await gate;
    await route.fulfill({ contentType: 'text/event-stream',
      body: 'event: result\ndata: {"answer":"Council answer","method":"M6","error":""}\n\n' });
  });
  await page.locator('textarea').fill('Review evidence.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Cancel response', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(toggle).toBeDisabled();
  await expect(page.getByRole('button', { name: /Copilot SDK · BYOK/ })).toBeDisabled();
  release();
  await expect(page.getByText('Council answer', { exact: true })).toBeVisible();
  expect(submitted).toMatchObject({ method: 'M6', workIQEnabled: false, selectedModel: null, agent: null });
  await page.getByRole('button', { name: /Copilot SDK · BYOK/ }).click();
  await expect(toggle).toBeEnabled();
  await expect(toggle).toBeChecked();
});
