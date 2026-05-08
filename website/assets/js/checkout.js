// Phantom Cast — NOWPayments BTC checkout (browser-side demo flow).
//
// In production, the create-invoice and status-poll calls MUST be proxied
// through your own backend so the NOWPayments API key isn't exposed.
// Recommended endpoints (Cloud Functions / Next.js route handlers):
//   POST /api/np/invoice      -> creates an invoice via NOWPayments REST API
//   GET  /api/np/status?id=   -> proxies GET /v1/payment/{id}
//   POST /api/np/webhook      -> verifies HMAC signature from NOWPayments,
//                                creates the license, emails the key
//
// This file mocks the flow so the static site is fully clickable while the
// backend wiring is being built. Replace MOCK calls with real fetches once
// /api endpoints exist.

const PRICES = {
  license:      { id: 'PC-LICENSE-770',  amount: 770, label: 'Phantom Cast — Lifetime License' },
  premium:      { id: 'PC-PREMIUM-30',   amount: 30,  label: 'Premium Add-on (1 month)' },
};
const PAY_CURRENCY = 'btc';

// ---- Mock helpers (replace with real backend calls) ----

function mockBtcAddress() {
  // Deterministic-looking but fake testnet-style address for demo display.
  const chars = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
  let a = 'bc1q';
  for (let i = 0; i < 38; i++) a += chars[Math.floor(Math.random() * chars.length)];
  return a;
}

async function fetchBtcRate() {
  // CoinGecko public API (no key) — used only for display estimate.
  try {
    const r = await fetch('https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd');
    if (!r.ok) throw new Error('rate fetch failed');
    const j = await r.json();
    return j.bitcoin && j.bitcoin.usd;
  } catch (e) {
    return 65000; // fallback estimate
  }
}

async function createInvoice({ plan, email }) {
  const product = PRICES[plan];
  if (!product) throw new Error('Unknown plan');

  // === REAL BACKEND CALL (uncomment once /api/np/invoice exists) ===
  // const r = await fetch('/api/np/invoice', {
  //   method: 'POST',
  //   headers: { 'Content-Type': 'application/json' },
  //   body: JSON.stringify({ price_amount: product.amount, price_currency: 'usd',
  //     pay_currency: PAY_CURRENCY, order_id: `${product.id}-${Date.now()}`,
  //     order_description: product.label, customer_email: email })
  // });
  // if (!r.ok) throw new Error('invoice creation failed');
  // return await r.json();

  // === MOCK ===
  const usdRate = await fetchBtcRate();
  const payAmount = +(product.amount / usdRate).toFixed(8);
  return {
    payment_id: 'np_' + Math.random().toString(36).slice(2, 12),
    payment_status: 'waiting',
    pay_address: mockBtcAddress(),
    price_amount: product.amount,
    price_currency: 'USD',
    pay_amount: payAmount,
    pay_currency: PAY_CURRENCY,
    network: 'btc',
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
    order_id: `${product.id}-${Date.now()}`,
    order_description: product.label,
    customer_email: email,
  };
}

async function pollStatus(paymentId) {
  // === REAL ===
  // const r = await fetch(`/api/np/status?id=${encodeURIComponent(paymentId)}`);
  // if (!r.ok) throw new Error('status poll failed');
  // return await r.json();

  // === MOCK: progresses through the lifecycle every few polls ===
  pollStatus._step = (pollStatus._step || 0) + 1;
  let status = 'waiting';
  if (pollStatus._step > 4)  status = 'confirming';
  if (pollStatus._step > 8)  status = 'confirmed';
  if (pollStatus._step > 10) status = 'finished';
  return { payment_id: paymentId, payment_status: status };
}

// Generate a fake but professional-looking license key for the success screen.
// Production: this comes from your `v1_stripe`-style webhook (renamed to
// `v1_np` in NOWPayments world) which writes to Firestore and emails Resend.
function makeLicenseKey() {
  const seg = (n) => Array.from({ length: n }, () =>
    'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'[Math.floor(Math.random() * 32)]).join('');
  return `PC-${seg(5)}-${seg(5)}-${seg(5)}-${seg(5)}`;
}

// ---- UI bindings ----

function copy(el, text) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = el.innerHTML;
    el.innerHTML = '<i data-lucide="check" class="w-4 h-4"></i> Copied';
    if (window.lucide) window.lucide.createIcons();
    setTimeout(() => { el.innerHTML = orig; if (window.lucide) window.lucide.createIcons(); }, 1400);
  });
}

function fmtBtc(n) { return n.toFixed(8).replace(/0+$/, '').replace(/\.$/, ''); }

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
        <div class="text-sm text-[var(--text-dim)] mb-6">≈ $${invoice.price_amount.toFixed(2)} ${invoice.price_currency}</div>

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
          Receipt and license key will be sent to <span class="text-white">${invoice.customer_email}</span>
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

  // Countdown
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

  // Highlight passed steps
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

function renderSuccess(root, invoice) {
  const key = makeLicenseKey();
  root.innerHTML = `
    <div class="text-center max-w-xl mx-auto">
      <div class="inline-flex w-16 h-16 rounded-full gradient-bg items-center justify-center mb-5">
        <i data-lucide="check" class="w-8 h-8" style="color:#0a0a0f"></i>
      </div>
      <h2 class="text-3xl font-bold mb-3">Payment confirmed.</h2>
      <p class="text-[var(--text-muted)] mb-8">
        Thanks — your payment of <strong class="text-white font-mono">${fmtBtc(invoice.pay_amount)} BTC</strong> has been received and your license is ready.
      </p>

      <div class="card p-6 text-left mb-6">
        <div class="text-xs uppercase tracking-wider text-[var(--text-dim)] mb-2">Your license key</div>
        <div class="flex items-center gap-3">
          <code class="font-mono text-lg gradient-text font-semibold flex-1 break-all">${key}</code>
          <button class="btn btn-secondary text-xs" id="pc-copy-key"><i data-lucide="copy" class="w-4 h-4"></i> Copy</button>
        </div>
        <div class="text-xs text-[var(--text-dim)] mt-3">Also sent to ${invoice.customer_email}.</div>
      </div>

      <div class="flex flex-col sm:flex-row gap-3 justify-center">
        <a href="../download.html"     class="btn btn-primary btn-lg"><i data-lucide="download" class="w-5 h-5"></i> Download Phantom Cast</a>
        <a href="../app/dashboard.html" class="btn btn-secondary btn-lg"><i data-lucide="layout-dashboard" class="w-5 h-5"></i> Go to dashboard</a>
      </div>
    </div>
  `;
  if (window.lucide) window.lucide.createIcons();
  document.getElementById('pc-copy-key').addEventListener('click', (e) => copy(e.currentTarget, key));
}

async function startCheckout({ plan, email, root }) {
  root.innerHTML = `
    <div class="card p-12 text-center">
      <i data-lucide="loader-2" class="w-8 h-8 animate-spin gradient-text mx-auto mb-3"></i>
      <div class="text-[var(--text-muted)]">Creating your invoice…</div>
    </div>`;
  if (window.lucide) window.lucide.createIcons();

  const invoice = await createInvoice({ plan, email });
  renderInvoice(invoice, root);

  // Poll status every 4s; in production use webhook + websocket / SSE for instant updates.
  const poll = setInterval(async () => {
    try {
      const s = await pollStatus(invoice.payment_id);
      updateStatus(s.payment_status);
      if (s.payment_status === 'finished') {
        clearInterval(poll);
        setTimeout(() => renderSuccess(root, invoice), 900);
      }
      if (s.payment_status === 'failed' || s.payment_status === 'expired') {
        clearInterval(poll);
      }
    } catch (e) { /* ignore transient errors */ }
  }, 4000);
}

window.PCCheckout = { startCheckout, PRICES };
