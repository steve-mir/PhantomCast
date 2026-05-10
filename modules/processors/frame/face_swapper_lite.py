"""Free-tier face-swap processor.

A deliberately reduced-quality wrapper around ``face_swapper``:

    * Internal swap runs at 320px on the long side. The result is upscaled
      back to the original frame size with nearest-neighbour interpolation,
      which produces a visible blocky / soft look.
    * Mouth mask, hair swap blending, and color-correction passes are
      forced off for the duration of the call.
    * Opacity is clamped to 1.0 so the swap is fully visible (no soft mix
      that would mask the resolution drop).

The "lite" pipeline exists so a free user always sees a strictly worse
output than a Premium user. Premium and 2-minute-preview routing is
handled in :mod:`modules.phantom_cast.core_bridge`; free callers should be
sent here in place of ``face_swapper``.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, List

import cv2
import numpy as np

import modules.globals
from modules.processors.frame import face_swapper as _full
from modules.typing import Face, Frame


NAME = "PCAST.FACE-SWAPPER-LITE"

# Long-side resolution the swap runs at. Anything below ~256 starts to
# fail face detection too often; 320 keeps detection reliable while still
# producing an obvious quality cliff vs. the 128/512-tile premium pipeline.
LITE_LONG_EDGE = 320


# ---------- module interface (mirrors face_swapper) ----------


def pre_check() -> bool:
    return _full.pre_check()


def pre_start() -> bool:
    return _full.pre_start()


@contextmanager
def _lite_globals() -> Iterator[None]:
    """Temporarily disable expensive post-processing for the swap call."""
    g = modules.globals
    saved = {
        "opacity": getattr(g, "opacity", 1.0),
        "mouth_mask": getattr(g, "mouth_mask", False),
        "show_mouth_mask_box": getattr(g, "show_mouth_mask_box", False),
        "mouth_mask_size": getattr(g, "mouth_mask_size", 0.0),
        "chin_mask_size": getattr(g, "chin_mask_size", 0.0),
        "color_correction": getattr(g, "color_correction", False),
        "hair_color": getattr(g, "hair_color", 0.0),
        "hair_texture": getattr(g, "hair_texture", 0.0),
    }
    try:
        g.opacity = 1.0
        g.mouth_mask = False
        g.show_mouth_mask_box = False
        g.mouth_mask_size = 0.0
        g.chin_mask_size = 0.0
        g.color_correction = False
        g.hair_color = 0.0
        g.hair_texture = 0.0
        yield
    finally:
        for k, v in saved.items():
            setattr(g, k, v)


def _downscale(frame: Frame) -> tuple[Frame, tuple[int, int]]:
    h, w = frame.shape[:2]
    long_edge = max(h, w)
    if long_edge <= LITE_LONG_EDGE:
        return frame, (w, h)
    scale = LITE_LONG_EDGE / float(long_edge)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    small = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return small, (w, h)


def _upscale(small: Frame, original_size: tuple[int, int]) -> Frame:
    w, h = original_size
    if small.shape[1] == w and small.shape[0] == h:
        return small
    # Nearest-neighbour on purpose — produces the blocky look that signals
    # "free tier" to the user and is cheaper than a smarter resampler.
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def process_frame(source_face: Face, temp_frame: Frame, target_face: Face = None) -> Frame:
    small, orig_size = _downscale(temp_frame)
    with _lite_globals():
        # target_face was detected against the full-res frame; bbox/landmarks
        # would be wrong at 320p, so re-detect inside the standard processor.
        out_small = _full.process_frame(source_face, small)
    return _upscale(out_small, orig_size)


def process_image(source_path: str, target_path: str, output_path: str) -> None:
    """Run the lite pipeline on a single image.

    Reads the target, swaps at low res, writes the upscaled result. We
    bypass ``face_swapper.process_image`` so the downscale/upscale shim
    actually applies.
    """
    target = cv2.imread(target_path)
    if target is None:
        return
    # Source face extraction: reuse the full module's helper path by
    # invoking process_frame which itself reads the source via globals.
    from modules.face_analyser import get_one_face
    source_img = cv2.imread(source_path)
    source_face = get_one_face(source_img) if source_img is not None else None
    if source_face is None:
        cv2.imwrite(output_path, target)
        return
    swapped = process_frame(source_face, target)
    cv2.imwrite(output_path, swapped)


def process_video(source_path: str, temp_frame_paths: List[str]) -> None:
    """Delegate to the full module's video driver.

    ``face_swapper.process_video`` reads each frame, runs ``process_frame``
    on the *current* module, and writes back. We override ``process_frame``
    above, so the lite path applies automatically when this module is the
    one being iterated.
    """
    _full.process_video(source_path, temp_frame_paths)


# Re-export the per-frame v2 entry too for parity with face_swapper. Some
# call sites bypass process_frame and reach for v2 directly when map_faces
# is on; we still want lite behaviour there.
def process_frame_v2(temp_frame: Frame, temp_frame_path: str = "") -> Frame:
    small, orig_size = _downscale(temp_frame)
    with _lite_globals():
        out_small = _full.process_frame_v2(small, temp_frame_path)
    return _upscale(out_small, orig_size)
