const tablists = document.querySelectorAll('[data-tablist]');

function activateTab(tablist, nextTab) {
  const tabs = [...tablist.querySelectorAll('[role="tab"]')];
  const group = tablist.dataset.tablist;

  tabs.forEach((tab) => {
    const selected = tab === nextTab;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });

  document.querySelectorAll(`[role="tabpanel"][data-panel]`).forEach((panel) => {
    if (!panel.id.startsWith(`${group}-`)) return;
    panel.hidden = panel.dataset.panel !== nextTab.dataset.tab;
  });
}

tablists.forEach((tablist) => {
  const tabs = [...tablist.querySelectorAll('[role="tab"]')];

  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => activateTab(tablist, tab));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;

      event.preventDefault();
      let nextIndex = index;
      if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === 'Home') nextIndex = 0;
      if (event.key === 'End') nextIndex = tabs.length - 1;

      activateTab(tablist, tabs[nextIndex]);
      tabs[nextIndex].focus();
    });
  });
});

const copyButton = document.querySelector('[data-copy-button]');
const copyStatus = document.querySelector('#copy-status');

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const temporaryInput = document.createElement('textarea');
  temporaryInput.value = text;
  temporaryInput.setAttribute('readonly', '');
  temporaryInput.style.position = 'fixed';
  temporaryInput.style.opacity = '0';
  document.body.appendChild(temporaryInput);
  temporaryInput.select();
  const copied = document.execCommand('copy');
  temporaryInput.remove();
  if (!copied) throw new Error('Clipboard copy was unsuccessful');
}

copyButton?.addEventListener('click', async () => {
  const activePanel = document.querySelector('#install [role="tabpanel"]:not([hidden])');
  const command = activePanel?.dataset.command;
  if (!command) return;

  copyButton.disabled = true;
  try {
    await copyText(command);
    copyButton.querySelector('span').textContent = 'Copied';
    copyStatus.textContent = 'Installation command copied to clipboard.';
  } catch {
    copyButton.querySelector('span').textContent = 'Try again';
    copyStatus.textContent = 'Could not copy the command. Select it manually instead.';
  }

  window.setTimeout(() => {
    copyButton.querySelector('span').textContent = 'Copy';
    copyButton.disabled = false;
  }, 1800);
});

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const revealItems = document.querySelectorAll('.reveal');

if (reducedMotion || !('IntersectionObserver' in window)) {
  revealItems.forEach((item) => item.classList.add('is-visible'));
} else {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      });
    },
    { threshold: 0.12, rootMargin: '0px 0px -5% 0px' },
  );

  revealItems.forEach((item) => observer.observe(item));
}

const header = document.querySelector('[data-header]');
const updateHeader = () => header?.classList.toggle('is-scrolled', window.scrollY > 12);
updateHeader();
window.addEventListener('scroll', updateHeader, { passive: true });

const year = document.querySelector('#year');
if (year) year.textContent = String(new Date().getFullYear());
