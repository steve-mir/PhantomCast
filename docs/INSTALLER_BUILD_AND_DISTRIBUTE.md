# Phantom Cast — Building & Distributing the Windows Installer

End-to-end runbook for cutting a release of `PhantomCast-Setup-X.Y.Z.exe`,
signing it, uploading it to a CDN, and pointing customers at it. Aimed at
the person responsible for releases — not the development team.

**Product name:** Phantom Cast
**Binary name:** `PhantomCast.exe`
**Installer name:** `PhantomCast-Setup-X.Y.Z.exe`
**Internal AppId:** `{4A4F8E6C-4B0F-4F4D-9A76-DLC-PRO-2026}` (do **not** change between minor versions — Inno Setup uses it to upgrade in place)

> ⚠ The repo currently uses the working title **DeepLiveCamPro** in code.
> Before the first public release, do a single global rename pass:
> `DeepLiveCamPro` → `PhantomCast`, `Deep-Live-Cam Pro` → `Phantom Cast`,
> `dlc_pro` → `phantomcast`. The build pipeline below assumes the rename
> is done; commands work identically for either name.

---

## 0. One-time setup (release machine)

You only have to do this once per release engineer.

### 0.1 Install build tools

| Tool | Version | Source |
|---|---|---|
| Windows 10/11 x64 | 22H2+ | — |
| Python | 3.11.x (64-bit) | https://www.python.org/downloads/windows/ |
| PowerShell | 7+ | `winget install Microsoft.PowerShell` |
| Inno Setup 6 | latest | https://jrsoftware.org/isdl.php |
| Windows SDK signtool | 10.0.22621+ | https://developer.microsoft.com/windows/downloads/windows-sdk/ |
| Git for Windows | latest | https://git-scm.com/ |
| AWS CLI / Cloudflare CLI | for upload | depends on your CDN choice |

Check after install:

```powershell
py -3.11 --version
pwsh --version
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /?
& "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe" sign /?
```

### 0.2 Get a code-signing certificate

You **need** code signing. Without it, Microsoft SmartScreen will warn
every first-time user with "Windows protected your PC", and your
conversion rate will drop 60-80%.

| Cert type | Cost / year | SmartScreen reputation | Notes |
|---|---|---|---|
| OV (Organization Validation) | $200-400 | Builds slowly (weeks-months of installs) | Cheaper, but bad first-impression for new product |
| **EV (Extended Validation)** | $300-600 | **Trusted immediately** | **Recommended.** Issued on hardware token (USB) or HSM. |

Recommended issuers: **SSL.com EV CodeSigning**, **DigiCert**,
**Sectigo**. EV certs ship on a YubiKey-style USB token; you need to
plug it in to sign.

### 0.3 Provision Firebase project + secrets

Follow `docs/SETUP.md` §3 to deploy Cloud Functions and Firestore rules.
Keep these secrets out of the repo:

```
DLC_SIGNING_PRIVATE_KEY    # RS256 PEM, signs claims JWTs
DLC_SIGNING_KID            # e.g. "phantomcast-2026-01"
STRIPE_SECRET_KEY          # sk_live_...
STRIPE_WEBHOOK_SECRET      # whsec_...
```

Pin the matching public key in `modules/dlc_pro/firebase/config.py:PINNED_PUBKEYS`
**before** building. Forgetting this means clients reject every claims token.

---

## 1. Versioning

Use semantic versioning for the user-facing version, and a build number
for internal traceability.

```
1.4.2          public version (semver)
1.4.2.387      version+build  (387 = monotonic build counter)
```

Update **all four** of these in lockstep:

| File | Field |
|---|---|
| `modules/dlc_pro/__init__.py` | `__version__ = "1.4.2"` |
| `installer/PhantomCast.iss` | `#define MyAppVersion "1.4.2"` |
| `build/version_info.txt` | `filevers=(1,4,2,387)` and `prodvers=(1,4,2,387)` |
| `backend/functions/package.json` | `"version": "1.4.2"` (only matters for Functions deploy) |

A 30-second pre-flight script catches drift:

```powershell
# scripts\check-version.ps1
$expected = "1.4.2"
$pyVer = (Get-Content modules\dlc_pro\__init__.py | Select-String '__version__').ToString()
$issVer = (Get-Content installer\PhantomCast.iss | Select-String 'MyAppVersion').ToString()
if (-not ($pyVer -match $expected)) { throw "py version drift" }
if (-not ($issVer -match $expected)) { throw "iss version drift" }
```

Run it from `build_windows.ps1` before doing anything destructive.

---

## 2. Build the installer

### 2.1 Standard build (one command)

From the repo root in PowerShell 7:

```powershell
pwsh -File build_windows.ps1
```

What it does (~15-25 minutes on a modern machine):

1. Creates / reuses `venv\` with Python 3.11
2. Installs pinned `requirements.txt` + `torch==2.5.1+cu128`
3. Runs PyInstaller against `build/PhantomCast.spec` → `dist\PhantomCast\`
4. Compiles `installer/PhantomCast.iss` with Inno Setup → `dist\installer\PhantomCast-Setup-X.Y.Z.exe`

Output:

```
dist\
├── PhantomCast\                           ← ~2.8 GB onefolder bundle
│   ├── PhantomCast.exe
│   ├── _runtime\cuda\bin\               (CUDA 12.8 + cuDNN 8.9.7 DLLs)
│   └── ...
└── installer\
    └── PhantomCast-Setup-1.4.2.exe        ← ~1.4 GB compressed (LZMA2 ultra64)
```

### 2.2 Build with signing

Plug in your EV USB token, then:

```powershell
pwsh -File build_windows.ps1 -Sign $true -CertSubject "Your Company LLC"
```

The script signs both `PhantomCast.exe` and the installer. Verify:

```powershell
signtool verify /pa /v dist\installer\PhantomCast-Setup-1.4.2.exe
```

Expected output: `Successfully verified` and `EV` in the description.

### 2.3 CI build (GitHub Actions)

Tag the release commit and push:

```bash
git tag v1.4.2
git push origin v1.4.2
```

`.github/workflows/release.yml` will:
1. Spin up a `windows-2022` runner
2. Build the installer
3. Sign with the PFX in `secrets.SIGN_PFX_BASE64` (base64-encoded PFX file)
4. Upload to a draft GitHub Release

Required GitHub secrets:

```
SIGN_PFX_BASE64       # base64-encoded PFX (only OV certs — EV needs HW token)
SIGN_PFX_PASSWORD     # PFX password
FIREBASE_PROJECT      # forwarded to build env
FIREBASE_API_KEY      # forwarded to build env
```

> EV certs cannot be used with PFX from CI because the private key lives
> on a hardware token. For EV-signed releases, run `build_windows.ps1`
> on a release-engineer machine with the token plugged in, then upload
> the artifact to GitHub Releases manually.

---

## 3. Smoke-test the installer

**Do not skip this.** A bad release breaks every user simultaneously.

Test on at least three clean VMs (use Hyper-V checkpoints to reset):

| VM | GPU | Expected |
|---|---|---|
| Win11 x64 + RTX 40-series | yes | Wizard → green GPU pill → live preview works |
| Win10 x64 + GTX 1660 | yes | GPU pill green; if smoke test fails on old card, falls back to CPU |
| Win11 x64 + integrated graphics | no | Yellow CPU pill, processing works (slow), no crash |

For each VM:

1. Run the installer with default options
2. Launch from the Start-menu shortcut
3. Complete the first-run wizard (skip license activation, take free trial)
4. Run a 10-second face-swap
5. Open Settings → Diagnostics → "Copy diagnostics" — verify the JSON is sane
6. Uninstall via Apps & Features and confirm `%LOCALAPPDATA%\PhantomCast` is wiped

Also test the upgrade path:

7. Install version N-1 first, activate a test license, run a swap
8. Install version N over the top **without** uninstalling — license should remain active, GPU detection should re-run silently

Keep a short Notion / Linear smoke-test checklist and tick it before every public release.

---

## 4. Hosting the installer

You need somewhere customers can download from. Pick **one** primary
host plus **one** mirror.

### 4.1 Primary host options

| Host | $/GB egress | TTFB worldwide | Pros | Cons |
|---|---|---|---|---|
| **Cloudflare R2** | $0 (egress free) | excellent (Cloudflare PoPs) | **Recommended.** Free egress = predictable cost regardless of viral spikes. S3-compatible API. | Cloudflare account + custom domain setup |
| Bunny.net Storage + CDN | ~$0.01 | excellent | Cheap, simple, good dashboard | Pay per GB egress |
| AWS S3 + CloudFront | ~$0.085 | excellent | Bulletproof | Most expensive at scale |
| Backblaze B2 + Cloudflare CDN | $0 (B2↔Cloudflare bandwidth alliance) | good | Cheap | More moving parts |
| GitHub Releases | $0 | OK | Free, integrates with `gh` CLI | 2 GB per asset; throttled for huge spikes; not great for paid product |

For a SaaS product priced at $19-49/month, **Cloudflare R2 + a custom
domain** is the sane default: zero egress cost means a viral TikTok demo
won't bankrupt you.

### 4.2 Cloudflare R2 setup (recommended path)

```bash
# 0. one-time
npm i -g wrangler
wrangler login

# 1. create bucket
wrangler r2 bucket create phantomcast-releases

# 2. upload installer
wrangler r2 object put phantomcast-releases/win/1.4.2/PhantomCast-Setup-1.4.2.exe \
    --file dist/installer/PhantomCast-Setup-1.4.2.exe \
    --content-type application/x-msdownload

# 3. upload manifest (used by auto-updater + website "latest" link)
wrangler r2 object put phantomcast-releases/win/latest.json \
    --file dist/installer/latest.json \
    --content-type application/json
```

Connect the bucket to a custom subdomain via Cloudflare Dashboard →
R2 → bucket → Settings → Custom Domains → add `dl.phantomcast.app`.
Now the installer URL is:

```
https://dl.phantomcast.app/win/1.4.2/PhantomCast-Setup-1.4.2.exe
```

### 4.3 Generate the release manifest

Auto-update + the website's "Download" button both consume `latest.json`:

```json
{
  "product": "PhantomCast",
  "channel": "stable",
  "version": "1.4.2",
  "build": 387,
  "released_at": "2026-05-06T18:00:00Z",
  "platforms": {
    "win-x64": {
      "url": "https://dl.phantomcast.app/win/1.4.2/PhantomCast-Setup-1.4.2.exe",
      "size_bytes": 1487654321,
      "sha256": "<hex>",
      "sig": "<base64 RSA-PSS sig over sha256, kid=phantomcast-2026-01>",
      "min_version_for_delta": "1.4.0"
    }
  },
  "release_notes_url": "https://phantomcast.app/changelog/1.4.2",
  "min_supported_os": "10.0.19041"
}
```

Generate it as part of the build:

```powershell
# scripts\write-manifest.ps1
$exe = "dist\installer\PhantomCast-Setup-$Version.exe"
$sha = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $exe).Length
@{
  product = "PhantomCast"
  channel = "stable"
  version = $Version
  build   = $Build
  released_at = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ" -AsUTC)
  platforms = @{
    "win-x64" = @{
      url = "https://dl.phantomcast.app/win/$Version/PhantomCast-Setup-$Version.exe"
      size_bytes = $size
      sha256 = $sha
    }
  }
} | ConvertTo-Json -Depth 5 | Out-File dist\installer\latest.json -Encoding utf8NoBOM
```

The signature line is critical for auto-update integrity. Sign it with
the same RS256 keypair you use for license claims:

```bash
openssl dgst -sha256 -sign signing.pem dist/installer/latest.json | base64
```

Embed the result in `latest.json` under `platforms.win-x64.sig`.

### 4.4 Mirror

Even with Cloudflare R2 you want a mirror for the rare R2 outage. The
cheapest option: **GitHub Releases**. After R2 upload, also:

```bash
gh release create v1.4.2 dist/installer/PhantomCast-Setup-1.4.2.exe \
    --title "Phantom Cast 1.4.2" \
    --notes-file CHANGELOG-1.4.2.md
```

Document both URLs in the release-notes page; the website "Download"
button always points at R2, but support can hand out the GitHub URL if
R2 is unreachable.

---

## 5. Auto-update channel

The installed app polls `latest.json` once on launch and once every 24h.
If `version` exceeds the running `__version__`, it shows an "Update
available" toast → on click, downloads in background, verifies SHA-256
+ RSA-PSS signature, then runs the new installer with `/SILENT
/CLOSEAPPLICATIONS=force`.

Implementation lives in `modules/dlc_pro/setup/auto_update.py` (to be
written in v1.1; v1.0 ships without auto-update and asks users to
download manually). Until then, the website should show the "Update
available" banner via the same `latest.json`.

Channel layout in R2:

```
phantomcast-releases/
├── win/
│   ├── latest.json                            # stable channel
│   ├── beta.json                              # beta channel (opt-in)
│   ├── 1.4.2/PhantomCast-Setup-1.4.2.exe
│   ├── 1.4.1/PhantomCast-Setup-1.4.1.exe
│   └── 1.4.0/PhantomCast-Setup-1.4.0.exe
```

Keep the last **three** stable releases live so users on N-1 / N-2 can
still recover from a bad N. Roll older versions to a `archive/` prefix
and exclude that prefix from CDN cache.

---

## 6. Cache & purge strategy

Set Cloudflare cache rules:

| Path | Cache TTL | Notes |
|---|---|---|
| `/win/*.exe` | 1 year | content-addressed by version, never changes |
| `/win/latest.json` | 60 seconds | needs to flip fast on a hot release |
| `/win/beta.json` | 60 seconds | same |

After uploading a new release, purge `latest.json` so customers see it
immediately:

```bash
curl -X POST "https://api.cloudflare.com/client/v4/zones/$ZONE/purge_cache" \
     -H "Authorization: Bearer $CF_API_TOKEN" \
     -H "Content-Type: application/json" \
     --data '{"files":["https://dl.phantomcast.app/win/latest.json"]}'
```

---

## 7. Release checklist

Copy this into your release ticket every time:

```
Pre-release
[ ] Bump __version__, .iss MyAppVersion, version_info.txt to X.Y.Z
[ ] Run scripts\check-version.ps1
[ ] Update CHANGELOG.md
[ ] Verify Firebase Functions deployed and PINNED_PUBKEYS up-to-date
[ ] git tag vX.Y.Z

Build
[ ] pwsh -File build_windows.ps1 -Sign $true
[ ] signtool verify /pa /v dist\installer\PhantomCast-Setup-X.Y.Z.exe   → Successfully verified
[ ] Compute SHA-256 and store in latest.json
[ ] Sign latest.json with RS256 key

Smoke test (3 clean VMs)
[ ] Win11 + NVIDIA discrete GPU
[ ] Win10 + older GPU (compute_cap 7.5)
[ ] Win11 + no NVIDIA (CPU fallback)
[ ] Upgrade-in-place from previous version

Distribute
[ ] wrangler r2 object put .../win/X.Y.Z/PhantomCast-Setup-X.Y.Z.exe
[ ] wrangler r2 object put .../win/latest.json
[ ] gh release create vX.Y.Z dist/installer/...exe
[ ] Cloudflare cache purge for /win/latest.json

Post-release
[ ] Verify https://dl.phantomcast.app/win/latest.json returns the new version
[ ] Download from incognito browser, run, verify SmartScreen does NOT warn (EV cert)
[ ] Tweet / email announcement
[ ] Monitor Firebase activation rate for 24h — if drop > 20% vs N-1 baseline, roll back
```

---

## 8. Rollback procedure

If telemetry or support tickets show a bad release:

1. Edit `latest.json` to point `version` and `url` back to the previous good version.
2. Upload it to R2 (overwriting the current file).
3. Purge `latest.json` from Cloudflare cache.
4. New downloads now serve the previous version. Already-installed bad clients keep running until they hit auto-update (next launch).

```powershell
# rollback.ps1 vX.Y.Z-1
param([string]$Version)
$prev = "dist\installer\latest.$Version.json"
wrangler r2 object put phantomcast-releases/win/latest.json --file $prev
# purge as in §6
```

Always keep the previous `latest.X.Y.Z.json` file checked into a private
`releases/` repo so rollback is a one-command operation.

---

## 9. Cost projection

For a $19/month product with 2 GB installer at modest scale:

| Users / month | Downloads / month | R2 storage | R2 egress | Total |
|---|---|---|---|---|
| 1,000 | 1,200 (incl re-installs + updates) | 30 GB | ~2.4 TB | $0.45/mo (storage only) |
| 10,000 | 12,000 | 30 GB | ~24 TB | $0.45/mo |
| 100,000 | 120,000 | 30 GB | ~240 TB | $0.45/mo |

Cloudflare R2 egress is free up to any practical limit. Storage at
$0.015/GB/month means even keeping 30 historical versions costs <$15/mo.

**Code-signing certificate** is the only real release-side recurring
cost: $400-600/year for an EV cert.

---

## 10. Common failure modes (and the fix)

| Symptom | Cause | Fix |
|---|---|---|
| SmartScreen "Windows protected your PC" | Cert not yet trusted (OV) or not signed | Use EV cert; verify `signtool verify /pa` succeeds before publishing |
| User reports "0xc000007b" on launch | VC++ runtime missing | Installer runs `VC_redist.x64.exe` automatically — rebuild with refreshed redist |
| GPU detected, but smoke test fails | Driver too old (< 525.60) for CUDA 12 | Wizard's remedy text already points users at NVIDIA driver download |
| Install hangs on "Extracting files..." | Antivirus scanning each of 8000+ DLLs | Document workaround: temporarily disable real-time scan during install |
| `nvcuda.dll not found` from a frozen build | PyInstaller missed a transitive nvidia DLL | Add the missing DLL pattern to `build/PhantomCast.spec` `binaries` list |
| First-run wizard never opens | First-run marker exists from previous install | Document: delete `%LOCALAPPDATA%\PhantomCast\.first_run_complete` or use Settings → "Re-run setup" |

Track each of these in the support knowledge base and link from the
website's `/help` page.
