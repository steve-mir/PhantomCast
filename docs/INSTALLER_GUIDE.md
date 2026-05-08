# Build & Distribute the Windows Installer

End-to-end steps to produce a signed, GPU-enabled `DeepLiveCamPro-Setup-X.Y.Z.exe`.

## Prerequisites (build machine, one-time)

1. Windows 10/11 x64 with the latest NVIDIA driver (≥ 525.60). Build does
   not strictly need a GPU but signing/QA does.
2. Python 3.11 (`py -3.11 --version`).
3. [Inno Setup 6](https://jrsoftware.org/isdl.php) installed at the default
   location.
4. Code-signing certificate (EV preferred). Either:
   - PFX file + password (CI: `secrets.SIGN_PFX_BASE64` + `SIGN_PFX_PASSWORD`)
   - Hardware token (use `signtool /n "Subject"`).
5. Stripe account + Firebase project provisioned (see
   `backend/firebase.json`).

## Local build

```powershell
git clone <repo>
cd Deep-Live-Cam-main
pwsh -File build_windows.ps1
```

Outputs:
- `dist\DeepLiveCamPro\DeepLiveCamPro.exe` and the onefolder runtime
- `dist\installer\DeepLiveCamPro-Setup-1.0.0.exe`

## What the installer does on the user's machine

1. Asks for admin rights.
2. Lays the onefolder build into `%ProgramFiles%\DeepLiveCamPro\`.
3. Drops VC++ 2015-2022 redistributable if missing.
4. Creates Start-menu + (optional) desktop shortcuts.
5. Launches `DeepLiveCamPro.exe`.

On first launch the bootstrapper (`launch.py` → `DeepLiveCamPro.exe`):
1. Configures the rotating log in `%LOCALAPPDATA%\DeepLiveCamPro\logs`.
2. Runs `prime_paths()` which prepends `_runtime/cuda/bin`, `torch/lib`, and
   each `nvidia/<pkg>/bin` to `PATH` *and* `os.add_dll_directory`.
3. Runs the GPU detection cascade (hardware → driver → DLLs → onnxruntime
   → smoke-test). Result is cached and surfaced in the StatusBar.
4. If the first-run marker is missing, opens the wizard: GPU check,
   model download, license activation.
5. Wraps `modules.ui.init` so the `StatusBar` docks into the existing UI.

## Signing

`build_windows.ps1 -Sign $true` will run `signtool sign` against both the
EXE and the installer. CI uses the same flags via `signtool` invoked from
the GitHub Actions workflow. Without code signing, Windows SmartScreen
will warn first-time users — it eats conversion rate.

## Auto-update (future)

Bake [Squirrel.Windows](https://github.com/Squirrel/Squirrel.Windows) into
the installer; change `OutputBaseFilename` to `Setup` and ship the
`releases/` directory next to it. The auto-updater verifies SHA-256 + EV
signature before swap; falls back to old binary on launch failure.
