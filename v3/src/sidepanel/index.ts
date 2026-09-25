/**
 * Context Passport Side Panel Logic
 */

import { ContextPassportApiClient, BackendMemoryDoc, BackendPreferenceDoc } from '../core/api';
import { applyMemoryBlockToDraft, formatMemoryBlock, MemoryItem, PreferenceItem } from '../core/injection';
import { containsSecret } from '../core/screener';
import { screenRecall } from '../core/recall';
import { getAllSettings, getSetting, isSiteCaptureEnabled, setSetting, setSiteCaptureEnabled } from '../core/storage';

let apiClient: ContextPassportApiClient;

async function ensureBackendPermission(rawUrl: string): Promise<void> {
  let url: URL;
  try { url = new URL(rawUrl); } catch { throw new Error('Enter a valid backend URL.'); }
  if (url.pathname !== '/' || url.search || url.hash) throw new Error('Enter the backend origin without a path or query.');
  const local = url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname) && url.port === '8000';
  if (!local && url.protocol !== 'https:') throw new Error('Use HTTPS for a hosted backend.');
  if (local) return;
  const origins = [`${url.origin}/*`];
  if (!(await chrome.permissions.request({ origins }))) {
    throw new Error('Allow access to this backend to continue.');
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const settings = await getAllSettings();
  apiClient = new ContextPassportApiClient(settings.backend_url, settings.auth_token);

  initTabs();
  initSettings(settings);
  initConnectionStatus();
  initVault();
  initPreferences();
  initFallback();
});

// Tab Navigation
function initTabs() {
  const tabBtns = document.querySelectorAll<HTMLButtonElement>('.cp-nav-btn');
  const tabPanes = document.querySelectorAll<HTMLElement>('.cp-tab-pane');

  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-tab');
      tabBtns.forEach((b) => b.classList.remove('active'));
      tabPanes.forEach((p) => p.classList.remove('active'));

      btn.classList.add('active');
      const pane = document.getElementById(`tab-${target}`);
      if (pane) pane.classList.add('active');

      if (target === 'vault') loadVaultMemories();
      if (target === 'preferences') loadPreferences();
    });
  });
}

// Connection Status
async function initConnectionStatus() {
  const badge = document.getElementById('connection-badge');
  if (!badge) return;

  const health = await apiClient.checkHealth();
  if (!health.ok) {
    badge.textContent = 'Offline';
    badge.className = 'cp-badge cp-badge-error';
    return;
  }
  const ready = await apiClient.checkReady();
  if (!ready.ready) {
    badge.textContent = 'Backend not ready';
    badge.className = 'cp-badge cp-badge-error';
    return;
  }
  if (!(await getSetting('auth_token'))) {
    badge.textContent = 'Sign in';
    badge.className = 'cp-badge cp-badge-neutral';
    return;
  }
  try {
    await apiClient.getPreferences();
    badge.textContent = 'Connected';
    badge.className = 'cp-badge cp-badge-success';
  } catch (err: any) {
    badge.textContent = /HTTP 401|HTTP 403|token_expired/i.test(err?.message || '') ? 'Sign in again' : 'Backend error';
    badge.className = 'cp-badge cp-badge-error';
  }
}

// Settings & Per-Site Toggles
async function initSettings(settings: any) {
  const toggleChatGPT = document.getElementById('toggle-chatgpt') as HTMLInputElement | null;
  const toggleClaude = document.getElementById('toggle-claude') as HTMLInputElement | null;
  const toggleGemini = document.getElementById('toggle-gemini') as HTMLInputElement | null;

  const urlInput = document.getElementById('setting-backend-url') as HTMLInputElement | null;
  const tokenInput = document.getElementById('setting-auth-token') as HTMLInputElement | null;
  const emailInput = document.getElementById('setting-email') as HTMLInputElement | null;
  const passwordInput = document.getElementById('setting-password') as HTMLInputElement | null;
  const signInBtn = document.getElementById('sign-in-btn') as HTMLButtonElement | null;
  const signOutBtn = document.getElementById('sign-out-btn') as HTMLButtonElement | null;
  const authMessage = document.getElementById('auth-message');
  const saveBtn = document.getElementById('save-settings-btn') as HTMLButtonElement | null;

  if (toggleChatGPT) {
    toggleChatGPT.checked = await isSiteCaptureEnabled('chatgpt');
    toggleChatGPT.addEventListener('change', async () => {
      await setSiteCaptureEnabled('chatgpt', toggleChatGPT.checked);
    });
  }

  if (toggleClaude) {
    toggleClaude.checked = await isSiteCaptureEnabled('claude');
    toggleClaude.addEventListener('change', async () => {
      await setSiteCaptureEnabled('claude', toggleClaude.checked);
    });
  }

  if (toggleGemini) {
    toggleGemini.checked = await isSiteCaptureEnabled('gemini');
    toggleGemini.addEventListener('change', async () => {
      await setSiteCaptureEnabled('gemini', toggleGemini.checked);
    });
  }

  if (urlInput) urlInput.value = settings.backend_url || 'http://127.0.0.1:8000';
  if (tokenInput) tokenInput.value = settings.auth_token || '';
  if (emailInput) emailInput.value = settings.user_email || '';

  signInBtn?.addEventListener('click', async () => {
    const email = emailInput?.value.trim() || '';
    const password = passwordInput?.value || '';
    if (!email || !password) {
      if (authMessage) authMessage.textContent = 'Enter your email and password.';
      return;
    }
    signInBtn.disabled = true;
    try {
      const backendUrl = urlInput?.value.trim() || 'http://localhost:8000';
      await ensureBackendPermission(backendUrl);
      apiClient.setBackendUrl(backendUrl);
      const token = await apiClient.login(email, password);
      await setSetting('backend_url', backendUrl);
      await setSetting('auth_token', token);
      await setSetting('user_email', email);
      if (tokenInput) tokenInput.value = token;
      if (authMessage) authMessage.textContent = 'Signed in. Your password was not saved.';
      await initConnectionStatus();
      await loadVaultMemories();
      await loadPreferences();
    } catch (err: any) {
      if (authMessage) authMessage.textContent = `Sign-in failed: ${err.message || 'Try again.'}`;
    } finally {
      if (passwordInput) passwordInput.value = '';
      signInBtn.disabled = false;
    }
  });

  signOutBtn?.addEventListener('click', async () => {
    await setSetting('auth_token', '');
    apiClient.setToken('');
    if (tokenInput) tokenInput.value = '';
    if (passwordInput) passwordInput.value = '';
    if (authMessage) authMessage.textContent = 'Signed out.';
    await initConnectionStatus();
    await loadVaultMemories();
    await loadPreferences();
  });

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const newUrl = urlInput?.value.trim() || 'http://localhost:8000';
      const newToken = tokenInput?.value.trim() || '';

      try { await ensureBackendPermission(newUrl); }
      catch (error: any) {
        if (authMessage) authMessage.textContent = error?.message || 'Backend permission was not granted.';
        return;
      }

      await setSetting('backend_url', newUrl);
      await setSetting('auth_token', newToken);

      apiClient.setBackendUrl(newUrl);
      apiClient.setToken(newToken);

      saveBtn.textContent = 'Saved!';
      setTimeout(() => (saveBtn.textContent = 'Save Settings'), 1500);

      await initConnectionStatus();
      await loadVaultMemories();
    });
  }
}

// Memory Vault
function initVault() {
  const refreshBtn = document.getElementById('vault-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadVaultMemories());
  }
  loadVaultMemories();
}

async function loadVaultMemories() {
  const listEl = document.getElementById('vault-list');
  if (!listEl) return;

  listEl.innerHTML = '<div class="cp-empty-state">Loading memories...</div>';

  try {
    const memories = await apiClient.getAllMemories();
    if (!memories || memories.length === 0) {
      listEl.innerHTML = '<div class="cp-empty-state">No memories stored yet.</div>';
      return;
    }

    listEl.innerHTML = '';
    memories.forEach((mem) => listEl.appendChild(renderMemoryCard(mem)));
  } catch (err: any) {
    listEl.innerHTML = `<div class="cp-empty-state">Failed to load memories: ${escapeHtml(err.message)}</div>`;
  }
}

function actionButton(label: string, action: () => Promise<void>, danger = false): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `cp-card-btn${danger ? ' cp-card-btn-danger' : ''}`;
  button.textContent = label;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await action(); } catch (error: any) {
      const status = button.closest('.cp-card')?.querySelector<HTMLElement>('.cp-card-status');
      if (status) status.textContent = error?.message || 'Action failed. Try again.';
    } finally { button.disabled = false; }
  });
  return button;
}

function editCard(card: HTMLElement, initialText: string, save: (value: string) => Promise<void>) {
  const editor = document.createElement('div');
  editor.className = 'cp-card-editor';
  const input = document.createElement('textarea');
  input.className = 'cp-textarea';
  input.value = initialText;
  input.rows = 3;
  input.setAttribute('aria-label', 'Correct wording');
  const actions = document.createElement('div');
  actions.className = 'cp-card-actions';
  actions.append(actionButton('Save correction', async () => {
    const value = input.value.trim();
    if (!value) throw new Error('Wording cannot be empty.');
    if (containsSecret(value)) throw new Error('Correction cannot contain credentials or secrets.');
    await save(value);
  }), actionButton('Cancel', async () => { editor.remove(); }));
  editor.append(input, actions);
  card.appendChild(editor);
  input.focus();
}

function cardStatus(card: HTMLElement): HTMLElement {
  const status = document.createElement('div');
  status.className = 'cp-card-status';
  status.setAttribute('role', 'status');
  card.appendChild(status);
  return status;
}

function renderMemoryCard(mem: BackendMemoryDoc): HTMLElement {
  const card = document.createElement('article');
  card.className = 'cp-card';
  card.setAttribute('data-id', mem.id);
  const top = document.createElement('div');
  top.className = 'cp-card-top';
  for (const [value, tone] of [[mem.classification || 'general', mem.classification === 'sensitive' ? 'error' : 'neutral'], [mem.status || 'active', mem.status === 'blocked' ? 'error' : 'success']]) {
    const badge = document.createElement('span');
    badge.className = `cp-badge cp-badge-${tone}`;
    badge.textContent = value;
    top.appendChild(badge);
  }
  const wording = document.createElement('div');
  wording.className = 'cp-card-text';
  wording.textContent = mem.text;
  const evidence = document.createElement('p');
  evidence.className = 'cp-evidence';
  evidence.textContent = `Evidence: ${mem.evidence_excerpt || 'No excerpt available'}`;
  const meta = document.createElement('div');
  meta.className = 'cp-card-meta';
  meta.textContent = `${mem.source || 'Unknown source'} · ${formatDate(mem.created_at) || 'Date unavailable'}`;
  const actions = document.createElement('div');
  actions.className = 'cp-card-actions';
  actions.appendChild(actionButton('Correct', async () => {
    card.querySelector('.cp-card-editor')?.remove();
    editCard(card, mem.text, async (value) => { await apiClient.updateMemory(mem.id, value); await loadVaultMemories(); });
  }));
  if (mem.status !== 'blocked') actions.appendChild(actionButton('Block', async () => {
    if (!(await apiClient.blockMemory(mem.id))) throw new Error('Could not block memory.');
    await loadVaultMemories();
  }));
  actions.appendChild(actionButton('Forget', async () => {
    if (!window.confirm('Forget this memory and its stored history?')) return;
    if (!(await apiClient.deleteMemory(mem.id))) throw new Error('Could not forget memory.');
    await loadVaultMemories();
  }, true));
  card.append(top, wording, evidence, meta, actions);
  cardStatus(card);
  return card;
}

// Learned Preferences
function initPreferences() {
  const refreshBtn = document.getElementById('pref-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadPreferences());
  }
  loadPreferences();
}

async function loadPreferences() {
  const listEl = document.getElementById('preferences-list');
  if (!listEl) return;

  listEl.innerHTML = '<div class="cp-empty-state">Loading preferences...</div>';

  try {
    const preferences = await apiClient.getPreferences();
    if (!preferences || preferences.length === 0) {
      listEl.innerHTML = '<div class="cp-empty-state">No preferences promoted yet.<br><small>Promotes after 3 observations across 2+ chats.</small></div>';
      return;
    }

    listEl.innerHTML = '';
    preferences.forEach((pref) => listEl.appendChild(renderPreferenceCard(pref)));
  } catch (err: any) {
    listEl.innerHTML = `<div class="cp-empty-state">Failed to load preferences: ${escapeHtml(err.message)}</div>`;
  }
}

function renderPreferenceCard(pref: BackendPreferenceDoc): HTMLElement {
  const card = document.createElement('article');
  card.className = 'cp-card';
  card.setAttribute('data-id', pref.id);
  const top = document.createElement('div');
  top.className = 'cp-card-top';
  const classBadge = document.createElement('span');
  classBadge.className = 'cp-badge cp-badge-neutral';
  classBadge.textContent = 'general';
  const state = document.createElement('span');
  let statusTone = 'neutral';
  if (pref.status === 'active') statusTone = 'success';
  else if (pref.status === 'blocked') statusTone = 'error';
  else if (pref.status === 'insufficient_evidence') statusTone = 'warning';
  state.className = `cp-badge cp-badge-${statusTone}`;
  state.textContent = pref.status || 'active';
  const evidenceCount = document.createElement('span');
  evidenceCount.className = 'cp-badge cp-badge-neutral';
  evidenceCount.textContent = pref.locked ? 'Your wording · locked' : `${pref.evidence_count || 0} observations`;
  top.append(classBadge, state, evidenceCount);
  const wording = document.createElement('div');
  wording.className = 'cp-card-text';
  wording.textContent = pref.preference_text;
  const meta = document.createElement('div');
  meta.className = 'cp-card-meta';
  meta.textContent = `${pref.preference_key.replaceAll('_', ' ')} · ${pref.conversation_count || 0} chats`;
  const evidence = document.createElement('div');
  evidence.className = 'cp-evidence-list';
  for (const observation of pref.supporting_observations || []) {
    const row = document.createElement('div');
    row.className = 'cp-evidence-row';
    const excerpt = document.createElement('span');
    excerpt.textContent = observation.excerpt || '[Evidence redacted]';
    row.append(excerpt, actionButton('Remove', async () => {
      if (!(await apiClient.removePreferenceEvidence(pref.id, observation.id))) throw new Error('Could not remove evidence.');
      await loadPreferences();
    }));
    evidence.appendChild(row);
  }
  const actions = document.createElement('div');
  actions.className = 'cp-card-actions';
  actions.appendChild(actionButton('Correct', async () => {
    card.querySelector('.cp-card-editor')?.remove();
    editCard(card, pref.preference_text, async (value) => {
      await apiClient.updatePreference(pref.id, value);
      await loadPreferences();
    });
  }));
  if (pref.status !== 'blocked') actions.appendChild(actionButton('Block', async () => {
    if (!(await apiClient.blockPreference(pref.id))) throw new Error('Could not block preference.');
    await loadPreferences();
  }));
  actions.appendChild(actionButton('Forget', async () => {
    if (!window.confirm('Forget this preference and remove its supporting evidence?')) return;
    if (!(await apiClient.forgetPreference(pref.id))) throw new Error('Could not forget preference.');
    await loadPreferences();
  }, true));
  card.append(top, wording, meta, evidence, actions);
  cardStatus(card);
  return card;
}

// Universal Fallback Route
function initFallback() {
  const draftInput = document.getElementById('fallback-input') as HTMLTextAreaElement | null;
  const retrieveBtn = document.getElementById('fallback-retrieve-btn') as HTMLButtonElement | null;
  const sensitivePanel = document.getElementById('fallback-sensitive-panel') as HTMLElement | null;
  const sensitiveText = document.getElementById('fallback-sensitive-text') as HTMLElement | null;
  const allowSensitiveCb = document.getElementById('fallback-allow-sensitive') as HTMLInputElement | null;
  const outputText = document.getElementById('fallback-output') as HTMLTextAreaElement | null;
  const copyBtn = document.getElementById('fallback-copy-btn') as HTMLButtonElement | null;

  let lastGeneralMemories: MemoryItem[] = [];
  let lastSensitiveMemories: MemoryItem[] = [];
  let lastPreferences: PreferenceItem[] = [];

  const updatePreparedOutput = () => {
    if (!outputText || !draftInput) return;
    const includeSensitive = allowSensitiveCb ? allowSensitiveCb.checked : false;
    const approvedSensitive = includeSensitive ? lastSensitiveMemories : [];

    const block = formatMemoryBlock(lastGeneralMemories, approvedSensitive, lastPreferences);
    const prepared = applyMemoryBlockToDraft(draftInput.value, block);
    outputText.value = prepared;
  };

  if (retrieveBtn) {
    retrieveBtn.addEventListener('click', async () => {
      const query = draftInput?.value.trim() || 'General user preferences';
      if (containsSecret(query)) {
        if (outputText) outputText.value = '';
        if (sensitivePanel) sensitivePanel.style.display = 'none';
        alert('Recognizable secret in draft. Memory recall cancelled for privacy.');
        return;
      }
      retrieveBtn.textContent = 'Retrieving...';

      try {
        const res = await apiClient.queryMemories(query, 3);
        if (draftInput?.value.trim() !== query) {
          retrieveBtn.textContent = 'Draft changed—retrieve again';
          return;
        }
        const screened = screenRecall(res);
        lastGeneralMemories = screened.general;
        lastSensitiveMemories = screened.sensitive;
        lastPreferences = screened.preferences;

        // Check for sensitive memories
        if (lastSensitiveMemories.length > 0 && sensitivePanel && sensitiveText) {
          sensitivePanel.style.display = 'block';
          sensitiveText.textContent = lastSensitiveMemories.map((m) => m.text).join('; ');
          if (allowSensitiveCb) allowSensitiveCb.checked = false; // default unchecked
        } else if (sensitivePanel) {
          sensitivePanel.style.display = 'none';
        }

        updatePreparedOutput();
        retrieveBtn.textContent = 'Memories Retrieved!';
        setTimeout(() => (retrieveBtn.textContent = 'Retrieve Relevant Memories'), 2000);
      } catch (err: any) {
        alert(`Error querying memories: ${err.message}`);
        retrieveBtn.textContent = 'Retrieve Relevant Memories';
      }
    });
  }

  draftInput?.addEventListener('input', () => {
    lastGeneralMemories = [];
    lastSensitiveMemories = [];
    lastPreferences = [];
    if (allowSensitiveCb) allowSensitiveCb.checked = false;
    if (sensitivePanel) sensitivePanel.style.display = 'none';
    if (outputText) outputText.value = draftInput.value;
  });

  if (allowSensitiveCb) {
    allowSensitiveCb.addEventListener('change', () => {
      updatePreparedOutput();
    });
  }

  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      if (!outputText || !outputText.value.trim()) return;
      await navigator.clipboard.writeText(outputText.value);
      copyBtn.textContent = 'Copied to Clipboard!';
      setTimeout(() => (copyBtn.textContent = 'Copy Prepared Prompt'), 2000);
    });
  }
}

function escapeHtml(str: string): string {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatDate(iso?: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}
