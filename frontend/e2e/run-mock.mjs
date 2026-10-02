/** Run the two mock projects with a Vite process owned by this runner.
 *
 * Playwright's webServer teardown can hang on Windows. Spawning Vite directly
 * lets us stop exactly the process we started, without reusing another server.
 */
import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { dirname, join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

const frontend = dirname(dirname(fileURLToPath(import.meta.url)))
const port = 5175
const url = `http://localhost:${port}`

async function requireFreePort() {
  const server = createServer()
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject)
      server.listen(port, 'localhost', resolve)
    })
  } catch (error) {
    throw new Error(`Port ${port} is already in use; refusing to attach mock tests to another server.`, { cause: error })
  } finally {
    if (server.listening) await new Promise((resolve) => server.close(resolve))
  }
}

async function waitForVite(child) {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error('Mock Vite exited before it became ready.')
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) })
      if (response.ok && child.exitCode === null && child.signalCode === null) return
    } catch {
      // Startup can take a few seconds; retry until the deadline.
    }
    await delay(200)
  }
  throw new Error('Mock Vite did not become ready within 30 seconds.')
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return
  const closed = new Promise((resolve) => child.once('close', resolve))
  child.kill()
  if (await Promise.race([closed.then(() => true), delay(5000).then(() => false)])) return
  child.kill('SIGKILL')
  await closed
}

let vite
let playwright
try {
  await requireFreePort()
  vite = spawn(process.execPath, [join(frontend, 'node_modules/vite/bin/vite.js'), '--port', String(port), '--strictPort'], {
    cwd: frontend,
    env: {
      ...process.env,
      VITE_API_MODE: 'mock',
      VITE_DEV_AGENT_IDENTITY: '',
      VITE_DEV_AGENT_CREDENTIAL: '',
    },
    stdio: 'inherit',
  })
  await waitForVite(vite)

  playwright = spawn(process.execPath, [join(frontend, 'node_modules/playwright/cli.js'), 'test', '--project=mock', '--project=mock-phone'], {
    cwd: frontend,
    env: { ...process.env, E2E_MOCK_SERVER_EXTERNAL: '1' },
    stdio: 'inherit',
  })
  const code = await new Promise((resolve, reject) => {
    playwright.once('error', reject)
    playwright.once('close', (exitCode, signal) => resolve(signal ? 130 : (exitCode ?? 1)))
  })
  process.exitCode = code
} catch (error) {
  console.error(error)
  process.exitCode = 1
} finally {
  await stopChild(playwright)
  await stopChild(vite)
}
