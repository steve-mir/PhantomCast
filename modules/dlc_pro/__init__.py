"""Phantom-Cast Pro: production layer.

GPU-first execution, license activation, subscription gating, Firebase backend,
and packaging glue. Imported by ``launch.py`` *before* the legacy
``modules.core`` so PATH/DLL bootstrap and licensing checks run first.
"""

__version__ = "1.0.0"
