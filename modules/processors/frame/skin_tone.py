"""[FEATURE:SKIN-TONE] Skin complexion matching.

Shift the target's visible skin — face, nose, ears and neck, segmented by
the BiSeNet face parser — toward the complexion of the person in the
source image.

This is NOT a paint job. The transfer is statistical (Reinhard-style, in
LAB space): the *distribution* of the target's skin pixels is moved onto
the source's distribution, so the target keeps its own shading, specular
highlights, shadows and pore-level texture — it reads as the same person
lit by the same light, just with the source's complexion. A white→black
or black→white swap shifts luminance and chroma together while the
clamped std-ratio preserves local contrast.

Per call:
  1. Source-side, cached: BiSeNet parse of the source image → skin mask →
     LAB mean/std over skin pixels.
  2. Target-side: skin mask derived from the label map handed in by
     ``appearance.py`` (one shared parse per frame).
  3. Target skin LAB stats, EMA-smoothed across frames so live video
     doesn't flicker when the visible skin area changes.
  4. Clamped Reinhard transfer on skin pixels, blended by the
     "Skin tone match" slider and a feathered mask edge.

Disabled (zero-cost) when ``modules.globals.skin_tone_strength`` is 0.
Delete this file + the [FEATURE:SKIN-TONE]/[FEATURE:APPEARANCE] blocks to
fully remove the feature.
"""

from typing import Optional
import threading

import cv2
import numpy as np

import modules.globals
from modules import face_parser
from modules.typing import Frame

NAME = "PCAST.SKIN-TONE"

_MIN_SKIN_PIXELS = 400

# How hard the std-ratio may stretch/squash local contrast. Tight on L so
# texture/shading is never crushed (flat-lit sources) or blown out;
# looser on chroma.
_L_STD_CLAMP = (0.75, 1.6)
_AB_STD_CLAMP = (0.5, 2.2)

# EMA weight of the NEW frame's stats (live anti-flicker).
_EMA_NEW = 0.35

# Source-side cache — tiny (LAB stats only), keyed by source_path.
_source_cache: dict = {}
_cache_lock = threading.Lock()
_SOURCE_CACHE_LIMIT = 4

# Temporally smoothed target stats.
_smooth: dict = {"mean": None, "std": None, "shape": None}
_smooth_lock = threading.Lock()


def reset_state() -> None:
    """Drop temporal smoothing state. Call between videos / on stop."""
    with _smooth_lock:
        _smooth["mean"] = None
        _smooth["std"] = None
        _smooth["shape"] = None


def reset_source_cache() -> None:
    with _cache_lock:
        _source_cache.clear()


def _extract_source_stats(source_path: str) -> Optional[dict]:
    """LAB mean/std over the source image's skin pixels, cached by path."""
    with _cache_lock:
        cached = _source_cache.get(source_path)
        if cached is not None:
            return cached if cached else None
        if not source_path:
            return None
        src_img = cv2.imread(source_path)
        if src_img is None:
            print(f"[{NAME}] Could not read source: {source_path}")
            return None
        # Parse a head-centered crop so busy source backgrounds don't
        # pollute the skin statistics.
        from modules.face_analyser import get_one_face

        src_face = get_one_face(src_img)
        label = face_parser.parse_head(src_img, src_face)
        if label is None:
            print(f"[{NAME}] Parser returned None — model not loaded?")
            return None
        mask_bool = face_parser.class_mask(label, face_parser.SKIN_CLASS_IDS) > 127
        n = int(mask_bool.sum())
        if n < _MIN_SKIN_PIXELS:
            print(
                f"[{NAME}] Source has too few skin pixels ({n}). "
                "Use a source image with a clearly visible face."
            )
            # Negative-cache so we don't re-parse a bad source every frame.
            if len(_source_cache) >= _SOURCE_CACHE_LIMIT:
                _source_cache.clear()
            _source_cache[source_path] = {}
            return None

        src_lab = cv2.cvtColor(src_img, cv2.COLOR_BGR2LAB).astype(np.float32)
        skin = src_lab[mask_bool]
        stats = {
            "mean": skin.mean(axis=0).astype(np.float32),
            "std": (skin.std(axis=0) + 1e-6).astype(np.float32),
            "pixels": n,
        }
        if len(_source_cache) >= _SOURCE_CACHE_LIMIT:
            _source_cache.clear()
        _source_cache[source_path] = stats
        m = stats["mean"]
        print(
            f"[{NAME}] Source skin stats: pixels={n} "
            f"L̄={m[0]:.1f} ā={m[1]:.1f} b̄={m[2]:.1f}"
        )
        return stats


def _smoothed_target_stats(
    skin_lab: np.ndarray, frame_shape: tuple
) -> tuple:
    """EMA-smooth per-frame target stats so live output doesn't flicker."""
    mean = skin_lab.mean(axis=0)
    std = skin_lab.std(axis=0) + 1e-6
    with _smooth_lock:
        if _smooth["mean"] is None or _smooth["shape"] != frame_shape:
            _smooth["mean"] = mean
            _smooth["std"] = std
            _smooth["shape"] = frame_shape
        else:
            _smooth["mean"] = (1.0 - _EMA_NEW) * _smooth["mean"] + _EMA_NEW * mean
            _smooth["std"] = (1.0 - _EMA_NEW) * _smooth["std"] + _EMA_NEW * std
        return _smooth["mean"].copy(), _smooth["std"].copy()


def apply_skin_tone(frame: Frame, label: np.ndarray, strength: float) -> Frame:
    """Move the frame's skin complexion toward the source image's.

    Args:
        frame: BGR frame (post face-swap).
        label: BiSeNet label map for *frame* (from ``appearance.py``).
        strength: 0..1 blend toward the full match.

    Returns the frame unchanged on any failure so callers never break.
    """
    if strength <= 0.0:
        return frame
    source_path = getattr(modules.globals, "source_path", None)
    if not source_path:
        return frame
    src = _extract_source_stats(source_path)
    if src is None:
        return frame

    mask = face_parser.class_mask(label, face_parser.SKIN_CLASS_IDS)
    if int((mask > 127).sum()) < _MIN_SKIN_PIXELS:
        return frame

    # All math happens on the mask's bounding box (the head/neck area) —
    # converting a full 1080p frame to LAB per frame would dominate the
    # live budget for nothing.
    ys, xs = np.where(mask > 0)
    pad = max(4, int(min(frame.shape[:2]) * 0.01))
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(frame.shape[0], int(ys.max()) + 1 + pad)
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(frame.shape[1], int(xs.max()) + 1 + pad)
    region = frame[y0:y1, x0:x1]
    region_mask = mask[y0:y1, x0:x1]
    mask_bool = region_mask > 127

    target_lab = cv2.cvtColor(region, cv2.COLOR_BGR2LAB).astype(np.float32)
    skin_lab = target_lab[mask_bool]
    t_mean, t_std = _smoothed_target_stats(skin_lab, frame.shape[:2])

    # Clamped Reinhard transfer: mean moves fully, std ratio is bounded so
    # the target's own texture/shading survives extreme tone shifts.
    ratio = src["std"] / t_std
    ratio[0] = np.clip(ratio[0], *_L_STD_CLAMP)
    ratio[1:] = np.clip(ratio[1:], *_AB_STD_CLAMP)
    matched = (skin_lab - t_mean) * ratio + src["mean"]

    new_lab = target_lab
    new_lab[mask_bool] = matched
    np.clip(new_lab, 0, 255, out=new_lab)
    matched_bgr = cv2.cvtColor(new_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

    # Feathered composite: soft mask edge × user strength.
    feather_px = max(3, int(min(frame.shape[:2]) * 0.006))
    k = feather_px * 2 + 1
    soft = cv2.GaussianBlur(region_mask, (k, k), 0).astype(np.float32) * (
        strength / 255.0
    )
    soft_3 = soft[..., None]
    blended = soft_3 * matched_bgr.astype(np.float32) + (
        1.0 - soft_3
    ) * region.astype(np.float32)
    out = frame.copy()
    out[y0:y1, x0:x1] = np.clip(blended, 0, 255).astype(np.uint8)
    return out
