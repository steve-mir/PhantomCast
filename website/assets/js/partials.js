// Phantom Cast — shared partials (header / footer) injected into every page.
// Pages must include <div data-pc-header></div> and <div data-pc-footer></div>.
// Set data-pc-section on the <body> (e.g., "features") to mark the active nav link.

(function () {
  const APP_NAME = 'Phantom Cast';
  const VERSION = '1.4.2';
  const RELEASED = 'May 6, 2026';

  // Resolve relative path back to root depending on current page depth.
  function rootPrefix() {
    // Determine current path under /website/...
    const path = location.pathname.replace(/\\/g, '/');
    // Strip trailing filename
    const dir = path.endsWith('/') ? path : path.replace(/[^/]*$/, '');
    // Count segments after /website/
    const idx = dir.indexOf('/website/');
    if (idx === -1) return ''; // dev fallback (root index)
    const after = dir.slice(idx + '/website/'.length);
    const depth = after.split('/').filter(Boolean).length;
    return '../'.repeat(depth);
  }

  const R = rootPrefix();

  function navLink(href, label, key, currentSection) {
    const active = currentSection === key ? 'active' : '';
    return `<a href="${R}${href}" class="nav-link ${active}" data-key="${key}">${label}</a>`;
  }

  function buildHeader(currentSection) {
    return `
<header class="site-header">
  <div class="container-pc flex items-center justify-between h-16">
    <a href="${R}index.html" class="flex items-center gap-2.5">
      <div class="w-8 h-8 rounded-lg gradient-bg flex items-center justify-center">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a0a0f" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"/><path d="M21 11.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"/><path d="M5 19c0-3 2.5-5 7-5s7 2 7 5"/></svg>
      </div>
      <span class="font-semibold text-[15px] tracking-tight">${APP_NAME}</span>
      <span class="chip chip-violet text-[10px] hidden sm:inline-flex">v${VERSION}</span>
    </a>

    <nav class="hidden lg:flex items-center gap-7">
      ${navLink('features.html', 'Features', 'features', currentSection)}
      ${navLink('pricing.html', 'Pricing', 'pricing', currentSection)}
      ${navLink('docs/index.html', 'Docs', 'docs', currentSection)}
      ${navLink('changelog.html', 'Changelog', 'changelog', currentSection)}
      ${navLink('help.html', 'Help', 'help', currentSection)}
    </nav>

    <div class="flex items-center gap-2">
      <a href="${R}app/login.html" class="btn btn-ghost hidden md:inline-flex">Sign in</a>
      <a href="${R}download.html" class="btn btn-primary">
        <i data-lucide="download" class="w-4 h-4"></i>
        Download
      </a>
      <button id="pc-mobile-toggle" class="lg:hidden btn btn-ghost p-2" aria-label="Open menu">
        <i data-lucide="menu" class="w-5 h-5"></i>
      </button>
    </div>
  </div>

  <div id="pc-mobile-menu" class="lg:hidden hidden border-t border-[var(--border-soft)]">
    <div class="container-pc py-4 flex flex-col gap-1">
      <a href="${R}features.html" class="nav-link py-2">Features</a>
      <a href="${R}pricing.html" class="nav-link py-2">Pricing</a>
      <a href="${R}docs/index.html" class="nav-link py-2">Docs</a>
      <a href="${R}changelog.html" class="nav-link py-2">Changelog</a>
      <a href="${R}help.html" class="nav-link py-2">Help</a>
      <a href="${R}app/login.html" class="nav-link py-2">Sign in</a>
    </div>
  </div>
</header>`;
  }

  function buildFooter() {
    const year = new Date().getFullYear();
    return `
<footer class="border-t border-[var(--border-soft)] mt-24">
  <div class="container-pc py-16">
    <div class="grid grid-cols-2 md:grid-cols-5 gap-10">
      <div class="col-span-2">
        <div class="flex items-center gap-2.5 mb-4">
          <div class="w-8 h-8 rounded-lg gradient-bg flex items-center justify-center">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#0a0a0f" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"/><path d="M21 11.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0z"/><path d="M5 19c0-3 2.5-5 7-5s7 2 7 5"/></svg>
          </div>
          <span class="font-semibold tracking-tight">${APP_NAME}</span>
        </div>
        <p class="text-sm text-[var(--text-muted)] max-w-xs leading-relaxed">
          Real-time face swap on your GPU. Local. Private. No frames leave your machine.
        </p>
        <div class="flex items-center gap-3 mt-5">
          <a href="https://twitter.com/phantomcast"  class="btn btn-ghost p-2" aria-label="Twitter / X"><i data-lucide="twitter" class="w-4 h-4"></i></a>
          <a href="https://youtube.com/@phantomcast" class="btn btn-ghost p-2" aria-label="YouTube"><i data-lucide="youtube" class="w-4 h-4"></i></a>
          <a href="https://github.com/phantomcast"   class="btn btn-ghost p-2" aria-label="GitHub"><i data-lucide="github"  class="w-4 h-4"></i></a>
          <a href="https://discord.gg/phantomcast"   class="btn btn-ghost p-2" aria-label="Discord"><i data-lucide="message-circle" class="w-4 h-4"></i></a>
        </div>
      </div>
      <div>
        <h4 class="text-xs uppercase tracking-wider text-[var(--text-dim)] mb-4">Product</h4>
        <ul class="space-y-2.5 text-sm">
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}features.html">Features</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}pricing.html">Pricing</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}download.html">Download</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}changelog.html">Changelog</a></li>
        </ul>
      </div>
      <div>
        <h4 class="text-xs uppercase tracking-wider text-[var(--text-dim)] mb-4">Resources</h4>
        <ul class="space-y-2.5 text-sm">
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}docs/index.html">Docs</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}help.html">Help &amp; FAQ</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}status/index.html">Status</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}press.html">Press kit</a></li>
        </ul>
      </div>
      <div>
        <h4 class="text-xs uppercase tracking-wider text-[var(--text-dim)] mb-4">Company</h4>
        <ul class="space-y-2.5 text-sm">
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}about.html">About</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}contact.html">Contact</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}legal/acceptable-use.html">Acceptable Use</a></li>
          <li><a class="text-[var(--text-muted)] hover:text-white" href="${R}legal/privacy.html">Privacy</a></li>
        </ul>
      </div>
    </div>

    <div class="divider my-10"></div>

    <div class="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 text-xs text-[var(--text-dim)]">
      <div>© ${year} Phantom Cast. All rights reserved.</div>
      <div class="flex flex-wrap items-center gap-x-5 gap-y-2">
        <a href="${R}legal/terms.html"           class="hover:text-white">Terms</a>
        <a href="${R}legal/privacy.html"         class="hover:text-white">Privacy</a>
        <a href="${R}legal/eula.html"            class="hover:text-white">EULA</a>
        <a href="${R}legal/refund-policy.html"   class="hover:text-white">Refunds</a>
        <a href="${R}legal/dpa.html"             class="hover:text-white">DPA</a>
        <a href="${R}status/index.html"          class="hover:text-white inline-flex items-center gap-1.5"><span class="dot dot-up"></span> All systems operational</a>
      </div>
    </div>
  </div>
</footer>`;
  }

  function init() {
    const section = document.body.dataset.pcSection || '';
    const headerSlot = document.querySelector('[data-pc-header]');
    const footerSlot = document.querySelector('[data-pc-footer]');
    if (headerSlot) headerSlot.outerHTML = buildHeader(section);
    if (footerSlot) footerSlot.outerHTML = buildFooter();

    // Wire mobile menu
    const tog = document.getElementById('pc-mobile-toggle');
    const menu = document.getElementById('pc-mobile-menu');
    if (tog && menu) tog.addEventListener('click', () => menu.classList.toggle('hidden'));

    // OS detection: any element with [data-pc-os] gets the OS string
    const ua = navigator.userAgent;
    let os = 'Windows';
    if (/Mac/.test(ua)) os = 'macOS';
    else if (/Linux/.test(ua)) os = 'Linux';
    document.querySelectorAll('[data-pc-os]').forEach(el => el.textContent = os);

    // Render Lucide icons (after DOM injection)
    if (window.lucide && window.lucide.createIcons) window.lucide.createIcons();

    // Glow card mousemove
    document.querySelectorAll('.glow-card').forEach(card => {
      card.addEventListener('mousemove', (e) => {
        const r = card.getBoundingClientRect();
        card.style.setProperty('--mx', `${e.clientX - r.left}px`);
        card.style.setProperty('--my', `${e.clientY - r.top}px`);
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose constants for pages that want them
  window.PC = { APP_NAME, VERSION, RELEASED };
})();
