# Deep-Live-Cam Pro — Developer Setup

End-to-end checklist for working on the Pro layer locally.

## 1. System prerequisites (Windows)

| Component        | Required version            | Source                                              |
| ---------------- | --------------------------- | --------------------------------------------------- |
| Python           | 3.11 (CPython, 64-bit)      | https://www.python.org/                             |
| NVIDIA driver    | ≥ 525.60                    | https://www.nvidia.com/Download/index.aspx          |
| Inno Setup 6     | latest                      | https://jrsoftware.org/isdl.php                     |
| Node.js          | 20 LTS (only for Functions) | https://nodejs.org/                                 |
| Firebase CLI     | latest                      | `npm i -g firebase-tools`                           |

CUDA 12.8 + cuDNN 8.9.7 are bundled into the installer via pip wheels —
nothing to install system-wide.

## 2. First-time clone

```powershell
git clone <repo>
cd Deep-Live-Cam-main
py -3.11 -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\pip install --extra-index-url https://download.pytorch.org/whl/cu128 `
    torch==2.5.1+cu128 torchvision==0.20.1+cu128
```

Run from source:

```powershell
.\venv\Scripts\python.exe launch.py
```

## 3. Firebase / Cloud Functions

```bash
cd backend
firebase login
firebase use <your-project-id>
cd functions && npm install
firebase functions:secrets:set DLC_SIGNING_PRIVATE_KEY DLC_SIGNING_KID \
    STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET
npm run build
firebase deploy --only functions,firestore,storage
```

After deploy: copy the project ID + Web API key into
`modules/dlc_pro/firebase/config.py` (or set `DLCPRO_FIREBASE_PROJECT` and
`DLCPRO_FIREBASE_API_KEY` in the build environment).

Generate the RS256 signing keypair once:

```bash
openssl genpkey -algorithm RSA -out signing.pem -pkeyopt rsa_keygen_bits:2048
openssl rsa -in signing.pem -pubout -out signing.pub
# private -> Functions secret DLC_SIGNING_PRIVATE_KEY
# public  -> modules/dlc_pro/firebase/config.py PINNED_PUBKEYS
```

Rotate by pinning two kids during the rollover window.

## 4. Build the installer

```powershell
pwsh -File build_windows.ps1 -Sign $true
```

See [INSTALLER_GUIDE.md](INSTALLER_GUIDE.md) for the full pipeline.

## 5. Smoke test matrix

Before signing a release, validate on at least:

- Clean Win11 x64 + RTX 30/40 series, no prior CUDA install
- Clean Win10 x64 + GTX 1660 (oldest CUDA-12-capable card)
- Clean Win11 x64 with no NVIDIA card (CPU-fallback path)
- Reinstall over previous version (state preservation)
- Uninstall (state cleared from `%LOCALAPPDATA%`)
