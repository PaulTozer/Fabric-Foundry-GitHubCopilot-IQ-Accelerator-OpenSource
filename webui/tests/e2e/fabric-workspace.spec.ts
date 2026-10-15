import { test, expect } from '@playwright/test';

const workspace = '33333333-3333-4333-8333-333333333333';
const lakehouse = '44444444-4444-4444-8444-444444444444';

test('saved account selection reloads; invalid edit/auth failure cannot default; clear preserves model and persona', async ({ page }) => {
  let currentOwner = 'fixture-owner';
  await page.route('**/api/me', route => route.fulfill({ json: {
    configOwner: currentOwner, demo: false, oid: '', aadTenantId: '', aadClientId: '',
  } }));
  await page.route('**/api/agent/config', route => route.fulfill({ json: {
    origins: [], authReferences: [], defaultModel: 'fixture-model', endpoint: '',
    authentication: 'fixture', fabricConfigured: false, ontologyConfigured: false,
    fabricTokenRequired: false, demo: false,
    defaultSystemPrompt: 'You are an enterprise consultancy analyst.',
  } }));
  await page.addInitScript(({ workspaceId, lakehouseId }) => {
    if (!localStorage.getItem('fixture-initialized')) {
      localStorage.setItem('enterpriseiq:agents:v1:fixture-owner', JSON.stringify({
        agents: [{ id: 'generic', name: 'Retained persona', systemPrompt: 'Be concise.', skills: [], servers: [] }],
        selectedAgent: 'generic', selectedModel: 'fixture-model',
        fabricSelection: { workspaceId, lakehouseId, ontologyId: null },
      }));
      localStorage.setItem('fixture-initialized', '1');
    }
  }, { workspaceId: workspace, lakehouseId: lakehouse });
  let chatRequests = 0;
  await page.route('**/api/chat/stream', route => {
    chatRequests++;
    return route.fulfill({ contentType: 'text/event-stream',
      body: 'event: result\ndata: {"answer":"Explicitly cleared default fixture","method":"M4"}\n\n' });
  });
  await page.goto('/');
  await expect(page.getByText(`Selected Fabric workspace: ${workspace}.`, { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('Fabric workspace ID', { exact: true }).fill('not-a-uuid');
  await page.getByRole('button', { name: 'Discover workspace items' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'hyphenated UUIDs' })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await page.locator('textarea').fill('Do not fall back to operator data');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('main').getByText('Verified Entra identity is required for a selected workspace.', { exact: false })).toBeVisible();
  expect(chatRequests).toBe(0);
  currentOwner = 'fixture-other';
  await page.locator('textarea').fill('Do not use the previous account selection');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('main').getByText('Authenticated account changed.', { exact: false })).toBeVisible();
  expect(chatRequests).toBe(0);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Clear workspace / use operator default' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Authenticated account changed.' })).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('enterpriseiq:agents:v1:fixture-owner')!).fabricSelection.workspaceId)).toBe(workspace);
  currentOwner = 'fixture-owner';
  await page.reload();
  await expect(page.getByText('Source provenance unknown until metadata validation.', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Fabric workspace ID', { exact: true })).toHaveValue(workspace);
  await page.getByRole('button', { name: 'Clear workspace / use operator default' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Cleared.' })).toBeVisible();
  await page.getByRole('button', { name: 'Close settings' }).click();
  await expect(page.getByText('No live grounding connected', { exact: false })).toBeVisible();
  await page.reload();
  await expect(page.getByText('Agent: Retained persona', { exact: false })).toBeVisible();
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('enterpriseiq:agents:v1:fixture-owner')!));
  expect(saved.fabricSelection).toBeNull();
  expect(saved.selectedModel).toBe('fixture-model');
  expect(saved.agents[0].name).toBe('Retained persona');
  await page.locator('textarea').fill('Operator default after explicit clearing');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('main').getByText('Explicitly cleared default fixture', { exact: true })).toBeVisible();
  expect(chatRequests).toBe(1);
});

test('sample prompts cannot bypass invalid persisted workspace preferences', async ({ page }) => {
  await page.route('**/api/me', route => route.fulfill({ json: { configOwner: 'fixture-owner', demo: false } }));
  await page.addInitScript(() => {
    localStorage.setItem('enterpriseiq:agents:v1:fixture-owner', JSON.stringify({
      agents: [{ id: 'generic', name: 'Generic', systemPrompt: '', skills: [], servers: [] }],
      selectedAgent: 'generic', selectedModel: null,
      fabricSelection: { workspaceId: 'invalid', lakehouseId: null, ontologyId: null },
    }));
  });
  let chatRequests = 0;
  await page.route('**/api/chat/stream', route => {
    chatRequests++;
    return route.abort();
  });
  await page.goto('/');
  await expect(page.getByRole('alert').filter({ hasText: 'UUID' })).toBeVisible();
  await page.getByRole('main').getByRole('button').first().click();
  await expect(page.getByRole('alert').filter({ hasText: 'Authenticated preferences must load successfully' })).toBeVisible();
  expect(chatRequests).toBe(0);
});
