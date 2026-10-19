import test from 'node:test'
import assert from 'node:assert/strict'
import { createClient, ApiError } from '../src/api/client.js'
const response = (data, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
const client = (options) =>
  createClient({ base: 'http://127.0.0.1:8000', getToken: () => 'test-token', ...options })

test('JWT only goes in Authorization; public requests omit it', async () => {
  const calls = []
  const request = client({
    fetchImpl: async (url, init) => {
      calls.push({ url, ...init })
      return response({})
    },
  })
  await request('/products')
  await request('/auth/login', {
    method: 'POST',
    data: { username: 'operator', password: 'not-persisted' },
    public: true,
  })
  assert.equal(calls[0].headers.Authorization, 'Bearer test-token')
  assert.equal(calls[0].credentials, 'omit')
  assert.equal(calls[1].headers.Authorization, undefined)
  assert.equal(calls[0].url.includes('token'), false)
})
test('401 expires the issued session; 403 and login errors do not', async () => {
  let expired = []
  const request = client({
    onUnauthorized: (token) => expired.push(token),
    fetchImpl: async () => response({ detail: 'expired' }, 401),
  })
  await assert.rejects(request('/products'), (e) => e instanceof ApiError && e.status === 401)
  await assert.rejects(request('/auth/login', { public: true }))
  assert.deepEqual(expired, ['test-token'])
  await assert.rejects(
    client({
      onUnauthorized: () => expired.push('wrong'),
      fetchImpl: async () => response({ detail: 'forbidden' }, 403),
    })('/products'),
  )
  assert.equal(expired.length, 1)
})
test('writes are never automatically retried on a network failure', async () => {
  let count = 0
  const request = client({
    fetchImpl: async () => {
      ++count
      throw new TypeError('connection lost')
    },
  })
  await assert.rejects(
    request('/publish-records/1/execute', { method: 'POST', data: { confirm_account: true } }),
    (e) => e.status === 0,
  )
  assert.equal(count, 1)
})
test('structured validation failures remain useful and a 204 has no JSON body', async () => {
  const request = client({
    fetchImpl: async () =>
      response({ detail: [{ loc: ['body', 'password'], msg: 'too short' }] }, 422),
  })
  await assert.rejects(request('/auth/register'), /password: too short/)
  assert.equal(
    await client({ fetchImpl: async () => new Response(null, { status: 204 }) })('/auth/logout', {
      method: 'POST',
    }),
    null,
  )
})
test('multipart upload preserves browser content type and binary access is authenticated', async () => {
  const form = new FormData()
  form.append('file', new Blob(['hello']), 'test.txt')
  let upload
  const request = client({
    fetchImpl: async (_, init) => {
      upload = init
      return response({ id: 1 })
    },
  })
  await request('/products/1/documents', { method: 'POST', data: form })
  assert.equal(upload.headers['Content-Type'], undefined)
  assert.equal(upload.body, form)
  const image = await client({
    fetchImpl: async (_, init) => {
      assert.equal(init.headers.Authorization, 'Bearer test-token')
      return new Response(new Blob(['png']))
    },
  })('/contents/1/images/0', { blob: true })
  assert.ok(image instanceof Blob)
})
test('API path cannot redirect the token to another host', async () => {
  let calls = 0
  const request = client({
    fetchImpl: () => {
      ++calls
    },
  })
  await assert.rejects(request('//untrusted.example/path'))
  await assert.rejects(request('https://untrusted.example/path'))
  assert.equal(calls, 0)
})
