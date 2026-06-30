"""[FEATURE:APPEARANCE] Single integration point for appearance matching.

Bundles the two appearance-matching features behind one call so every
pipeline (image, video, live webcam) touches exactly one extra block:

  * [FEATURE:SKIN-TONE]      ``skin_tone.apply_skin_tone``
  * [FEATURE:HAIR-TRANSFER]  ``hair_transfer.apply_hair_transfer``

Both need a BiSeNet parse of the current (post-swap) frame; this module
runs that parse ONCE per frame — throttleable for live webcam via
``parse_every`` — and hands the label map to both features.

Removal: delete this file plus ``skin_tone.py`` / ``hair_transfer.py``
and every call-site block marked [FEATURE:APPEARANCE] (grep for it).
With both sliders at 0 the pipeline is byte-identical to the legacy
behavior — ``apply_appearance`` is never even called.
"""

from typing import Optional
import threading

import numpy as np

import modules.globals
from modules import face_parser
from modules.processors.frame import skin_tone, hair_transfer
from modules.typing import Frame, Face

NAME = "PCAST.APPEARANCE"

# Throttled label-map cache (mirrors hair_swap's target cache so live
# webcam amortizes the ~10-15ms parse over several frames).
_label_cache: dict = {
    "label": None,
    "frame_shape": None,
    "counter": 0,
}
_label_lock = threading.Lock()


def is_enabled() -> bool:
    """True when either appearance feature is active."""
    return (
        getattr(modules.globals, "skin_tone_strength", 0.0) > 0.0
        or getattr(modules.globals, "hair_transfer_strength", 0.0) > 0.0
    )


def reset_state() -> None:
    """Drop per-stream state. Call between videos / on stop."""
    with _label_lock:
        _label_cache["label"] = None
        _label_cache["frame_shape"] = None
        _label_cache["counter"] = 0
    skin_tone.reset_state()
    hair_transfer.reset_state()


def reset_source_cache() -> None:
    skin_tone.reset_source_cache()
    hair_transfer.reset_source_cache()


def _get_label(
    frame: Frame, target_face: Face, parse_every: int
) -> Optional[np.ndarray]:
    with _label_lock:
        cache = _label_cache
        shape = frame.shape[:2]
        cache["counter"] += 1
        needs_parse = (
            cache["label"] is None
            or cache["frame_shape"] != shape
            or cache["counter"] >= max(1, int(parse_every))
        )
        if needs_parse:
            label = face_parser.parse_head(frame, target_face)
            if label is None:
                cache["label"] = None
                cache["frame_shape"] = None
                return None
            cache["label"] = label
            cache["frame_shape"] = shape
            cache["counter"] = 0
        return cache["label"]


def apply_appearance(
    frame: Frame, target_face: Face, parse_every: int = 1
) -> Frame:
    """Apply skin-tone matching then hair transfer to a post-swap frame.

    Order matters: complexion first so the hair relighting reads the
    already-matched skin; hair last so it overlaps the hairline and hides
    the swap seam.

    Args:
        parse_every: re-parse the frame every Nth call (live webcam uses
            5 to amortize the BiSeNet cost; offline pipelines use 1).

    Returns the frame unchanged when disabled or on any failure.
    """
    if not is_enabled():
        return frame
    label = _get_label(frame, target_face, parse_every)
    if label is None:
        return frame

    skin_pct = max(
        0.0, min(100.0, getattr(modules.globals, "skin_tone_strength", 0.0))
    )
    hair_pct = max(
        0.0, min(100.0, getattr(modules.globals, "hair_transfer_strength", 0.0))
    )

    if skin_pct > 0.0:
        frame = skin_tone.apply_skin_tone(frame, label, skin_pct / 100.0)
    if hair_pct > 0.0:
        frame = hair_transfer.apply_hair_transfer(
            frame, label, target_face, hair_pct / 100.0
        )
    return frame
