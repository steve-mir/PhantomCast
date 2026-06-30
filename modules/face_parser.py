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

NAME = "PCAST.FACE-PARSER"

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

from modules.paths import MODELS_DIR as models_dir


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


# --- [FEATURE:SKIN-TONE] / [FEATURE:HAIR-TRANSFER] helpers -----------------
# Additive helpers shared by the appearance-matching modules. Safe to delete
# together with modules/processors/frame/{appearance,skin_tone,hair_transfer}.py.

# zllrunning/face-parsing.PyTorch class ids that read as bare skin:
# 1 = facial skin, 7/8 = ears, 10 = nose, 14 = neck.
SKIN_CLASS_IDS = (1, 7, 8, 10, 14)


def class_mask(label: np.ndarray, class_ids) -> np.ndarray:
    """Binary uint8 mask (255 = any of *class_ids*) from a label map."""
    mask = np.zeros(label.shape, dtype=np.uint8)
    for cid in class_ids:
        mask[label == cid] = 255
    return mask


def get_skin_mask(frame: Frame) -> Optional[np.ndarray]:
    """Binary uint8 mask of all visible skin (face, ears, nose, neck)."""
    label = parse(frame)
    if label is None:
        return None
    return class_mask(label, SKIN_CLASS_IDS)


# Head ROI expansion factors relative to a detected face bbox. BiSeNet is
# trained on portrait crops — parsing a head-centered crop instead of the
# whole frame makes labels far more reliable (busy backgrounds stop being
# classified as skin/hair) and confines effects to the tracked person.
_ROI_SIDE = 0.9       # extra width on each side, × face width
_ROI_UP = 1.4         # extra height above, × face height (hair)
_ROI_DOWN = 1.1       # extra height below, × face height (neck)


def head_roi(frame: Frame, face) -> Optional[tuple]:
    """(x0, y0, x1, y1) head crop around the detected face, frame-clamped."""
    bbox = getattr(face, "bbox", None) if face is not None else None
    if bbox is None:
        return None
    h, w = frame.shape[:2]
    fx0, fy0, fx1, fy1 = [float(v) for v in bbox[:4]]
    fw, fh = max(fx1 - fx0, 1.0), max(fy1 - fy0, 1.0)
    x0 = int(max(0, fx0 - _ROI_SIDE * fw))
    x1 = int(min(w, fx1 + _ROI_SIDE * fw))
    y0 = int(max(0, fy0 - _ROI_UP * fh))
    y1 = int(min(h, fy1 + _ROI_DOWN * fh))
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return (x0, y0, x1, y1)


def parse_head(frame: Frame, face) -> Optional[np.ndarray]:
    """Full-frame label map parsed from the head ROI only. Everything
    outside the ROI is class 0 (background). Falls back to a full-frame
    parse when no usable bbox exists."""
    roi = head_roi(frame, face)
    if roi is None:
        return parse(frame)
    x0, y0, x1, y1 = roi
    roi_label = parse(frame[y0:y1, x0:x1])
    if roi_label is None:
        return None
    label = np.zeros(frame.shape[:2], dtype=np.uint8)
    label[y0:y1, x0:x1] = roi_label
    return label
