import { expect, test } from '@playwright/test'
import { mockControl } from './helpers'

test('a voice call keeps the chat header and case panel, and its offer is answered with the chat card', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()

  await page.getByRole('button', { name: 'Call', exact: true }).click()
  const call = page.getByRole('main', { name: 'Voice call' })
  await expect(call.getByRole('heading', { name: 'Talk to Resolve' })).toBeVisible()
  await expect(call.getByText('Ready', { exact: true })).toBeVisible()
  // The chat header stays: brand and language switch, but no second Call button.
  await expect(page.getByRole('radiogroup', { name: 'Language' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Call', exact: true })).toHaveCount(0)

  await call.getByRole('button', { name: 'Start voice call' }).click()
  await expect(call.getByText('Listening', { exact: true })).toBeVisible()
  await call.getByRole('button', { name: 'Try a balance issue' }).click()

  const offer = call.getByRole('region', { name: 'Your confirmation is needed' })
  await expect(offer).toContainText('Stop a subscription renewing')
  await expect(page.getByRole('complementary').getByText('Balance or recharge')).toBeVisible()

  // While Voice is waiting for a spoken answer the buttons stay off; after an interruption a tap answers.
  await expect(offer.getByRole('button', { name: 'Yes, go ahead' })).toBeDisabled()
  await call.getByRole('button', { name: 'Interrupt' }).click()
  await expect(offer.getByRole('button', { name: 'Yes, go ahead' })).toBeEnabled()
  await offer.getByRole('button', { name: 'Yes, go ahead' }).click()

  await expect(call).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Stopping subscription renewal' })).toContainText('Succeeded')
})

test('the call screen follows the chat language', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()

  await page.getByRole('button', { name: 'Call', exact: true }).click()
  await page.locator('[data-lang="si"]').click()
  await expect(page.getByRole('main', { name: 'හඬ ඇමතුම' }).getByRole('heading', { name: 'Resolve සමඟ කතා කරන්න' })).toBeVisible()
})
