import { expect, type Page } from '@playwright/test'

/** Mock fixtures (docs/contracts/examples.json): the two cases the mock agent queue holds. */
export const MOCK = {
  D: { line: 'SIM-LK-0004', id: '90000000-0000-4000-8000-000000000041' },
  A: { line: 'SIM-LK-0001', id: '90000000-0000-4000-8000-000000000003' },
}

type MockControls = typeof import('../src/api/mock').mockControls
type ControlName = { [K in keyof MockControls]: MockControls[K] extends (...a: never[]) => unknown ? K : never }[keyof MockControls]

/**
 * Call one of the app's mock controls inside the page. Vite serves the same module instance the
 * app uses, so the change is visible to the running UI.
 */
export async function mockControl(page: Page, name: ControlName, ...args: unknown[]) {
  await page.evaluate(
    async ({ name, args }) => {
      const { mockControls } = await import(/* @vite-ignore */ '/src/api/mock.ts')
      return (mockControls[name] as (...a: unknown[]) => unknown)(...args)
    },
    { name, args },
  )
}

/** Mock mode accepts any agent identity and credential. */
export async function signInMockAgent(page: Page) {
  await page.goto('/agent')
  await expect(page.getByRole('heading', { name: /cases that need a person/i })).toBeVisible()
  await page.getByLabel('Demo identity').fill('agent.e2e')
  await page.getByLabel('Credential').fill('mock')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Review queue' })).toBeVisible()
}

export const queue = (page: Page) => page.getByRole('list', { name: 'Cases' })
export const queueRow = (page: Page, line: string) => queue(page).getByRole('link', { name: new RegExp(line) })
export const reviewPanel = (page: Page) => page.getByRole('region', { name: 'Your review' })
