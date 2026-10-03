import { expect, test } from '@playwright/test'
import { mockControl } from './helpers'

test('the intro plays once, then the landing page links to each system', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('status', { name: 'Loading HUTCH Resolve' })).toBeVisible()
  await expect(page.getByRole('status', { name: 'Loading HUTCH Resolve' })).toHaveCount(0, { timeout: 5000 })
  await expect(page.getByRole('heading', { level: 1 })).toContainText('resolved with care')
  for (const name of ['Preview of the customer chat on a phone', 'Preview of a voice call on a phone', 'Preview of the agent dashboard on a computer']) {
    await expect(page.getByRole('img', { name })).toBeVisible()
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBe(0)

  await page.getByRole('link', { name: 'Open the dashboard' }).click()
  await expect(page).toHaveURL(/\/agent$/)

  // Coming back in the same tab skips the intro.
  await page.goBack()
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByRole('status', { name: 'Loading HUTCH Resolve' })).toHaveCount(0)
  await page.getByRole('link', { name: 'Open the chat' }).click()
  await expect(page).toHaveURL(/\/chat$/)
  await expect(page.getByRole('textbox').last()).toBeEnabled()
})

test('the voice link asks a guest to sign in, and opens the call for a demo line', async ({ page }) => {
  await page.goto('/chat?call=1')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Sign in to a demo line to start a voice call')
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()
  const call = page.getByRole('region', { name: 'Voice call' })
  await expect(call.getByRole('heading', { name: 'Talk to Resolve' })).toBeVisible()
  await call.getByRole('button', { name: 'Back to chat' }).click()
  await expect(call).toHaveCount(0)
  await expect(page).toHaveURL(/\/chat$/)
})
