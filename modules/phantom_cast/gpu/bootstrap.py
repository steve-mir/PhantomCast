"""Inject CUDA / cuDNN DLL search paths *before* onnxruntime is imported.

Order of operations matters: once ``onnxruntime`` has been imported, its
DLL probe has already run and CUDAExecutionProvider may be missing even if
the runtime files exist on disk. Always call :func:`prime_paths` from
``launch.py`` before anything imports onnxruntime.

Lookup order (first hit wins):
    1. Bundled installer payload  -> install_root/_runtime/cuda/bin
    2. PyTorch's lib dir          -> site-packages/torch/lib
    3. pip nvidia-* packages      -> site-packages/nvidia/*/bin
    4. CUDA_PATH from environment -> %CUDA_PATH%/bin
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List

from modules.dlc_pro.logger import get
from modules.dlc_pro.paths import cuda_bin_dir, install_root

log = get("gpu.bootstrap")

_PRIMED = False


def _candidate_dirs() -> List[Path]:
    candidates: List[Path] = []

    # 1. Bundled runtime
    bundled = cuda_bin_dir()
    if bundled.is_dir():
        candidates.append(bundled)

    # 2. PyTorch lib (cuDNN bundled by pip torch wheels)
    for sp in _site_packages_dirs():
        torch_lib = sp / "torch" / "lib"
        if torch_lib.is_dir():
            candidates.append(torch_lib)

        # 3. pip nvidia-* packages — cublas, cudnn, cuda-runtime, nvjpeg, etc.
        nvidia_dir = sp / "nvidia"
        if nvidia_dir.is_dir():
            for pkg in nvidia_dir.iterdir():
                bin_dir = pkg / "bin"
                if bin_dir.is_dir():
                    candidates.append(bin_dir)

    # 4. System CUDA install
    cuda_path = os.environ.get("CUDA_PATH") or os.environ.get("CUDA_HOME")
    if cuda_path:
        bin_dir = Path(cuda_path) / "bin"
        if bin_dir.is_dir():
            candidates.append(bin_dir)

    return candidates


def _site_packages_dirs() -> List[Path]:
    seen: List[Path] = []
    for path in sys.path:
        p = Path(path)
        if p.name == "site-packages" and p.is_dir():
            seen.append(p)
    # Also check the source-tree venv when running from a dev checkout
    venv_sp = install_root() / "venv" / "Lib" / "site-packages"
    if venv_sp.is_dir():
        seen.append(venv_sp)
    return seen


def prime_paths() -> List[Path]:
    """Idempotent: prepend candidate dirs to PATH and register with the
    Win32 DLL loader. Returns the list of dirs that were activated."""
    global _PRIMED
    if _PRIMED:
        return []
    _PRIMED = True

    activated: List[Path] = []
    for d in _candidate_dirs():
        d_str = str(d)
        if d_str not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = d_str + os.pathsep + os.environ.get("PATH", "")
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(d_str)
            except (FileNotFoundError, OSError) as exc:
                log.warning("add_dll_directory(%s) failed: %s", d_str, exc)
                continue
        activated.append(d)

    log.info("primed %d CUDA dirs", len(activated))
    for d in activated:
        log.debug("  -> %s", d)
    return activated
