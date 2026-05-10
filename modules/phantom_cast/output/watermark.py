"""Free-tier watermark renderer.

Draws a discreet "Phantom Cast" badge in the bottom-right corner of the
output frame when the ``no_watermark`` entitlement is missing. Safe for
both image and video paths — the call is in-place-ish (we draw on a copy)
and shape-agnostic.

Design goals:

    * Visible but not obstructive — corner placement, ~4% of frame height
    * Readable on any background — semi-transparent dark pill behind
      white text, plus a subtle text shadow
    * Professional look — rounded pill, brand-mark dot, soft alpha

Performance: a couple of ``cv2`` primitives drawn into a small ROI then
alpha-blended — negligible compared to the swap itself.
"""
from __future__ import annotations

import cv2
import numpy as np

from modules.typing import Frame


WATERMARK_TEXT = "Phantom Cast"

# All sizes are fractions of the frame's long edge so the badge looks the
# same at 480p preview and 1080p export.
_FONT = cv2.FONT_HERSHEY_DUPLEX
_TEXT_SCALE_RATIO = 0.0011        # font scale per pixel of long edge
_TEXT_THICK_RATIO = 0.0018        # text stroke thickness
_MARGIN_RATIO = 0.020             # padding from frame edge
_PAD_X_RATIO = 0.012              # horizontal padding inside pill
_PAD_Y_RATIO = 0.008              # vertical padding inside pill
_DOT_GAP_RATIO = 0.008            # gap between brand dot and text
_DOT_RADIUS_RATIO = 0.0050        # brand dot radius

_PILL_ALPHA = 0.45                # pill background opacity
_PILL_COLOR = (24, 24, 28)        # near-black BGR
_TEXT_COLOR = (255, 255, 255)
_SHADOW_COLOR = (0, 0, 0)
_DOT_COLOR = (180, 110, 255)      # subtle violet accent


def _metrics(frame: Frame) -> dict:
    long_edge = max(frame.shape[:2])
    scale = max(0.45, long_edge * _TEXT_SCALE_RATIO)
    thick = max(1, int(round(long_edge * _TEXT_THICK_RATIO)))
    margin = max(8, int(round(long_edge * _MARGIN_RATIO)))
    pad_x = max(6, int(round(long_edge * _PAD_X_RATIO)))
    pad_y = max(4, int(round(long_edge * _PAD_Y_RATIO)))
    dot_gap = max(4, int(round(long_edge * _DOT_GAP_RATIO)))
    dot_r = max(2, int(round(long_edge * _DOT_RADIUS_RATIO)))
    return {
        "scale": scale, "thick": thick, "margin": margin,
        "pad_x": pad_x, "pad_y": pad_y, "dot_gap": dot_gap, "dot_r": dot_r,
    }


def _draw_filled_pill(img: Frame, x1: int, y1: int, x2: int, y2: int, color) -> None:
    radius = (y2 - y1) // 2
    cv2.rectangle(img, (x1 + radius, y1), (x2 - radius, y2), color, -1, cv2.LINE_AA)
    cv2.circle(img, (x1 + radius, y1 + radius), radius, color, -1, cv2.LINE_AA)
    cv2.circle(img, (x2 - radius, y1 + radius), radius, color, -1, cv2.LINE_AA)


def draw_watermark(frame: Frame) -> Frame:
    """Return a watermarked copy of ``frame``.

    Single bottom-right pill badge, semi-transparent. Output has the same
    shape and dtype as the input.
    """
    if frame is None or frame.size == 0:
        return frame

    h, w = frame.shape[:2]
    m = _metrics(frame)

    (text_w, text_h), baseline = cv2.getTextSize(
        WATERMARK_TEXT, _FONT, m["scale"], m["thick"],
    )

    # Pill geometry. Height is text + vertical padding; width fits dot +
    # gap + text + horizontal padding on both sides.
    content_w = (m["dot_r"] * 2) + m["dot_gap"] + text_w
    pill_h = text_h + baseline + m["pad_y"] * 2
    pill_w = content_w + m["pad_x"] * 2

    # Anchor in the bottom-right corner with margin.
    x2 = w - m["margin"]
    y2 = h - m["margin"]
    x1 = x2 - pill_w
    y1 = y2 - pill_h

    # Frame too small for a legible badge — bail rather than smear.
    if x1 < 0 or y1 < 0:
        return frame

    # Alpha-blend the pill background only over its ROI so the rest of
    # the frame is byte-for-byte identical to the input.
    out = frame.copy()
    roi = out[y1:y2, x1:x2].copy()
    pill_layer = roi.copy()
    _draw_filled_pill(
        pill_layer, 0, 0, pill_w, pill_h, _PILL_COLOR,
    )
    blended = cv2.addWeighted(pill_layer, _PILL_ALPHA, roi, 1.0 - _PILL_ALPHA, 0)
    out[y1:y2, x1:x2] = blended

    # Brand dot (vertically centered in pill).
    dot_cx = x1 + m["pad_x"] + m["dot_r"]
    dot_cy = y1 + pill_h // 2
    cv2.circle(out, (dot_cx, dot_cy), m["dot_r"], _DOT_COLOR, -1, cv2.LINE_AA)

    # Text baseline: pill_h vertical center adjusted for ascender.
    text_x = dot_cx + m["dot_r"] + m["dot_gap"]
    text_y = y1 + m["pad_y"] + text_h

    # Soft shadow then crisp white text — keeps it readable on any background.
    cv2.putText(
        out, WATERMARK_TEXT, (text_x + 1, text_y + 1),
        _FONT, m["scale"], _SHADOW_COLOR, m["thick"] + 1, cv2.LINE_AA,
    )
    cv2.putText(
        out, WATERMARK_TEXT, (text_x, text_y),
        _FONT, m["scale"], _TEXT_COLOR, m["thick"], cv2.LINE_AA,
    )
    return out


def watermark_if_free(frame: Frame) -> Frame:
    """Apply :func:`draw_watermark` iff the user lacks ``no_watermark``."""
    # Lazy import: keeps this module importable from low-level processors
    # without dragging in the licence stack at module load.
    from modules.phantom_cast.subscription.gate import has_feature

    if has_feature("no_watermark"):
        return frame
    return draw_watermark(frame)
