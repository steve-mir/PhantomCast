"""Rotating file logger plus stderr mirror for Phantom-Cast Pro.

Used for everything in dlc_pro. Keeps logs in
``%LOCALAPPDATA%/DeepLiveCamPro/logs`` so the user can grab a diagnostics
bundle from the UI's "Copy diagnostics" button.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from modules.dlc_pro.paths import logs_dir


_CONFIGURED = False


def configure(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    file_h = RotatingFileHandler(
        logs_dir() / "dlc_pro.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_h.setFormatter(fmt)

    stream_h = logging.StreamHandler(sys.stderr)
    stream_h.setFormatter(fmt)

    root = logging.getLogger("dlc_pro")
    root.setLevel(level)
    root.addHandler(file_h)
    root.addHandler(stream_h)
    root.propagate = False


def get(name: str) -> logging.Logger:
    configure()
    return logging.getLogger(f"dlc_pro.{name}")
