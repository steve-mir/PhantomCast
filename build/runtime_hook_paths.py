"""PyInstaller runtime hook — runs *first* in every frozen process.

Order matters: this hook needs to extend PATH and register DLL directories
*before* PyInstaller's own loader pulls in onnxruntime / torch / nvidia
extensions. We therefore avoid importing phantom_cast here (it depends on
logging which depends on filesystem locations); we duplicate the minimum
search-path priming inline.
"""
import os
import sys


def _prime():
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    candidates = [
        os.path.join(base, "_runtime", "cuda", "bin"),
        os.path.join(base, "torch", "lib"),
    ]
    nvidia_root = os.path.join(base, "nvidia")
    if os.path.isdir(nvidia_root):
        for pkg in os.listdir(nvidia_root):
            d = os.path.join(nvidia_root, pkg, "bin")
            if os.path.isdir(d):
                candidates.append(d)

    cur = os.environ.get("PATH", "")
    for d in candidates:
        if not os.path.isdir(d):
            continue
        if d not in cur.split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + cur
            cur = os.environ["PATH"]
        if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass


_prime()
