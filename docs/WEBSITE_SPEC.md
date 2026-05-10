# Phantom Cast — Website Specification

> The marketing + commerce + support website for the desktop product.

**Product:** Phantom Cast
**Tagline:** *Real-time face swap, on your GPU. Built for streamers, creators, and producers.*
**Domain:** `phantomcast.space`
**Subdomains:**
- `dl.phantomcast.space` — installer CDN (Cloudflare R2)
- `app.phantomcast.space` — customer portal (login, subscription, license keys)
- `docs.phantomcast.space` — user docs (optional, can be a `/docs` path instead)
- `status.phantomcast.space` — public status page (Better Stack / Instatus)

---

## 1. Positioning

Phantom Cast sells GPU-accelerated, locally-run face swap. The pitch is
**privacy + performance**:

- *Local* — frames never leave the user's machine. No cloud upload.
- *Real-time* — 30 fps live webcam swap on a $400 GPU.
- *Studio-grade* — 4K export, GPEN-512 enhancement, batch queue.

Primary audiences:

| Segment | Use case | Plan |
|---|---|---|
| Streamers / VTubers | Live face overlay during Twitch/YouTube streams | Pro ($19/mo) |
| Content creators | Pre-recorded TikTok / Shorts edits | Pro |
| Indie filmmakers | Look development, stunt-double VFX | Studio ($49/mo) |
| Hobbyists | Curiosity, family videos | Free trial → Pro |

Secondary positioning: **explicit anti-deepfake-misuse stance** in
copy, ToS, and prominent watermark on free-tier output. This is both
ethical and a legal/compliance moat: the product opts itself out of
"deepfake tool" media coverage.

---

## 2. Tech stack

Recommended (in order of pragmatism):

| Layer | Choice | Why |
|---|---|---|
| Framework | **Next.js 15 (App Router)** on **Vercel** | SEO + edge SSR + zero-config preview deploys |
| Styling | Tailwind + shadcn/ui | Fast, ownable components |
| Auth | **Firebase Auth** (same project as the desktop app) | One identity for purchase, portal, and app activation |
| Payments | **Stripe Checkout + Customer Portal** | Hosted, PCI-out-of-scope, supports tax (Stripe Tax) |
| CMS | **Sanity** or **Contentlayer + MDX** | Marketing pages + blog without a backend |
| Email | **Resend** + React Email | Transactional (license keys, receipts, expiry notice) |
| Analytics | **PostHog** (self-hosted EU) | Product analytics + session replay; GDPR-friendly |
| Status | **Instatus** | $20/mo public status page |
| Docs | **Mintlify** or `/docs` path with MDX | Versioned product docs |
| Forms / support | **Plain** or **Crisp** | Live chat + ticketing |

Hosted on Vercel for the marketing site; the customer portal at
`app.phantomcast.space` can be the same Next.js app under an `(app)`
route group with auth-gating, or a separate Next.js project.

---

## 3. Sitemap

```
phantomcast.space/
├── /                                    Homepage
├── /features                            Features deep-dive
├── /pricing                             Pricing + plan compare
├── /download                            Download page (OS detection, version banner)
├── /changelog                           Versioned release notes
├── /changelog/[version]
├── /blog                                Blog index
├── /blog/[slug]                         Article
├── /docs                                Documentation index
│   ├── /docs/getting-started
│   ├── /docs/gpu-troubleshooting
│   ├── /docs/streaming-obs-integration
│   ├── /docs/account-billing
│   └── /docs/api                        (placeholder, future)
├── /help                                FAQ + support form
├── /about                               Team, mission, anti-misuse stance
├── /contact
├── /legal/
│   ├── /legal/terms
│   ├── /legal/privacy
│   ├── /legal/acceptable-use            *** strong anti-deepfake clause ***
│   ├── /legal/eula
│   ├── /legal/refund-policy
│   └── /legal/dpa                       (for B2B customers)
├── /press                               Press kit (logos, screenshots, founder bio)
└── /sitemap.xml + /robots.txt + /llms.txt

app.phantomcast.space/                     (auth-gated)
├── /login
├── /signup
├── /forgot-password
├── /verify-email
├── /dashboard                           Plan, license keys, devices, downloads
├── /dashboard/billing                   Stripe Customer Portal redirect
├── /dashboard/devices                   List of activated machines, deactivate button
├── /dashboard/api-keys                  (Studio only — future API access)
└── /admin                               Internal — staff only, behind allowlist
```

---

## 4. Page-by-page brief

### 4.1 Homepage (`/`)

**Goal:** convert a curious visitor → free trial download in <10 seconds.

Sections, top to bottom:

1. **Hero**
   - H1: *"Real-time face swap. On your GPU. In private."*
   - Sub: *"Phantom Cast turns a live webcam or 4K video into a different face — without uploading a single frame to the cloud."*
   - Primary CTA: `Download for Windows` (auto-detects OS; shows the version + size from `latest.json`)
   - Secondary CTA: `See it in action` → opens 30-second autoplay-muted MP4 reel
   - Trust badges: *Runs locally · Code-signed · No frames leave your machine*

2. **Live demo strip** — three short looping MP4s (webcam swap, 4K export, batch). Use `<video preload="metadata" muted loop playsinline>` so iOS Safari behaves.

3. **Three-pillar value props** with icons:
   - GPU-accelerated (CUDA 12, 30 fps live)
   - Private by design (local inference)
   - Studio output (4K, GPEN-512 enhance, no watermark on Studio)

4. **Comparison band** — single horizontal table contrasting "Cloud face-swap services" vs "Phantom Cast" on Privacy / Speed / Cost / Quality.

5. **Feature highlight reel** — 4 scroll-snapping cards, each linking to the deeper `/features` section.

6. **Pricing teaser** — three plans, plus *"Start with a 7-day free trial"*.

7. **Testimonials** — 3 quotes from streamers / creators (collect via "$50 for a real video testimonial" outreach in early days).

8. **Anti-misuse banner** (small, just before footer) — *"We do not allow Phantom Cast to be used for non-consensual likenesses or deception. Read our [Acceptable Use](/legal/acceptable-use)."*

9. **Footer** — sitemap, social, status page link, language switcher (defer i18n until $10K MRR).

### 4.2 Features (`/features`)

Long-scroll page, one section per feature, each with a 5-10 second demo loop:

- Real-time webcam face swap (with OBS routing instructions)
- 4K video export with GPEN-512 enhancement
- Map source-to-target faces (Studio)
- Hyperswap full-head mode (Studio)
- Batch processing queue (Studio)
- GPU diagnostics + automatic CPU fallback
- Privacy: local-only inference (link to architecture diagram)

End each section with a small "Available on: Pro / Studio" pill.

### 4.3 Pricing (`/pricing`)

Three-tier card layout (Free Trial / **Pro** / Studio), with the
recommended plan highlighted. Below the cards:

- Toggle: monthly / annual (annual = 20% discount)
- "Frequently asked" 6-question accordion
- Trust strip: *Cancel anytime · Refund within 14 days · Stripe-secured payments · No cloud upload*

Each "Buy" button kicks off Stripe Checkout. Success redirects to
`/dashboard?welcome=1` which:
1. Confirms the subscription via Stripe API
2. Generates the license key (Cloud Function `v1_stripe` webhook does this)
3. Emails the key via Resend
4. Shows the key inline on the dashboard with a "Copy" button and a "Download Phantom Cast" CTA

### 4.4 Download (`/download`)

```
┌─ Download Phantom Cast 0.0.23 ────────────────┐
│  Released May 6, 2026 · 1.4 GB · SHA-256 ... │
│                                              │
│  [ Download for Windows 10/11 (x64) ]        │
│                                              │
│  System requirements:                        │
│  • Windows 10 21H2 / Windows 11              │
│  • 8 GB RAM (16 GB recommended)              │
│  • NVIDIA GPU with driver ≥ 525.60 for GPU mode │
│                                              │
│  Other downloads:                            │
│  • Previous versions                         │
│  • View installation guide                   │
│  • Verify checksum (instructions)            │
└──────────────────────────────────────────────┘
```

Server component fetches `https://dl.phantomcast.space/win/latest.json`
and renders version + URL. Cache with `revalidate: 60` so a release
shows up within a minute.

### 4.5 Changelog (`/changelog`)

MDX-driven, one file per release. Each entry: version, date, "What's
new" / "Fixed" / "Breaking" sections. RSS feed at `/changelog/feed.xml`.

### 4.6 Docs (`/docs/...`)

Critical articles to ship at launch:

- **Getting started** — install, activate, first swap
- **GPU troubleshooting** — what each detection step means and how to fix it
- **OBS integration** — virtual camera setup for streaming
- **Account & billing** — plan changes, license moves, refunds
- **System requirements** — exact GPU compatibility list
- **Privacy & data** — what we collect (license fingerprint hash, license key, no frames)
- **Acceptable use** — what's allowed and what isn't (link to legal)

Each article ends with a "Was this helpful?" widget logged to PostHog.

### 4.7 Help (`/help`)

FAQ accordion (10-15 questions) + contact form. Form submits to Plain
or Crisp; auto-tags by selected category. Show a status banner at the
top if `status.phantomcast.space` reports an active incident (consume the
status page's JSON feed at build time + runtime poll).

### 4.8 Legal pages

- **Terms of Service** — standard SaaS terms
- **Privacy Policy** — list every data flow:
  - License fingerprint hash (composite SHA-256, never raw components)
  - Email + name (Stripe-collected)
  - IP address (hashed, kept 30 days for fraud detection)
  - PostHog product analytics (self-hosted, EU region; opt-out toggle)
  - **Frames, audio, video are NEVER transmitted** — bold this
- **EULA** — single-machine binding, no reverse engineering, no rental, etc.
- **Acceptable Use Policy** — explicitly prohibits:
  - Non-consensual likeness use of any kind
  - Impersonation for fraud, harassment, or deception
  - Political deepfakes
  - Sexual content involving minors (auto-banned tier 1)
  - Use without disclosing AI-generated content where required by law
  - Reserves the right to revoke license for ToS violation; revocation propagates within 6 hours via heartbeat
- **Refund Policy** — 14-day no-questions refund; clearly stated
- **DPA (Data Processing Addendum)** — for B2B / studio customers

### 4.9 Customer portal (`app.phantomcast.space/dashboard`)

Authenticated with Firebase Auth. Sections:

- **Plan card** — current plan, billing date, "Manage subscription" → Stripe portal
- **License keys card** — list of keys (one per active subscription); reveal/copy/regenerate
- **Devices card** — list of activated machines: `{nickname, OS, GPU, last seen, plan}` with a "Deactivate" button per row
- **Downloads card** — links to current + previous installer versions
- **Move license** — flow that lets a user re-bind to a new machine (rate-limited 1/year), surfaces the same `v1_moveLicense` Cloud Function the desktop app calls

### 4.10 About / Press / Contact

Short pages. About has 1-2 paragraphs on the company mission and
anti-misuse stance. Press has a downloadable ZIP with logos (SVG +
PNG), product screenshots, and a 100-word company boilerplate.

---

## 5. Conversion funnel

```
  Organic / Ad / Social
          │
          ▼
   Homepage (/)
          │  primary CTA
          ▼
  /download         ← OS detection picks the right CTA copy
          │
          ▼
  Free-trial install ── 7-day countdown ──┐
          │                                │
          ├── Wizard step 3 → Activate ─→ Stripe Checkout
          │                                       │
          └─ in-app paywall on premium feature ──┘
                                                  │
                                                  ▼
                                  /dashboard?welcome=1
                                  (license key + Download CTA)
```

Track these PostHog events end-to-end:

- `homepage_view`
- `download_clicked` (props: version, channel)
- `installer_completed` (fired by the desktop app after first launch via a one-shot ping to a `/v1_install_complete` endpoint — anonymous, just a counter)
- `trial_started`
- `paywall_shown` (props: feature)
- `checkout_started`
- `subscription_active`
- `license_activated` (fired by the desktop app's first successful `v1_activate`)

Watch the **install → trial-start** and **paywall → checkout** ratios
weekly; those are the two cheapest knobs to optimize.

---

## 6. SEO

Target queries (primary):

- "live face swap software" — direct intent
- "real-time face swap obs" — streaming intent
- "best face swap for streamers"
- "face swap without cloud upload" — privacy intent
- "deepfake software for creators" — broad-but-relevant

Strategy:

1. **Pillar pages**: `/features`, `/use-cases/streamers`, `/use-cases/filmmakers`, `/use-cases/educators`
2. **Comparison content**: `/vs/cloud-face-swap`, `/vs/deepfacelab` (DFL is open-source, hard to install — easy comparison win)
3. **Tutorial blog**: 1 high-quality post per week for the first 3 months. Examples:
   - *"How to set up Phantom Cast as an OBS virtual camera in 5 minutes"*
   - *"Why your CUDA face-swap is running on CPU (and how to fix it)"*
   - *"GPU benchmarks: Phantom Cast on RTX 3060 vs 4060 vs 4090"*
4. **Schema.org**: `SoftwareApplication`, `FAQPage`, `BreadcrumbList`, `Product` on `/pricing`
5. **`/llms.txt`** at the root listing canonical product info, so LLM-based search surfaces Phantom Cast accurately

Submit to AlternativeTo, Product Hunt (launch day), G2, Capterra, and
the OBS plugin directory (when virtual camera support ships).

---

## 7. Email lifecycle (via Resend)

Transactional:

| Trigger | Template | Notes |
|---|---|---|
| Sign-up | "Verify your email" | Firebase Auth handles |
| First purchase | "Welcome + your license key + download link" | Triggered by `v1_stripe` |
| Subscription renewed | "Receipt" | Triggered by Stripe → webhook → Resend |
| Subscription failed payment | "Update payment method" | Standard Stripe dunning + your email layered on top |
| 3 days before trial expires | "Your trial ends in 3 days" | Cron-driven |
| Subscription cancelled | "We're sorry to see you go + 1-click reactivate" | Stripe webhook |
| New version available | OPT-IN; once per release | Cron-driven from `latest.json` change |
| Inactive 30+ days | "Come back, we shipped X, Y, Z" | Cron, suppressible per user |

Lifecycle marketing (after first paid month):

- Day 0: welcome
- Day 3: "Top 3 things to try first" (OBS integration, GPEN-512 enhance, batch)
- Day 14: case study from a power user
- Day 30: "Your first month — here's what's new"

Keep all of these in code (`emails/*.tsx` with React Email) so reviews
go through Git.

---

## 8. Compliance

- **GDPR / UK GDPR** — DSAR endpoint at `/legal/dsar` lets users
  request data export or deletion. Cloud Function `v1_dsar` fulfills.
- **CCPA / CPRA** — same DSAR flow + "Do not sell" link in footer (no-op for us; we don't sell data).
- **EU AI Act (transparency)** — content generated by the app is AI-generated; we surface a "watermark" toggle that's enforced by the desktop app (free tier always on; paid tiers can disable but with a notice that disclosure may still be legally required in their jurisdiction).
- **Stripe Tax** — enable; Stripe handles VAT/GST collection and remittance.
- **Cookie consent** — required for EU visitors. Use `cookieyes` or a self-rolled banner. Block PostHog until consent.

---

## 9. Launch checklist

```
[ ] Domain registered + Cloudflare DNS configured
[ ] Vercel project deployed at phantomcast.space
[ ] Customer portal at app.phantomcast.space live + auth-gated
[ ] Stripe products created (Pro $19/mo, Pro $190/yr, Studio $49/mo, Studio $490/yr)
[ ] Stripe webhook → v1_stripe Cloud Function endpoint registered
[ ] latest.json reachable at dl.phantomcast.space/win/latest.json
[ ] Code-signed installer downloadable from /download
[ ] Resend domain verified (SPF + DKIM)
[ ] All legal pages drafted by counsel (or at least templated + reviewed)
[ ] Acceptable Use Policy explicitly prohibits non-consensual likeness use
[ ] Privacy Policy enumerates every data flow
[ ] PostHog instrumented with all key events from §5
[ ] Status page live and linked from footer
[ ] /docs has 5+ working articles (Getting Started + GPU Troubleshooting at minimum)
[ ] /help form routes to a real inbox or Plain workspace
[ ] OG images generated for each page (use @vercel/og)
[ ] Sitemap + robots.txt + llms.txt deployed
[ ] Manual end-to-end purchase test: signup → checkout → license email → activate desktop app → premium feature works → cancel → premium feature locks within 6h
```

---

## 10. Future expansion

- **Affiliate program** — 30% recurring, 12-month attribution; track via Tolt or Rewardful
- **Team plans** — seat-based licensing in the same Firestore schema (already accommodated in `licenses.seats` field)
- **Web preview** — drag a video to a `/preview` page that uses a hosted GPU to produce a 5-second watermarked sample (lead magnet, paid per minute on RunPod / Modal)
- **API access (Studio+)** — REST endpoint that takes a video URL and returns a swapped video; metered per minute of output
- **Localization** — start with ES, PT-BR, JA, ZH-CN; use Next.js i18n routing
- **Comparison microsites** — `vs.phantomcast.space/deepfacelab` etc. for SEO long-tail capture
