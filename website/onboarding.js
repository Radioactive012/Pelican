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
      if (!ring || !node) continue;
      ring.classList.remove('active', 'done');
      node.classList.remove('active', 'done');
      if (i < current) { ring.classList.add('done'); node.classList.add('done'); }
      else if (i === current) { ring.classList.add('active'); node.classList.add('active'); }
    }
    for (let i = 1; i < steps; i++) {
      const seg = byId('ps' + i);
      if (seg) seg.classList.toggle('filled', i < current);
    }
  }

  function platform(name) {
    const row = byId('pr-' + name);
    const tog = byId('tog-' + name);
    if (row && tog) row.classList.toggle('on', tog.checked);
  }

  function goTo(step) {
    if (step < 1 || step > steps) return;
    const curElem = byId('step-' + current);
    if (curElem) curElem.classList.remove('active');
    current = step;
    const nextElem = byId('step-' + current);
    if (nextElem) nextElem.classList.add('active');
    progress();
    window.scrollTo({top: 0, behavior: 'smooth'});
    if (step === 3) {
      for (const tool of ['chatgpt', 'claude', 'gemini']) {
        const tc = byId('tc-' + tool);
        const tog = byId('tog-' + tool);
        if (tc && tog && tc.checked) tog.checked = true;
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
    if (!raw) return [];
    const fenced = raw.match(/```[^\n]*\n([\s\S]*?)```/);
    const source = fenced ? fenced[1] : raw;
    return [...new Set(source.split(/\r?\n/).map(line => line.trim()
      .replace(/^```.*$/, '').replace(/^\[[^\]]{1,40}\]\s*[-–:]\s*/, '')
      .replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim()).filter(Boolean))];
  }

  function setAccountMode(mode) {
    const modeInput = byId('accountMode');
    const tabSignUp = byId('tabSignUp');
    const tabSignIn = byId('tabSignIn');
    const finishBtn = byId('finishBtn');
    const pwInput = byId('accountPassword');
    const googleSignUpBtn = byId('step3GoogleSignUpBtn');
    const googleSignInBtn = byId('step3GoogleSignInBtn');

    if (modeInput) modeInput.value = mode;
    if (mode === 'signup') {
      if (tabSignUp) { tabSignUp.classList.add('active'); tabSignUp.setAttribute('aria-selected', 'true'); }
      if (tabSignIn) { tabSignIn.classList.remove('active'); tabSignIn.setAttribute('aria-selected', 'false'); }
      if (finishBtn) finishBtn.textContent = 'Create account & finish 🎉';
      if (pwInput) pwInput.placeholder = 'Choose a password (8+ characters)';
      if (googleSignUpBtn) { googleSignUpBtn.classList.remove('outline'); }
      if (googleSignInBtn) { googleSignInBtn.classList.add('outline'); }
    } else {
      if (tabSignUp) { tabSignUp.classList.remove('active'); tabSignUp.setAttribute('aria-selected', 'false'); }
      if (tabSignIn) { tabSignIn.classList.add('active'); tabSignIn.setAttribute('aria-selected', 'true'); }
      if (finishBtn) finishBtn.textContent = 'Sign in & save ↗';
      if (pwInput) pwInput.placeholder = 'Your account password';
      if (googleSignInBtn) { googleSignInBtn.classList.remove('outline'); }
      if (googleSignUpBtn) { googleSignUpBtn.classList.add('outline'); }
    }
  }

  function savePendingOnboarding() {
    const tools = [];
    for (const tool of ['chatgpt', 'claude', 'gemini']) {
      if (byId('tc-' + tool)?.checked) tools.push(tool);
    }
    const platforms = {};
    for (const p of ['chatgpt', 'claude', 'gemini']) {
      platforms[p] = !!byId('tog-' + p)?.checked;
    }
    const pending = {
      name: byId('u-name')?.value.trim() || '',
      age: byId('u-age')?.value ? Number(byId('u-age').value) : null,
      role: byId('u-role')?.value.trim() || '',
      tools,
      platforms,
      memories: parseMemories(byId('importData')?.value || '')
    };
    sessionStorage.setItem('pelican_pending_onboarding', JSON.stringify(pending));
  }

  async function startGoogleAuth(targetUrl) {
    const errEl = byId('setup-error') || byId('modalError');
    if (errEl) errEl.textContent = '';
    savePendingOnboarding();
    const returnUrl = targetUrl || (window.location.origin + '/onboarding.html');
    try {
      const res = await fetch(`${api}/api/v1/auth/google?redirect_to=${encodeURIComponent(returnUrl)}`);
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.url) {
        window.location.href = data.url;
      } else {
        throw new Error(data.detail || 'Could not start Google sign in.');
      }
    } catch (err) {
      if (errEl) errEl.textContent = err.message || 'Google sign-in is not available right now.';
    }
  }

  async function finish() {
    const error = byId('setup-error');
    const button = document.querySelector('[data-action="finish-setup"]') || byId('finishBtn');
    if (error) error.textContent = '';
    if (button) {
      button.disabled = true;
      button.textContent = 'Processing…';
    }

    try {
      if (extension) {
        const configured = await chrome.storage.local.get('backend_url');
        api = (configured.backend_url || chrome.runtime?.getManifest?.().homepage_url || api).replace(/\/+$/, '');
        if (api.startsWith('https://') && chrome.permissions?.request) {
          const allowed = await chrome.permissions.request({origins: [new URL(api).origin + '/*']});
          if (!allowed) throw new Error('Allow Pelican to connect to your backend to save memories.');
        }
      }

      const email = (byId('accountEmail')?.value || '').trim();
      const password = byId('accountPassword')?.value || '';
      const oldStorage = extension ? await chrome.storage.local.get(['auth_token', 'user_email']) : {};
      let token = extension
        ? oldStorage.auth_token
        : (sessionStorage.getItem('pelican_auth_token') || localStorage.getItem('pelican_auth_token'));

      // If user provided email or password, or isn't logged in, authenticate them
      if (email || password || !token) {
        if (!email || !password) {
          throw new Error('Please enter both your account email and password.');
        }
        const mode = byId('accountMode')?.value || 'signup';
        let response;
        let auth = {};
        if (mode === 'signup') {
          try {
            response = await fetch(`${api}/api/v1/auth/signup`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({email, password})
            });
            auth = await response.json().catch(() => ({}));
          } catch {
            response = await fetch(`${api}/api/v1/auth/token`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({email, password})
            });
            auth = await response.json().catch(() => ({}));
          }
        } else {
          response = await fetch(`${api}/api/v1/auth/token`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({email, password})
          });
          auth = await response.json().catch(() => ({}));
        }
        if (!response.ok) {
          if (mode === 'signin' && (response.status === 401 || auth.detail?.includes('incorrect'))) {
            throw new Error('Email or password is incorrect. Need to create an account? Select "Create account" above.');
          }
          throw new Error(auth.detail || 'Could not sign in. Please verify your credentials.');
        }
        if (auth.status === 'confirmation_required') {
          throw new Error('Confirm your account in your email, then return and sign in.');
        }
        token = auth.access_token;
        if (!token) throw new Error('Sign-in did not return a session.');
      }

      // Parse memory input and profile data
      const memories = parseMemories(byId('importData')?.value || '');
      if (memories.length > 30) throw new Error('Import at most 30 memories at a time.');
      if (memories.some(memory => memory.length > 500)) throw new Error('Each memory must be at most 500 characters.');

      const age = byId('u-age')?.value;
      const payload = {
        name: byId('u-name')?.value.trim() || null,
        age: age ? Number(age) : null,
        role: byId('u-role')?.value.trim() || null,
        tools: ['chatgpt', 'claude', 'gemini'].filter(tool => {
          const tc = byId('tc-' + tool);
          const tog = byId('tog-' + tool);
          return (tc && tc.checked) || (tog && tog.checked);
        }),
        memories
      };

      const hasDetails = Boolean(payload.name || payload.age !== null || payload.role || payload.tools.length || payload.memories.length);
      let saved = {saved_count: 0, duplicate_count: 0, skipped_count: 0, saved: []};

      if (hasDetails) {
        if (button) button.textContent = 'Saving memories…';
        const savedResponse = await fetch(`${api}/api/v1/memories/import`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token}`},
          body: JSON.stringify(payload)
        });
        saved = await savedResponse.json().catch(() => ({}));
        if (!savedResponse.ok) throw new Error(saved.detail || 'Memories could not be saved.');

        if (saved.saved && saved.saved.length) {
          const vaultRes = await fetch(`${api}/api/v1/memories`, {
            headers: {Authorization: `Bearer ${token}`}
          });
          const vault = await vaultRes.json().catch(() => []);
          const vaultIds = new Set((Array.isArray(vault) ? vault : []).map(m => m.id));
          for (const item of saved.saved) {
            if (item.id && !vaultIds.has(item.id)) {
              throw new Error(`Imported memory ID ${item.id} was not found in your vault.`);
            }
          }
        }
      }

      // Persist auth token in both localStorage and sessionStorage
      if (extension) {
        await chrome.storage.local.set({
          auth_token: token,
          user_email: email || oldStorage.user_email || '',
          backend_url: api
        });
      } else {
        sessionStorage.setItem('pelican_auth_token', token);
        localStorage.setItem('pelican_auth_token', token);
        if (email) localStorage.setItem('pelican_user_email', email);
      }

      if (byId('accountPassword')) byId('accountPassword').value = '';

      // Update success message
      const successMsg = byId('success-message');
      if (successMsg) {
        if (hasDetails && (saved.saved_count > 0 || saved.duplicate_count > 0)) {
          successMsg.textContent = `${saved.saved_count} new memories saved to your vault. ${saved.duplicate_count} already existed. ${saved.skipped_count} credential-like details skipped.`;
        } else {
          successMsg.textContent = 'Your Pelican account is ready and connected. Start chatting on ChatGPT, Claude, or Gemini!';
        }
      }

      const dlLink = byId('download-extension');
      if (dlLink) {
        if (extension) dlLink.hidden = true;
        else dlLink.href = `${api}/download/pelican-v3.zip`;
      }

      const curElem = byId('step-' + current);
      if (curElem) curElem.classList.remove('active');
      const doneElem = byId('step-done');
      if (doneElem) doneElem.classList.add('active');
      current = steps + 1;
      progress();
      window.scrollTo({top: 0, behavior: 'smooth'});
    } catch (reason) {
      if (error) error.textContent = reason.message || 'Setup failed. Please try again.';
      if (button) {
        button.disabled = false;
        button.textContent = byId('accountMode')?.value === 'signin' ? 'Sign in & save ↗' : 'Create account & finish 🎉';
      }
    }
  }

  // Quick sign-in modal logic
  function setupQuickSignIn() {
    const modal = byId('quickSignInModal');
    const openBtn = byId('quickSignInLink');
    const closeBtn = byId('closeModalBtn');
    const form = byId('quickSignInForm');
    const modalError = byId('modalError');
    const modalSubmit = byId('modalSignInSubmit');

    if (openBtn && modal) {
      openBtn.addEventListener('click', () => {
        if (modalError) modalError.textContent = '';
        modal.showModal();
        byId('modalEmail')?.focus();
      });
    }

    if (closeBtn && modal) {
      closeBtn.addEventListener('click', () => modal.close());
    }

    if (modal) {
      modal.addEventListener('click', event => {
        if (event.target === modal) modal.close();
      });
    }

    if (form) {
      form.addEventListener('submit', async event => {
        event.preventDefault();
        if (modalError) modalError.textContent = '';
        const email = byId('modalEmail')?.value.trim();
        const password = byId('modalPassword')?.value;
        if (!email || !password) {
          if (modalError) modalError.textContent = 'Please enter your email and password.';
          return;
        }

        if (modalSubmit) {
          modalSubmit.disabled = true;
          modalSubmit.textContent = 'Signing in…';
        }

        try {
          const response = await fetch(`${api}/api/v1/auth/token`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({email, password})
          });
          const auth = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(auth.detail || 'Sign-in failed. Check your email and password.');
          }
          const token = auth.access_token;
          if (!token) throw new Error('No session returned.');

          if (extension) {
            await chrome.storage.local.set({auth_token: token, user_email: email, backend_url: api});
          } else {
            sessionStorage.setItem('pelican_auth_token', token);
            localStorage.setItem('pelican_auth_token', token);
            localStorage.setItem('pelican_user_email', email);
          }

          modal.close();
          window.location.href = './dashboard.html';
        } catch (err) {
          if (modalError) modalError.textContent = err.message || 'Sign in failed.';
        } finally {
          if (modalSubmit) {
            modalSubmit.disabled = false;
            modalSubmit.textContent = 'Sign in ↗';
          }
        }
      });
    }
  }

  // Check if user is already authenticated
  async function checkExistingAuth() {
    const token = extension
      ? ((await chrome.storage.local.get('auth_token')).auth_token || '')
      : (sessionStorage.getItem('pelican_auth_token') || localStorage.getItem('pelican_auth_token') || '');

    const email = extension
      ? ((await chrome.storage.local.get('user_email')).user_email || '')
      : (localStorage.getItem('pelican_user_email') || '');

    const notice = byId('loggedInNotice');
    const noticeEmail = byId('loggedInEmail');
    const switchBtn = byId('switchAccountBtn');
    const finishBtn = byId('finishBtn');
    const googleBox = byId('step3GoogleBox');
    const accountFields = byId('accountFields');
    const accountSelector = document.querySelector('.account-selector');

    if (token && notice && noticeEmail) {
      notice.hidden = false;
      noticeEmail.textContent = email || 'active account';
      if (finishBtn) finishBtn.textContent = 'Save to my vault ↗';
      if (googleBox) googleBox.style.display = 'none';
      if (accountFields) accountFields.style.display = 'none';
      if (accountSelector) accountSelector.style.display = 'none';
    }

    if (switchBtn) {
      switchBtn.addEventListener('click', () => {
        if (!extension) {
          sessionStorage.removeItem('pelican_auth_token');
          localStorage.removeItem('pelican_auth_token');
          localStorage.removeItem('pelican_user_email');
        }
        if (notice) notice.hidden = true;
        if (googleBox) googleBox.style.display = '';
        if (accountFields) accountFields.style.display = '';
        if (accountSelector) accountSelector.style.display = '';
        setAccountMode('signin');
      });
    }
  }

  async function handleOAuthCallback() {
    const hash = window.location.hash.substring(1);
    const hashParams = new URLSearchParams(hash);
    const queryParams = new URLSearchParams(window.location.search);

    const errorDesc = hashParams.get('error_description') || queryParams.get('error_description') || hashParams.get('error') || queryParams.get('error');
    if (errorDesc) {
      history.replaceState(null, '', window.location.pathname);
      const errEl = byId('setup-error') || byId('modalError');
      let msg = decodeURIComponent(errorDesc.replace(/\+/g, ' '));
      if (msg.toLowerCase().includes('bad_oauth_state') || msg.toLowerCase().includes('state not found')) {
        msg = 'Google OAuth state verification failed. In Brave or browsers with ad blockers, disable Shields/blockers for localhost & supabase and try again.';
      }
      if (errEl) errEl.textContent = msg;
      goTo(3);
      return;
    }

    let token = hashParams.get('access_token');
    let email = '';

    if (token) {
      try {
        const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
        email = payload.email || '';
      } catch {}
    } else {
      const code = queryParams.get('code');
      if (code) {
        try {
          const resp = await fetch(`${api}/api/v1/auth/oauth-callback`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ code })
          });
          const data = await resp.json().catch(() => ({}));
          if (resp.ok && data.access_token) {
            token = data.access_token;
            email = data.user?.email || '';
          }
        } catch {}
      }
    }

    if (token) {
      if (extension) {
        await chrome.storage.local.set({auth_token: token, user_email: email, backend_url: api});
      } else {
        sessionStorage.setItem('pelican_auth_token', token);
        localStorage.setItem('pelican_auth_token', token);
        if (email) localStorage.setItem('pelican_user_email', email);
      }
      history.replaceState(null, '', window.location.pathname);

      // Check if there was pending onboarding data to import
      const pendingRaw = sessionStorage.getItem('pelican_pending_onboarding');
      if (pendingRaw) {
        try {
          const pending = JSON.parse(pendingRaw);
          sessionStorage.removeItem('pelican_pending_onboarding');
          if (pending.platforms) {
            if (extension) {
              const cap = {};
              for (const [k, v] of Object.entries(pending.platforms)) {
                cap['capture_' + k] = v;
              }
              await chrome.storage.local.set(cap);
            } else {
              localStorage.setItem('pelican_capture_sites', JSON.stringify(pending.platforms));
            }
          }
          const importPayload = {
            name: pending.name || null,
            age: pending.age || null,
            role: pending.role || null,
            tools: pending.tools || [],
            memories: pending.memories || []
          };
          const importResp = await fetch(`${api}/api/v1/memories/import`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${token}`},
            body: JSON.stringify(importPayload)
          });
          const result = await importResp.json().catch(() => ({}));
          const count = result.saved_count ?? (result.saved?.length ?? 0);
          const msg = byId('success-message');
          if (msg) {
            msg.textContent = count > 0
              ? `${count} memory items saved to your account.`
              : 'Your preferences were saved. Pelican is ready to use.';
          }
          const curElem = byId('step-' + current);
          if (curElem) curElem.classList.remove('active');
          const stepDone = byId('step-done');
          if (stepDone) stepDone.classList.add('active');
          return;
        } catch (e) {
          console.warn('Pending import error:', e);
        }
      }

      goTo(3);
    }
  }

  document.querySelectorAll('[data-step]').forEach(button => {
    button.addEventListener('click', () => goTo(Number(button.dataset.step)));
  });

  document.querySelector('[data-action="copy-prompt"]')?.addEventListener('click', copyPrompt);
  document.querySelectorAll('[data-action="finish-setup"]').forEach(btn => btn.addEventListener('click', finish));
  document.querySelectorAll('[data-platform]').forEach(toggle => {
    toggle.addEventListener('change', () => platform(toggle.dataset.platform));
  });

  byId('tabSignUp')?.addEventListener('click', () => setAccountMode('signup'));
  byId('tabSignIn')?.addEventListener('click', () => setAccountMode('signin'));

  byId('step3GoogleSignUpBtn')?.addEventListener('click', () => startGoogleAuth());
  byId('step3GoogleSignInBtn')?.addEventListener('click', () => startGoogleAuth());
  byId('modalGoogleSignInBtn')?.addEventListener('click', () => startGoogleAuth(window.location.origin + '/dashboard.html'));
  byId('modalGoogleSignUpBtn')?.addEventListener('click', () => startGoogleAuth(window.location.origin + '/dashboard.html'));

  setupQuickSignIn();
  handleOAuthCallback().then(() => checkExistingAuth());
})();
