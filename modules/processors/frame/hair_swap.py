"""Hair color + texture transfer.

Approach: do NOT replace the target's hair geometry. Parse the target's
existing hair via BiSeNet, then shift its color and (optionally) its
luminance high-frequency variance toward statistics extracted once from
the source image. Because we operate on the target's actual hair pixels,
the result moves with the head, smiles when the user smiles, and matches
the target's lighting — qualities a paste-warp can never produce.

Pipeline (per call):
  1. Source-side, cached: BiSeNet → hair mask → LAB mean/std on hair
     pixels + std of high-frequency luminance.
  2. Target-side: BiSeNet → hair mask. Throttleable so live webcam
     amortizes the ~10-15 ms parser over multiple frames.
  3. Reinhard LAB transfer restricted to target hair pixels, scaled by
     the Hair Color slider.
  4. Optional luminance high-frequency variance match scaled by the Hair
     Texture slider, also restricted to target hair pixels.
  5. Feathered hair mask used for the final composite so the hair/skin
     edge dissolves cleanly.

Disabled by setting both sliders to 0. Skipped in many_faces / map_faces
modes today (the per-frame parse covers all hair regardless of which
face owns it, but those modes haven't been validated).
"""

from typing import Optional
import threading

import cv2
import numpy as np

import modules.globals
from modules.face_parser import get_hair_mask
from modules.typing import Frame, Face

NAME = "PCAST.HAIR-SWAP"

# Source-side cache. Just LAB stats — tiny memory footprint. Keyed on
# source_path so swapping source images naturally re-extracts.
_source_cache: dict = {}
_cache_lock = threading.Lock()
_SOURCE_CACHE_LIMIT = 4

# Target-side cache. The parser is the dominant cost (~10-15 ms on M2
# CPU); throttling lets live webcam reuse the mask across N frames.
_target_cache: dict = {
    "mask": None,
    "frame_shape": None,
    "counter": 0,
    "interval": 1,  # set per-call via parse_every
}
_target_lock = threading.Lock()


def reset_state() -> None:
    """Drop the target-side cache. Call between videos / on stop."""
    with _target_lock:
        _target_cache["mask"] = None
        _target_cache["frame_shape"] = None
        _target_cache["counter"] = 0


def reset_source_cache() -> None:
    """Drop cached source stats. Cache is path-keyed so this is mostly
    hygiene — new sources naturally don't reuse old entries."""
    with _cache_lock:
        _source_cache.clear()


def _extract_source_stats(source_path: str) -> Optional[dict]:
    """Extract and cache LAB hair statistics from the source image."""
    with _cache_lock:
        cached = _source_cache.get(source_path)
        if cached is not None:
            return cached
        if not source_path:
            return None
        src_img = cv2.imread(source_path)
        if src_img is None:
            print(f"[{NAME}] Could not read source: {source_path}")
            return None
        hair_mask = get_hair_mask(src_img, include_hat=True)
        if hair_mask is None:
            print(f"[{NAME}] Parser returned None — model not loaded?")
            return None
        mask_bool = hair_mask > 127
        hair_pixels = int(mask_bool.sum())
        if hair_pixels < 200:
            print(
                f"[{NAME}] Source has too few hair pixels ({hair_pixels}). "
                "Try a different source image, or change HAIR_CLASS_ID in "
                "modules/face_parser.py."
            )
            return None

        src_lab = cv2.cvtColor(src_img, cv2.COLOR_BGR2LAB).astype(np.float32)
        hair_lab = src_lab[mask_bool]  # (N, 3)
        lab_mean = hair_lab.mean(axis=0)
        lab_std = hair_lab.std(axis=0) + 1e-6

        # High-frequency luminance variance. Captures hair "texture" — the
        # fine variation between strands/sheen. Computed only on hair
        # pixels so background gradient doesn't pollute the stat.
        L = src_lab[..., 0]
        sigma_px = max(2, min(src_img.shape[:2]) // 50)
        k = sigma_px * 2 + 1
        L_low = cv2.GaussianBlur(L, (k, k), 0)
        L_high = L - L_low
        L_high_std = float(L_high[mask_bool].std() + 1e-6)

        stats = {
            "lab_mean": lab_mean.astype(np.float32),
            "lab_std": lab_std.astype(np.float32),
            "L_high_std": L_high_std,
            "hair_pixels": hair_pixels,
        }
        if len(_source_cache) >= _SOURCE_CACHE_LIMIT:
            _source_cache.clear()
        _source_cache[source_path] = stats
        print(
            f"[{NAME}] Source hair stats: pixels={hair_pixels} "
            f"L̄={lab_mean[0]:.1f} ā={lab_mean[1]:.1f} b̄={lab_mean[2]:.1f}"
        )
        return stats


def _get_target_hair_mask(frame: Frame, parse_every: int) -> Optional[np.ndarray]:
    """Return the target hair mask for this frame.

    When ``parse_every > 1`` and a cached mask matches the frame shape,
    reuse it for ``parse_every - 1`` calls before re-parsing. This lets
    live webcam call the parser every 5th frame instead of every frame.
    """
    with _target_lock:
        cache = _target_cache
        cache["interval"] = max(1, int(parse_every))
        shape = frame.shape[:2]
        cache["counter"] += 1
        needs_parse = (
            cache["mask"] is None
            or cache["frame_shape"] != shape
            or cache["counter"] >= cache["interval"]
        )
        if needs_parse:
            mask = get_hair_mask(frame, include_hat=True)
            if mask is None:
                cache["mask"] = None
                cache["frame_shape"] = None
                return None
            cache["mask"] = mask
            cache["frame_shape"] = shape
            cache["counter"] = 0
        return cache["mask"]


def apply_hair_swap(
    target_frame: Frame,
    source_face: Face,
    target_face: Face,
    parse_every: int = 1,
) -> Frame:
    """Recolor the target's hair toward the source's color/texture.

    Args:
        parse_every: re-parse target hair every Nth call (1 = every call).
            Set higher in live webcam to amortize parser cost.

    Returns ``target_frame`` unchanged when both sliders are 0, source
    stats can't be extracted, or the target frame contains no hair.
    """
    color_pct = max(0.0, min(100.0, getattr(modules.globals, "hair_color", 0.0)))
    texture_pct = max(0.0, min(100.0, getattr(modules.globals, "hair_texture", 0.0)))
    if color_pct <= 0.0 and texture_pct <= 0.0:
        return target_frame

    source_path = getattr(modules.globals, "source_path", None)
    if not source_path:
        return target_frame
    src = _extract_source_stats(source_path)
    if src is None:
        return target_frame

    target_mask = _get_target_hair_mask(target_frame, parse_every)
    if target_mask is None:
        return target_frame
    mask_bool = target_mask > 127
    if int(mask_bool.sum()) < 100:
        # No detectable target hair — bald, hat-occluded, or out of frame.
        return target_frame

    # Convert target to LAB float32 once. We'll mutate the L,A,B channels
    # only on hair pixels and then reconstruct a BGR composite.
    target_lab = cv2.cvtColor(target_frame, cv2.COLOR_BGR2LAB).astype(np.float32)
    hair_lab = target_lab[mask_bool]
    target_mean = hair_lab.mean(axis=0)
    target_std = hair_lab.std(axis=0) + 1e-6

    new_hair_lab = hair_lab.copy()

    if color_pct > 0.0:
        # Reinhard LAB color transfer, blended by color_strength so users
        # can dial in a partial recolor.
        recolored = (hair_lab - target_mean) * (src["lab_std"] / target_std) + src["lab_mean"]
        alpha = color_pct / 100.0
        new_hair_lab = (1.0 - alpha) * new_hair_lab + alpha * recolored

    if texture_pct > 0.0:
        # Scale the hair's high-frequency luminance variance to match the
        # source's. Clamp the multiplier so noise can't blow up.
        L = target_lab[..., 0]
        sigma_px = max(2, min(target_frame.shape[:2]) // 50)
        k = sigma_px * 2 + 1
        L_low_full = cv2.GaussianBlur(L, (k, k), 0)
        L_high_full = L - L_low_full
        target_L_high_std = float(L_high_full[mask_bool].std() + 1e-6)
        scale = src["L_high_std"] / target_L_high_std
        scale = float(np.clip(scale, 0.5, 2.5))
        beta = texture_pct / 100.0
        # Apply only to hair pixels: L_new = L_low + L_high * (1 + beta*(scale-1))
        L_high_hair = L_high_full[mask_bool]
        L_low_hair = L_low_full[mask_bool]
        L_textured = L_low_hair + L_high_hair * (1.0 + beta * (scale - 1.0))
        # Replace the L channel of new_hair_lab with the textured version.
        new_hair_lab[:, 0] = L_textured

    # Write modified hair pixels back into the LAB frame.
    target_lab[mask_bool] = new_hair_lab
    np.clip(target_lab, 0, 255, out=target_lab)
    recolored_bgr = cv2.cvtColor(target_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

    # Feather the hair mask edges so the boundary with skin doesn't show
    # a hard line where color shifts.
    feather_px = max(3, int(min(target_frame.shape[:2]) * 0.005))
    k = feather_px * 2 + 1
    soft = cv2.GaussianBlur(target_mask, (k, k), 0).astype(np.float32) * (1.0 / 255.0)
    soft_3 = soft[..., None]

    blended = soft_3 * recolored_bgr.astype(np.float32) + (1.0 - soft_3) * target_frame.astype(np.float32)
    return np.clip(blended, 0, 255).astype(np.uint8)
