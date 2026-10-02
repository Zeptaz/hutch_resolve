import { expect, test } from '@playwright/test'
import { MOCK, queue, queueRow, signInMockAgent } from './helpers'

// Phone width: the queue and the case take turns, and nothing scrolls sideways.

test('queue and case take turns on a phone', async ({ page }) => {
  await signInMockAgent(page)
  await expect(queue(page)).toBeVisible()
  await queueRow(page, MOCK.D.line).click()
  await expect(page.getByRole('region', { name: 'Why this case is here' })).toBeVisible()
  await expect(queue(page)).toBeHidden()

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  await page.getByRole('link', { name: 'Queue' }).click()
  await expect(queue(page)).toBeVisible()
})
