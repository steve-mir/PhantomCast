# Deep-Live-Cam Pro — Production-Ready Windows SaaS Plan

GPU-first commercial distribution of Deep-Live-Cam: signed Windows installer,
license-bound activation, monthly subscription, Firebase backend, polished UI.

---

## 0. High-Level Architecture

```
+-----------------------------------------------------------+
|                  Deep-Live-Cam Pro (Windows .exe)         |
|                                                           |
|  launch.py  -> Bootstrapper (preflight, env, watchdog)    |
|     |                                                     |
|     v                                                     |
|  modules/dlc_pro/                                         |
|     setup/        First-run wizard, model downloader      |
|     gpu/          CUDA detection, validation, fallback    |
|     license/      Fingerprint, activation, secure store   |
|     subscription/ Plan resolution, feature gating         |
|     firebase/     REST client, token refresh, retries     |
|     ui/           Status bar, settings, paywall, dialogs  |
|     core_bridge.py  Glue into existing modules/core.py    |
|                                                           |
|  Existing modules/* (face_swap, ui, processors, …)        |
|                                                           |
|  Local store: %LOCALAPPDATA%/DeepLiveCamPro/              |
|     state.bin (DPAPI encrypted)                           |
|     cache.json (signed claims)                            |
|     logs/                                                 |
+----------------------|------------------------------------+
                       | HTTPS (TLS1.2+, cert pinning)
                       v
+-----------------------------------------------------------+
|                Firebase (Google Cloud)                    |
|                                                           |
|  Auth          Anonymous + custom-token (license-bound)   |
|  Firestore     users / licenses / devices / subscriptions |
|                / activation_audit / feature_flags         |
|  Functions     activate, heartbeat, deactivate,           |
|                webhook(stripe), refreshClaims             |
|  Stripe        Checkout + Customer Portal + webhooks      |
+-----------------------------------------------------------+
```

### Component contracts

| Component          | Responsibility                                                               | Hard guarantees                                          |
| ------------------ | ---------------------------------------------------------------------------- | -------------------------------------------------------- |
| `gpu.detector`     | Decide CUDA vs CPU before any ONNX import                                    | Pure read; no side effects on user system                |
| `gpu.bootstrap`    | Inject CUDA/cuDNN DLLs onto PATH; preload via `os.add_dll_directory`         | Idempotent; safe on missing dirs                         |
| `license.manager`  | Store + verify activation; expose `is_licensed()` and `current_license()`    | Never blocks UI thread; one-call API                     |
| `subscription.gate`| `require_feature(name)` decorator/check; offline grace via signed cache      | Fails closed for paid features when expired and offline > grace |
| `firebase.client`  | Idempotent REST calls with retry, jitter, offline-aware                      | Never raises into UI thread; surfaces typed errors       |
| `setup.wizard`     | First-run + drift-recovery flow                                              | Re-runnable; resumable; logs every step                  |

### Threading model

- **Main thread**: Tk/CTk UI only.
- **Worker pool**: Existing frame-processor pool (untouched).
- **`AsyncRunner`** (new in `dlc_pro/util/async_runner.py`): single background thread + queue used by license/subscription/firebase calls. UI subscribes via Tk `after()` callbacks.

---

## 1. GPU-First Execution Design

### 1.1 Detection cascade (cold-boot, < 200 ms)

1. **Hardware probe** — Win32 `SetupDiGetClassDevs` via WMI fallback to detect any GPU whose `VEN_10DE` (NVIDIA) appears. Avoids importing `torch` if no NVIDIA card present.
2. **Driver probe** — `nvidia-smi --query-gpu=driver_version,compute_cap --format=csv,noheader`. If absent or driver < required min (≥ 525.60 for CUDA 12.x), classify as `NO_DRIVER`.
3. **CUDA runtime probe** — Look for `cudart64_12.dll` and `cudnn64_9.dll` (or `cudnn64_8.dll` for cuDNN 8.9.7) in:
   - bundled `_runtime/cuda/bin/`
   - `%CUDA_PATH%/bin`
   - `PATH`
4. **ONNX provider probe** — `import onnxruntime; 'CUDAExecutionProvider' in onnxruntime.get_available_providers()`. **Performed last** because importing onnxruntime triggers DLL search; PATH must already be primed.
5. **Smoke test** — Allocate a 1×3×64×64 zero session and run a single inference of a tiny embedded ONNX model. If it raises, fall back. This is the *only* real "is CUDA usable" answer.

Each step writes a `GpuProbeResult` with `severity` ∈ {`OK`, `WARN`, `FAIL`} and a remedy string. The wizard renders this verbatim.

### 1.2 Modes

| Mode      | Trigger                                       | Behaviour                                              |
| --------- | --------------------------------------------- | ------------------------------------------------------ |
| `CUDA`    | Smoke test passes                             | Default. Banner "GPU: NVIDIA RTX xxx (CUDA 12.x)"      |
| `CPU`     | Smoke test fails or user override             | Yellow banner "Running on CPU — expect 5–15× slower"   |
| `BLOCKED` | License invalid AND CUDA available            | UI lock screen; processing disabled                    |

### 1.3 Packaging strategy for CUDA

**Decision: bundle the runtime, do not bundle the driver.**

The NVIDIA driver is the user's responsibility (EULA forbids redistribution). Everything *above* the driver — CUDA 12.8 runtime, cuDNN 8.9.7, cuBLAS — is bundled inside the installer payload at `app/_runtime/cuda/bin/`. Total ≈ 800 MB.

Why bundle:
- Eliminates "works on my machine" support burden.
- Avoids polluting user's CUDA installs (we never write to `%CUDA_PATH%`).
- Lets us ship a known-good cuDNN/cuBLAS pair without version drift.

PATH injection is handled by `gpu.bootstrap.prime_paths()` called *before* the first `import onnxruntime`. See `modules/dlc_pro/gpu/bootstrap.py`.

### 1.4 Fallback semantics

- Any `FAIL` step → enter CPU mode, display non-blocking toast with one-click "Retry GPU" that re-runs the cascade.
- User can force CPU in Settings → Execution Mode. Stored in registry under `HKCU\Software\DeepLiveCamPro\Execution\ForceCPU`.

---

## 2. License & Activation Design

### 2.1 Machine fingerprint

Composite, weighted, with tolerance for hardware drift:

| Component               | Source                                  | Weight |
| ----------------------- | --------------------------------------- | ------ |
| Motherboard UUID        | `wmic csproduct get UUID` / Win32 API   | 3      |
| CPU ID                  | `cpuid` instruction (vendor + signature)| 2      |
| Disk serial (system)    | Win32 `IOCTL_STORAGE_QUERY_PROPERTY`    | 2      |
| MAC address (primary)   | `GetAdaptersAddresses`                  | 1      |
| Windows MachineGUID     | `HKLM\SOFTWARE\Microsoft\Cryptography`  | 2      |

Fingerprint = SHA-256 of canonical JSON. Server stores both the canonical hash *and* per-component hashes. On activation re-check, ≥ 7/10 weighted components matching = same machine.

This prevents:
- Cloning a VM image to bypass binding (motherboard UUID changes).
- Single hardware swap (NIC, disk replacement) breaking activation.

### 2.2 Activation flow

```
Client                                     Cloud Function `activate`
------                                     --------------------------
collect fingerprint                        validate license_key exists
sign payload with embedded                 check status == 'unbound' OR
  pubkey-pinned cert                          (status == 'bound' AND fp matches)
POST /v1/activate ────────────────────►    set status='bound', fingerprint=fp
                                           write devices/{deviceId}
   ◄──────── { token, claims, expiry }     return signed JWT (custom token)
verify token signature                     (RS256, kid pinned in app)
DPAPI-encrypt + persist                    audit log entry
```

`token` is a Firebase custom token whose claims include `licenseId`, `plan`, `subExpiresAt`, `featureFlags[]`, signed `iat`/`exp` (24h). The desktop app exchanges it for an ID token that authorises Firestore reads.

### 2.3 Local secure storage

Path: `%LOCALAPPDATA%\DeepLiveCamPro\state.bin`

```
state.bin  =  DPAPI(scope=USER, payload=JSON{
                license_key,
                license_id,
                fingerprint,
                last_validated_at,
                claims_jwt,
                claims_jwt_exp,
                offline_grace_until
              })
```

Why DPAPI: per-user, OS-managed key, defeats trivial copy-paste between accounts. Combined with fingerprint binding, defeats full machine cloning.

### 2.4 Edge cases

| Scenario              | Detection                                    | Resolution                                                  |
| --------------------- | -------------------------------------------- | ----------------------------------------------------------- |
| OS reinstall          | DPAPI store missing, key was previously bound| User re-enters license; fingerprint matches → re-issue token|
| Hardware partial swap | ≥ 7/10 components match                      | Auto re-bind; audit logged                                  |
| Major hardware swap   | < 7/10 match                                 | Force user to "Move License" flow (1/3 free per year)       |
| Clock tampering       | `NtQuerySystemTime` vs server `Date` header  | If skew > 24h, force online check                            |
| Network outage        | Firebase unreachable                         | Run on signed cached claims while `now < offline_grace_until`|

---

## 3. Subscription & Feature Gating

### 3.1 Plans

| Plan       | Monthly | Features                                                            |
| ---------- | ------- | ------------------------------------------------------------------- |
| Free Trial | $0      | 7 days. CPU-only. Watermark. 480p export cap.                       |
| Pro        | $19     | GPU. Live webcam swap. 4K export. Face enhancer GPEN-512.            |
| Studio     | $49     | Pro + map_faces, batch queue, Hyperswap full-head, no watermark.    |

Plan name → set of feature flags is defined server-side in Firestore at `feature_flags/plans/{plan}` so we can change features without app updates.

### 3.2 Gating implementation

```python
# usage in any module
from modules.dlc_pro.subscription import require_feature, has_feature

@require_feature("hyperswap_full_head")
def run_hyperswap(...): ...

if has_feature("export_4k"):
    enable_4k_button()
```

`require_feature` checks the locally cached, server-signed claims. If feature absent: raises `FeatureLocked` and the UI catches → opens upgrade dialog.

### 3.3 Offline grace

- Cached claims signed by Cloud Functions with RS256 (kid pinned).
- Default grace: **72 hours** since `last_validated_at`.
- Heartbeat ping every 6h while online, refreshing the cache.
- Past grace and offline → strip every flag except `core_cpu`. Free tier still works.

---

## 4. Firebase Backend

### 4.1 Firestore schema

```
users/{uid}
  email, displayName, createdAt, stripeCustomerId

licenses/{licenseId}
  ownerUid, plan, status: "active"|"suspended"|"cancelled",
  createdAt, currentPeriodEnd,
  stripeSubscriptionId,
  fingerprintHash | null,    # bound device
  deviceId       | null,
  moveCountThisYear

devices/{deviceId}
  licenseId, fingerprintHash, fingerprintComponents{...},
  os, gpuName, appVersion, firstSeen, lastSeen

subscriptions/{stripeSubId}
  uid, licenseId, status, currentPeriodEnd, cancelAtPeriodEnd

activation_audit/{autoId}
  licenseId, deviceId, ip(hashed), action, ts, ok, reason

feature_flags/plans/{plan}
  features: ["gpu_inference","export_4k", ...]
```

### 4.2 Cloud Functions

- `POST /v1/activate` — bind license to device, return custom token.
- `POST /v1/heartbeat` — refresh claims, accept fingerprint drift report.
- `POST /v1/deactivate` — release device slot (rate-limited).
- `POST /v1/move-license` — force re-bind (rate-limited 1/4mo).
- `webhook /stripe` — fulfil subscription state changes.
- `scheduled cleanupExpiredActivations` (daily).

All endpoints validate App Check token + license signature; rate-limited per IP and per license.

### 4.3 Security rules (Firestore)

```
match /licenses/{lid} {
  allow read: if isOwner(resource.data.ownerUid) || hasCustomClaim('licenseId', lid);
  allow write: if false;   // only Cloud Functions write
}
match /devices/{did} {
  allow read: if hasCustomClaim('deviceId', did);
  allow write: if false;
}
```

Client never writes directly. All mutating paths go through Functions, which enforce server-side invariants (single-device binding, plan resolution, audit).

---

## 5. UI/UX Improvements

New CTk widgets in `modules/dlc_pro/ui/`:

- **`StatusBar`** (always visible, bottom of root) — GPU/CPU pill, plan pill, online/offline pill.
- **`ActivationDialog`** — license key entry, "Buy now" link, error states.
- **`PaywallDialog`** — opens when locked feature is invoked, shows plan comparison table + upgrade CTA → opens Stripe portal in default browser.
- **`SettingsPanel`** — Execution Mode (Auto/GPU/CPU), Diagnostics, License → Deactivate, Open logs folder.
- **`SetupWizard`** — first-run only: GPU check → model download → activation → done.
- **Loading/Error states** — central `Toast` and `Spinner` overlay; errors carry remediation text and a "Copy diagnostics" button.

The old `modules/ui.py` is *not* rewritten; we wrap its `init()` and add the status bar plus dialog hooks via `dlc_pro.ui.bootstrap_ui(root)`.

---

## 6. Step-by-Step Implementation Plan

| #  | Step                                                | Output                                          |
| -- | --------------------------------------------------- | ----------------------------------------------- |
| 1  | Add `modules/dlc_pro/` skeleton + utilities         | async runner, paths, logger                     |
| 2  | Implement GPU detector + bootstrap + smoke test     | `gpu/detector.py`, `gpu/bootstrap.py`           |
| 3  | Replace direct `import onnxruntime` paths to honour bootstrap | Patch `run.py`, `modules/core.py`         |
| 4  | Implement fingerprint, secure store, license manager| `license/*`                                     |
| 5  | Implement Firebase REST client + token cache        | `firebase/client.py`                            |
| 6  | Implement subscription + feature gate               | `subscription/*`                                |
| 7  | Build status bar, activation, paywall, settings UI  | `ui/*.py`                                       |
| 8  | Build first-run wizard + bootstrapper `launch.py`   | `setup/wizard.py`, `launch.py`                  |
| 9  | Author Firestore rules + Cloud Functions            | `backend/`                                      |
| 10 | Author PyInstaller spec, Inno Setup script, build PS| `build/`, `installer/`, `build_windows.ps1`     |
| 11 | Add CI: lint, test, signed build, draft GitHub release | `.github/workflows/release.yml`              |
| 12 | Smoke matrix: clean Win10/11 + RTX 30/40 + GTX 16   | manual QA checklist                             |

---

## 7. Folder & File Changes

```
Deep-Live-Cam-main/
├── docs/PRODUCTION_PLAN.md                       (NEW — this doc)
├── launch.py                                     (NEW — bootstrapper entrypoint)
├── run.py                                        (MODIFIED — delegates to launch.py)
├── requirements.txt                              (MODIFIED — pin cu128 stack)
├── modules/
│   └── dlc_pro/                                  (NEW)
│       ├── __init__.py
│       ├── paths.py
│       ├── async_runner.py
│       ├── logger.py
│       ├── gpu/
│       │   ├── detector.py
│       │   ├── bootstrap.py
│       │   └── smoke_model.onnx                  (tiny test model)
│       ├── license/
│       │   ├── fingerprint.py
│       │   ├── secure_store.py
│       │   └── manager.py
│       ├── subscription/
│       │   ├── gate.py
│       │   └── claims.py
│       ├── firebase/
│       │   ├── client.py
│       │   ├── pubkeys.py                        (pinned RS256 kid → pem)
│       │   └── config.py
│       ├── ui/
│       │   ├── status_bar.py
│       │   ├── activation_dialog.py
│       │   ├── paywall_dialog.py
│       │   ├── settings_panel.py
│       │   ├── setup_wizard.py
│       │   └── toast.py
│       └── setup/
│           ├── first_run.py
│           └── model_downloader.py
├── backend/
│   ├── firestore/firestore.rules
│   ├── functions/
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── src/index.ts
├── build/
│   ├── DeepLiveCamPro.spec                       (PyInstaller)
│   ├── runtime_hook_paths.py
│   └── version_info.txt
├── installer/
│   ├── DeepLiveCamPro.iss                        (Inno Setup)
│   └── prerequisites/
│       ├── vc_redist.x64.exe                     (placeholder)
│       └── README.txt                            (CUDA driver guidance)
├── build_windows.ps1
└── .github/workflows/release.yml
```

---

## 8. Security Considerations

1. **Code signing**: EV cert on `DeepLiveCamPro.exe`, the installer, and the auto-updater payload. Without it, Windows SmartScreen will eat conversion rate.
2. **Pinned RS256 keys**: hard-coded `kid → pem` map in `firebase/pubkeys.py`. Token verification refuses unknown kids; rotate via app update.
3. **Cert-pin Firebase host**: pin the Google Trust Services intermediate; allow rollover by pinning two.
4. **DPAPI scope = user**: prevents cross-account cloning; combined with fingerprint, prevents disk-image cloning.
5. **No secrets in the installer**: only public Firebase config + client API key (which is intentionally public per Firebase model). All authority is in custom claims signed by Functions.
6. **Anti-debug**: not added. Determined attackers will crack any Python app; we focus on preventing casual sharing, not state-actor reverse engineering. Time spent there is better spent on server-side enforcement (license server tracks misuse, suspends keys).
7. **Server-side enforcement**: every premium ONNX model URL is fetched with a short-lived signed URL gated by `hasCustomClaim('plan' in {pro,studio})`. Cracked clients can't get the model files.
8. **Telemetry minimisation**: we send fingerprint *hash* only, never raw components. IP is hashed before storage. Logs are user-readable in `%LOCALAPPDATA%\DeepLiveCamPro\logs`.
9. **Update integrity**: updater verifies SHA-256 + EV signature before swap; falls back to old binary on launch failure.

---

## 9. Future Improvements

- **AMD ROCm path** behind the same detector cascade (Windows ROCm story still nascent, defer).
- **DirectML fallback for non-NVIDIA discrete GPUs** (Intel Arc) before falling all the way to CPU.
- **Telemetry opt-in** (Sentry) for crash reports — defaults off, governed by EU privacy.
- **Per-feature trial unlocks** (3 free 4K exports/month) to soften paywall.
- **License pool / team plan** with seat allocation in Firestore.
- **macOS / Linux builds** reusing the same `dlc_pro` core. Substitute DPAPI with Keychain / libsecret.
- **Auto-update via Squirrel.Windows** so installer + app share update channel.
- **Background model pre-warming** during the wizard's last step.
