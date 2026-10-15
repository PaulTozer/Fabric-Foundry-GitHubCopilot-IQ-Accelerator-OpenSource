import { test, expect } from '@playwright/test';

test('demo label distinguishes evidence from inference and setup is usable by keyboard without GitHub', async ({ page }) => {
  await page.route('https://github.com/**', route => route.abort());
  await page.goto('/');
  const banner = page.getByRole('status').filter({ hasText: 'DEMO sources:' });
  await expect(banner).toContainText('synthetic Fabric IQ, Foundry IQ and Work IQ evidence');
  await expect(banner).toContainText('real model inference');
  await expect(banner).toContainText('not a provider status check');
  const help = page.getByRole('link', { name: 'Provider setup (opens in a new tab)', exact: true });
  await help.focus();
  const popup = page.waitForEvent('popup');
  await page.keyboard.press('Enter');
  const setup = await popup;
  await expect(setup).toHaveURL(/\/provider-setup$/);
  await expect(page).toHaveURL('/');
  await expect(setup.getByRole('heading', { name: 'Provider setup', exact: true })).toBeVisible();
  await expect(setup.getByText('static guidance, not a detected provider failure', { exact: false })).toBeVisible();
  await expect(setup.getByRole('heading', { name: 'Using Agent Manager and the hosted app' })).toBeVisible();
  await expect(setup.getByText('Hosted users do not need local environment variables', { exact: false })).toBeVisible();
  await expect(setup.getByLabel('Foundry server configuration')).toContainText('COPILOT_WIRE_API=responses');
  await expect(setup.getByLabel('Start the backend with a securely entered key')).toContainText('-AsSecureString');
  await expect(setup.getByText('Cognitive Services OpenAI User', { exact: false }).first()).toBeVisible();
  await expect(setup.locator('input')).toHaveCount(0);
  await expect(setup.getByRole('link', { name: 'README provider setup (GitHub)' })).toHaveAttribute('href', /README\.md#provider-setup$/);
  await expect(setup.getByRole('link', { name: 'Full BYOK reference (GitHub)' })).toHaveAttribute('href', /docs\/BYOK\.md$/);
  const back = setup.getByRole('link', { name: 'Back to chat' });
  await back.focus();
  await setup.keyboard.press('Enter');
  await expect(setup).toHaveURL('/');
  await expect(help).toBeVisible();
});

test('provider help is discoverable when demo sources are disabled and reflows on mobile', async ({ page }) => {
  await page.route('**/api/me', route => route.fulfill({ json: { demo: false, configOwner: 'mobile-test' } }));
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await expect(page.getByText('DEMO sources:', { exact: false })).toHaveCount(0);
  const popup = page.waitForEvent('popup');
  await page.getByRole('link', { name: 'Provider setup (opens in a new tab)', exact: true }).click();
  const setup = await popup;
  await expect(setup.getByRole('heading', { name: 'Provider setup', exact: true })).toBeVisible();
  expect(await setup.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('opening help does not hide genuine missing-provider errors in regular or SSE chat', async ({ page }) => {
  await page.goto('/provider-setup');
  await page.getByRole('link', { name: 'Back to chat' }).click();
  const response = await page.request.post('/api/chat', {
    data: { message: 'Check provider configuration.', method: 'M4' },
  });
  const result = await response.json();
  expect(result.error).toContain('Set COPILOT_MODEL and COPILOT_BASE_URL');
  expect(result.answer).toBeUndefined();
  await page.locator('textarea').fill('Check provider configuration.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByText(/Set COPILOT_MODEL and COPILOT_BASE_URL/)).toBeVisible();
  await expect(page.locator('textarea')).toBeEnabled();
  await expect(page.getByRole('link', { name: 'Provider setup (opens in a new tab)', exact: true })).toBeVisible();
});
