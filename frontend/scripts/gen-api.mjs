// Generates src/api/schema.d.ts from the shared contract.
//
// The contract's oneOf unions (TurnInput, Card, ...) declare `discriminator: {propertyName: "type"}`
// without a `mapping`. Per OpenAPI, the implicit mapping is the schema *name* ("TextInput"), which
// contradicts each variant's `const` ("text") and the published examples. Each variant's `const`
// already discriminates the union, so drop mapping-less discriminators before generating.
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const source = path.resolve(root, '../docs/contracts/openapi.json')
const tmpDir = path.join(root, 'node_modules/.cache/gen-api')
const tmp = path.join(tmpDir, 'openapi.json')

let stripped = 0
function strip(node) {
  if (Array.isArray(node)) return node.forEach(strip)
  if (!node || typeof node !== 'object') return
  if (node.discriminator && !node.discriminator.mapping) {
    delete node.discriminator
    stripped++
  }
  Object.values(node).forEach(strip)
}

const spec = JSON.parse(readFileSync(source, 'utf8'))
strip(spec)
mkdirSync(tmpDir, { recursive: true })
writeFileSync(tmp, JSON.stringify(spec))
console.log(`Removed ${stripped} mapping-less discriminator(s).`)

execFileSync('npx', ['--yes', 'openapi-typescript@7', tmp, '-o', path.join(root, 'src/api/schema.d.ts')], {
  stdio: 'inherit',
})
