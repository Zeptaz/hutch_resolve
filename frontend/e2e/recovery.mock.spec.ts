import { expect, test } from '@playwright/test'
import { mockControl } from './helpers'

test('uncertain customer turn stays locked and retry reuses it without duplicate messages', async ({ page }) => {
  const text = 'I recharged LKR 1000 but my balance is LKR 420'
  await page.goto('/chat')
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Browse data packages' })).toBeEnabled()

  await mockControl(page, 'setOutage', true)
  const input = page.getByRole('textbox').last()
  await input.fill(text)
  await input.press('Enter')
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
  await expect(input).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Send' })).toBeDisabled()

  await mockControl(page, 'setOutage', false)
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByRole('button', { name: 'Yes, go ahead' })).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-message-id]').filter({ hasText: text })).toHaveCount(1)
})
