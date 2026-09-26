import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const dist = path.resolve('dist');

test('hosted website welcome-back page uses its own backend and links both ways', async () => {
  const website = path.resolve('../website');
  const home = await readFile(path.join(website, 'index.html'), 'utf8');
  const html = await readFile(path.join(website, 'dashboard.html'), 'utf8');
  const script = await readFile(path.join(website, 'dashboard.js'), 'utf8');
  assert.match(home, /href="\.\/dashboard\.html"/);
  assert.match(home, /href="\.\/download\/pelican-v3\.zip"/);
  assert.match(html, /href="\.\/index\.html"/);
  const dom = new JSDOM(html, { url: 'https://pelican-demo.onrender.com/dashboard.html', runScripts: 'outside-only' });
  const calls = [];
  dom.window.fetch = async url => {
    calls.push(url);
    if (url.endsWith('/ready')) return {ok:true,json:async()=>({status:'ready'})};
    if (url.endsWith('/api/v1/auth/token')) return {ok:true,json:async()=>({access_token:'test-token'})};
    if (url.endsWith('/api/v1/memories') || url.endsWith('/api/v1/preferences')) return {ok:true,json:async()=>([])};
    throw new Error('Unexpected URL: ' + url);
  };
  dom.window.eval(script);
  dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
  await new Promise(resolve => setTimeout(resolve, 30));
  assert(calls.includes('https://pelican-demo.onrender.com/ready'));
  assert.equal(dom.window.document.getElementById('authGate').hidden, false);
  assert.equal(dom.window.document.getElementById('backendAddress').textContent, 'https://pelican-demo.onrender.com');
  dom.window.close();
});

test('first-run form saves explicit memories, verifies the vault, and shares auth with dashboard', async () => {
  const html = await readFile(path.join(dist, 'onboarding.html'), 'utf8');
  const script = await readFile(path.join(dist, 'onboarding.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'chrome-extension://pelican/onboarding.html', runScripts: 'outside-only' });
  const { window } = dom;
  window.scrollTo = () => {};
  const savedStorage = {};
  window.chrome = { storage: { local: {
    get: async key => ({ [key]: savedStorage[key] }),
    set: async data => Object.assign(savedStorage, data),
  } } };
  const requests = [];
  window.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (url.endsWith('/api/v1/auth/token')) return { ok: true, json: async () => ({ access_token: 'signed-in-token' }) };
    if (url.endsWith('/api/v1/memories/import')) return { ok: true, json: async () => ({ saved: [{ id: 'memory-1' }], saved_count: 1, duplicate_count: 0, skipped_count: 0 }) };
    if (url.endsWith('/api/v1/memories')) return { ok: true, json: async () => [{ id: 'memory-1', text: 'User likes concise examples.' }] };
    throw new Error('unexpected endpoint ' + url);
  };
  window.eval(script);
  const doc = window.document;
  doc.getElementById('u-name').value = 'Ada';
  doc.getElementById('importData').value = 'Here is your export:\n```text\n- User likes concise examples.\n- User likes concise examples.\n```\nThat is the complete set.';
  doc.getElementById('accountEmail').value = 'ada@example.com';
  doc.getElementById('accountPassword').value = 'safe-test-password';
  doc.querySelector('[data-step="2"]').click();
  doc.querySelector('[data-step="3"]').click();
  doc.querySelector('[data-action="finish-setup"]').click();
  await new Promise(resolve => setTimeout(resolve, 30));

  assert.equal(doc.getElementById('step-done').classList.contains('active'), true, doc.getElementById('setup-error').textContent);
  assert.equal(savedStorage.auth_token, 'signed-in-token');
  assert.equal(savedStorage.user_email, 'ada@example.com');
  const importRequest = requests.find(item => item.url.endsWith('/api/v1/memories/import'));
  assert.deepEqual(JSON.parse(importRequest.options.body).memories, ['User likes concise examples.']);
  assert.equal(importRequest.options.headers.Authorization, 'Bearer signed-in-token');
  assert.match(doc.getElementById('success-message').textContent, /1 new memories saved/);
  dom.window.close();
});

test('onboarding refuses to report success when the vault cannot confirm saved IDs', async () => {
  const html = await readFile(path.join(dist, 'onboarding.html'), 'utf8');
  const script = await readFile(path.join(dist, 'onboarding.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'chrome-extension://pelican/onboarding.html', runScripts: 'outside-only' });
  const { window } = dom;
  window.scrollTo = () => {};
  window.chrome = { storage: { local: { get: async () => ({ auth_token: 'token' }), set: async () => {} } } };
  window.fetch = async url => ({ ok: true, json: async () => url.endsWith('/api/v1/memories/import') ? { saved: [{ id: 'missing' }], saved_count: 1, duplicate_count: 0, skipped_count: 0 } : [] });
  window.eval(script);
  const doc = window.document;
  doc.getElementById('u-name').value = 'Ada';
  doc.querySelector('[data-step="2"]').click();
  doc.querySelector('[data-step="3"]').click();
  doc.querySelector('[data-action="finish-setup"]').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(doc.getElementById('step-done').classList.contains('active'), false);
  assert.match(doc.getElementById('setup-error').textContent, /not found in your vault/);
  dom.window.close();
});

test('dashboard renders the real vault and keeps sensitive recall behind a checkbox', async () => {
  const html = await readFile(path.join(dist, 'dashboard.html'), 'utf8');
  const script = await readFile(path.join(dist, 'dashboard.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'chrome-extension://pelican/dashboard.html', runScripts: 'outside-only' });
  const { window } = dom;
  const memory = { id: 'm-1', text: 'User prefers concise TypeScript examples.', classification: 'general', status: 'active', source: 'manual' };
  const privateMemory = { id: 'm-2', text: "User's name is Ada.", classification: 'sensitive', status: 'active', source: 'manual' };
  window.chrome = { storage: { local: {
    get: async () => ({ auth_token: 'signed-in-token', capture_chatgpt: false, capture_claude: false, capture_gemini: false }),
    set: async () => {},
  } } };
  window.fetch = async url => {
    if (url.endsWith('/ready')) return {ok:true,json:async()=>({status:'ready'})};
    if (url.endsWith('/api/v1/memories')) return {ok:true,json:async()=>[memory,privateMemory]};
    if (url.endsWith('/api/v1/preferences')) return {ok:true,json:async()=>[]};
    if (url.endsWith('/api/v1/memories/query')) return {ok:true,json:async()=>({general_memories:[memory],sensitive_memories:[privateMemory],preferences:[]})};
    throw new Error('unexpected endpoint ' + url);
  };
  window.eval(script);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise(resolve => setTimeout(resolve, 40));
  const doc = window.document;
  assert.equal(doc.getElementById('memoryCount').textContent, '02');
  assert.equal(doc.getElementById('authGate').hidden, true);
  assert.match(doc.getElementById('recentList').textContent, /concise TypeScript/);
  doc.querySelector('[data-view="use"]').click();
  doc.getElementById('recallQuery').value = 'How should you explain TypeScript?';
  doc.getElementById('recallForm').dispatchEvent(new window.Event('submit', {cancelable:true}));
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.match(doc.getElementById('generalResults').textContent, /concise TypeScript/);
  assert.equal(doc.getElementById('sensitiveResults').querySelector('input').checked, false);
  dom.window.close();
});
