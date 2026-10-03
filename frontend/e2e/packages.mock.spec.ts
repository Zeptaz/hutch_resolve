import { expect, test } from '@playwright/test'
import { mockControl } from './helpers'

test('package selection requires explicit confirmation and reports canonical pending then success', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Mock data from contract examples.')).toBeVisible()
  await expect(page.getByRole('textbox').last()).toBeEnabled()
  await page.waitForLoadState('networkidle')
  await mockControl(page, 'continueAsDemoLine')
  await page.reload()

  await page.getByRole('button', { name: 'Browse data packages' }).click()
  const catalogue = page.getByRole('region', { name: 'Available data packages' })
  await expect(catalogue).toBeVisible()
  await expect(catalogue).toContainText('Monthly 6 GB')
  await expect(catalogue).toContainText('Recommended from your usage')
  await expect(catalogue).toContainText('No automatic renewal')
  await catalogue.getByRole('button', { name: 'Choose package' }).nth(1).click()

  const confirmation = page.getByRole('region', { name: 'Your confirmation is needed' })
  await expect(confirmation).toBeVisible()
  await expect(confirmation).toContainText('Monthly 6 GB')
  await expect(confirmation).toContainText('LKR 499.00')
  await expect(confirmation).toContainText('6 GB')
  await expect(confirmation).toContainText('30 days')
  await expect(page.getByText(/active on your line/i)).toHaveCount(0)

  await confirmation.getByRole('button', { name: 'Yes, go ahead' }).click()
  await expect(page.getByText(/submitted the package activation/i)).toBeVisible()
  const operation = page.getByRole('region', { name: 'Activating your package' })
  await expect(operation).toBeVisible()
  await expect(operation).toContainText(/Pending|In progress/)
  await expect(operation).toContainText(/not done yet/)
  await expect(operation).not.toContainText('Done. The package is active on your line.')
  await expect(operation).toContainText('Done. The package is active on your line.', { timeout: 10_000 })
})
