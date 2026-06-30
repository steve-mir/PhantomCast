"""[FEATURE:HAIR-TRANSFER] Source hair transfer (shape + appearance).

Replace the target's hair with the *source person's* hair so the hair is
part of the swap, the way the face is — not a recolor of the target's
existing hair (that remains the job of the legacy ``hair_swap.py``
sliders), and not a static sticker either.

What makes the result read as "in the video" rather than pasted on:

  1. **AI segmentation** — the source hair is extracted with the BiSeNet
     face parser, with an eroded + feathered alpha matte so no background
     halo travels with it.
  2. **Pose tracking** — every frame, a similarity transform is estimated
     from the source's 5 facial landmarks to the target's, so the hair
     follows head position, scale and in-plane rotation. The transform is
     EMA-smoothed to kill landmark jitter.
  3. **Relighting** — the warped hair is re-lit in LAB space using the
     ratio between the target's and source's skin luminance/chroma, so
     hair shot in a bright studio sits naturally in a dim webcam scene.
  4. **Grain matching** — sensor noise of the live frame is estimated
     (Immerkær method) and matching grain is added to the hair layer so
     it shares the video's texture instead of looking like a clean cutout.
  5. **Orphan-hair removal** — regions where the target's own hair sticks
     out beyond the transferred hair are inpainted away (Telea, at reduced
     resolution for speed) instead of showing through.
  6. **Feathered composite** — soft alpha edges dissolve the hairline into
     skin and background.

Known limits (v1): the similarity transform tracks position/scale/roll but
not 3-D yaw/pitch, so extreme head turns reduce realism; long-to-short
transfers rely on inpainting which softens the revealed background.

Disabled (zero-cost) when ``modules.globals.hair_transfer_strength`` is 0.
Delete this file + the [FEATURE:HAIR-TRANSFER]/[FEATURE:APPEARANCE] blocks
to fully remove the feature.
"""

from typing import Optional
import threading

import cv2
import numpy as np

import modules.globals
from modules import face_parser
from modules.typing import Frame, Face

NAME = "PCAST.HAIR-TRANSFER"

_MIN_HAIR_PIXELS = 400

# Strength of the relighting applied to the warped hair. 1.0 would fully
# trust the skin-derived lighting estimate; partial values look the most
# natural across mixed lighting.
_RELIGHT_L = 0.75
_RELIGHT_AB = 0.5
_L_GAIN_CLAMP = (0.45, 2.2)

# EMA weights (new-sample fraction) for the pose transform and the
# relighting params. Lower = steadier, higher = more responsive.
_EMA_POSE = 0.45
_EMA_LIGHT = 0.25

# A translation jump larger than this fraction of the frame diagonal
# resets pose smoothing (scene cut / face re-acquired).
_POSE_RESET_FRAC = 0.12

# Inpainting of orphan target hair runs at this max width for speed.
_INPAINT_MAX_W = 420

# Grain sigma added to the hair layer is clamped to this range.
_GRAIN_CLAMP = (0.0, 4.0)

_source_cache: dict = {}
_cache_lock = threading.Lock()
_SOURCE_CACHE_LIMIT = 2

# Per-stream temporal state (pose + relight smoothing).
_state: dict = {"M": None, "shape": None, "light": None}
_state_lock = threading.Lock()


def reset_state() -> None:
    """Drop temporal smoothing state. Call between videos / on stop."""
    with _state_lock:
        _state["M"] = None
        _state["shape"] = None
        _state["light"] = None


def reset_source_cache() -> None:
    with _cache_lock:
        _source_cache.clear()


# ---------------------------------------------------------------------------
# Source preparation (cached per source_path).
# ---------------------------------------------------------------------------


def _prep_source(source_path: str) -> Optional[dict]:
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

        # get_one_face is imported lazily to avoid import cycles at module
        # load time (face_analyser pulls in heavy deps).
        from modules.face_analyser import get_one_face

        src_face = get_one_face(src_img)
        if src_face is None or getattr(src_face, "kps", None) is None:
            print(f"[{NAME}] No face/landmarks in source — cannot align hair.")
            _negative_cache(source_path)
            return None

        # Parse a head-centered crop so busy source backgrounds don't get
        # classified as hair and travel with the matte.
        label = face_parser.parse_head(src_img, src_face)
        if label is None:
            print(f"[{NAME}] Parser returned None — model not loaded?")
            return None
        hair_mask = (label == face_parser.HAIR_CLASS_ID).astype(np.uint8) * 255
        n = int((hair_mask > 0).sum())
        if n < _MIN_HAIR_PIXELS:
            print(
                f"[{NAME}] Source has too few hair pixels ({n}) — bald, "
                "hat-covered or out of frame. Hair transfer disabled for "
                "this source."
            )
            _negative_cache(source_path)
            return None

        # Build the alpha matte: erode first so edge pixels polluted by
        # background don't travel with the hair, then feather.
        er = max(1, int(min(src_img.shape[:2]) * 0.004))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (er * 2 + 1, er * 2 + 1))
        eroded = cv2.erode(hair_mask, kernel)
        fe = max(2, int(min(src_img.shape[:2]) * 0.008))
        alpha = cv2.GaussianBlur(eroded, (fe * 2 + 1, fe * 2 + 1), 0)
        alpha = alpha.astype(np.float32) * (1.0 / 255.0)

        # Source skin LAB means — the relighting reference.
        skin_bool = face_parser.class_mask(label, face_parser.SKIN_CLASS_IDS) > 127
        src_lab = cv2.cvtColor(src_img, cv2.COLOR_BGR2LAB).astype(np.float32)
        if int(skin_bool.sum()) >= 100:
            skin_mean = src_lab[skin_bool].mean(axis=0)
        else:
            # No visible skin (unlikely) — fall back to neutral reference.
            skin_mean = np.array([128.0, 128.0, 128.0], dtype=np.float32)

        prepped = {
            "img": src_img,
            "alpha": alpha,
            "kps": src_face.kps.astype(np.float32),
            "skin_mean": skin_mean.astype(np.float32),
            "hair_pixels": n,
        }
        if len(_source_cache) >= _SOURCE_CACHE_LIMIT:
            _source_cache.clear()
        _source_cache[source_path] = prepped
        print(f"[{NAME}] Source hair prepared: {n} px, landmarks locked.")
        return prepped


def _negative_cache(source_path: str) -> None:
    if len(_source_cache) >= _SOURCE_CACHE_LIMIT:
        _source_cache.clear()
    _source_cache[source_path] = {}


# ---------------------------------------------------------------------------
# Per-frame helpers.
# ---------------------------------------------------------------------------


def _smooth_pose(M: np.ndarray, frame_shape: tuple) -> np.ndarray:
    """EMA-smooth the 2x3 similarity transform; reset on big jumps."""
    with _state_lock:
        prev = _state["M"]
        if prev is None or _state["shape"] != frame_shape:
            _state["M"] = M
            _state["shape"] = frame_shape
            return M
        diag = float(np.hypot(*frame_shape))
        jump = float(np.hypot(M[0, 2] - prev[0, 2], M[1, 2] - prev[1, 2]))
        if jump > _POSE_RESET_FRAC * diag:
            _state["M"] = M
            return M
        sm = (1.0 - _EMA_POSE) * prev + _EMA_POSE * M
        _state["M"] = sm
        return sm


def _smooth_light(params: np.ndarray) -> np.ndarray:
    """EMA-smooth (L_gain, a_shift, b_shift)."""
    with _state_lock:
        prev = _state["light"]
        if prev is None:
            _state["light"] = params
            return params
        sm = (1.0 - _EMA_LIGHT) * prev + _EMA_LIGHT * params
        _state["light"] = sm
        return sm


def _estimate_noise_sigma(frame: Frame) -> float:
    """Fast Immerkær noise estimate on a downscaled gray frame."""
    h, w = frame.shape[:2]
    scale = 320.0 / max(w, 1)
    small = cv2.resize(frame, (max(32, int(w * scale)), max(32, int(h * scale))))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    kern = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float32)
    conv = cv2.filter2D(gray, -1, kern)
    sigma = float(np.sqrt(np.pi / 2.0) * np.abs(conv).mean() / 6.0)
    return float(np.clip(sigma, *_GRAIN_CLAMP))


def _inpaint_orphan_hair(
    frame: Frame, label: np.ndarray, warped_alpha: np.ndarray
) -> Frame:
    """Remove the target's own hair before the new hair is composited.

    Everything the new hair won't fully cover is inpainted away —
    including hair *underneath* semi-transparent (feathered) parts of the
    new layer, so the two hairstyles never ghost into each other. Telea
    inpainting runs at reduced resolution and is blended back feathered.
    """
    tgt_hair = (
        (label == face_parser.HAIR_CLASS_ID)
        | (label == face_parser.HAT_CLASS_ID)
    )
    orphan = (tgt_hair & (warped_alpha < 0.8)).astype(np.uint8) * 255
    if int((orphan > 0).sum()) < 80:
        return frame
    orphan = cv2.dilate(
        orphan, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=2
    )

    h, w = frame.shape[:2]
    scale = min(1.0, _INPAINT_MAX_W / float(w))
    sw, sh = max(32, int(w * scale)), max(32, int(h * scale))
    small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA).astype(
        np.float32
    )
    small_mask = cv2.resize(orphan, (sw, sh), interpolation=cv2.INTER_NEAREST)

    # Normalized-convolution fill: pull colors in from outside the mask
    # with two Gaussian passes (second, wider pass covers deep holes).
    # O(pixels) regardless of mask size — cv2.inpaint's cost explodes on
    # the large masks a full hairstyle removal produces.
    unknown = small_mask >= 128
    keep = (~unknown).astype(np.float32)
    filled_small = small.copy()
    for sigma in (float(max(3, sw * 0.02)), float(max(8, sw * 0.07))):
        base = filled_small * keep[..., None]
        num = cv2.GaussianBlur(base, (0, 0), sigma)
        den = cv2.GaussianBlur(keep, (0, 0), sigma)
        fillable = unknown & (den > 1e-3)
        if fillable.any():
            filled_small[fillable] = num[fillable] / den[fillable][..., None]
            keep[fillable] = 1.0
            unknown &= ~fillable
        if not unknown.any():
            break
    filled_small = np.clip(filled_small, 0, 255).astype(np.uint8)
    filled = cv2.resize(filled_small, (w, h), interpolation=cv2.INTER_LINEAR)

    fe = max(2, int(min(h, w) * 0.006))
    soft = cv2.GaussianBlur(orphan, (fe * 2 + 1, fe * 2 + 1), 0).astype(
        np.float32
    ) * (1.0 / 255.0)
    soft_3 = soft[..., None]
    out = soft_3 * filled.astype(np.float32) + (1.0 - soft_3) * frame.astype(
        np.float32
    )
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Main entry point.
# ---------------------------------------------------------------------------


def apply_hair_transfer(
    frame: Frame, label: np.ndarray, target_face: Face, strength: float
) -> Frame:
    """Composite the source person's hair onto the target frame.

    Args:
        frame: BGR frame, post face-swap (the hair overlaps the hairline
            and hides the swap seam).
        label: BiSeNet label map for *frame* (from ``appearance.py``);
            used for orphan-hair removal.
        target_face: detected target face — its 5-point landmarks drive
            the per-frame alignment.
        strength: 0..1 — overall opacity of the transferred hair.

    Returns the frame unchanged on any failure.
    """
    if strength <= 0.0:
        return frame
    if target_face is None or getattr(target_face, "kps", None) is None:
        return frame
    source_path = getattr(modules.globals, "source_path", None)
    if not source_path:
        return frame
    src = _prep_source(source_path)
    if src is None:
        return frame

    try:
        h, w = frame.shape[:2]
        M, _ = cv2.estimateAffinePartial2D(
            src["kps"], target_face.kps.astype(np.float32), method=cv2.LMEDS
        )
        if M is None:
            return frame
        M = _smooth_pose(M.astype(np.float32), (h, w))

        warped = cv2.warpAffine(
            src["img"], M, (w, h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0,
        )
        walpha = cv2.warpAffine(
            src["alpha"], M, (w, h), flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT, borderValue=0.0,
        )

        ys, xs = np.where(walpha > 0.01)
        if len(xs) < 50:
            return frame
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        y0, y1 = int(ys.min()), int(ys.max()) + 1

        # 1) Remove the target's own hair that the new hair won't cover.
        frame = _inpaint_orphan_hair(frame, label, walpha)

        # 2) Relight the hair crop to the scene. Reference: target skin
        #    LAB means (from the shared label map) vs source skin means.
        crop = warped[y0:y1, x0:x1]
        crop_lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)

        skin_mask = face_parser.class_mask(label, face_parser.SKIN_CLASS_IDS)
        skin_ys, skin_xs = np.where(skin_mask > 127)
        if len(skin_xs) >= 100:
            # LAB-convert only the skin bounding box, not the whole frame.
            sy0, sy1 = int(skin_ys.min()), int(skin_ys.max()) + 1
            sx0, sx1 = int(skin_xs.min()), int(skin_xs.max()) + 1
            skin_region_lab = cv2.cvtColor(
                frame[sy0:sy1, sx0:sx1], cv2.COLOR_BGR2LAB
            ).astype(np.float32)
            skin_bool = skin_mask[sy0:sy1, sx0:sx1] > 127
            tgt_skin_mean = skin_region_lab[skin_bool].mean(axis=0)
            l_gain = float(
                np.clip(
                    tgt_skin_mean[0] / max(src["skin_mean"][0], 1e-3),
                    *_L_GAIN_CLAMP,
                )
            )
            a_shift = float(tgt_skin_mean[1] - src["skin_mean"][1])
            b_shift = float(tgt_skin_mean[2] - src["skin_mean"][2])
        else:
            l_gain, a_shift, b_shift = 1.0, 0.0, 0.0
        l_gain, a_shift, b_shift = _smooth_light(
            np.array([l_gain, a_shift, b_shift], dtype=np.float32)
        )
        crop_lab[..., 0] *= 1.0 + _RELIGHT_L * (l_gain - 1.0)
        crop_lab[..., 1] += _RELIGHT_AB * a_shift
        crop_lab[..., 2] += _RELIGHT_AB * b_shift

        # 3) Grain match: add the frame's estimated sensor noise to the
        #    hair luminance so the layer shares the video's texture.
        sigma = _estimate_noise_sigma(frame)
        if sigma > 0.15:
            crop_lab[..., 0] += np.random.normal(
                0.0, sigma, crop_lab.shape[:2]
            ).astype(np.float32)

        np.clip(crop_lab, 0, 255, out=crop_lab)
        crop_bgr = cv2.cvtColor(crop_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

        # 4) Feathered alpha composite, scaled by the user slider.
        a = (walpha[y0:y1, x0:x1] * strength)[..., None]
        region = frame[y0:y1, x0:x1].astype(np.float32)
        blended = a * crop_bgr.astype(np.float32) + (1.0 - a) * region
        out = frame.copy()
        out[y0:y1, x0:x1] = np.clip(blended, 0, 255).astype(np.uint8)
        return out
    except Exception as e:
        print(f"[{NAME}] hair transfer failed: {e}")
        return frame
