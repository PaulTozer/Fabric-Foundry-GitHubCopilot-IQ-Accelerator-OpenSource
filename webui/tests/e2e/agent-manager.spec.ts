import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { SEED_SKILLS } from '../../lib/skills-seed';

const configuration = {
  origins: [{ origin: 'https://approved.example', authRefs: ['public'] }],
  authReferences: [{ id: 'public', label: 'Public service', type: 'none' }],
  endpoint: 'https://model.example/openai/v1/', authentication: 'Azure managed identity',
  defaultModel: 'deployment-a', fabricConfigured: false, demo: false,
  defaultSystemPrompt: readFileSync(resolve('..', 'agents', 'CopilotSDK_Instructions.md'), 'utf8'),
};
const models = [
  { id: 'deployment-a', model: 'chat-model', version: '1' },
  { id: 'deployment-b', model: 'second-chat-model', version: '2' },
];

async function configure(page: Page, owner = 'account-one') {
  await page.route('**/api/me', route => route.fulfill({ json: { demo: false, configOwner: owner } }));
  await page.route('**/api/agent/config', route => route.fulfill({ json: configuration }));
  await page.route('**/api/models*', route => route.fulfill({ json: { models } }));
}
async function manager(page: Page) {
  await page.getByRole('button', { name: 'Agent Manager', exact: true }).first().click();
  return page.getByRole('dialog', { name: 'Agent Manager' });
}

test('Skills and MCP controls exist only in Agent Manager while tools and wire IDs still apply', async ({ page }) => {
  await configure(page);
  await page.route('**/api/mcp', route => route.fulfill({ json: { servers: [{
    id: 'operator-docs', transport: 'http', enabled: true, demo: false, error: '',
    tools: [{ name: 'search_docs', description: 'Search operator documents' }],
  }] } }));
  await page.goto('/');
  await expect(page.locator('textarea')).toBeEnabled();
  await expect(page.getByRole('button', { name: /^Skills\b/ })).toHaveCount(0);
  await expect(page.locator('summary').filter({ hasText: /MCP servers/i })).toHaveCount(0);
  await expect(page.getByText(/\bM4\b/)).toHaveCount(0);
  await page.locator('textarea').fill('/Evidence checklist');
  await expect(page.getByRole('listbox', { name: 'Skill picker' })).toHaveCount(0);
  await page.locator('textarea').clear();

  const dialog = await manager(page);
  await dialog.getByRole('button', { name: 'Skills', exact: true }).click();
  await dialog.getByRole('button', { name: 'Add skill', exact: true }).click();
  await dialog.getByLabel('Skill name', { exact: true }).fill('Evidence checklist');
  await dialog.getByRole('textbox', { name: 'Skill instructions', exact: true }).fill('Identify evidence gaps.');
  await dialog.getByRole('button', { name: 'Save skill', exact: true }).click();
  await dialog.getByRole('button', { name: 'MCP Servers', exact: true }).click();
  await dialog.getByText('Operator-managed MCP servers', { exact: true }).click();
  await dialog.getByRole('button', { name: 'Refresh tools and health' }).click();
  await expect(dialog.getByText('search_docs', { exact: true })).toBeVisible();
  await dialog.getByRole('checkbox', { name: /operator-docs/ }).uncheck();
  await dialog.getByRole('button', { name: 'Add MCP server', exact: true }).click();
  await dialog.getByLabel('Server ID', { exact: true }).fill('review');
  await dialog.getByLabel('MCP URL', { exact: true }).fill('https://approved.example/mcp');
  await dialog.getByLabel('Tool allowlist', { exact: false }).fill('search_docs');
  await dialog.getByLabel('Enable on next chat', { exact: true }).check();
  await dialog.getByRole('button', { name: 'Save MCP server', exact: true }).click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: /^Skills\b/ })).toHaveCount(0);
  await expect(page.locator('summary').filter({ hasText: /MCP servers/i })).toHaveCount(0);

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByText(/\bM4\b/)).toHaveCount(0);
  await expect(page.getByRole('button', { name: /M6 · Council/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /M7 · Work IQ/ })).toHaveCount(0);
  await expect(page.getByRole('switch', { name: 'Work IQ', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  let submitted: Record<string, unknown> | undefined;
  await page.route('**/api/chat/stream', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({
      contentType: 'text/event-stream',
      body: 'event: result\ndata: {"answer":"Checklist ready.","method":"M4","error":""}\n\n',
    });
  });
  await page.locator('textarea').fill('Review evidence.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('Checklist ready.', { exact: true })).toBeVisible();
  expect(submitted?.method).toBe('M4');
  expect(submitted?.disabledMcpServers).toEqual(['operator-docs']);
  expect(submitted?.agent).toMatchObject({
    skills: [{ name: 'Evidence checklist', prompt: 'Identify evidence gaps.', enabled: true }],
    servers: [{ id: 'review', enabled: true, tools: ['search_docs'] }],
  });
  await expect(page.getByText(/\bM4\b/)).toHaveCount(0);
});

test('demo operator tool discovery does not acquire a live Fabric token', async ({ page }) => {
  await configure(page);
  await page.route('**/api/agent/config', route => route.fulfill({
    json: { ...configuration, demo: true, fabricTokenRequired: true },
  }));
  let registryRequests = 0;
  await page.route('**/api/mcp', route => {
    registryRequests++;
    return route.fulfill({ json: { servers: [] } });
  });
  await page.goto('/');
  const dialog = await manager(page);
  await dialog.getByRole('button', { name: 'MCP Servers', exact: true }).click();
  await dialog.getByText('Operator-managed MCP servers', { exact: true }).click();
  await dialog.getByRole('button', { name: 'Refresh tools and health' }).click();
  await expect(dialog.getByText('No operator-managed additional MCP servers configured.')).toBeVisible();
  expect(registryRequests).toBe(1);
  await expect(dialog.getByRole('alert')).toHaveCount(0);
});

test('persona, skills, prompt and enabled model persist and drive the next SDK turn', async ({ page }) => {
  test.setTimeout(60000);
  await configure(page);
  await page.goto('/');
  await expect(page.locator('textarea')).toBeEnabled();
  const dialog = await manager(page);
  await dialog.getByRole('button', { name: 'Add agent', exact: true }).click();
  await dialog.getByRole('button', { name: 'System Prompt', exact: true }).click();
  await dialog.getByLabel('Persona name', { exact: true }).fill('Portfolio reviewer');
  await dialog.getByRole('textbox', { name: 'System prompt', exact: true }).fill('Explain uncertainty clearly.');
  await dialog.getByRole('button', { name: 'Save system prompt' }).click();
  await dialog.getByRole('button', { name: 'Skills', exact: true }).click();
  await dialog.getByRole('button', { name: 'Add skill', exact: true }).click();
  await dialog.getByLabel('Skill name', { exact: true }).fill('Evidence checklist');
  await dialog.getByLabel('Description', { exact: true }).fill('Separate facts from assumptions.');
  await dialog.getByRole('textbox', { name: 'Skill instructions', exact: true }).fill('Identify missing evidence.');
  await dialog.getByRole('button', { name: 'Save skill', exact: true }).click();
  await dialog.getByLabel('Enable Evidence checklist', { exact: true }).uncheck();
  await dialog.getByRole('button', { name: 'Edit Evidence checklist', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Skill instructions', exact: true }).fill('Identify missing evidence and owners.');
  await dialog.getByRole('button', { name: 'Save skill', exact: true }).click();
  await dialog.getByLabel('Enable Evidence checklist', { exact: true }).check();
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();

  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Model deployment', { exact: true })).toBeEnabled();
  await expect(page.getByLabel('Model deployment').locator('option')).toHaveCount(2);
  await page.getByLabel('Model deployment').selectOption('deployment-b');
  await page.getByRole('button', { name: 'Cancel model change' }).click();
  await expect(page.getByLabel('Model deployment')).toHaveValue('deployment-a');
  await page.getByLabel('Model deployment').selectOption('deployment-b');
  await page.getByRole('button', { name: 'Save model', exact: true }).click();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await page.reload();
  await expect(page.getByText('Agent: Portfolio reviewer', { exact: false })).toContainText('deployment-b');
  const reloaded = await manager(page);
  await expect(reloaded.getByLabel('Enable Evidence checklist')).toBeChecked();
  await reloaded.getByRole('button', { name: 'System Prompt', exact: true }).click();
  await expect(reloaded.getByRole('textbox', { name: 'System prompt', exact: true })).toHaveValue('Explain uncertainty clearly.');
  await page.keyboard.press('Escape');
  let submitted: Record<string, unknown> | undefined;
  await page.route('**/api/chat/stream', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({
      contentType: 'text/event-stream',
      body: 'event: result\ndata: {"answer":"Ungrounded model response: checklist ready.","method":"M4","model":"deployment-b","grounding":"ungrounded","error":""}\n\n',
    });
  });
  await page.locator('textarea').fill('Review the evidence.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('Ungrounded model response: checklist ready.', { exact: true })).toBeVisible();
  expect(submitted?.selectedModel).toBe('deployment-b');
  expect(submitted?.agent).toMatchObject({
    name: 'Portfolio reviewer', systemPrompt: 'Explain uncertainty clearly.', servers: [],
    skills: [{ name: 'Evidence checklist', prompt: 'Identify missing evidence and owners.', enabled: true }],
  });
  const preserved = await manager(page);
  await preserved.getByRole('button', { name: 'Skills', exact: true }).click();
  page.once('dialog', prompt => prompt.accept());
  await preserved.getByRole('button', { name: 'Delete Evidence checklist', exact: true }).click();
  await expect(preserved.getByLabel('Enable Evidence checklist')).toHaveCount(0);
  await expect(preserved.getByText('Built-in', { exact: true })).toHaveCount(6);
  page.once('dialog', prompt => prompt.accept());
  await preserved.getByRole('button', { name: 'Remove agent', exact: true }).click();
  await expect(preserved.getByLabel('Agent persona').locator('option')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(page.getByText('Ungrounded model response: checklist ready.', { exact: true })).toBeVisible();
});

test('real MCP definition discovery, CRUD and errors never accept browser credentials', async ({ page }) => {
  await configure(page);
  await page.route('**/api/agent/discover', route => route.fulfill({ json: { servers: [{
    id: 'user-docs', transport: 'http', enabled: true, error: '',
    tools: [{ name: 'search_docs', description: 'Search approved service' }],
  }] } }));
  await page.goto('/');
  const dialog = await manager(page);
  await dialog.getByRole('button', { name: 'MCP Servers', exact: true }).click();
  await expect(dialog.getByText('No real MCP servers connected.', { exact: false })).toBeVisible();
  await dialog.getByRole('button', { name: 'Add MCP server', exact: true }).click();
  await dialog.getByLabel('Server ID', { exact: true }).fill('docs');
  await dialog.getByLabel('MCP URL', { exact: true }).fill('https://approved.example/mcp');
  await dialog.getByLabel('Tool allowlist', { exact: false }).pressSequentially('search_docs,get_document');
  await dialog.getByRole('button', { name: 'Test connection and discover tools' }).click();
  await expect(dialog.getByRole('heading', { name: 'user-docs: Connected' })).toBeVisible();
  await expect(dialog.getByText('search_docs: Search approved service')).toBeVisible();
  await dialog.getByLabel('Enable on next chat', { exact: true }).check();
  await dialog.getByRole('button', { name: 'Save MCP server', exact: true }).click();
  await expect(dialog.getByLabel('Enable docs', { exact: true })).toBeChecked();
  await dialog.getByRole('button', { name: 'Edit docs', exact: true }).click();
  await expect(dialog.getByLabel('Server ID', { exact: true })).toBeDisabled();
  await dialog.getByLabel('MCP URL', { exact: true }).fill('https://approved.example/updated-mcp');
  await dialog.getByRole('button', { name: 'Save MCP server', exact: true }).click();
  await dialog.getByLabel('Enable docs', { exact: true }).uncheck();
  await page.reload();
  const saved = await manager(page);
  await saved.getByRole('button', { name: 'MCP Servers', exact: true }).click();
  await expect(saved.getByLabel('Enable docs', { exact: true })).not.toBeChecked();
  await expect(saved.getByText('https://approved.example/updated-mcp', { exact: false })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('enterpriseiq:agents:v1:account-one'))).toContain('"tools":["search_docs","get_document"]');
  await page.route('**/api/agent/discover', route => route.fulfill({ status: 400, json: { error: 'MCP origin is not operator-approved.' } }));
  await saved.getByRole('button', { name: 'Test docs', exact: true }).click();
  await expect(saved.getByRole('alert')).toContainText('not operator-approved');
  await saved.getByRole('button', { name: 'Add MCP server', exact: true }).click();
  await saved.getByLabel('Server ID', { exact: true }).fill('unsafe');
  await saved.getByLabel('MCP URL', { exact: true }).fill('https://unapproved.example/mcp');
  await saved.getByLabel('Enable on next chat', { exact: true }).check();
  await saved.getByRole('button', { name: 'Save MCP server', exact: true }).click();
  await expect(saved.getByRole('alert')).toContainText('not operator-approved');
  await saved.getByLabel('Enable on next chat', { exact: true }).uncheck();
  await saved.getByLabel('MCP URL', { exact: true }).fill('https://approved.example/mcp?token=never-store-this');
  await saved.getByRole('button', { name: 'Save MCP server', exact: true }).click();
  await expect(saved.getByRole('alert')).toContainText('without credentials, query or fragment');
  const stored = await page.evaluate(() => localStorage.getItem('enterpriseiq:agents:v1:account-one'));
  expect(stored).not.toContain('never-store-this');
  await saved.getByRole('button', { name: 'Cancel MCP edit' }).click();
  page.once('dialog', prompt => prompt.accept());
  await saved.getByRole('button', { name: 'Remove docs', exact: true }).click();
  await expect(saved.getByText('No real MCP servers connected.', { exact: false })).toBeVisible();
  await page.keyboard.press('Escape');
});

test('authenticated account namespaces stay isolated and catalog failures are explicit', async ({ page }) => {
  await configure(page);
  await page.goto('/');
  const first = await manager(page);
  await first.getByRole('button', { name: 'System Prompt', exact: true }).click();
  await first.getByLabel('Persona name').fill('Private account label');
  await first.getByRole('button', { name: 'Save system prompt' }).click();
  await page.keyboard.press('Escape');
  await configure(page, 'account-two');
  await page.reload();
  const second = await manager(page);
  await expect(second.getByLabel('Agent persona').locator('option')).toHaveText('Enterprise Consultancy Analyst');
  await second.getByRole('button', { name: 'Close Agent Manager' }).focus();
  await page.keyboard.press('Tab');
  await expect(second.getByLabel('Agent persona')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(second.getByRole('button', { name: 'Close Agent Manager' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Agent Manager', exact: true }).first()).toBeFocused();
  await page.route('**/api/models*', route => route.fulfill({ status: 503, json: { error: 'Model inventory permission denied.' } }));
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'permission denied' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save model', exact: true })).toBeDisabled();
  await page.route('**/api/models*', route => route.fulfill({ json: { models: [] } }));
  await page.getByRole('button', { name: 'Refresh enabled models' }).click();
  await expect(page.getByText('No enabled chat deployments compatible', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save model', exact: true })).toBeDisabled();
});

test('pending discovery cannot cross personas and removing an edited connection closes the editor', async ({ page }) => {
  await configure(page);
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/api/agent/discover', async route => {
    await gate;
    await route.fulfill({ json: { servers: [{ id: 'old-persona', error: '', tools: [], enabled: true, transport: 'http' }] } });
  });
  await page.goto('/');
  const dialog = await manager(page);
  await dialog.getByRole('button', { name: 'MCP Servers', exact: true }).click();
  await dialog.getByRole('button', { name: 'Add MCP server', exact: true }).click();
  await dialog.getByLabel('Server ID', { exact: true }).fill('docs');
  await dialog.getByLabel('MCP URL', { exact: true }).fill('https://approved.example/mcp');
  await dialog.getByRole('button', { name: 'Save MCP server', exact: true }).click();
  await dialog.getByRole('button', { name: 'Test docs', exact: true }).click();
  await expect(dialog.getByText('Connecting and discovering tools...')).toBeVisible();
  await dialog.getByRole('button', { name: 'Add agent', exact: true }).click();
  release();
  await expect(dialog.getByText('No real MCP servers connected.', { exact: false })).toBeVisible();
  await expect(dialog.getByText('old-persona: Connected')).toHaveCount(0);
  await dialog.getByLabel('Agent persona').selectOption({ index: 0 });
  await dialog.getByRole('button', { name: 'Edit docs', exact: true }).click();
  page.once('dialog', prompt => prompt.accept());
  await dialog.getByRole('button', { name: 'Remove docs', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Save MCP server', exact: true })).toHaveCount(0);
});

test('all shipped built-ins and the packaged domain are restored in Manager and selected SDK requests', async ({ page }) => {
  await configure(page);
  const requests: Record<string, unknown>[] = [];
  await page.route('**/api/chat/stream', route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({ contentType: 'text/event-stream',
      body: 'event: result\ndata: {"answer":"Offline enterprise response.","method":"M4"}\n\n' });
  });
  await page.goto('/');
  const dialog = await manager(page);
  await expect(dialog.getByLabel('Agent persona').locator('option')).toHaveText('Enterprise Consultancy Analyst');
  await expect(dialog.getByText('Built-in', { exact: true })).toHaveCount(6);
  for (const skill of SEED_SKILLS) {
    await expect(dialog.getByRole('heading', { name: skill.name, exact: true })).toBeVisible();
    await expect(dialog.getByLabel(`Enable ${skill.name}`, { exact: true })).not.toBeChecked();
    await expect(dialog.getByRole('button', { name: `Delete ${skill.name}`, exact: true })).toHaveCount(0);
  }
  await dialog.getByRole('button', { name: 'System Prompt', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'System prompt', exact: true })).toHaveValue(configuration.defaultSystemPrompt);
  await dialog.getByRole('button', { name: 'Skills', exact: true }).click();
  const selected = SEED_SKILLS[0]!;
  await dialog.getByLabel(`Enable ${selected.name}`, { exact: true }).check();
  await page.keyboard.press('Escape');
  await page.locator('textarea').fill('Qualify the pursuit.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('Offline enterprise response.', { exact: true })).toBeVisible();
  expect(requests[0]?.agent).toMatchObject({
    systemPrompt: configuration.defaultSystemPrompt, servers: [],
    skills: [{ id: selected.id, prompt: selected.prompt, enabled: true }],
  });
  expect(requests[0]?.method).toBe('M4');
  await page.reload();
  const reloaded = await manager(page);
  await expect(reloaded.getByLabel(`Enable ${selected.name}`, { exact: true })).toBeChecked();
  await reloaded.getByLabel(`Enable ${selected.name}`, { exact: true }).uncheck();
  await page.keyboard.press('Escape');
  await page.reload();
  const disabled = await manager(page);
  await expect(disabled.getByLabel(`Enable ${selected.name}`, { exact: true })).not.toBeChecked();
  await page.keyboard.press('Escape');
  await page.locator('textarea').fill('General portfolio question.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText('Offline enterprise response.', { exact: true })).toBeVisible();
  expect(requests[1]?.agent).toMatchObject({ skills: [{ id: selected.id, enabled: false }] });
});

test('existing empty/custom preferences get catalog availability, not forced domain or skill selection', async ({ page }) => {
  await configure(page);
  await page.addInitScript(() => {
    if (localStorage.getItem('initialized-existing')) return;
    localStorage.setItem('enterpriseiq:agents:v1:account-one', JSON.stringify({
      agents: [{ id: 'existing', name: 'Generic Assistant', systemPrompt: '', skills: [], servers: [] }],
      selectedAgent: 'existing', selectedModel: 'deployment-b',
    }));
    localStorage.setItem('initialized-existing', '1');
  });
  await page.goto('/');
  const dialog = await manager(page);
  await expect(dialog.getByText('Built-in', { exact: true })).toHaveCount(6);
  await expect(dialog.getByRole('checkbox', { checked: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: 'System Prompt', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'System prompt', exact: true })).toHaveValue('');
  await dialog.getByRole('button', { name: 'Use enterprise analyst prompt' }).click();
  await expect(dialog.getByRole('textbox', { name: 'System prompt', exact: true })).toHaveValue(configuration.defaultSystemPrompt);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('enterpriseiq:agents:v1:account-one')!).agents[0].systemPrompt)).toBe('');
  await dialog.getByRole('button', { name: 'Save system prompt' }).click();
  await page.keyboard.press('Escape');
  await page.reload();
  const restored = await manager(page);
  await restored.getByRole('button', { name: 'System Prompt', exact: true }).click();
  await expect(restored.getByRole('textbox', { name: 'System prompt', exact: true })).toHaveValue(configuration.defaultSystemPrompt);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('enterpriseiq:agents:v1:account-one')!).selectedModel)).toBe('deployment-b');
});

test('missing packaged domain fails visibly instead of silently creating generic defaults', async ({ page }) => {
  await configure(page);
  await page.route('**/api/agent/config', route => route.fulfill({
    status: 503, json: { error: 'The packaged enterprise analyst instructions are unavailable.' },
  }));
  await page.goto('/');
  await expect(page.getByRole('alert').filter({ hasText: 'packaged enterprise analyst instructions' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  expect(await page.evaluate(() => localStorage.getItem('enterpriseiq:agents:v1:account-one'))).toBeNull();
});

test('valid saved personas still hydrate when operator configuration is unavailable', async ({ page }) => {
  await configure(page);
  await page.addInitScript(() => {
    localStorage.setItem('enterpriseiq:agents:v1:account-one', JSON.stringify({
      agents: [{ id: 'saved', name: 'Retained reviewer', systemPrompt: 'Use my explicit rules.',
        skills: [{ id: 'custom', name: 'Custom', description: '', prompt: 'Custom instructions.', enabled: false }],
        servers: [] }],
      selectedAgent: 'saved', selectedModel: 'deployment-b',
    }));
  });
  await page.route('**/api/agent/config', route => route.fulfill({
    status: 503, json: { error: 'Operator configuration unavailable.' },
  }));
  await page.goto('/');
  await expect(page.getByRole('alert').filter({ hasText: 'Operator configuration unavailable.' })).toBeVisible();
  await expect(page.locator('textarea')).toBeEnabled();
  const dialog = await manager(page);
  await expect(dialog.getByLabel('Agent persona').locator('option')).toHaveText('Retained reviewer');
  await expect(dialog.getByText('Built-in', { exact: true })).toHaveCount(6);
  await expect(dialog.getByLabel('Enable Custom', { exact: true })).not.toBeChecked();
  await dialog.getByRole('button', { name: 'System Prompt', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: 'System prompt', exact: true })).toHaveValue('Use my explicit rules.');
  await expect(dialog.getByRole('button', { name: 'Use enterprise analyst prompt' })).toBeDisabled();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('enterpriseiq:agents:v1:account-one')!).selectedModel)).toBe('deployment-b');
});

test('saving built-in edits preserves newer enable choices and failed persistence does not alter them', async ({ page }) => {
  await configure(page);
  await page.goto('/');
  const dialog = await manager(page);
  const builtin = SEED_SKILLS[0]!;
  const toggle = dialog.getByLabel(`Enable ${builtin.name}`, { exact: true });
  for (const enabled of [true, false]) {
    await dialog.getByRole('button', { name: `Edit ${builtin.name}`, exact: true }).click();
    await dialog.getByRole('textbox', { name: 'Skill instructions', exact: true }).fill(`Explicit edited instructions ${enabled}.`);
    await toggle.setChecked(enabled);
    await dialog.getByRole('button', { name: 'Save skill', exact: true }).click();
    await expect(toggle).toBeChecked({ checked: enabled });
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('enterpriseiq:agents:v1:account-one')!).agents[0].skills[0]);
    expect(saved.enabled).toBe(enabled);
    expect(saved.prompt).toBe(`Explicit edited instructions ${enabled}.`);
  }
  await toggle.check();
  await dialog.getByRole('button', { name: `Edit ${builtin.name}`, exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Skill instructions', exact: true }).fill('Keep this unsaved text during a toggle failure.');
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key.startsWith('enterpriseiq:agents:')) {
        Storage.prototype.setItem = original;
        throw new DOMException('Offline fixture storage failure.', 'QuotaExceededError');
      }
      return original.call(this, key, value);
    };
  });
  await toggle.click();
  await expect(dialog.getByRole('alert')).toContainText('storage failure');
  await expect(toggle).toBeChecked();
  await expect(dialog.getByRole('textbox', { name: 'Skill instructions', exact: true })).toHaveValue('Keep this unsaved text during a toggle failure.');
  await dialog.getByRole('button', { name: 'Save skill', exact: true }).click();
  await expect(toggle).toBeChecked();
  await page.keyboard.press('Escape');
  await page.reload();
  const reloaded = await manager(page);
  await expect(reloaded.getByLabel(`Enable ${builtin.name}`, { exact: true })).toBeChecked();
});
