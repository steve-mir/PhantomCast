// Phantom Cast — NOWPayments BTC checkout, wired to the diivix1 backend.
//
// Flow:
//   1. Customer picks plan (pro/studio) + enters email → POST /v1_createPayment
//      Backend creates a $910 NOWPayments invoice and stores a pending license.
//   2. We display BTC pay_address + amount + QR + status timeline.
//   3. Poll /v1_paymentStatus every 4s. When license_status flips to "active",
//      the issued license_key appears in the response — we render the success
//      screen with a copy button.
//
// No NOWPayments secrets ever touch the browser; the API key + IPN secret
// stay in Cloud Functions secret env.

const BACKEND_BASE = (window.PCBackendBase || 'https://us-central1-diivix1.cloudfunctions.net');

// Single-tier activation: $910 buys the activation key + 30 days of full access.
// After day 30 the customer starts a monthly subscription at their chosen tier.
const PRICES = {
  pro:    { id: 'PC-ACT-910-PRO',    amount: 910, label: 'Phantom Cast — Activation Key (Pro)',    monthlyAfterGrace: 19 },
  studio: { id: 'PC-ACT-910-STUDIO', amount: 910, label: 'Phantom Cast — Activation Key (Studio)', monthlyAfterGrace: 49 },
};
const PAY_CURRENCY = 'btc';
const POLL_INTERVAL_MS = 4000;
const POLL_MAX_DURATION_MS = 60 * 60 * 1000;

// ---- Backend calls ----

async function createInvoice({ plan, email }) {
  const r = await fetch(`${BACKEND_BASE}/v1_createPayment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan, email, pay_currency: PAY_CURRENCY }),
  });
  const text = await r.text();
  let json = {};
  try { json = JSON.parse(text); } catch { /* ignore */ }
  if (!r.ok) {
    const msg = json?.error?.message || text || `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return json;
}

async function pollStatus(orderId) {
  const r = await fetch(`${BACKEND_BASE}/v1_paymentStatus?order_id=${encodeURIComponent(orderId)}`);
  if (!r.ok) throw new Error(`status HTTP ${r.status}`);
  return await r.json();
}

async function fetchBtcRate() {
  try {
    const r = await fetch('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd');
    if (!r.ok) throw new Error('rate fetch failed');
    const j = await r.json();
    return j.bitcoin && j.bitcoin.usd;
  } catch (e) {
    return null;
  }
}

// ---- UI helpers ----

function copy(el, text) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = el.innerHTML;
    el.innerHTML = '<i data-lucide="check" class="w-4 h-4"></i> Copied';
    if (window.lucide) window.lucide.createIcons();
    setTimeout(() => { el.innerHTML = orig; if (window.lucide) window.lucide.createIcons(); }, 1400);
  });
}

function fmtBtc(n) {
  if (n == null || isNaN(n)) return '—';
  return Number(n).toFixed(8).replace(/0+$/, '').replace(/\.$/, '');
}

function renderInvoice(invoice, root) {
  const qrData = `bitcoin:${invoice.pay_address}?amount=${invoice.pay_amount}&label=${encodeURIComponent(invoice.order_description)}`;
  root.innerHTML = `
    <div class="grid md:grid-cols-2 gap-8">
      <div>
        <div class="chip chip-amber mb-4"><i data-lucide="clock" class="w-3 h-3"></i> Awaiting payment</div>
        <h2 class="text-2xl font-bold mb-1">Send exactly</h2>
        <div class="flex items-baseline gap-2 mb-1">
          <span class="text-4xl font-bold font-mono gradient-text">${fmtBtc(invoice.pay_amount)}</span>
          <span class="text-xl text-[var(--text-muted)] font-mono">BTC</span>
        </div>
        <div class="text-sm text-[var(--text-dim)] mb-6">≈ $${Number(invoice.price_amount).toFixed(2)} ${invoice.price_currency}</div>

        <div class="card p-5 mb-4">
          <div class="text-xs text-[var(--text-dim)] uppercase tracking-wider mb-2">BTC address (Bitcoin mainnet)</div>
          <div class="font-mono text-sm break-all leading-relaxed mb-3" id="pc-pay-address">${invoice.pay_address}</div>
          <div class="flex gap-2">
            <button class="btn btn-secondary text-xs" id="pc-copy-addr"><i data-lucide="copy" class="w-4 h-4"></i> Copy address</button>
            <button class="btn btn-secondary text-xs" id="pc-copy-amt"><i data-lucide="copy" class="w-4 h-4"></i> Copy amount</button>
          </div>
        </div>

        <ul class="text-sm text-[var(--text-muted)] space-y-1.5 leading-relaxed">
          <li>• Network: <strong class="text-white">Bitcoin (BTC)</strong> — do not send from any other chain.</li>
          <li>• Send the exact amount in a single transaction.</li>
          <li>• 1 confirmation typically settles within 10–30 minutes.</li>
          <li>• Invoice expires in <span class="font-mono" id="pc-countdown">60:00</span></li>
        </ul>
      </div>

      <div>
        <div class="card p-6 flex flex-col items-center">
          <div id="pc-qr" class="bg-white p-4 rounded-xl"></div>
          <div class="text-xs text-[var(--text-dim)] mt-4 text-center">Scan with any Bitcoin wallet (BlueWallet, Muun, Phoenix, Trezor Suite, etc.)</div>
        </div>

        <div class="card p-5 mt-4">
          <div class="flex items-center justify-between mb-3">
            <div class="text-sm font-semibold">Payment status</div>
            <div class="chip" id="pc-status-chip"><span class="dot dot-warn"></span> waiting</div>
          </div>
          <div class="space-y-2 text-sm" id="pc-status-steps">
            <div class="flex items-center gap-2 text-[var(--text-muted)]" data-step="waiting">    <i data-lucide="circle"          class="w-4 h-4"></i> Awaiting transaction</div>
            <div class="flex items-center gap-2 text-[var(--text-dim)]"   data-step="confirming"> <i data-lucide="circle"          class="w-4 h-4"></i> Confirming on the network</div>
            <div class="flex items-center gap-2 text-[var(--text-dim)]"   data-step="confirmed">  <i data-lucide="circle"          class="w-4 h-4"></i> Confirmed</div>
            <div class="flex items-center gap-2 text-[var(--text-dim)]"   data-step="finished">   <i data-lucide="circle"          class="w-4 h-4"></i> License issued</div>
          </div>
        </div>

        <div class="text-xs text-[var(--text-dim)] mt-4">
          Order: <span class="font-mono">${invoice.order_id}</span><br>
          Plan after grace: <span class="text-white">$${invoice.monthly_after_grace}/mo</span> (starts in 30 days)
        </div>
      </div>
    </div>
  `;

  if (window.lucide) window.lucide.createIcons();
  if (window.QRCode) {
    new window.QRCode(document.getElementById('pc-qr'), {
      text: qrData, width: 200, height: 200, correctLevel: window.QRCode.CorrectLevel.M
    });
  }
  document.getElementById('pc-copy-addr').addEventListener('click', (e) => copy(e.currentTarget, invoice.pay_address));
  document.getElementById('pc-copy-amt') .addEventListener('click', (e) => copy(e.currentTarget, fmtBtc(invoice.pay_amount)));

  const expires = new Date(invoice.expires_at).getTime();
  const tick = () => {
    const diff = Math.max(0, expires - Date.now());
    const m = Math.floor(diff / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    const el = document.getElementById('pc-countdown');
    if (el) el.textContent = `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  };
  tick(); setInterval(tick, 1000);
}

function updateStatus(status) {
  const chip = document.getElementById('pc-status-chip');
  if (!chip) return;
  const map = {
    waiting:    { dot: 'warn', text: 'waiting',    color: 'chip-amber' },
    confirming: { dot: 'warn', text: 'confirming', color: 'chip-cyan' },
    confirmed:  { dot: 'up',   text: 'confirmed',  color: 'chip-success' },
    finished:   { dot: 'up',   text: 'finished',   color: 'chip-success' },
    failed:     { dot: 'down', text: 'failed',     color: 'chip' },
    expired:    { dot: 'down', text: 'expired',    color: 'chip' },
  };
  const m = map[status] || map.waiting;
  chip.className = `chip ${m.color}`;
  chip.innerHTML = `<span class="dot dot-${m.dot}"></span> ${m.text}`;

  const order = ['waiting','confirming','confirmed','finished'];
  const idx = order.indexOf(status);
  document.querySelectorAll('#pc-status-steps [data-step]').forEach(row => {
    const i = order.indexOf(row.dataset.step);
    const icon = row.querySelector('i');
    if (i < idx) {
      row.className = 'flex items-center gap-2 text-emerald-400';
      icon.setAttribute('data-lucide', 'check-circle-2');
    } else if (i === idx) {
      row.className = 'flex items-center gap-2 text-white';
      icon.setAttribute('data-lucide', 'loader-2');
      icon.classList.add('animate-spin');
    } else {
      row.className = 'flex items-center gap-2 text-[var(--text-dim)]';
      icon.setAttribute('data-lucide', 'circle');
    }
  });
  if (window.lucide) window.lucide.createIcons();
}

function renderSuccess(root, invoice, licenseKey) {
  root.innerHTML = `
    <div class="text-center max-w-2xl mx-auto">
      <div class="inline-flex w-16 h-16 rounded-full gradient-bg items-center justify-center mb-5">
        <i data-lucide="check" class="w-8 h-8" style="color:#0a0a0f"></i>
      </div>
      <h2 class="text-3xl font-bold mb-3">Payment confirmed.</h2>
      <p class="text-[var(--text-muted)] mb-8">
        Thanks — your payment has been received. <strong class="text-white">Copy your activation key below — this is the only place we'll show it.</strong>
      </p>

      <div class="card p-6 text-left mb-6 border-2 border-amber-500/30 bg-amber-500/5">
        <div class="text-xs uppercase tracking-wider text-amber-400 mb-2 flex items-center gap-2">
          <i data-lucide="key" class="w-4 h-4"></i> Your activation key
        </div>
        <div class="flex items-center gap-3">
          <code class="font-mono text-xl gradient-text font-bold flex-1 break-all" id="pc-license-key">${licenseKey}</code>
          <button class="btn btn-primary text-xs" id="pc-copy-key"><i data-lucide="copy" class="w-4 h-4"></i> Copy</button>
        </div>
        <div class="text-xs text-[var(--text-dim)] mt-4 leading-relaxed">
          ⚠️ <strong class="text-white">Save this key now.</strong> Email delivery isn't enabled yet — if you lose this key,
          we can recover it from order ID <span class="font-mono">${invoice.order_id}</span>, but please save it locally first.
        </div>
      </div>

      <div class="card p-5 text-left mb-6">
        <div class="text-sm font-semibold mb-2">What happens next</div>
        <ol class="text-sm text-[var(--text-muted)] space-y-1.5 list-decimal list-inside">
          <li>Download Phantom-Cast Pro from the link below.</li>
          <li>On first launch, paste your key into the activation dialog. The app will bind it to this PC.</li>
          <li>You have <strong class="text-white">30 days of full access</strong> to every feature.</li>
          <li>After day 30, your subscription rolls into <strong class="text-white">$${invoice.monthly_after_grace}/mo</strong> billed in BTC.</li>
        </ol>
      </div>

      <div class="flex flex-col sm:flex-row gap-3 justify-center">
        <a href="download.html" class="btn btn-primary btn-lg"><i data-lucide="download" class="w-5 h-5"></i> Download Phantom-Cast</a>
        <a href="app/dashboard.html" class="btn btn-secondary btn-lg"><i data-lucide="layout-dashboard" class="w-5 h-5"></i> Go to dashboard</a>
      </div>
    </div>
  `;
  if (window.lucide) window.lucide.createIcons();
  document.getElementById('pc-copy-key').addEventListener('click', (e) => copy(e.currentTarget, licenseKey));

  // Stash the key in localStorage so the dashboard can show it (until they
  // explicitly clear it). Best-effort — most browsers persist localStorage
  // across sessions; users on private windows lose it.
  try {
    localStorage.setItem('pc_license_key', licenseKey);
    localStorage.setItem('pc_order_id', invoice.order_id);
    localStorage.setItem('pc_owner_email', invoice.customer_email || '');
  } catch (e) { /* private mode */ }
}

function renderError(root, message) {
  root.innerHTML = `
    <div class="text-center max-w-xl mx-auto py-8">
      <div class="inline-flex w-16 h-16 rounded-full bg-rose-500/15 items-center justify-center mb-5">
        <i data-lucide="alert-triangle" class="w-8 h-8 text-rose-400"></i>
      </div>
      <h2 class="text-2xl font-bold mb-2">Couldn't start checkout</h2>
      <p class="text-[var(--text-muted)] mb-6">${message}</p>
      <a href="checkout.html" class="btn btn-secondary">Try again</a>
    </div>
  `;
  if (window.lucide) window.lucide.createIcons();
}

async function startCheckout({ plan, email, root }) {
  root.innerHTML = `
    <div class="card p-12 text-center">
      <i data-lucide="loader-2" class="w-8 h-8 animate-spin gradient-text mx-auto mb-3"></i>
      <div class="text-[var(--text-muted)]">Creating your invoice…</div>
    </div>`;
  if (window.lucide) window.lucide.createIcons();

  let invoice;
  try {
    invoice = await createInvoice({ plan, email });
  } catch (e) {
    renderError(root, e.message || 'unknown error');
    return;
  }
  renderInvoice(invoice, root);

  const startedAt = Date.now();
  const poll = setInterval(async () => {
    if (Date.now() - startedAt > POLL_MAX_DURATION_MS) {
      clearInterval(poll); return;
    }
    try {
      const s = await pollStatus(invoice.order_id);
      updateStatus(s.payment_status || 'waiting');
      if (s.license_status === 'active' && s.license_key) {
        clearInterval(poll);
        setTimeout(() => renderSuccess(root, invoice, s.license_key), 800);
      }
      if (s.payment_status === 'failed' || s.payment_status === 'expired') {
        clearInterval(poll);
      }
    } catch (e) { /* transient — keep polling */ }
  }, POLL_INTERVAL_MS);
}

window.PCCheckout = { startCheckout, PRICES, BACKEND_BASE };
