import { defineConfig, devices } from '@playwright/test'

// Two suites:
//  - mock  (default): the app's own contract-example mock on a private port. No backend, deterministic.
//  - live  (E2E_LIVE=1): an already running dev stack (Vite in live mode + Resolve + seeded PostgreSQL).
//    See e2e/README.md for setup. It changes review state, so use a throwaway database.
const LIVE = process.env.E2E_LIVE === '1'
const MOCK_PORT = 5175

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'mock',
      testMatch: /.*\.mock\.spec\.ts/,
      testIgnore: /phone\.mock\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], baseURL: `http://localhost:${MOCK_PORT}` },
    },
    {
      name: 'mock-phone',
      testMatch: /phone\.mock\.spec\.ts/,
      use: { ...devices['Pixel 7'], baseURL: `http://localhost:${MOCK_PORT}` },
    },
    ...(LIVE
      ? [
          {
            name: 'live',
            testMatch: /.*\.live\.spec\.ts/,
            use: { ...devices['Desktop Chrome'], baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173' },
          },
        ]
      : []),
  ],
  webServer: {
    command: `npx vite --port ${MOCK_PORT} --strictPort`,
    url: `http://localhost:${MOCK_PORT}`,
    env: { VITE_API_MODE: 'mock', VITE_DEV_AGENT_IDENTITY: '', VITE_DEV_AGENT_CREDENTIAL: '' },
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
