import { expect, test } from '@playwright/test'
import { MOCK, mockControl, queue, queueRow, reviewPanel, signInMockAgent } from './helpers'

// Agent dashboard against the in-app contract mock. Behaviour only: the real API is covered by *.live.spec.ts.

test.beforeEach(async ({ page }) => {
  await signInMockAgent(page)
})

test('queue lists cases and flags the ones that need attention', async ({ page }) => {
  await expect(page.getByText('Signed in as')).toContainText('synthetic-review-agent')
  await expect(queue(page).getByRole('listitem')).toHaveCount(2)
  const d = queueRow(page, MOCK.D.line)
  await expect(d).toContainText('Evidence conflicts')
  await expect(d).toContainText('Evidence: Conflicting')
  await expect(queueRow(page, MOCK.A.line)).not.toContainText('Evidence conflicts')
  await expect(page.getByText('Pick a case from the queue')).toBeVisible()
})

test('case packet answers first: why it is here and the key numbers', async ({ page }) => {
  await queueRow(page, MOCK.D.line).click()
  await expect(page).toHaveURL(new RegExp(`/agent/cases/${MOCK.D.id}$`))
  const why = page.getByRole('region', { name: 'Why this case is here' })
  await expect(why).toContainText('Records disagree')
  await expect(why).toContainText('Expected')
  await expect(why).toContainText('LKR 420.00')
  await expect(why).toContainText('LKR 350.00')
  await expect(why).toContainText('−LKR 70.00')
  // Separate state families, each under its own label.
  await expect(page.getByRole('definition').filter({ hasText: 'Needs human review' })).toBeVisible()
  await expect(page.getByRole('definition').filter({ hasText: 'Conflicting' })).toBeVisible()
})

test('tabs show actions and ticket, receipts and history', async ({ page }) => {
  await queueRow(page, MOCK.D.line).click()
  await page.getByRole('tab', { name: /Actions & ticket/ }).click()
  const ticket = page.getByRole('region', { name: 'Review ticket' })
  await expect(ticket).toContainText('Delivery to the ticket system')
  await expect(ticket).toContainText('Not assigned yet')
  await page.getByRole('tab', { name: /History/ }).click()
  await expect(page.getByRole('tabpanel')).toBeVisible()
  await page.getByRole('tab', { name: /Evidence/ }).click()
  await expect(page.getByRole('region', { name: 'Sources checked' })).toBeVisible()
})

test('filters, chips and the status selector narrow the queue on the server', async ({ page }) => {
  await page.getByRole('combobox', { name: 'Evidence filter' }).click()
  await page.getByRole('option', { name: 'Conflicting' }).click()
  await expect(queue(page).getByRole('listitem')).toHaveCount(1)
  await expect(queueRow(page, MOCK.D.line)).toBeVisible()
  await page.getByRole('button', { name: /Remove filter Evidence: conflicting/ }).click()
  await expect(queue(page).getByRole('listitem')).toHaveCount(2)

  await page.getByRole('radio', { name: 'Closed' }).click()
  await expect(page.getByText('No cases match')).toBeVisible()
  await page.getByRole('button', { name: 'Show all cases' }).click()
  await expect(queue(page).getByRole('listitem')).toHaveCount(2)
})

test('search is exact and "/" focuses it', async ({ page }) => {
  await page.locator('body').press('/')
  const search = page.getByRole('textbox', { name: /Search by exact case ID or line/ })
  await expect(search).toBeFocused()
  await search.fill(MOCK.A.line)
  await search.press('Enter')
  await expect(queue(page).getByRole('listitem')).toHaveCount(1)
  await expect(queueRow(page, MOCK.A.line)).toBeVisible()
  await page.getByRole('button', { name: 'Clear all' }).click()
  await expect(queue(page).getByRole('listitem')).toHaveCount(2)
})

test('review: start, close with an outcome, reopen with a reason', async ({ page }) => {
  await queueRow(page, MOCK.D.line).click()
  const panel = reviewPanel(page)
  await expect(panel).toContainText('Nobody has picked this up yet.')

  await panel.getByRole('button', { name: 'Start review' }).click()
  await expect(panel).toContainText('Under review')
  await expect(panel.getByText(/Saved as version 3/)).toBeVisible()

  // Close needs an outcome and a note; the button stays off until both are there.
  await panel.getByRole('button', { name: 'Close…' }).click()
  const closeBtn = panel.getByRole('button', { name: 'Close review' })
  await expect(closeBtn).toBeDisabled()
  await panel.getByRole('radio', { name: /Needs operator follow-up/ }).check()
  await expect(closeBtn).toBeDisabled()
  await panel.getByLabel('Closing note').fill('Billing operator must confirm the missing 70 LKR.')
  await expect(closeBtn).toBeEnabled()
  await closeBtn.click()
  await expect(panel).toContainText('Closed: needs operator follow-up')
  await expect(panel.getByRole('listitem').filter({ hasText: 'Billing operator must confirm' })).toBeVisible()

  // Reopen needs a reason.
  await panel.getByRole('button', { name: 'Reopen…' }).click()
  const reopenBtn = panel.getByRole('button', { name: 'Reopen review' })
  await expect(reopenBtn).toBeDisabled()
  await panel.getByLabel('Reason for reopening').fill('Customer sent a new statement.')
  await reopenBtn.click()
  await expect(panel).toContainText('Under review')
  await expect(panel.getByText(/Saved as version 5/)).toBeVisible()
})

test('a change by another agent keeps the draft and sends nothing until checked', async ({ page }) => {
  await queueRow(page, MOCK.D.line).click()
  const panel = reviewPanel(page)
  const note = panel.getByLabel('Internal note', { exact: true })
  await note.fill('My findings so far.')

  await mockControl(page, 'reviewElsewhere', MOCK.D.id, 'Colleague: customer called again.')
  await panel.getByRole('button', { name: 'Add note' }).click()

  await expect(panel.getByRole('alert')).toContainText('Someone changed this case while you were writing')
  await expect(note).toHaveValue('My findings so far.')
  await expect(panel.getByRole('listitem').filter({ hasText: 'Colleague: customer called again.' })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Add note' })).toBeDisabled()

  await panel.getByRole('button', { name: "I've checked it" }).click()
  await panel.getByRole('button', { name: 'Add note' }).click()
  await expect(panel.getByText(/Saved as version 4/)).toBeVisible()
  await expect(note).toHaveValue('')
  await expect(panel.getByRole('listitem').filter({ hasText: 'My findings so far.' })).toBeVisible()
})

test('drafts survive a reload', async ({ page }) => {
  await queueRow(page, MOCK.D.line).click()
  await reviewPanel(page).getByLabel('Internal note', { exact: true }).fill('Half-written note')
  await page.reload()
  await expect(reviewPanel(page).getByLabel('Internal note', { exact: true })).toHaveValue('Half-written note')
})

test('j / k move through the queue, but never while typing a note', async ({ page }) => {
  await queueRow(page, MOCK.D.line).focus()
  await page.keyboard.press('j')
  await expect(page).toHaveURL(new RegExp(`/agent/cases/${MOCK.D.id}$`))
  await page.keyboard.press('j')
  await expect(page).toHaveURL(new RegExp(`/agent/cases/${MOCK.A.id}$`))
  await page.keyboard.press('k')
  await expect(page).toHaveURL(new RegExp(`/agent/cases/${MOCK.D.id}$`))

  const note = reviewPanel(page).getByLabel('Internal note', { exact: true })
  const url = page.url()
  await note.fill('')
  await note.pressSequentially('jk jk')
  await expect(page).toHaveURL(url)
  await expect(note).toHaveValue('jk jk')
})

test('an expired session sends the agent back to sign in', async ({ page }) => {
  await mockControl(page, 'expireSession', 'agent')
  await page.reload()
  await expect(page.getByText('Your session ended. Sign in again to continue.')).toBeVisible()
})

test('a Resolve outage shows an error with retry, not an empty queue', async ({ page }) => {
  await mockControl(page, 'setOutage', true)
  await page.reload()
  await expect(page.getByText('Could not load the queue')).toBeVisible()
  await mockControl(page, 'setOutage', false)
  await page.getByRole('button', { name: /Try again|Retry/i }).first().click()
  await expect(queue(page).getByRole('listitem')).toHaveCount(2)
})
