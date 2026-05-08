#!/usr/bin/env python3
"""Legacy entry point — delegates to ``launch.py``.

Kept so existing scripts (run-cuda.bat, run-directml.bat, IDE configs) keep
working. Production builds ship ``launch.py`` as the EXE entry directly.
"""
import os
import sys

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from launch import main
    sys.exit(main())
