import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const source = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const client = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)

test('failed inventory reads never return demo inventory', async () => {
  const previous = globalThis.fetch
  try {
    globalThis.fetch = async () => { throw new Error('offline') }
    await assert.rejects(client.getSnapshot('http://localhost:8080'), /offline/)
    globalThis.fetch = async () => new Response('{}', { status: 503 })
    await assert.rejects(client.getSnapshot('http://localhost:8080'), error => error.status === 503)
  } finally { globalThis.fetch = previous }
})

test('idempotency key and inventory quantities reach the caller unchanged', async () => {
  const previous = globalThis.fetch
  try {
    globalThis.fetch = async (_url, options) => {
      assert.equal(options.headers['Idempotency-Key'], 'receive-1')
      return new Response(JSON.stringify({ on_hand: 10, reserved: 3, available: 7 }))
    }
    const result = await client.apiRequest('http://localhost:8080', '/test', {
      method: 'POST', idempotencyKey: 'receive-1',
    })
    assert.deepEqual(result, { on_hand: 10, reserved: 3, available: 7 })
  } finally { globalThis.fetch = previous }
})
