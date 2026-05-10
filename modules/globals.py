# --- START OF FILE globals.py ---

import os
from typing import List, Dict, Any

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKFLOW_DIR = os.path.join(ROOT_DIR, "workflow")

file_types = [
    ("Image", ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.bmp")),
    ("Video", ("*.mp4", "*.mkv")),
]

# Face Mapping Data
source_target_map: List[Dict[str, Any]] = [] # Stores detailed map for image/video processing
simple_map: Dict[str, Any] = {}             # Stores simplified map (embeddings/faces) for live/simple mode

# Paths
source_path: str | None = None
target_path: str | None = None
output_path: str | None = None

# Processing Options
frame_processors: List[str] = []
keep_fps: bool = True
keep_audio: bool = True
keep_frames: bool = False
many_faces: bool = False         # Process all detected faces with default source
map_faces: bool = False          # Use source_target_map or simple_map for specific swaps
poisson_blend: bool = False      # Enable Poisson Blending for smoother face swaps
color_correction: bool = False   # Enable color correction (implementation specific)
nsfw_filter: bool = False

# Video Output Options
video_encoder: str | None = None
video_quality: int | None = None # Typically a CRF value or bitrate

# Live Mode Options
live_mirror: bool = False
live_resizable: bool = True
camera_input_combobox: Any | None = None # Placeholder for UI element if needed
webcam_preview_running: bool = False
show_fps: bool = False

# System Configuration
max_memory: int | None = None        # Memory limit in GB? (Needs clarification)
execution_providers: List[str] = []  # e.g., ['CUDAExecutionProvider', 'CPUExecutionProvider']
execution_threads: int | None = None # Number of threads for CPU execution
headless: bool | None = None         # Run without UI?
log_level: str = "error"             # Logging level (e.g., 'debug', 'info', 'warning', 'error')

# Face Processor UI Toggles. HyperSwap is the default head/face swap engine.
fp_ui: Dict[str, bool] = {"face_swapper": False, "face_swapper_hyperswap": True, "face_enhancer": False, "face_enhancer_gpen256": False, "face_enhancer_gpen512": False}

# Face Swapper Specific Options
face_swapper_enabled: bool = True # General toggle for the swapper processor
opacity: float = 1.0              # Blend factor for the swapped face (0.0-1.0)
sharpness: float = 0.0            # Sharpness enhancement for swapped face (0.0-1.0+)

# Mouth Mask Options
mouth_mask: bool = False           # Enable mouth area masking/pasting
show_mouth_mask_box: bool = False  # Visualize the mouth mask area (for debugging)
mask_feather_ratio: int = 12       # Denominator for feathering calculation (higher = smaller feather)
mask_down_size: float = 0.1        # Expansion factor for lower lip mask (relative)
mask_size: float = 1.0             # Expansion factor for upper lip mask (relative)
mouth_mask_size: float = 0.0       # Mouth mask size (0-100; 0=off, 100=mouth to chin)

# Quick Lip Mask — fast, narrow lip-only paste-back (preserves the
# original talker's lip motion without exposing the whole mouth-to-chin
# area like ``mouth_mask`` does). Driven by ``quick_lip_size`` (0=off).
quick_lip_mask: bool = False
quick_lip_size: float = 0.0        # 0-100; >0 enables the quick lip paste-back

# Chin Mask — eliminates harsh jaw boundaries by feathering the chin/jaw
# region of the swap toward the original. ``chin_mask_size`` controls the
# vertical extent (0-100; 0=off).
chin_mask: bool = False
chin_mask_size: float = 0.0

# Eyes Mask — keeps eyes sharp/expressive by pasting the original eye
# region back onto the swap. ``eyes_mask_size`` widens the elliptical
# coverage area (0-100; 0=off).
eyes_mask: bool = False
eyes_mask_size: float = 0.0

# Eyebrows Mask — preserves eyebrow detail and arch from the original.
# ``eyebrows_mask_size`` controls the curved-brow padding (0-100; 0=off).
eyebrows_mask: bool = False
eyebrows_mask_size: float = 0.0

# Forehead / Head Match Options
# Both 0-100. Default 0 keeps the legacy face-only ellipse and forehead extension.
# Higher values stretch the swap blend region (and the face mask used for Poisson
# blending) upward and laterally so more of the head/forehead identity from the
# source is matched onto the target.
forehead_size: float = 0.0         # Vertical forehead/head extension (0=off, 100=full head)
forehead_width: float = 0.0        # Lateral forehead/head extension (0=off, 100=widest)

# Hair Match Options. Recolor the target's actual hair toward the source's
# color (LAB Reinhard transfer) and optionally match its high-frequency
# luminance texture. Disabled when both are 0; not active in many_faces /
# map_faces modes today.
hair_color: float = 0.0            # Color match strength toward source hair (0-100)
hair_texture: float = 0.0          # Hi-freq luminance variance match strength (0-100)

# --- START: Added for Frame Interpolation ---
enable_interpolation: bool = True # Toggle temporal smoothing
interpolation_weight: float = 0  # Blend weight for current frame (0.0-1.0). Lower=smoother.
# --- END: Added for Frame Interpolation ---

# --- Face Enhancer Scaler ---
# 0..100 slider that scales how much of the enhanced face is blended
# back over the original. 0 = original (enhancer disabled in practice),
# 100 = full enhanced face. Applies to GFPGAN, GPEN-256 and GPEN-512.
enhancer_blend: float = 100.0

# --- Realtime Face Enhancer ---
# Frame-skip stride for live mode. The enhancer runs ONNX inference once
# every ``live_enhance_skip`` frames and pastes the cached enhanced
# face back on the in-between frames so live FPS stays high. 1 = run
# every frame (highest fidelity, lowest FPS); larger = faster but lossier.
live_enhance_skip: int = 2

# --- Camera / Output / UI (2.7 parity) ---
# Virtual Camera (OBS-friendly). When enabled the live swap pipeline pushes
# every processed frame to a system virtual webcam (pyvirtualcam) so OBS,
# Zoom, Meet, etc. can pick it up as "Phantom Cast Virtual Camera".
virtual_cam_enabled: bool = False
virtual_cam_active: bool = False    # runtime: did we actually open the device?
virtual_cam_backend: str = ""       # runtime: backend name, e.g. "obs" / "v4l2"

# Live Recorder — save every processed frame to disk so users can review
# the swap output later without the real-time preview lag they may see
# on slower hardware (e.g. Apple Silicon Macs). Saved at the actual
# measured pipeline FPS so playback runs at real-time speed.
record_live_enabled: bool = False
record_live_active: bool = False    # runtime: did we open a writer?
record_last_path: str = ""          # runtime: last finalised file path

# Resolution Switch — preferred capture resolution for the live cam.
# "Auto" uses the legacy 1920x1080@60 request and lets the camera pick.
live_resolution: str = "Auto"       # one of: Auto, 480p, 720p, 1080p, 1440p, 4K
live_resolution_options: tuple = (
    "Auto", "480p", "720p", "1080p", "1440p", "4K",
)

# Optimized Rendering — auto-tune detection stride / preview scale based on
# measured FPS. When off the legacy fixed-stride live loop runs.
optimized_rendering: bool = True

# LUTs for color grading — cinematic .cube LUT applied as the final step
# before the frame leaves the swap pipeline. ``lut_path`` is None when no
# LUT is loaded; ``lut_strength`` is the 0..100 blend with the ungraded image.
lut_path: str | None = None
lut_name: str = "None"              # display name for UI
lut_strength: float = 100.0         # 0..100; 0 = no grade, 100 = full LUT

# Window Projection — borderless pop-out feed for OBS window-capture.
# 0 = legacy preview (windowed Toplevel), 1 = borderless overrideredirect,
# 2 = fullscreen ("F" toggle in the projection window also flips this).
projection_mode: int = 0

# In-Window Preview — render the live swap inside the main dashboard rather
# than as a separate Toplevel. Mutually exclusive with Window Projection.
in_window_preview: bool = False

# GPU Changer / Multi-GPU support — index of the CUDA device the swap engine
# runs on. 0 = primary GPU. Used when constructing onnxruntime sessions and
# exported to CUDA_VISIBLE_DEVICES at startup.
gpu_device_id: int = 0
gpu_device_count: int = 1           # populated at startup by detect_gpus()
gpu_device_names: list = []         # populated at startup

# Forced GPU usage on laptops — sets NVIDIA Optimus / AMD Switchable env
# vars before onnxruntime imports the CUDA loader so dual-GPU laptops use
# the discrete card instead of the iGPU.
force_discrete_gpu: bool = False

# --- END OF FILE globals.py ---

import threading
dml_lock = threading.Lock()
