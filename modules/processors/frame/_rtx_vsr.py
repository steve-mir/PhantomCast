"""[FEATURE:RTX-UPSCALER] Experimental NVIDIA Video Effects SDK shim.

ctypes binding for the "SuperRes" effect of the NVIDIA Video Effects SDK
(a.k.a. Maxine VideoFX — the engine behind RTX Video Super Resolution).
This is the guide's "Choice A: ctypes shim" (no compilation).

STATUS: EXPERIMENTAL / UNTESTED — written against the public
``nvVFX.h`` / ``nvCVImage.h`` headers but never executed on RTX hardware
from this codebase. It is therefore strictly opt-in:

  1. Install the NVIDIA Video Effects SDK runtime (Windows, RTX GPU):
     https://www.nvidia.com/en-us/geforce/broadcasting/broadcast-sdk/resources/
  2. Set the environment variable ``PCAST_NVVFX_HOME`` to the install
     directory (the one containing ``NVVideoEffects.dll`` and ``models``),
     e.g.  C:\\Program Files\\NVIDIA Corporation\\NVIDIA Video Effects
  3. Enable the RTX Upscaler switch in the UI.

Without the env var, ``rtx_upscaler.py`` never imports this module and
everyone gets the Real-ESRGAN ONNX fallback. Any load/run failure here is
caught upstream and also falls back. Note that a struct-layout mismatch
in a future SDK could crash rather than raise — hence opt-in only.
"""

import ctypes
import os
import sys

import numpy as np

NAME = "PCAST.RTX-VSR"

# ---- nvCVImage.h constants -------------------------------------------------
NVCV_BGR = 5            # NvCVImage_PixelFormat
NVCV_U8 = 1             # NvCVImage_ComponentType
NVCV_CHUNKY = 0         # layout (interleaved)
NVCV_CPU = 0            # memSpace
NVCV_GPU = 1
NVCV_SUCCESS = 0


class NvCVImage(ctypes.Structure):
    # Field layout per nvCVImage.h (SDK 0.7.x). Enums are C ints.
    _fields_ = [
        ("width", ctypes.c_uint),
        ("height", ctypes.c_uint),
        ("pitch", ctypes.c_int),
        ("pixelFormat", ctypes.c_int),
        ("componentType", ctypes.c_int),
        ("pixelBytes", ctypes.c_ubyte),
        ("componentBytes", ctypes.c_ubyte),
        ("numComponents", ctypes.c_ubyte),
        ("planar", ctypes.c_ubyte),
        ("gpuMem", ctypes.c_ubyte),
        ("colorspace", ctypes.c_ubyte),
        ("reserved", ctypes.c_ubyte * 2),
        ("pixels", ctypes.c_void_p),
        ("deletePtr", ctypes.c_void_p),
        ("deleteProc", ctypes.c_void_p),
        ("bufferBytes", ctypes.c_ulonglong),
    ]


class RtxVsr:
    """Wraps one NvVFX SuperRes effect instance.

    The effect is (re)built lazily whenever the input size or scale
    changes, since SuperRes bakes resolution into its Load() call.
    """

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("NVIDIA Video Effects SDK is Windows-only")
        home = os.environ.get("PCAST_NVVFX_HOME", "")
        if not home or not os.path.isdir(home):
            raise OSError("PCAST_NVVFX_HOME not set or not a directory")
        dll_path = self._find_dll(home)
        # The SDK's dependent DLLs live next to the main one.
        os.add_dll_directory(os.path.dirname(dll_path))
        self._vfx = ctypes.WinDLL(dll_path)
        cv_dll = os.path.join(os.path.dirname(dll_path), "NVCVImage.dll")
        self._cv = ctypes.WinDLL(cv_dll) if os.path.isfile(cv_dll) else self._vfx
        self._model_dir = self._find_model_dir(home)

        self._handle = ctypes.c_void_p(None)
        self._src = NvCVImage()
        self._dst = NvCVImage()
        self._tmp = NvCVImage()
        self._built_for = (0, 0, 0.0)   # (w, h, scale)

    @staticmethod
    def _find_dll(home: str) -> str:
        for cand in ("NVVideoEffects.dll", os.path.join("bin", "NVVideoEffects.dll")):
            p = os.path.join(home, cand)
            if os.path.isfile(p):
                return p
        raise OSError(f"NVVideoEffects.dll not found under {home}")

    @staticmethod
    def _find_model_dir(home: str) -> bytes:
        for cand in ("models", os.path.join("bin", "models")):
            p = os.path.join(home, cand)
            if os.path.isdir(p):
                return p.encode("utf-8")
        return home.encode("utf-8")

    def _check(self, status: int, what: str) -> None:
        if status != NVCV_SUCCESS:
            raise RuntimeError(f"{what} failed (NvCV_Status={status})")

    def _destroy(self) -> None:
        if self._handle:
            try:
                self._vfx.NvVFX_DestroyEffect(self._handle)
            except Exception:
                pass
            self._handle = ctypes.c_void_p(None)
        for img in (self._src, self._dst, self._tmp):
            try:
                self._cv.NvCVImage_Dealloc(ctypes.byref(img))
            except Exception:
                pass

    def _build(self, w: int, h: int, scale: float) -> None:
        self._destroy()
        out_w, out_h = int(round(w * scale)), int(round(h * scale))

        handle = ctypes.c_void_p(None)
        self._check(
            self._vfx.NvVFX_CreateEffect(b"SuperRes", ctypes.byref(handle)),
            "NvVFX_CreateEffect",
        )
        self._handle = handle
        self._check(
            self._vfx.NvVFX_SetString(handle, b"ModelDir", self._model_dir),
            "NvVFX_SetString(ModelDir)",
        )
        # Mode 1 = strong enhancement (0 = weak).
        self._vfx.NvVFX_SetU32(handle, b"Mode", ctypes.c_uint(1))

        # GPU-resident src/dst images.
        self._check(
            self._cv.NvCVImage_Alloc(
                ctypes.byref(self._src), ctypes.c_uint(w), ctypes.c_uint(h),
                ctypes.c_int(NVCV_BGR), ctypes.c_int(NVCV_U8),
                ctypes.c_uint(NVCV_CHUNKY), ctypes.c_uint(NVCV_GPU),
                ctypes.c_uint(1),
            ),
            "NvCVImage_Alloc(src)",
        )
        self._check(
            self._cv.NvCVImage_Alloc(
                ctypes.byref(self._dst), ctypes.c_uint(out_w), ctypes.c_uint(out_h),
                ctypes.c_int(NVCV_BGR), ctypes.c_int(NVCV_U8),
                ctypes.c_uint(NVCV_CHUNKY), ctypes.c_uint(NVCV_GPU),
                ctypes.c_uint(1),
            ),
            "NvCVImage_Alloc(dst)",
        )
        self._check(
            self._vfx.NvVFX_SetImage(handle, b"SrcImage", ctypes.byref(self._src)),
            "NvVFX_SetImage(src)",
        )
        self._check(
            self._vfx.NvVFX_SetImage(handle, b"DstImage", ctypes.byref(self._dst)),
            "NvVFX_SetImage(dst)",
        )
        self._check(self._vfx.NvVFX_Load(handle), "NvVFX_Load")
        self._built_for = (w, h, scale)

    @staticmethod
    def _wrap_host(arr: np.ndarray) -> NvCVImage:
        """Wrap a contiguous HxWx3 uint8 BGR ndarray as a CPU NvCVImage."""
        img = NvCVImage()
        img.width = arr.shape[1]
        img.height = arr.shape[0]
        img.pitch = arr.strides[0]
        img.pixelFormat = NVCV_BGR
        img.componentType = NVCV_U8
        img.pixelBytes = 3
        img.componentBytes = 1
        img.numComponents = 3
        img.planar = NVCV_CHUNKY
        img.gpuMem = NVCV_CPU
        img.pixels = arr.ctypes.data_as(ctypes.c_void_p)
        img.bufferBytes = arr.nbytes
        return img

    def run(self, frame_bgr: np.ndarray, scale: float) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        if self._built_for != (w, h, scale):
            self._build(w, h, scale)
        if not frame_bgr.flags["C_CONTIGUOUS"]:
            frame_bgr = np.ascontiguousarray(frame_bgr)

        host_in = self._wrap_host(frame_bgr)
        self._check(
            self._cv.NvCVImage_Transfer(
                ctypes.byref(host_in), ctypes.byref(self._src),
                ctypes.c_float(1.0), None, None,
            ),
            "NvCVImage_Transfer(in)",
        )
        self._check(self._vfx.NvVFX_Run(self._handle, ctypes.c_int(0)), "NvVFX_Run")

        out = np.empty(
            (int(round(h * scale)), int(round(w * scale)), 3), dtype=np.uint8
        )
        host_out = self._wrap_host(out)
        self._check(
            self._cv.NvCVImage_Transfer(
                ctypes.byref(self._dst), ctypes.byref(host_out),
                ctypes.c_float(1.0), None, None,
            ),
            "NvCVImage_Transfer(out)",
        )
        return out

    def __del__(self) -> None:
        try:
            self._destroy()
        except Exception:
            pass
