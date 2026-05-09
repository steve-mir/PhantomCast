"""NSFW prediction wrapper.

opennsfw2 + tensorflow were removed from the bundled Windows build to keep
the installer under GitHub's 2GB release-asset cap. When the optional
opennsfw2 dependency is missing, the predict_* functions degrade to "not
NSFW" so the --nsfw-filter CLI flag becomes a no-op rather than crashing.

To restore real NSFW filtering: `pip install opennsfw2` (also pulls in
TensorFlow), or migrate to a small ONNX NSFW model that fits the existing
onnxruntime stack.
"""
import numpy
from PIL import Image
import cv2
import modules.globals
from modules.gpu_processing import gpu_cvt_color
from modules.typing import Frame

try:
    import opennsfw2
    HAS_OPENNSFW2 = True
except ImportError:
    HAS_OPENNSFW2 = False

MAX_PROBABILITY = 0.85
model = None
_warned = False


def _warn_disabled() -> None:
    global _warned
    if _warned:
        return
    _warned = True
    print("[nsfw] opennsfw2 not installed in this build; --nsfw-filter is a no-op.")


def predict_frame(target_frame: Frame) -> bool:
    if not HAS_OPENNSFW2:
        _warn_disabled()
        return False

    if modules.globals.color_correction:
        target_frame = gpu_cvt_color(target_frame, cv2.COLOR_BGR2RGB)

    image = Image.fromarray(target_frame)
    image = opennsfw2.preprocess_image(image, opennsfw2.Preprocessing.YAHOO)
    global model
    if model is None:
        model = opennsfw2.make_open_nsfw_model()

    views = numpy.expand_dims(image, axis=0)
    _, probability = model.predict(views)[0]
    return probability > MAX_PROBABILITY


def predict_image(target_path: str) -> bool:
    if not HAS_OPENNSFW2:
        _warn_disabled()
        return False
    return opennsfw2.predict_image(target_path) > MAX_PROBABILITY


def predict_video(target_path: str) -> bool:
    if not HAS_OPENNSFW2:
        _warn_disabled()
        return False
    _, probabilities = opennsfw2.predict_video_frames(video_path=target_path, frame_interval=100)
    return any(probability > MAX_PROBABILITY for probability in probabilities)
