"""Shared path constants for the Phantom-Cast project."""

import os

from modules.dlc_pro.paths import models_dir as _models_dir

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Resolved by ``modules.dlc_pro.paths.models_dir()`` so frozen installer
# builds redirect to a per-user writable directory instead of trying to
# write under %ProgramFiles%. See that function for the rationale.
MODELS_DIR = str(_models_dir())
