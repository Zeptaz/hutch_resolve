import { expect, test } from '@playwright/test'
import { mockControl } from './helpers'

// Customer journey A against the mock: complaint -> evidence -> explicit accept -> one success -> receipt.

test('journey A: evidence, explicit accept, success and a receipt', async ({ page }) => {
  await page.goto('/chat')
  await expect(page.getByText('Mock data from contract examples.')).toBeVisible()
  // Let the page finish creating its guest session first, or it can overwrite the switch below.
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()
  // The welcome message appears once the conversation is open; a message sent before that is not taken.
  await expect(page.getByText("What's going on?")).toBeVisible()

  const box = page.getByRole('textbox').last()
  await box.fill('I recharged LKR 1000 but my balance is LKR 420')
  await box.press('Enter')

  // Nothing is changed until the customer says yes.
  const accept = page.getByRole('button', { name: 'Yes, go ahead' })
  await expect(accept).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('Simulation — no real account is changed.').first()).toBeVisible()
  await accept.click()

  await expect(page.getByText('Succeeded').first()).toBeVisible({ timeout: 20_000 })
  await expect(accept).toHaveCount(0)
})

test('a signed-in customer can open the call panel before any proposal', async ({ page }) => {
  await page.goto('/chat')
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()

  await page.getByRole('button', { name: 'Call', exact: true }).click()
  await expect(page.getByText('Talk to Resolve')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Start voice call' })).toBeVisible()
})

test('spoken yes keeps the call offer visible until its button records the decision, then the call shows the receipt', async ({ page }) => {
  await page.goto('/chat')
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()

  await page.getByRole('button', { name: 'Call', exact: true }).click()
  await page.getByRole('button', { name: 'Start voice call' }).click()
  await expect(page.getByRole('button', { name: 'Try a balance issue' })).toBeVisible()
  await page.getByRole('button', { name: 'Try a balance issue' }).click()
  await expect(page.getByRole('heading', { name: 'Confirm an action' })).toBeVisible()

  await page.getByRole('button', { name: 'Say yes' }).click()
  await expect(page.getByRole('heading', { name: 'Confirm an action' })).toBeVisible()
  await expect(page.getByText('Please review the offer on your screen and tap "Yes, go ahead" or "No, leave it". Nothing has changed yet.')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toBeEnabled()
  await page.getByRole('button', { name: 'Yes, go ahead' }).click()
  await expect(page.getByRole('heading', { name: 'Confirm an action' })).toHaveCount(0)

  // The decision is recorded inside the call: the call stays connected and shows the receipt.
  await expect(page.getByRole('button', { name: 'End call' })).toBeVisible()
  await expect(page.getByText('Talk to Resolve')).toBeVisible()
  await expect(page.getByText('Trust Receipt')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('button', { name: 'Download receipt (JSON)' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Say no' })).toBeEnabled()
})

test('a review confirmed during a call keeps the call open and shows the review receipt', async ({ page }) => {
  await page.goto('/chat')
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()
  await expect(page.getByText("What's going on?")).toBeVisible()

  const box = page.getByRole('textbox').last()
  await box.fill('I recharged LKR 1000 but my balance is LKR 420')
  await box.press('Enter')
  await page.getByRole('button', { name: 'No, thanks' }).click({ timeout: 15_000 })

  await page.getByRole('button', { name: 'Ask for a human review' }).click()
  await page.getByLabel('What should the reviewer look at?').fill('Please check the remaining deduction.')
  await page.getByRole('button', { name: 'Prepare review request' }).click()
  await expect(page.getByText('A human review is ready to send', { exact: false })).toBeVisible()

  await page.getByRole('button', { name: 'Call', exact: true }).click()
  await page.getByRole('button', { name: 'Start voice call' }).click()
  await expect(page.getByRole('heading', { name: 'Confirm an action' })).toBeVisible()
  await page.getByRole('button', { name: 'Yes, go ahead' }).click()

  await expect(page.getByRole('button', { name: 'End call' })).toBeVisible()
  await expect(page.getByText('Sent for review')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('Review receipt')).toBeVisible()
  await expect(page.getByText(/^Ticket number SIM-TKT-/)).toBeVisible()
})
