"""Canonical filesystem layout for Phantom-Cast Pro.

Centralised so installer, wizard, license store, and logger all agree.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "DeepLiveCamPro"


def _user_local_app_data() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(root) / APP_NAME


def install_root() -> Path:
    """Directory of the installed binary (or the source checkout in dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def runtime_dir() -> Path:
    """Bundled CUDA / cuDNN runtime payload."""
    return install_root() / "_runtime"


def cuda_bin_dir() -> Path:
    return runtime_dir() / "cuda" / "bin"


def models_dir() -> Path:
    """Where ONNX models live. Source tree uses ``models/``; installed builds
    keep the same relative path for compatibility with the existing code."""
    return install_root() / "models"


def state_dir() -> Path:
    p = _user_local_app_data()
    p.mkdir(parents=True, exist_ok=True)
    return p


def logs_dir() -> Path:
    p = state_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_file() -> Path:
    return state_dir() / "state.bin"


def claims_cache_file() -> Path:
    return state_dir() / "claims.cache"


def settings_file() -> Path:
    return state_dir() / "settings.json"


def first_run_marker() -> Path:
    return state_dir() / ".first_run_complete"
