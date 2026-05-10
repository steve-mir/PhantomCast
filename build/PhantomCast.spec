# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Phantom Cast — slim Windows build.

Run from the repo root with:
    pyinstaller build/PhantomCast.spec --clean --noconfirm

GPU support: end users install NVIDIA's CUDA 12.x Runtime themselves.
Bundling cuDNN/cuBLAS/cuda-runtime + torch+cu128 (~3GB) blew past
GitHub's 2GB release-asset cap; onnxruntime-gpu finds the user-installed
CUDA at load time. torch is the CPU build (small blend ops in
face_swapper/face_enhancer fall back to CPU when torch.cuda.is_available()
is False).
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(os.getcwd())
APP_NAME = "PhantomCast"

# ---------- collect CUDA / cuDNN payload ----------
import site
SITE = next(p for p in site.getsitepackages() if Path(p, "site-packages").exists() or p.endswith("site-packages"))
SITE = Path(SITE)

binaries = []
datas = []

# CUDA bundling DROPPED. torch is now the CPU build (no torch/lib CUDA
# DLLs to grab) and nvidia-* pip packages are no longer in requirements.
# End users install CUDA 12.x Runtime themselves — onnxruntime-gpu picks
# it up at load time. Dropping ~3GB of bundled libs got us under the
# GitHub 2GB release-asset cap.
#
# TensorRT provider DLLs (onnxruntime_providers_tensorrt.dll + nvinfer_*)
# may still be pulled in transitively by PyInstaller's onnxruntime hook;
# they reference deps we never had, so we filter them post-Analysis below.

# onnxruntime data files (provider plugins).
datas += collect_data_files("onnxruntime")
datas += collect_data_files("insightface")
datas += collect_data_files("customtkinter")

# Project assets shipped alongside the EXE.
for rel in ("locales", "media", "modules/processors", "modules/phantom_cast/setup"):
    src = ROOT / rel
    if src.is_dir():
        for f in src.rglob("*"):
            if f.is_file():
                datas.append((str(f), str(f.parent.relative_to(ROOT))))

# Hidden imports that PyInstaller's static analysis misses.
hiddenimports = []
hiddenimports += collect_submodules("modules.processors.frame")
hiddenimports += collect_submodules("modules.phantom_cast")
hiddenimports += ["onnxruntime", "onnxruntime.capi", "tkinter", "customtkinter"]


# TensorRT bits the onnxruntime hook may pull transitively. Filtered
# post-Analysis (see below).
TENSORRT_DLL_PATTERNS = (
    "onnxruntime_providers_tensorrt",
    "nvinfer_",
    "nvonnxparser_",
)


a = Analysis(
    [str(ROOT / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[str(ROOT / "build" / "runtime_hook_paths.py")],
    excludes=[
        # plotting/notebook stack — never used at runtime
        "matplotlib", "scipy.spatial.cKDTree",
        "tornado", "notebook", "IPython", "jupyter",
        "pytest", "pytest_runner", "nose",

        # NSFW filter dropped from this build (see modules/predicter.py
        # and requirements.txt for context). ~1.1GB savings.
        "tensorflow", "tensorboard", "keras",
        "opennsfw2", "gdown",

        # torch training-only modules — inference doesn't need them.
        # ~300MB combined.
        "torch.distributed", "torch.testing", "torch._inductor",
        "torch._dynamo", "torch.fx", "torch.onnx",
        "torch.utils.benchmark", "torch.utils.tensorboard",
        "torch.profiler",

        # torchvision sub-packages we don't use
        "torchvision.datasets", "torchvision.models",
    ],
    cipher=None,
    noarchive=False,
)

# Strip TensorRT-related binaries pulled in transitively by onnxruntime's
# PyInstaller hook. They reference deps we don't bundle.
def _is_tensorrt(entry):
    name = entry[0].lower() if isinstance(entry, tuple) else str(entry).lower()
    return any(p in name for p in TENSORRT_DLL_PATTERNS)


_before = len(a.binaries)
a.binaries = [b for b in a.binaries if not _is_tensorrt(b)]
print(f"[spec] stripped {_before - len(a.binaries)} TensorRT-related binaries")

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
    icon=str(ROOT / "media" / "PhantomCast.ico") if (ROOT / "media" / "PhantomCast.ico").is_file() else None,
    version=str(ROOT / "build" / "version_info.txt") if (ROOT / "build" / "version_info.txt").is_file() else None,
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
