(() => {
  'use strict';
  let current = 1;
  const steps = 3;
  const byId = id => document.getElementById(id);
  const extension = typeof chrome !== 'undefined' && chrome.storage && chrome.storage.local;
  let api = extension ? (window.PELICAN_BACKEND_URL || 'http://127.0.0.1:8000') : location.origin;

  function progress() {
    for (let i = 1; i <= steps; i++) {
      const ring = byId('pr' + i);
      const node = byId('pn' + i);
      ring.classList.remove('active', 'done');
      node.classList.remove('active', 'done');
      if (i < current) { ring.classList.add('done'); node.classList.add('done'); }
      else if (i === current) { ring.classList.add('active'); node.classList.add('active'); }
    }
    for (let i = 1; i < steps; i++) byId('ps' + i).classList.toggle('filled', i < current);
  }
  function platform(name) {
    byId('pr-' + name).classList.toggle('on', byId('tog-' + name).checked);
  }
  function goTo(step) {
    if (step < 1 || step > steps) return;
    byId('step-' + current).classList.remove('active');
    current = step;
    byId('step-' + current).classList.add('active');
    progress();
    window.scrollTo({top: 0, behavior: 'smooth'});
    if (step === 3) {
      for (const tool of ['chatgpt', 'claude', 'gemini']) {
        if (byId('tc-' + tool).checked) byId('tog-' + tool).checked = true;
        platform(tool);
      }
    }
  }
  async function copyPrompt() {
    const button = byId('copyBtn');
    try {
      await navigator.clipboard.writeText(byId('promptBody').textContent);
      button.textContent = 'Copied!';
      setTimeout(() => { button.textContent = 'Copy prompt'; }, 2600);
    } catch { button.textContent = 'Could not copy'; }
  }
  function parseMemories(raw) {
    const fenced = raw.match(/```[^\n]*\n([\s\S]*?)```/);
    const source = fenced ? fenced[1] : raw;
    return [...new Set(source.split(/\r?\n/).map(line => line.trim()
      .replace(/^```.*$/, '').replace(/^\[[^\]]{1,40}\]\s*[-–:]\s*/, '')
      .replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim()).filter(Boolean))];
  }
  async function finish() {
    const error = byId('setup-error');
    const button = document.querySelector('[data-action="finish-setup"]');
    error.textContent = '';
    button.disabled = true;
    button.textContent = 'Saving…';
    try {
      if (extension) {
        const configured = await chrome.storage.local.get('backend_url');
        api = (configured.backend_url || chrome.runtime?.getManifest?.().homepage_url || api).replace(/\/+$/, '');
        if (api.startsWith('https://') && chrome.permissions?.request) {
          const allowed = await chrome.permissions.request({origins: [new URL(api).origin + '/*']});
          if (!allowed) throw new Error('Allow Pelican to connect to your backend to save memories.');
        }
      }
      const email = byId('accountEmail').value.trim();
      const password = byId('accountPassword').value;
      const oldStorage = extension ? await chrome.storage.local.get(['auth_token', 'user_email']) : {};
      let token = extension ? oldStorage.auth_token : sessionStorage.getItem('pelican_auth_token');
      if (email || password || !token) {
        if (!email || !password) throw new Error('Enter your account email and password to save memories.');
        const mode = byId('accountMode').value;
        const response = await fetch(`${api}/api/v1/auth/${mode === 'signup' ? 'signup' : 'token'}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email, password})
        });
        const auth = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(auth.detail || 'Could not sign in. Check your account details.');
        if (auth.status === 'confirmation_required') throw new Error('Confirm your account in your email, then return and sign in.');
        token = auth.access_token;
        if (!token) throw new Error('Sign-in did not return a session.');
      }
      const memories = parseMemories(byId('importData').value);
      if (memories.length > 30) throw new Error('Import at most 30 memories at a time.');
      if (memories.some(memory => memory.length > 500)) throw new Error('Each memory must be at most 500 characters.');
      const age = byId('u-age').value;
      const payload = {
        name: byId('u-name').value.trim() || null,
        age: age ? Number(age) : null,
        role: byId('u-role').value.trim() || null,
        tools: ['chatgpt', 'claude', 'gemini'].filter(tool => byId('tc-' + tool).checked || byId('tog-' + tool).checked),
        memories
      };
      if (!payload.name && !payload.age && !payload.role && !payload.tools.length && !payload.memories.length) {
        throw new Error('Add at least one detail before saving.');
      }
      const savedResponse = await fetch(`${api}/api/v1/memories/import`, {
        method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`}, body: JSON.stringify(payload)
      });
      const saved = await savedResponse.json().catch(() => ({}));
      if (!savedResponse.ok) throw new Error(saved.detail || 'Memories could not be saved.');
      const vaultResponse = await fetch(`${api}/api/v1/memories`, {headers: {Authorization: `Bearer ${token}`}});
      if (!vaultResponse.ok) throw new Error('Saved, but could not verify your vault. Please retry.');
      const vault = await vaultResponse.json();
      if (!Array.isArray(vault) || !saved.saved.every(item => vault.some(memory => memory.id === item.id))) {
        throw new Error('A saved memory was not found in your vault. Please retry.');
      }
      if (extension) await chrome.storage.local.set({auth_token: token, user_email: email || oldStorage.user_email || '', backend_url: api});
      else sessionStorage.setItem('pelican_auth_token', token);
      byId('accountPassword').value = '';
      byId('success-message').textContent = `${saved.saved_count} new memories saved. ${saved.duplicate_count} already in your vault. ${saved.skipped_count} credential-like details skipped.`;
      if (extension) byId('download-extension').hidden = true;
      else byId('download-extension').href = `${api}/download/pelican-v3.zip`;
      byId('step-' + current).classList.remove('active');
      byId('step-done').classList.add('active');
      current = steps + 1;
      progress();
      window.scrollTo({top: 0, behavior: 'smooth'});
    } catch (reason) {
      error.textContent = reason.message || 'Setup failed. Please try again.';
      button.disabled = false;
      button.textContent = 'Save my memories ↗';
    }
  }
  document.querySelectorAll('[data-step]').forEach(button => button.addEventListener('click', () => goTo(Number(button.dataset.step))));
  document.querySelector('[data-action="copy-prompt"]').addEventListener('click', copyPrompt);
  document.querySelector('[data-action="finish-setup"]').addEventListener('click', finish);
  document.querySelectorAll('[data-platform]').forEach(toggle => toggle.addEventListener('change', () => platform(toggle.dataset.platform)));
})();
