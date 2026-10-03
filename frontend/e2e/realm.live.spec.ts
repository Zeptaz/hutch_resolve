import { expect, test } from '@playwright/test'

// Read-only browser checks for realm selection. The API routes are stubbed so
// this does not require credentials or mutate a Resolve database.
test.skip(!process.env.E2E_BASE_URL, 'Set E2E_BASE_URL to a running frontend')

for (const realm of ['customer', 'agent'] as const) {
  test(`${realm} browser requests identify their Resolve realm`, async ({ page }) => {
    let seenRealm: string | undefined
    await page.route('**/api/v1/**', async (route) => {
      seenRealm = (await route.request().allHeaders())['x-resolve-realm']
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ error: { code: 'AUTH_REQUIRED', message: 'Sign in required' } }),
      })
    })

    await page.goto(realm === 'agent' ? '/agent' : '/')
    await expect.poll(() => seenRealm).toBe(realm)
  })
}
