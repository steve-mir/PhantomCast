"""HyperSwap (1A 256) face swapper.

Wraps the HyperSwap ONNX model (256x256, ArcFace-aligned) in the same
simple face-only swap pipeline used by Inswapper:

1. Align the target face into ArcFace 256x256 space.
2. Run HyperSwap to produce a 256x256 swapped face.
3. Paste the swap back onto the target frame with a feathered face mask.
"""

from typing import Any, List, Optional, Tuple
import os
import threading

import cv2
import numpy as np
import onnxruntime

import modules.globals
import modules.processors.frame.core
from modules.core import update_status
from modules.face_analyser import get_one_face, get_many_faces, default_source_face
from modules.cluster_analysis import find_closest_centroid
from modules.typing import Frame, Face
from modules.utilities import (
    conditional_download,
    is_image,
    is_video,
)
from modules.gpu_processing import gpu_add_weighted

from modules.processors.frame.face_swapper import (
    _fast_paste_back,
    apply_post_processing,
    create_face_mask,
    create_lower_mouth_mask,
    apply_mouth_area,
    draw_mouth_mask_visualization,
)
from modules.processors.frame.face_masking import (
    apply_mask_area,
    create_chin_mask,
    create_eyebrows_mask,
    create_eyes_mask,
    create_quick_lip_mask,
)
from modules.processors.frame import hair_swap as _hair_swap

NAME = "DLC.FACE-SWAPPER-HYPERSWAP"
INPUT_SIZE = 256
MODEL_FILE = "hyperswap_1a_256.onnx"
MODEL_URL = (
    "https://huggingface.co/facefusion/models-3.3.0/resolve/main/"
    "hyperswap_1a_256.onnx"
)

FACE_SWAPPER: onnxruntime.InferenceSession | None = None
THREAD_LOCK = threading.Lock()
THREAD_SEMAPHORE = threading.Semaphore()

abs_dir = os.path.dirname(os.path.abspath(__file__))
models_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(abs_dir))), "models"
)


# Public helper used by the in-memory video pipeline to reset state on each
# new video.  Mirrors the ``PREVIOUS_FRAME_RESULT`` reset done elsewhere.
PREVIOUS_FRAME_RESULT = None


# ---------------------------------------------------------------------------
# Model loading.
# ---------------------------------------------------------------------------


def pre_check() -> bool:
    try:
        os.makedirs(models_dir, exist_ok=True)
    except OSError as e:
        update_status(f"Failed to create models directory: {e}", NAME)
        return False
    model_path = os.path.join(models_dir, MODEL_FILE)
    if not os.path.exists(model_path):
        update_status(f"Downloading {MODEL_FILE} (~403MB)...", NAME)
        conditional_download(models_dir, [MODEL_URL])
    return os.path.exists(model_path)


def pre_start() -> bool:
    model_path = os.path.join(models_dir, MODEL_FILE)
    if not os.path.exists(model_path):
        update_status(
            f"Model not found at {model_path}. Run with internet to "
            f"download, or place {MODEL_FILE} manually in {models_dir}.",
            NAME,
        )
        return False
    if get_face_swapper() is None:
        return False
    return True


def _build_providers() -> List[Any]:
    cfg: List[Any] = []
    for p in modules.globals.execution_providers:
        if p == "CoreMLExecutionProvider":
            cfg.append((
                "CoreMLExecutionProvider",
                {
                    "ModelFormat": "MLProgram",
                    "MLComputeUnits": "ALL",
                    "SpecializationStrategy": "FastPrediction",
                    "AllowLowPrecisionAccumulationOnGPU": 1,
                    "EnableOnSubgraphs": 1,
                },
            ))
        else:
            cfg.append(p)
    return cfg


def get_face_swapper() -> onnxruntime.InferenceSession | None:
    global FACE_SWAPPER
    with THREAD_LOCK:
        if FACE_SWAPPER is None:
            model_path = os.path.join(models_dir, MODEL_FILE)
            if not os.path.exists(model_path):
                update_status(f"Model not found: {model_path}", NAME)
                return None
            try:
                update_status(f"Loading HyperSwap model from: {model_path}", NAME)
                FACE_SWAPPER = onnxruntime.InferenceSession(
                    model_path, providers=_build_providers()
                )
                input_names = {i.name for i in FACE_SWAPPER.get_inputs()}
                if "source" not in input_names or "target" not in input_names:
                    update_status(
                        f"Unexpected model inputs {input_names}; "
                        "expected 'source' and 'target'.",
                        NAME,
                    )
                    FACE_SWAPPER = None
                    return None
                update_status("HyperSwap model loaded successfully.", NAME)
            except Exception as e:
                update_status(f"Error loading HyperSwap model: {e}", NAME)
                FACE_SWAPPER = None
                return None
    return FACE_SWAPPER


# ---------------------------------------------------------------------------
# ArcFace 256x256 alignment (identical to insightface's 5-point template
# scaled to 256). Used to feed both HyperSwap and _fast_paste_back.
# ---------------------------------------------------------------------------
_ARCFACE_DST_112 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)
_ARCFACE_DST_256 = _ARCFACE_DST_112 * 2.0 + np.array([16.0, 0.0], dtype=np.float32)


def _align_face_arcface_256(frame: Frame, kps: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    if kps is None or len(kps) < 5:
        return None, None
    M, _ = cv2.estimateAffinePartial2D(
        kps.astype(np.float32), _ARCFACE_DST_256, method=cv2.LMEDS
    )
    if M is None:
        return None, None
    aligned = cv2.warpAffine(
        frame, M, (INPUT_SIZE, INPUT_SIZE),
        borderMode=cv2.BORDER_CONSTANT, borderValue=0.0,
    )
    return aligned, M


# ---------------------------------------------------------------------------
# HyperSwap I/O.
# ---------------------------------------------------------------------------


def _prepare_target(aligned_bgr: np.ndarray) -> np.ndarray:
    rgb = aligned_bgr[:, :, ::-1].astype(np.float32) * (1.0 / 255.0)
    rgb = (rgb - 0.5) / 0.5
    chw = np.transpose(rgb, (2, 0, 1))
    return chw[np.newaxis, ...].astype(np.float32, copy=False)


def _postprocess_swap(out: np.ndarray) -> np.ndarray:
    face = out[0]
    face = np.transpose(face, (1, 2, 0))
    face = face * 0.5 + 0.5
    np.clip(face, 0.0, 1.0, out=face)
    face = (face * 255.0).astype(np.uint8)
    return face[:, :, ::-1].copy()


def _run_inference(source_emb: np.ndarray, target_chw: np.ndarray) -> np.ndarray:
    session = FACE_SWAPPER
    with THREAD_SEMAPHORE:
        outputs = session.run(
            None, {"source": source_emb, "target": target_chw}
        )
    return outputs[0]


# ---------------------------------------------------------------------------
# Public swap_face — same signature as face_swapper.swap_face.
# ---------------------------------------------------------------------------


def swap_face(source_face: Face, target_face: Face, temp_frame: Frame) -> Frame:
    session = get_face_swapper()
    if session is None:
        return temp_frame
    if source_face is None or target_face is None:
        return temp_frame
    if not hasattr(source_face, "normed_embedding") or source_face.normed_embedding is None:
        return temp_frame
    if not hasattr(target_face, "kps") or target_face.kps is None:
        return temp_frame

    opacity = max(0.0, min(1.0, getattr(modules.globals, "opacity", 1.0)))
    mouth_mask_enabled = getattr(modules.globals, "mouth_mask", False)
    quick_lip_enabled = (
        getattr(modules.globals, "quick_lip_mask", False)
        and getattr(modules.globals, "quick_lip_size", 0.0) > 0
    )
    chin_mask_enabled = (
        getattr(modules.globals, "chin_mask", False)
        and getattr(modules.globals, "chin_mask_size", 0.0) > 0
    )
    eyes_mask_enabled = (
        getattr(modules.globals, "eyes_mask", False)
        and getattr(modules.globals, "eyes_mask_size", 0.0) > 0
    )
    eyebrows_mask_enabled = (
        getattr(modules.globals, "eyebrows_mask", False)
        and getattr(modules.globals, "eyebrows_mask_size", 0.0) > 0
    )
    needs_original = (
        opacity < 1.0
        or mouth_mask_enabled
        or quick_lip_enabled
        or chin_mask_enabled
        or eyes_mask_enabled
        or eyebrows_mask_enabled
    )
    original_frame = temp_frame.copy() if needs_original else temp_frame

    if temp_frame.dtype != np.uint8:
        temp_frame = np.clip(temp_frame, 0, 255).astype(np.uint8)
    if not temp_frame.flags["C_CONTIGUOUS"]:
        temp_frame = np.ascontiguousarray(temp_frame)

    try:
        aligned_face, M = _align_face_arcface_256(temp_frame, target_face.kps)
        if aligned_face is None:
            return original_frame

        source_emb = source_face.normed_embedding.reshape(1, -1).astype(np.float32)
        target_in = _prepare_target(aligned_face)
        out = _run_inference(source_emb, target_in)
        bgr_fake = _postprocess_swap(out)

        # Paste swapped face back into the target frame.
        dummy_aimg = np.empty((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.uint8)
        swapped_frame = _fast_paste_back(temp_frame, bgr_fake, dummy_aimg, M)
    except Exception as e:
        print(f"[{NAME}] Error during face swap: {e}")
        return original_frame

    # Build the shared face mask once if any masking feature needs it.
    feature_mask_active = (
        mouth_mask_enabled
        or quick_lip_enabled
        or chin_mask_enabled
        or eyes_mask_enabled
        or eyebrows_mask_enabled
    )
    shared_face_mask = (
        create_face_mask(target_face, original_frame) if feature_mask_active else None
    )

    if mouth_mask_enabled:
        mouth_mask, mouth_cutout, mouth_box, lower_lip_polygon = (
            create_lower_mouth_mask(target_face, original_frame)
        )
        if mouth_cutout is not None and mouth_box != (0, 0, 0, 0):
            swapped_frame = apply_mouth_area(
                swapped_frame, mouth_cutout, mouth_box, shared_face_mask, lower_lip_polygon
            )
            if getattr(modules.globals, "show_mouth_mask_box", False):
                swapped_frame = draw_mouth_mask_visualization(
                    swapped_frame,
                    target_face,
                    (mouth_mask, mouth_cutout, mouth_box, lower_lip_polygon),
                )

    # Quick lip mask — narrow lip-strip paste-back. Skipped when the
    # broader mouth mask is already active to avoid double pastes.
    if quick_lip_enabled and not mouth_mask_enabled:
        _, lip_cutout, lip_box, lip_polygon = create_quick_lip_mask(
            target_face, original_frame
        )
        if lip_cutout is not None and lip_box != (0, 0, 0, 0):
            swapped_frame = apply_mask_area(
                swapped_frame, lip_cutout, lip_box, shared_face_mask, lip_polygon
            )

    # Eyes mask — preserves the original eyes for sharp, expressive gaze.
    if eyes_mask_enabled:
        _, eyes_cutout, eyes_box, eyes_polygon = create_eyes_mask(
            target_face, original_frame
        )
        if eyes_cutout is not None and eyes_box != (0, 0, 0, 0):
            swapped_frame = apply_mask_area(
                swapped_frame, eyes_cutout, eyes_box, shared_face_mask, eyes_polygon
            )

    # Eyebrows mask — preserves brow detail/arch from the original.
    if eyebrows_mask_enabled:
        _, brow_cutout, brow_box, brow_polygon = create_eyebrows_mask(
            target_face, original_frame
        )
        if brow_cutout is not None and brow_box != (0, 0, 0, 0):
            swapped_frame = apply_mask_area(
                swapped_frame, brow_cutout, brow_box, shared_face_mask, brow_polygon
            )

    # Chin mask — feathers the jaw boundary toward the original.
    if chin_mask_enabled:
        _, chin_cutout, chin_box, chin_polygon = create_chin_mask(
            target_face, original_frame
        )
        if chin_cutout is not None and chin_box != (0, 0, 0, 0):
            swapped_frame = apply_mask_area(
                swapped_frame, chin_cutout, chin_box, shared_face_mask, chin_polygon
            )

    if getattr(modules.globals, "poisson_blend", False):
        face_mask = create_face_mask(target_face, temp_frame)
        if face_mask is not None:
            ys, xs = np.where(face_mask > 0)
            if len(xs) > 0 and len(ys) > 0:
                x_min, x_max = int(xs.min()), int(xs.max())
                y_min, y_max = int(ys.min()), int(ys.max())
                center = ((x_min + x_max) // 2, (y_min + y_max) // 2)
                src_crop = swapped_frame[y_min:y_max + 1, x_min:x_max + 1]
                mask_crop = face_mask[y_min:y_max + 1, x_min:x_max + 1]
                try:
                    swapped_frame = cv2.seamlessClone(
                        src_crop, original_frame, mask_crop, center,
                        cv2.NORMAL_CLONE,
                    )
                except Exception as e:
                    print(f"[{NAME}] Poisson blending failed: {e}")

    if opacity >= 1.0:
        return swapped_frame.astype(np.uint8)

    return gpu_add_weighted(
        original_frame.astype(np.uint8), 1.0 - opacity,
        swapped_frame.astype(np.uint8), opacity, 0,
    ).astype(np.uint8)


# ---------------------------------------------------------------------------
# Frame / image / video pipeline.
# ---------------------------------------------------------------------------


def process_frame(source_face: Face, temp_frame: Frame, target_face: Face = None) -> Frame:
    if getattr(modules.globals, "opacity", 1.0) == 0:
        return temp_frame

    processed_frame = temp_frame
    swapped_face_bboxes: List[np.ndarray] = []

    if modules.globals.many_faces:
        many = get_many_faces(processed_frame)
        if many:
            current = processed_frame.copy()
            for face in many:
                current = swap_face(source_face, face, current)
                if face is not None and hasattr(face, "bbox") and face.bbox is not None:
                    swapped_face_bboxes.append(face.bbox.astype(int))
            processed_frame = current
    else:
        if target_face is None:
            target_face = get_one_face(processed_frame)
        if target_face:
            processed_frame = swap_face(source_face, target_face, processed_frame)
            if hasattr(target_face, "bbox") and target_face.bbox is not None:
                swapped_face_bboxes.append(target_face.bbox.astype(int))
            if (
                getattr(modules.globals, "hair_color", 0.0) > 0
                or getattr(modules.globals, "hair_texture", 0.0) > 0
            ):
                processed_frame = _hair_swap.apply_hair_swap(
                    processed_frame, source_face, target_face, parse_every=1
                )

    return apply_post_processing(processed_frame, swapped_face_bboxes)


def process_frame_v2(temp_frame: Frame, temp_frame_path: str = "") -> Frame:
    if getattr(modules.globals, "opacity", 1.0) == 0:
        return temp_frame

    processed_frame = temp_frame
    swapped_face_bboxes: List[np.ndarray] = []
    source_target_pairs: List[tuple] = []

    source_target_map = getattr(modules.globals, "source_target_map", None)
    simple_map = getattr(modules.globals, "simple_map", None)
    is_file_target = modules.globals.target_path and (
        is_image(modules.globals.target_path) or is_video(modules.globals.target_path)
    )

    if is_file_target and source_target_map:
        if modules.globals.many_faces:
            source_face = default_source_face()
            if source_face:
                for map_data in source_target_map:
                    if is_image(modules.globals.target_path):
                        target_info = map_data.get("target", {})
                        target_face = target_info.get("face") if target_info else None
                        if target_face:
                            source_target_pairs.append((source_face, target_face))
                    elif is_video(modules.globals.target_path):
                        target_frames_data = map_data.get("target_faces_in_frame", [])
                        target_frames = [
                            f for f in target_frames_data
                            if f and f.get("location") == temp_frame_path
                        ]
                        for frame_data in target_frames:
                            for tf in frame_data.get("faces", []) or []:
                                source_target_pairs.append((source_face, tf))
        else:
            for map_data in source_target_map:
                source_info = map_data.get("source", {})
                source_face = source_info.get("face") if source_info else None
                if not source_face:
                    continue
                if is_image(modules.globals.target_path):
                    target_info = map_data.get("target", {})
                    target_face = target_info.get("face") if target_info else None
                    if target_face:
                        source_target_pairs.append((source_face, target_face))
                elif is_video(modules.globals.target_path):
                    target_frames_data = map_data.get("target_faces_in_frame", [])
                    target_frames = [
                        f for f in target_frames_data
                        if f and f.get("location") == temp_frame_path
                    ]
                    for frame_data in target_frames:
                        for tf in frame_data.get("faces", []) or []:
                            source_target_pairs.append((source_face, tf))
    else:
        detected = get_many_faces(processed_frame)
        if detected:
            if modules.globals.many_faces:
                source_face = default_source_face()
                if source_face:
                    for tf in detected:
                        source_target_pairs.append((source_face, tf))
            elif simple_map:
                source_faces = simple_map.get("source_faces", [])
                target_embeddings = simple_map.get("target_embeddings", [])
                if source_faces and target_embeddings and len(source_faces) == len(target_embeddings):
                    if len(detected) <= len(target_embeddings):
                        for d in detected:
                            if d.normed_embedding is None:
                                continue
                            idx, _ = find_closest_centroid(target_embeddings, d.normed_embedding)
                            if 0 <= idx < len(source_faces):
                                source_target_pairs.append((source_faces[idx], d))
                    else:
                        det_emb = [f.normed_embedding for f in detected if f.normed_embedding is not None]
                        det_faces = [f for f in detected if f.normed_embedding is not None]
                        if det_emb:
                            for i, te in enumerate(target_embeddings):
                                if 0 <= i < len(source_faces):
                                    idx, _ = find_closest_centroid(det_emb, te)
                                    if 0 <= idx < len(det_faces):
                                        source_target_pairs.append((source_faces[i], det_faces[idx]))
            else:
                source_face = default_source_face()
                target_face = get_one_face(processed_frame, detected)
                if source_face and target_face:
                    source_target_pairs.append((source_face, target_face))

    current = processed_frame.copy()
    for source_face, target_face in source_target_pairs:
        if source_face and target_face:
            current = swap_face(source_face, target_face, current)
            if target_face is not None and hasattr(target_face, "bbox") and target_face.bbox is not None:
                swapped_face_bboxes.append(target_face.bbox.astype(int))
    processed_frame = current

    return apply_post_processing(processed_frame, swapped_face_bboxes)


def process_frames(
    source_path: str, temp_frame_paths: List[str], progress: Any = None
) -> None:
    use_v2 = getattr(modules.globals, "map_faces", False)
    source_face = None

    if not use_v2:
        if not source_path or not os.path.exists(source_path):
            update_status(f"Source path invalid: {source_path}", NAME)
        else:
            try:
                src_img = cv2.imread(source_path)
                if src_img is None:
                    update_status(f"Could not read source: {source_path}", NAME)
                else:
                    source_face = get_one_face(src_img)
                    if source_face is None:
                        update_status(
                            f"No face detected in source {source_path}; "
                            "swaps will be skipped.",
                            NAME,
                        )
                    del src_img
            except Exception as e:
                update_status(f"Error analyzing source {source_path}: {e}", NAME)

    if not use_v2 and source_face is None:
        update_status("Halting: no source face for HyperSwap simple mode.", NAME)
        if progress:
            remaining = (len(temp_frame_paths) - progress.n
                         if hasattr(progress, "n") else len(temp_frame_paths))
            if remaining > 0:
                progress.update(remaining)
        return

    for temp_frame_path in temp_frame_paths:
        try:
            temp_frame = cv2.imread(temp_frame_path)
            if temp_frame is None:
                if progress:
                    progress.update(1)
                continue
        except Exception:
            if progress:
                progress.update(1)
            continue

        try:
            if use_v2:
                result_frame = process_frame_v2(temp_frame, temp_frame_path)
            else:
                result_frame = process_frame(source_face, temp_frame)
            if result_frame is None:
                result_frame = temp_frame
        except Exception as e:
            print(f"[{NAME}] Error processing {temp_frame_path}: {e}")
            result_frame = temp_frame

        try:
            cv2.imwrite(
                temp_frame_path, result_frame, [cv2.IMWRITE_PNG_COMPRESSION, 3]
            )
        except Exception as e:
            print(f"[{NAME}] Error writing {temp_frame_path}: {e}")

        if progress:
            progress.update(1)


def process_image(source_path: str, target_path: str, output_path: str) -> None:
    use_v2 = getattr(modules.globals, "map_faces", False)
    _hair_swap.reset_state()
    _hair_swap.reset_source_cache()
    target_frame = cv2.imread(target_path)
    if target_frame is None:
        update_status(f"Could not read target: {target_path}", NAME)
        return
    try:
        if use_v2:
            result = process_frame_v2(target_frame, target_path)
        else:
            src_img = cv2.imread(source_path)
            if src_img is None:
                update_status(f"Could not read source: {source_path}", NAME)
                return
            source_face = get_one_face(src_img)
            if not source_face:
                update_status(f"No face in source: {source_path}", NAME)
                return
            result = process_frame(source_face, target_frame)
        if result is not None:
            if cv2.imwrite(output_path, result):
                update_status(f"Output saved to {output_path}", NAME)
            else:
                update_status(f"Failed to write {output_path}", NAME)
    except Exception as e:
        update_status(f"Image processing error: {e}", NAME)


def process_video(source_path: str, temp_frame_paths: List[str]) -> None:
    update_status("Processing video with HyperSwap face-swap pipeline.", NAME)
    _hair_swap.reset_state()
    _hair_swap.reset_source_cache()
    modules.processors.frame.core.process_video(
        source_path, temp_frame_paths, process_frames
    )
