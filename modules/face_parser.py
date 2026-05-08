"""BiSeNet face/hair parser.

Wraps the BiSeNet ONNX model (CelebAMask-HQ, 19 classes) to produce a
per-pixel class label map. Used by the hair-swap pipeline to extract
hair regions from source and target frames.

Class ordering follows zllrunning/face-parsing.PyTorch — the most common
BiSeNet export. If a different ONNX export is used, override the
``HAIR_CLASS_ID`` / ``HAT_CLASS_ID`` constants below.
"""

from typing import Optional
import os
import threading

import cv2
import numpy as np
import onnxruntime

import modules.globals
from modules.core import update_status
from modules.utilities import conditional_download
from modules.typing import Frame

NAME = "DLC.FACE-PARSER"

# zllrunning/face-parsing.PyTorch ordering. Override if a different
# ONNX export is used.
HAIR_CLASS_ID = 17
HAT_CLASS_ID = 18

MODEL_FILE = "bisenet_resnet_18.onnx"
# facefusion mirror — confirmed live as of last check.
MODEL_URL = (
    "https://huggingface.co/facefusion/models-3.1.0/resolve/main/"
    "bisenet_resnet_18.onnx"
)

INPUT_SIZE = 512
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(1, 1, 3)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(1, 1, 3)

PARSER_SESSION: Optional[onnxruntime.InferenceSession] = None
_LOCK = threading.Lock()
_SEMA = threading.Semaphore()

abs_dir = os.path.dirname(os.path.abspath(__file__))
models_dir = os.path.join(os.path.dirname(abs_dir), "models")


def pre_check() -> bool:
    try:
        os.makedirs(models_dir, exist_ok=True)
    except OSError as e:
        update_status(f"Failed to create models dir: {e}", NAME)
        return False
    model_path = os.path.join(models_dir, MODEL_FILE)
    if not os.path.exists(model_path):
        update_status(f"Downloading {MODEL_FILE} (~50MB)...", NAME)
        conditional_download(models_dir, [MODEL_URL])
    return os.path.exists(model_path)


def _build_providers():
    """BiSeNet contains a GAP + channel-axis concat (1x128x1x1 with 1x128x64x64)
    that Apple's CoreML/MPS backend rejects with::

        'mps.concat' op invalid input tensor shapes, all input shapes must
        match except at axis

    and aborts the process. The same op runs fine on CPU and CUDA. Since the
    parser only fires once per source image (cached), forcing CPU here costs
    nothing in practice and keeps the app stable on Apple Silicon.
    """
    cfg = []
    for p in modules.globals.execution_providers:
        if p == "CoreMLExecutionProvider":
            continue  # skip — incompatible with this BiSeNet export
        cfg.append(p)
    if not any(
        (isinstance(p, str) and p == "CPUExecutionProvider")
        or (isinstance(p, tuple) and p[0] == "CPUExecutionProvider")
        for p in cfg
    ):
        cfg.append("CPUExecutionProvider")
    return cfg


def get_session() -> Optional[onnxruntime.InferenceSession]:
    global PARSER_SESSION
    with _LOCK:
        if PARSER_SESSION is None:
            model_path = os.path.join(models_dir, MODEL_FILE)
            if not os.path.exists(model_path):
                if not pre_check():
                    return None
            try:
                update_status(f"Loading face parser from: {model_path}", NAME)
                PARSER_SESSION = onnxruntime.InferenceSession(
                    model_path, providers=_build_providers()
                )
            except Exception as e:
                update_status(f"Error loading {MODEL_FILE}: {e}", NAME)
                PARSER_SESSION = None
                return None
    return PARSER_SESSION


def _preprocess(frame: Frame) -> np.ndarray:
    rgb = frame[:, :, ::-1]
    resized = cv2.resize(
        rgb, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR
    )
    f = resized.astype(np.float32) * (1.0 / 255.0)
    f = (f - _MEAN) / _STD
    chw = np.transpose(f, (2, 0, 1))
    return chw[np.newaxis, ...].astype(np.float32, copy=False)


def parse(frame: Frame) -> Optional[np.ndarray]:
    """Run BiSeNet and return a per-pixel class label map at the frame's
    resolution. Returns None on any failure so callers can fall through
    to a no-op."""
    if frame is None or frame.size == 0 or frame.ndim != 3 or frame.shape[2] != 3:
        return None
    session = get_session()
    if session is None:
        return None
    h, w = frame.shape[:2]
    inp = _preprocess(frame)
    try:
        with _SEMA:
            out = session.run(None, {session.get_inputs()[0].name: inp})[0]
    except Exception as e:
        update_status(f"Face parser inference failed: {e}", NAME)
        return None
    label = np.argmax(out[0], axis=0).astype(np.uint8)
    if (h, w) != (INPUT_SIZE, INPUT_SIZE):
        label = cv2.resize(label, (w, h), interpolation=cv2.INTER_NEAREST)
    return label


def get_hair_mask(frame: Frame, include_hat: bool = True) -> Optional[np.ndarray]:
    """Return a binary uint8 mask (255 = hair, 0 = not) at the frame's
    resolution. ``include_hat`` adds the hat class so caps don't punch
    holes through the swapped region."""
    label = parse(frame)
    if label is None:
        return None
    mask = (label == HAIR_CLASS_ID).astype(np.uint8) * 255
    if include_hat:
        mask = np.where(label == HAT_CLASS_ID, np.uint8(255), mask)
    return mask
