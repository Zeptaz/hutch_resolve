import { defineConfig, devices } from '@playwright/test'

// The mock projects start their own Vite server. Select --project=live explicitly for
// the real stack; its tests also require E2E_DISPOSABLE_DB=1 before mutating cases.
const MOCK_PORT = 5175
const selectedProjects = process.argv.flatMap((arg, index, args) =>
  arg.startsWith('--project=') ? [arg.slice('--project='.length)] : arg === '--project' ? [args[index + 1]] : [],
)
const liveOnly = selectedProjects.length === 1 && selectedProjects[0] === 'live'
// Workaround for environments where a Vite child process prevents Playwright's
// webServer shutdown. The tests assert the mock banner before using any controls.
const externalMockServer = process.env.E2E_MOCK_SERVER_EXTERNAL === '1'

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
    ...(process.env.E2E_CHROMIUM_EXECUTABLE
      ? { launchOptions: { executablePath: process.env.E2E_CHROMIUM_EXECUTABLE } }
      : {}),
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
    {
      name: 'live',
      testMatch: /.*\.live\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173' },
    },
  ],
  webServer: liveOnly || externalMockServer ? undefined : {
    command: `node node_modules/vite/bin/vite.js --port ${MOCK_PORT} --strictPort`,
    url: `http://localhost:${MOCK_PORT}`,
    env: { VITE_API_MODE: 'mock', VITE_DEV_AGENT_IDENTITY: '', VITE_DEV_AGENT_CREDENTIAL: '' },
    // Never attach mock tests to an arbitrary server that could be in live mode.
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
