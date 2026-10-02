import { randomUUID } from 'node:crypto'
import { expect, request as playwrightRequest, test, type APIRequestContext, type Page } from '@playwright/test'
import { queue, reviewPanel } from './helpers'

// Agent dashboard against a real Resolve (Harry's app) with seeded synthetic cases. Opt-in: E2E_LIVE=1.
// Needs a throwaway database: these tests add notes and change review status. See e2e/README.md.

const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:5173'
const AGENT = { demo_identity: process.env.E2E_AGENT_ID ?? '', credential: process.env.E2E_AGENT_CREDENTIAL ?? '' }
const CUSTOMER = { demo_identity: process.env.E2E_CUSTOMER_ID ?? '', credential: process.env.E2E_CUSTOMER_CREDENTIAL ?? '' }
const D_LINE = 'SIM-LK-0004'

test.skip(!AGENT.demo_identity || !AGENT.credential, 'Set E2E_AGENT_ID and E2E_AGENT_CREDENTIAL for the live suite')

/** A second, independent agent session: the "colleague" for conflict tests and the API-level checks. */
async function apiSession(realm: 'agent' | 'customer', who: typeof AGENT) {
  const ctx = await playwrightRequest.newContext({ baseURL: BASE, extraHTTPHeaders: { Origin: new URL(BASE).origin } })
  const res = await ctx.post(realm === 'agent' ? '/api/v1/agent/sessions' : '/api/v1/demo/sessions', { data: who })
  expect(res.status(), `${realm} sign-in`).toBe(200)
  return { ctx, csrf: ((await res.json()) as { csrf_token: string }).csrf_token }
}

async function signIn(page: Page) {
  await page.goto('/agent')
  const quick = page.getByRole('button', { name: /^Continue as / })
  if (await quick.isVisible().catch(() => false)) {
    await quick.click()
  } else {
    await page.getByLabel('Demo identity').fill(AGENT.demo_identity)
    await page.getByLabel('Credential').fill(AGENT.credential)
    await page.getByRole('button', { name: 'Sign in' }).click()
  }
  await expect(page.getByRole('heading', { name: 'Review queue' })).toBeVisible()
}

/** Newest case for line D (search is exact; the queue is newest first). */
async function openNewestD(page: Page) {
  const search = page.getByRole('textbox', { name: /Search by exact case ID or line/ })
  await search.fill(D_LINE)
  await search.press('Enter')
  await expect(page.getByText(`“${D_LINE}”`)).toBeVisible()
  await queue(page).getByRole('link').first().click()
  await expect(page.getByRole('region', { name: 'Why this case is here' })).toBeVisible()
  return page.url().split('/').pop()!
}

async function caseVersion(ctx: APIRequestContext, caseId: string) {
  const res = await ctx.get(`/api/v1/agent/cases/${caseId}`)
  expect(res.status()).toBe(200)
  return ((await res.json()) as { case: { version: number; review_status: string } }).case
}

test('D shows the conflict and the server numbers', async ({ page }) => {
  await signIn(page)
  await openNewestD(page)
  const why = page.getByRole('region', { name: 'Why this case is here' })
  await expect(why).toContainText('Records disagree')
  await expect(why).toContainText('LKR 420.00')
  await expect(why).toContainText('LKR 350.00')
  await expect(why).toContainText('−LKR 70.00')
})

test("a colleague's note causes a 409; the draft is kept and sent only after checking", async ({ page }) => {
  await signIn(page)
  const caseId = await openNewestD(page)
  const panel = reviewPanel(page)
  const mine = `E2E draft ${randomUUID().slice(0, 8)}`
  await panel.getByLabel('Internal note', { exact: true }).fill(mine)

  const colleague = await apiSession('agent', AGENT)
  const theirs = `E2E colleague ${randomUUID().slice(0, 8)}`
  const current = await caseVersion(colleague.ctx, caseId)
  const patch = await colleague.ctx.patch(`/api/v1/agent/cases/${caseId}/review`, {
    headers: { 'X-CSRF-Token': colleague.csrf, 'Idempotency-Key': randomUUID() },
    data: { expected_version: current.version, note: theirs },
  })
  expect(patch.status()).toBe(200)

  await panel.getByRole('button', { name: 'Add note' }).click()
  await expect(panel.getByRole('alert')).toContainText('Someone changed this case')
  await expect(panel.getByLabel('Internal note', { exact: true })).toHaveValue(mine)
  await expect(panel.getByRole('listitem').filter({ hasText: theirs })).toBeVisible()

  await panel.getByRole('button', { name: "I've checked it" }).click()
  await panel.getByRole('button', { name: 'Add note' }).click()
  await expect(panel.getByText(/Saved as version/)).toBeVisible()
  const after = (await (await colleague.ctx.get(`/api/v1/agent/cases/${caseId}`)).json()) as { review_notes: { note: string }[] }
  expect(after.review_notes.map((n) => n.note)).toEqual(expect.arrayContaining([mine, theirs]))
  await colleague.ctx.dispose()
})

test('close needs an outcome and a note; reopen needs a reason', async ({ page }) => {
  await signIn(page)
  await openNewestD(page)
  const panel = reviewPanel(page)

  // Works from any starting state the previous runs left behind.
  if (await panel.getByRole('button', { name: 'Reopen…' }).isVisible()) {
    await panel.getByRole('button', { name: 'Reopen…' }).click()
    await panel.getByLabel('Reason for reopening').fill('E2E: reopen before the close check.')
    await panel.getByRole('button', { name: 'Reopen review' }).click()
    await expect(panel.getByRole('button', { name: 'Close…' })).toBeVisible()
  }

  await panel.getByRole('button', { name: 'Close…' }).click()
  const close = panel.getByRole('button', { name: 'Close review' })
  await expect(close).toBeDisabled()
  await panel.getByRole('radio', { name: /Review complete/ }).check()
  await expect(close).toBeDisabled()
  await panel.getByLabel('Closing note').fill('E2E: evidence reviewed.')
  await close.click()
  await expect(panel).toContainText('Closed: review complete')

  await panel.getByRole('button', { name: 'Reopen…' }).click()
  await expect(panel.getByRole('button', { name: 'Reopen review' })).toBeDisabled()
  await panel.getByLabel('Reason for reopening').fill('E2E: new information.')
  await panel.getByRole('button', { name: 'Reopen review' }).click()
  await expect(panel).toContainText('Under review')
})

test('agent APIs refuse anonymous callers, customers and missing CSRF', async () => {
  const anon = await playwrightRequest.newContext({ baseURL: BASE })
  expect((await anon.get('/api/v1/agent/cases')).status()).toBe(401)
  await anon.dispose()

  const agent = await apiSession('agent', AGENT)
  const first = ((await (await agent.ctx.get('/api/v1/agent/cases?limit=1')).json()) as { items: { case_id: string; version: number }[] }).items[0]
  const noCsrf = await agent.ctx.patch(`/api/v1/agent/cases/${first.case_id}/review`, {
    headers: { 'Idempotency-Key': randomUUID() },
    data: { expected_version: first.version, note: 'should be refused' },
  })
  expect(noCsrf.status()).toBe(403)
  await agent.ctx.dispose()

  test.skip(!CUSTOMER.demo_identity, 'Set E2E_CUSTOMER_ID / E2E_CUSTOMER_CREDENTIAL to check the customer role')
  const customer = await apiSession('customer', CUSTOMER)
  expect([401, 403]).toContain((await customer.ctx.get('/api/v1/agent/cases')).status())
  await customer.ctx.dispose()
})

test('a customer session does not open the dashboard', async ({ browser }) => {
  test.skip(!CUSTOMER.demo_identity, 'Set E2E_CUSTOMER_ID / E2E_CUSTOMER_CREDENTIAL to check the customer role')
  const context = await browser.newContext({ baseURL: BASE })
  const page = await context.newPage()
  await page.goto('/')
  const res = await page.request.post('/api/v1/demo/sessions', { data: CUSTOMER, headers: { Origin: new URL(BASE).origin } })
  expect(res.status()).toBe(200)
  await page.goto('/agent')
  await expect(page.getByRole('heading', { name: /cases that need a person/i })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Review queue' })).toHaveCount(0)
  await context.close()
})
