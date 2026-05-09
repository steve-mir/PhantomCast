# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Phantom-Cast Pro — GPU-first build.

Run from the repo root with:
    pyinstaller build/DeepLiveCamPro.spec --clean --noconfirm

The CUDA / cuDNN runtime is collected from the venv's pip-installed
nvidia-* wheels (and torch/lib) so the resulting onefolder build has
working GPU support out of the box.
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(os.getcwd())
APP_NAME = "DeepLiveCamPro"

# ---------- collect CUDA / cuDNN payload ----------
import site
SITE = next(p for p in site.getsitepackages() if Path(p, "site-packages").exists() or p.endswith("site-packages"))
SITE = Path(SITE)

binaries = []
datas = []

# torch/lib carries cuDNN, cublas, cuda-runtime DLLs.
torch_lib = SITE / "torch" / "lib"
if torch_lib.is_dir():
    for f in torch_lib.glob("*.dll"):
        binaries.append((str(f), "torch/lib"))

# pip nvidia-* packages (cuda-runtime, cudnn, cublas, cufft, …).
nvidia_root = SITE / "nvidia"
if nvidia_root.is_dir():
    for pkg in nvidia_root.iterdir():
        bin_dir = pkg / "bin"
        if bin_dir.is_dir():
            for f in bin_dir.glob("*.dll"):
                binaries.append((str(f), f"nvidia/{pkg.name}/bin"))

# onnxruntime data files (provider plugins).
datas += collect_data_files("onnxruntime")
datas += collect_data_files("insightface")
datas += collect_data_files("customtkinter")

# Project assets shipped alongside the EXE.
for rel in ("locales", "media", "modules/processors", "modules/dlc_pro/setup"):
    src = ROOT / rel
    if src.is_dir():
        for f in src.rglob("*"):
            if f.is_file():
                datas.append((str(f), str(f.parent.relative_to(ROOT))))

# Hidden imports that PyInstaller's static analysis misses.
hiddenimports = []
hiddenimports += collect_submodules("modules.processors.frame")
hiddenimports += collect_submodules("modules.dlc_pro")
hiddenimports += ["onnxruntime", "onnxruntime.capi", "tkinter", "customtkinter"]


a = Analysis(
    [str(ROOT / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[str(ROOT / "build" / "runtime_hook_paths.py")],
    excludes=[
        "matplotlib", "scipy.spatial.cKDTree",  # imported but not on hot path
        "tornado", "notebook",
    ],
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX breaks CUDA DLL load on some Windows builds
    console=False,        # GUI app
    icon=str(ROOT / "media" / "DLC.ico") if (ROOT / "media" / "DLC.ico").is_file() else None,
    version="build/version_info.txt" if (ROOT / "build" / "version_info.txt").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
