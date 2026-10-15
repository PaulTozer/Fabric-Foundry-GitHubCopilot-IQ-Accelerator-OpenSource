import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  use: {
    baseURL: 'http://127.0.0.1:3005',
    headless: true,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  workers: 1,
  expect: { timeout: 15000 },
  reporter: 'list',
  webServer: [
    {
      command: 'node scripts/browser-mcp.mjs',
      url: 'http://127.0.0.1:8876/registry',
      timeout: 60000,
      reuseExistingServer: false,
      env: {
        IQ_DEMO_MODE: '1', IQ_MCP_CONFIG: '', COPILOT_MODEL: '', COPILOT_BASE_URL: '',
        COPILOT_API_KEY: '', COPILOT_BEARER_TOKEN: '',
      },
    },
    {
      command: 'npm run dev -- --hostname 127.0.0.1 --port 3005',
      url: 'http://127.0.0.1:3005',
      timeout: 60000,
      reuseExistingServer: false,
      env: {
        MCP_BASE_URL: 'http://127.0.0.1:8876', IQ_DEMO_MODE: '1', IQ_LOCAL_AUTH_BYPASS: '1',
      },
    },
  ],
});
