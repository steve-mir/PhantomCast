"""Output-side helpers (watermarking, export caps).

Importing this package only exposes the watermark module — installing the
gate over the legacy frame-swapper happens via :mod:`modules.dlc_pro.core_bridge`.
"""

from modules.dlc_pro.output.watermark import draw_watermark, watermark_if_free

__all__ = ["draw_watermark", "watermark_if_free"]
