(() => {
  'use strict';
  let API = window.PELICAN_BACKEND_URL || 'http://127.0.0.1:8000';
  const extension = typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local;
  if (!extension) API = location.origin;

  let token = '';
  let memories = [];
  let preferences = [];
  let recallResult = {general_memories: [], sensitive_memories: []};
  const byId = id => document.getElementById(id);
  const text = (id, value) => { byId(id).textContent = value; };

  async function storedToken() {
    return extension
      ? ((await chrome.storage.local.get('auth_token')).auth_token || '')
      : (sessionStorage.getItem('pelican_auth_token') || localStorage.getItem('pelican_auth_token') || '');
  }
  async function saveToken(value) {
    token = value || '';
    if (extension) {
      await chrome.storage.local.set({auth_token: token, backend_url: API});
    } else if (token) {
      sessionStorage.setItem('pelican_auth_token', token);
      localStorage.setItem('pelican_auth_token', token);
    } else {
      sessionStorage.removeItem('pelican_auth_token');
      localStorage.removeItem('pelican_auth_token');
      localStorage.removeItem('pelican_user_email');
    }
  }
  async function request(path, options = {}) {
    const headers = Object.assign({'Content-Type': 'application/json'}, options.headers || {});
    if (token) headers.Authorization = 'Bearer ' + token;
    const response = await fetch(API + path, Object.assign({}, options, {headers}));
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof body.detail === 'string' ? body.detail : 'Request failed (HTTP ' + response.status + ')';
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function showAuth(message = '') {
    byId('dashboardViews').hidden = true;
    byId('authGate').hidden = false;
    text('authError', message);
  }
  function showDashboard() {
    byId('authGate').hidden = true;
    byId('dashboardViews').hidden = false;
  }
  function setView(name) {
    byId('dashboardNav').querySelectorAll('[data-view]').forEach(button => {
      const active = button.dataset.view === name;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    document.querySelectorAll('[data-view-panel]').forEach(panel => panel.classList.toggle('active', panel.dataset.viewPanel === name));
    text('viewBreadcrumb', 'MY MEMORIES / ' + name.toUpperCase());
    location.hash = name === 'overview' ? '' : name;
  }

  function empty(message, action) {
    const box = document.createElement('div');
    box.className = 'empty';
    box.textContent = message;
    if (action) {
      const link = document.createElement('a');
      link.href = './onboarding.html';
      link.textContent = ' Add memories ↗';
      box.appendChild(link);
    }
    return box;
  }
  function memoryCard(memory, actions) {
    const card = document.createElement('article');
    card.className = 'memory-card';
    const icon = document.createElement('span');
    icon.className = 'memory-icon' + (memory.classification === 'sensitive' ? ' sensitive' : '');
    icon.textContent = memory.classification === 'sensitive' ? '◈' : '✳';
    icon.setAttribute('aria-hidden', 'true');
    const body = document.createElement('div');
    body.className = 'memory-body';
    const title = document.createElement('strong');
    title.textContent = memory.text || memory.memory || '';
    const meta = document.createElement('small');
    meta.textContent = (memory.source || 'Saved memory') + ' · ' + (memory.classification || 'sensitive') + (memory.status === 'blocked' ? ' · blocked' : '');
    body.append(title, meta);
    card.append(icon, body);
    if (actions) {
      const controls = document.createElement('div');
      controls.className = 'memory-actions';
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.textContent = 'Edit';
      edit.addEventListener('click', async () => {
        const wording = prompt('Correct this memory', memory.text || '');
        if (wording === null || !wording.trim()) return;
        try { await request('/api/v1/memories/' + encodeURIComponent(memory.id), {method:'PUT', body:JSON.stringify({text:wording.trim()})}); await loadData(); }
        catch (error) { alert(error.message); }
      });
      const block = document.createElement('button');
      block.type = 'button';
      block.textContent = 'Block';
      block.disabled = memory.status === 'blocked';
      block.addEventListener('click', async () => {
        try { await request('/api/v1/memories/' + encodeURIComponent(memory.id) + '/block', {method:'POST'}); await loadData(); }
        catch (error) { alert(error.message); }
      });
      const forget = document.createElement('button');
      forget.type = 'button';
      forget.className = 'danger';
      forget.textContent = 'Forget';
      forget.addEventListener('click', async () => {
        if (!confirm('Permanently forget this memory?')) return;
        try { await request('/api/v1/memories/' + encodeURIComponent(memory.id), {method:'DELETE'}); await loadData(); }
        catch (error) { alert(error.message); }
      });
      controls.append(edit, block, forget);
      card.appendChild(controls);
    }
    return card;
  }
  function renderMemories() {
    const active = memories.filter(item => item.status === 'active');
    text('memoryCount', String(active.length).padStart(2, '0'));
    text('navCount', String(active.length).padStart(2, '0'));
    const recent = byId('recentList');
    recent.replaceChildren(...(active.slice(0, 3).map(item => memoryCard(item, false))));
    if (!recent.children.length) recent.appendChild(empty('No memories yet. Import a few details to get started.', true));
    const filter = byId('memoryFilter').value.trim().toLowerCase();
    const shown = memories.filter(item => !filter || (item.text || '').toLowerCase().includes(filter));
    const list = byId('allMemoryList');
    list.replaceChildren(...shown.map(item => memoryCard(item, true)));
    if (!list.children.length) list.appendChild(empty(filter ? 'No memories match that search.' : 'Nothing saved yet.', !filter));
  }
  function renderPreferences() {
    const list = byId('preferenceList');
    list.replaceChildren();
    if (!preferences.length) { list.appendChild(empty('Pelican will show learned explanation preferences here after enough conversations.', false)); return; }
    preferences.forEach(item => {
      const card = memoryCard({text:item.preference_text || item.text || item.preference_key, classification:item.classification || 'general', source:'Learned preference', id:item.id}, false);
      list.appendChild(card);
    });
  }
  async function renderCapture() {
    const wrap = byId('siteToggles');
    wrap.replaceChildren();
    if (!extension) {
      text('captureStatus', 'EXT');
      text('captureHelp', 'Open the Pelican extension to choose where capture is enabled.');
      return;
    }
    const settings = await chrome.storage.local.get(['capture_chatgpt','capture_claude','capture_gemini']);
    const sites = [['chatgpt','ChatGPT'],['claude','Claude'],['gemini','Gemini']];
    text('captureStatus', sites.some(([key]) => settings['capture_' + key]) ? 'ON' : 'OFF');
    text('captureHelp', 'Capture is off by default and only runs on the sites you enable.');
    sites.forEach(([key,label]) => {
      const row = document.createElement('label');
      row.className = 'site-row';
      const name = document.createElement('span');
      name.textContent = label;
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = Boolean(settings['capture_' + key]);
      input.setAttribute('aria-label', 'Capture my messages on ' + label);
      input.addEventListener('change', async () => {
        await chrome.storage.local.set({['capture_' + key]: input.checked});
        await renderCapture();
      });
      row.append(name,input);
      wrap.appendChild(row);
    });
  }
  async function loadData() {
    try {
      [memories, preferences] = await Promise.all([request('/api/v1/memories'), request('/api/v1/preferences')]);
      renderMemories();
      renderPreferences();
      await renderCapture();
      showDashboard();
      const initial = location.hash.slice(1);
      setView(['overview','memories','use','preferences','settings'].includes(initial) ? initial : 'overview');
    } catch (error) {
      if (error.status === 401) { await saveToken(''); showAuth('Your session expired. Please sign in again.'); }
      else showAuth('Could not load your memories: ' + error.message);
    }
  }
  async function checkBackend() {
    try {
      const ready = await request('/ready');
      const ok = ready.status === 'ready';
      text('backendStatus', ok ? 'Connected' : 'Setup required');
      text('sidebarStatus', ok ? 'All systems ready' : 'Backend needs setup');
      document.querySelector('.status-dot').classList.toggle('offline', !ok);
    } catch {
      text('backendStatus', 'Offline');
      text('sidebarStatus', 'Backend offline');
      document.querySelector('.status-dot').classList.add('offline');
    }
  }
  function renderRecall() {
    const general = byId('generalResults');
    const sensitive = byId('sensitiveResults');
    general.replaceChildren(...recallResult.general_memories.map(item => memoryCard(item, false)));
    if (!general.children.length) general.appendChild(empty('No general memories match this question.', false));
    sensitive.replaceChildren();
    recallResult.sensitive_memories.forEach(item => {
      const row = document.createElement('label');
      row.className = 'memory-card sensitive-choice';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = item.id;
      const body = document.createElement('span');
      body.textContent = item.text || item.memory || '';
      row.append(input, body);
      sensitive.appendChild(row);
    });
    byId('sensitiveWrap').hidden = !recallResult.sensitive_memories.length;
    byId('recallResults').hidden = false;
  }

  document.addEventListener('DOMContentLoaded', async () => {
    if (extension) {
      const configured = await chrome.storage.local.get('backend_url');
      API = (configured.backend_url || chrome.runtime?.getManifest?.().homepage_url || API).replace(/\/+$/, '');
      document.querySelectorAll('.brand, .sidebar-brand').forEach(link => { link.href = API + '/index.html'; });
    }
    text('backendAddress', API);
    byId('dashboardNav').addEventListener('click', event => {
      const button = event.target.closest('[data-view]');
      if (button) setView(button.dataset.view);
    });
    document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => setView(button.dataset.go)));
    byId('memoryFilter').addEventListener('input', renderMemories);
    async function startGoogleAuth() {
      text('authError', '');
      try {
        const returnUrl = window.location.origin + '/dashboard.html';
        const res = await fetch(API + '/api/v1/auth/google?redirect_to=' + encodeURIComponent(returnUrl));
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.url) {
          window.location.href = data.url;
        } else {
          throw new Error(data.detail || 'Could not start Google sign in.');
        }
      } catch (err) {
        text('authError', err.message || 'Google sign-in is not available right now.');
      }
    }

    async function handleOAuthCallback() {
      const hash = window.location.hash.substring(1);
      const hashParams = new URLSearchParams(hash);
      const queryParams = new URLSearchParams(window.location.search);

      const errorDesc = hashParams.get('error_description') || queryParams.get('error_description') || hashParams.get('error') || queryParams.get('error');
      if (errorDesc) {
        history.replaceState(null, '', window.location.pathname);
        let msg = decodeURIComponent(errorDesc.replace(/\+/g, ' '));
        if (msg.toLowerCase().includes('bad_oauth_state') || msg.toLowerCase().includes('state not found')) {
          msg = 'Google OAuth state verification failed. In Brave or browsers with ad blockers, disable Shields/blockers for localhost & supabase and try again.';
        }
        showAuth(msg);
        return;
      }

      const accessToken = hashParams.get('access_token');
      if (accessToken) {
        let email = '';
        try {
          const payload = JSON.parse(atob(accessToken.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
          email = payload.email || '';
        } catch {}
        await saveToken(accessToken);
        if (email) localStorage.setItem('pelican_user_email', email);
        history.replaceState(null, '', window.location.pathname);
        return;
      }

      const code = queryParams.get('code');
      if (code) {
        try {
          const data = await request('/api/v1/auth/oauth-callback', {
            method: 'POST',
            body: JSON.stringify({ code })
          });
          if (data.access_token) {
            await saveToken(data.access_token);
            if (data.user?.email) localStorage.setItem('pelican_user_email', data.user.email);
          }
        } catch (err) {
          showAuth(err.message || 'OAuth exchange failed.');
        } finally {
          history.replaceState(null, '', window.location.pathname);
        }
      }
    }

    byId('dashGoogleSignInBtn')?.addEventListener('click', startGoogleAuth);
    byId('dashGoogleSignUpBtn')?.addEventListener('click', startGoogleAuth);

    byId('authForm').addEventListener('submit', async event => {
      event.preventDefault();
      text('authError', '');
      const button = byId('signInBtn');
      button.disabled = true;
      try {
        if (extension && API.startsWith('https://') && chrome.permissions?.request) {
          const allowed = await chrome.permissions.request({origins: [new URL(API).origin + '/*']});
          if (!allowed) throw new Error('Allow Pelican to connect to your backend to sign in.');
        }
        const emailVal = byId('email').value.trim();
        const data = await request('/api/v1/auth/token', {method:'POST', body:JSON.stringify({email:emailVal, password:byId('password').value})});
        await saveToken(data.access_token);
        localStorage.setItem('pelican_user_email', emailVal);
        byId('password').value = '';
        await loadData();
      } catch (error) { text('authError', error.message); }
      finally { button.disabled = false; }
    });
    byId('signUpBtn').addEventListener('click', async () => {
      text('authError', '');
      try {
        if (extension && API.startsWith('https://') && chrome.permissions?.request) {
          const allowed = await chrome.permissions.request({origins: [new URL(API).origin + '/*']});
          if (!allowed) throw new Error('Allow Pelican to connect to your backend to create an account.');
        }
        const emailVal = byId('email').value.trim();
        const data = await request('/api/v1/auth/signup', {method:'POST', body:JSON.stringify({email:emailVal, password:byId('password').value})});
        if (!data.access_token) { text('authError', 'Check your email to confirm your account, then sign in.'); return; }
        await saveToken(data.access_token);
        localStorage.setItem('pelican_user_email', emailVal);
        byId('password').value = '';
        await loadData();
      } catch (error) { text('authError', error.message); }
    });
    byId('signOutBtn').addEventListener('click', async () => { await saveToken(''); memories = []; preferences = []; showAuth('Signed out.'); });
    byId('recallForm').addEventListener('submit', async event => {
      event.preventDefault();
      text('recallStatus', 'Finding relevant memories…');
      try {
        recallResult = await request('/api/v1/memories/query', {method:'POST', body:JSON.stringify({query:byId('recallQuery').value.trim(), max_general:3})});
        renderRecall();
        text('recallStatus', 'Choose any private details you want to allow once.');
      } catch (error) { text('recallStatus', error.message); }
    });
    byId('copyContextBtn').addEventListener('click', async () => {
      const general = recallResult.general_memories.map(item => item.text || item.memory);
      const selected = new Set([...byId('sensitiveResults').querySelectorAll('input:checked')].map(input => input.value));
      const allowed = recallResult.sensitive_memories.filter(item => selected.has(item.id)).map(item => item.text || item.memory);
      const lines = [...general, ...allowed];
      if (!lines.length) { text('recallStatus', 'Select a memory first.'); return; }
      try {
        await navigator.clipboard.writeText('Relevant context I chose to share for this prompt:\n' + lines.map(item => '- ' + item).join('\n'));
        text('recallStatus', 'Approved context copied. Paste it into your AI chat.');
      } catch { text('recallStatus', 'Clipboard unavailable. Try Use Memory in the extension side panel.'); }
    });
    await handleOAuthCallback();
    token = await storedToken();
    await checkBackend();
    if (token) await loadData(); else showAuth();
  });
})();
