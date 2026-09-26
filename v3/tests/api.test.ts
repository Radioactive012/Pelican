import test from 'node:test';
import assert from 'node:assert/strict';
import { ContextPassportApiClient } from '../src/core/api.ts';
import { getAllSettings, getSetting } from '../src/core/storage.ts';

test('a freshly installed hosted ZIP reads its backend from the stamped manifest', async () => {
  const previousChrome = (globalThis as any).chrome;
  (globalThis as any).chrome = {
    runtime: { getManifest: () => ({homepage_url: 'https://pelican-demo.onrender.com'}) },
    storage: {local: {get: (_keys: unknown, callback: (data: object) => void) => callback({})}},
  };
  try {
    assert.equal(await getSetting('backend_url'), 'https://pelican-demo.onrender.com');
    assert.equal((await getAllSettings()).backend_url, 'https://pelican-demo.onrender.com');
  } finally {
    (globalThis as any).chrome = previousChrome;
  }
});

test('manual Bearer prefix is normalized before authenticated requests', async () => {
  const originalFetch = globalThis.fetch;
  let authorization = '';
  globalThis.fetch = async (_url, options) => {
    authorization = new Headers(options?.headers).get('Authorization') || '';
    return new Response(JSON.stringify([]), { status: 200 });
  };
  try {
    const client = new ContextPassportApiClient('http://localhost:8000', 'Bearer test-bearer-user');
    await client.getPreferences();
    assert.equal(authorization, 'Bearer test-bearer-user');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('email/password sign-in returns an access token for later requests', async () => {
  const originalFetch = globalThis.fetch;
  const seen: string[] = [];
  globalThis.fetch = async (url, options) => {
    seen.push(String(url));
    if (String(url).endsWith('/auth/token')) {
      assert.deepEqual(JSON.parse(String(options?.body)), { email: 'test@example.com', password: 'sample-pass' });
      return new Response(JSON.stringify({ access_token: 'signed-in-token' }), { status: 200 });
    }
    assert.equal(new Headers(options?.headers).get('Authorization'), 'Bearer signed-in-token');
    return new Response(JSON.stringify([]), { status: 200 });
  };
  try {
    const client = new ContextPassportApiClient('http://localhost:8000');
    assert.equal(await client.login('test@example.com', 'sample-pass'), 'signed-in-token');
    await client.getPreferences();
    assert.equal(seen.length, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
