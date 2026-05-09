import os
import webbrowser
import customtkinter as ctk
from typing import Callable, Tuple
import cv2
from modules.gpu_processing import gpu_cvt_color, gpu_resize, gpu_flip
from PIL import Image, ImageOps
import time
import json
import queue
import threading
import numpy as np
import requests
import tempfile
import modules.globals
import modules.metadata
from modules.face_analyser import (
    get_one_face,
    get_many_faces,
    detect_one_face_fast,
    detect_many_faces_fast,
    get_unique_faces_from_target_image,
    get_unique_faces_from_target_video,
    add_blank_map,
    has_valid_map,
    simplify_maps,
)
from modules.capturer import get_video_frame, get_video_frame_total
from modules.processors.frame.core import get_frame_processors_modules
from modules.processors.frame import hair_swap as _hair_swap
from modules.utilities import (
    is_image,
    is_video,
    resolve_relative_path,
    has_image_extension,
)
from modules.video_capture import VideoCapturer
from modules.gettext import LanguageManager
from modules.ui_tooltip import ToolTip
from modules import globals
from modules.output_pipeline import (
    LutTable,
    apply_force_discrete_gpu,
    apply_lut,
    detect_gpus,
    get_virtual_cam,
    load_cube_lut,
    resolution_labels,
    resolution_map,
    set_cuda_visible_device,
)
import platform

if platform.system() == "Windows":
    from pygrabber.dshow_graph import FilterGraph

# --- Tk 9.0 compatibility patch ---
# In Tk 9.0, Menu.index("end") returns "" instead of raising TclError
# when the menu is empty. CustomTkinter's CTkOptionMenu doesn't handle
# this, causing crashes. This patch adds the missing guard.
try:
    from customtkinter.windows.widgets.core_widget_classes import DropdownMenu as _DropdownMenu

    _original_add_menu_commands = _DropdownMenu._add_menu_commands

    def _patched_add_menu_commands(self, *args, **kwargs):
        try:
            end_index = self._menu.index("end")
            if end_index == "" or end_index is None:
                return
        except Exception:
            pass
        _original_add_menu_commands(self, *args, **kwargs)

    _DropdownMenu._add_menu_commands = _patched_add_menu_commands
except (ImportError, AttributeError):
    pass  # CustomTkinter version doesn't have this class path
# --- End Tk 9.0 patch ---

ROOT = None
POPUP = None
POPUP_LIVE = None
ROOT_HEIGHT = 800
ROOT_WIDTH = 600

PREVIEW = None
PREVIEW_MAX_HEIGHT = 700
PREVIEW_MAX_WIDTH = 1200
PREVIEW_DEFAULT_WIDTH = 640
PREVIEW_DEFAULT_HEIGHT = 360

POPUP_WIDTH = 750
POPUP_HEIGHT = 810
POPUP_SCROLL_WIDTH = (740,)
POPUP_SCROLL_HEIGHT = 700

POPUP_LIVE_WIDTH = 900
POPUP_LIVE_HEIGHT = 820
POPUP_LIVE_SCROLL_WIDTH = (890,)
POPUP_LIVE_SCROLL_HEIGHT = 700

MAPPER_PREVIEW_MAX_HEIGHT = 100
MAPPER_PREVIEW_MAX_WIDTH = 100

DEFAULT_BUTTON_WIDTH = 200
DEFAULT_BUTTON_HEIGHT = 40

RECENT_DIRECTORY_SOURCE = None
RECENT_DIRECTORY_TARGET = None
RECENT_DIRECTORY_OUTPUT = None

_ = None
preview_label = None
preview_slider = None
source_label = None
target_label = None
status_label = None
popup_status_label = None
popup_status_label_live = None
source_label_dict = {}
source_label_dict_live = {}
target_label_dict_live = {}

# In-Window Preview / Window Projection
in_window_preview_label = None      # CTkLabel embedded in main dashboard
projection_window = None            # CTkToplevel created on demand
projection_label = None             # label inside projection_window
camera_optionmenu_ref = None        # set in create_root for camera-refresh
camera_indices_ref: list = []       # mutable so refresh can update closures
camera_names_ref: list = []
resolution_optionmenu_ref = None
gpu_optionmenu_ref = None
lut_status_label = None
vcam_status_label = None

# Loaded LUT state — kept here so the live thread can read without ui-import
_active_lut: LutTable | None = None
_active_lut_path: str | None = None

# Active VideoCapturer for the live preview — exposed so the resolution
# changer / camera refresh can reach it without restarting the threads.
_active_capturer: VideoCapturer | None = None

img_ft, vid_ft = modules.globals.file_types


def init(start: Callable[[], None], destroy: Callable[[], None], lang: str) -> ctk.CTk:
    global ROOT, PREVIEW, _

    lang_manager = LanguageManager(lang)
    _ = lang_manager._
    ROOT = create_root(start, destroy)
    PREVIEW = create_preview(ROOT)

    return ROOT


def save_switch_states():
    switch_states = {
        "keep_fps": modules.globals.keep_fps,
        "keep_audio": modules.globals.keep_audio,
        "keep_frames": modules.globals.keep_frames,
        "many_faces": modules.globals.many_faces,
        "map_faces": modules.globals.map_faces,
        "poisson_blend": modules.globals.poisson_blend,
        "color_correction": modules.globals.color_correction,
        "nsfw_filter": modules.globals.nsfw_filter,
        "live_mirror": modules.globals.live_mirror,
        "live_resizable": modules.globals.live_resizable,
        "fp_ui": modules.globals.fp_ui,
        "show_fps": modules.globals.show_fps,
        "mouth_mask": modules.globals.mouth_mask,
        "show_mouth_mask_box": modules.globals.show_mouth_mask_box,
        "mouth_mask_size": modules.globals.mouth_mask_size,
        "quick_lip_size": modules.globals.quick_lip_size,
        "chin_mask_size": modules.globals.chin_mask_size,
        "eyes_mask_size": modules.globals.eyes_mask_size,
        "eyebrows_mask_size": modules.globals.eyebrows_mask_size,
        "forehead_size": modules.globals.forehead_size,
        "forehead_width": modules.globals.forehead_width,
        "hair_color": modules.globals.hair_color,
        "hair_texture": modules.globals.hair_texture,
        "enhancer_blend": modules.globals.enhancer_blend,
        "live_enhance_skip": modules.globals.live_enhance_skip,
        "enable_interpolation": modules.globals.enable_interpolation,
        "interpolation_weight": modules.globals.interpolation_weight,
        "virtual_cam_enabled": modules.globals.virtual_cam_enabled,
        "live_resolution": modules.globals.live_resolution,
        "optimized_rendering": modules.globals.optimized_rendering,
        "lut_path": modules.globals.lut_path,
        "lut_name": modules.globals.lut_name,
        "lut_strength": modules.globals.lut_strength,
        "projection_mode": modules.globals.projection_mode,
        "in_window_preview": modules.globals.in_window_preview,
        "gpu_device_id": modules.globals.gpu_device_id,
        "force_discrete_gpu": modules.globals.force_discrete_gpu,
    }
    with open("switch_states.json", "w") as f:
        json.dump(switch_states, f)


def load_switch_states():
    try:
        with open("switch_states.json", "r") as f:
            switch_states = json.load(f)
        modules.globals.keep_fps = switch_states.get("keep_fps", True)
        modules.globals.keep_audio = switch_states.get("keep_audio", True)
        modules.globals.keep_frames = switch_states.get("keep_frames", False)
        modules.globals.many_faces = switch_states.get("many_faces", False)
        modules.globals.map_faces = switch_states.get("map_faces", False)
        modules.globals.poisson_blend = switch_states.get("poisson_blend", False)
        modules.globals.color_correction = switch_states.get("color_correction", False)
        modules.globals.nsfw_filter = switch_states.get("nsfw_filter", False)
        modules.globals.live_mirror = switch_states.get("live_mirror", False)
        modules.globals.live_resizable = switch_states.get("live_resizable", False)
        modules.globals.fp_ui = switch_states.get("fp_ui", {"face_enhancer": False})
        # HyperSwap is the default head/face swap engine.
        modules.globals.fp_ui.setdefault("face_swapper", False)
        modules.globals.fp_ui.setdefault("face_swapper_hyperswap", True)
        # Drop legacy keys that no longer correspond to a processor module.
        modules.globals.fp_ui.pop("hair_swapper", None)
        modules.globals.show_fps = switch_states.get("show_fps", False)
        modules.globals.mouth_mask_size = switch_states.get("mouth_mask_size", 0.0)
        # mouth_mask is driven by the slider: on if size > 0, off if 0
        modules.globals.mouth_mask = modules.globals.mouth_mask_size > 0
        modules.globals.show_mouth_mask_box = False  # always start hidden
        # New mask sliders — boolean toggles auto-derive from size > 0.
        modules.globals.quick_lip_size = switch_states.get("quick_lip_size", 0.0)
        modules.globals.quick_lip_mask = modules.globals.quick_lip_size > 0
        modules.globals.chin_mask_size = switch_states.get("chin_mask_size", 0.0)
        modules.globals.chin_mask = modules.globals.chin_mask_size > 0
        modules.globals.eyes_mask_size = switch_states.get("eyes_mask_size", 0.0)
        modules.globals.eyes_mask = modules.globals.eyes_mask_size > 0
        modules.globals.eyebrows_mask_size = switch_states.get("eyebrows_mask_size", 0.0)
        modules.globals.eyebrows_mask = modules.globals.eyebrows_mask_size > 0
        modules.globals.forehead_size = switch_states.get("forehead_size", 0.0)
        modules.globals.forehead_width = switch_states.get("forehead_width", 0.0)
        # New keys take precedence; fall back to legacy hair_swap_strength
        # / hair_coverage so existing switch_states.json files don't reset
        # the user's prior selection on first run after the upgrade.
        modules.globals.hair_color = switch_states.get(
            "hair_color", switch_states.get("hair_swap_strength", 0.0)
        )
        modules.globals.hair_texture = switch_states.get("hair_texture", 0.0)
        modules.globals.enhancer_blend = switch_states.get("enhancer_blend", 100.0)
        modules.globals.live_enhance_skip = int(
            switch_states.get("live_enhance_skip", 2)
        )
        modules.globals.enable_interpolation = switch_states.get(
            "enable_interpolation", True
        )
        modules.globals.interpolation_weight = switch_states.get(
            "interpolation_weight", 0.0
        )
        # Camera / Output / UI (2.7 parity)
        modules.globals.virtual_cam_enabled = bool(
            switch_states.get("virtual_cam_enabled", False)
        )
        modules.globals.live_resolution = switch_states.get(
            "live_resolution", "Auto"
        )
        modules.globals.optimized_rendering = bool(
            switch_states.get("optimized_rendering", True)
        )
        modules.globals.lut_path = switch_states.get("lut_path", None)
        modules.globals.lut_name = switch_states.get("lut_name", "None")
        modules.globals.lut_strength = float(
            switch_states.get("lut_strength", 100.0)
        )
        modules.globals.projection_mode = int(
            switch_states.get("projection_mode", 0)
        )
        modules.globals.in_window_preview = bool(
            switch_states.get("in_window_preview", False)
        )
        modules.globals.gpu_device_id = int(
            switch_states.get("gpu_device_id", 0)
        )
        modules.globals.force_discrete_gpu = bool(
            switch_states.get("force_discrete_gpu", False)
        )
        # Eagerly load the LUT if a path was persisted
        global _active_lut, _active_lut_path
        if modules.globals.lut_path and os.path.isfile(modules.globals.lut_path):
            try:
                _active_lut = load_cube_lut(modules.globals.lut_path)
                _active_lut_path = modules.globals.lut_path
            except Exception as e:
                print(f"[ui] failed to reload LUT {modules.globals.lut_path}: {e}")
                modules.globals.lut_path = None
                modules.globals.lut_name = "None"
        # Apply the discrete-GPU hint if set (must run before CUDA loads)
        if modules.globals.force_discrete_gpu:
            apply_force_discrete_gpu()
        if modules.globals.gpu_device_id:
            set_cuda_visible_device(modules.globals.gpu_device_id)
    except FileNotFoundError:
        # If the file doesn't exist, use default values
        pass


def create_root(start: Callable[[], None], destroy: Callable[[], None]) -> ctk.CTk:
    global source_label, target_label, status_label, show_fps_switch

    load_switch_states()

    ctk.deactivate_automatic_dpi_awareness()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme(resolve_relative_path("ui.json"))

    # Palette tokens (mirror of website/assets/css/styles.css :root)
    BG = "#0a0a0f"
    BG_ELEV = "#13131a"
    BG_ELEV_2 = "#1a1a23"
    BORDER = "#26262e"
    BORDER_HI = "#34343f"
    TEXT = "#e4e4e7"
    TEXT_MUTED = "#a1a1aa"
    TEXT_DIM = "#71717a"
    VIOLET = "#8b5cf6"
    VIOLET_HI = "#a78bfa"
    CYAN = "#22d3ee"
    EMERALD = "#10b981"
    EMERALD_HI = "#34d399"
    ROSE = "#ef4444"

    PAD = 14

    root = ctk.CTk()
    root.geometry(f"760x980")
    root.minsize(680, 820)
    root.title(
        f"Phantom Cast {modules.metadata.version} — {modules.metadata.edition}"
    )
    root.configure(fg_color=BG)
    root.protocol("WM_DELETE_WINDOW", lambda: destroy())

    # ===================== HEADER =====================
    header = ctk.CTkFrame(root, fg_color="transparent", border_width=0, corner_radius=0)
    header.pack(side="top", fill="x", padx=PAD, pady=(PAD, 6))

    brand_wrap = ctk.CTkFrame(header, fg_color="transparent", border_width=0)
    brand_wrap.pack(side="left")

    brand_dot = ctk.CTkLabel(
        brand_wrap, text="◆", text_color=VIOLET,
        font=ctk.CTkFont(size=16, weight="bold"),
    )
    brand_dot.pack(side="left", padx=(0, 6))

    brand_label = ctk.CTkLabel(
        brand_wrap, text="Phantom Cast",
        font=ctk.CTkFont(size=18, weight="bold"),
        text_color=TEXT,
    )
    brand_label.pack(side="left")

    version_chip = ctk.CTkLabel(
        brand_wrap,
        text=f"v{modules.metadata.version}",
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color=VIOLET,
        fg_color="#1c1530",
        corner_radius=999,
        padx=10,
        pady=2,
    )
    version_chip.pack(side="left", padx=(10, 0))

    gpu_chip = ctk.CTkLabel(
        header,
        text="●  GPU active",
        font=ctk.CTkFont(size=10, weight="bold"),
        text_color=EMERALD,
        fg_color="#0d1f17",
        corner_radius=999,
        padx=10,
        pady=2,
    )
    gpu_chip.pack(side="right")

    # ===================== FOOTER (bottom) =====================
    footer = ctk.CTkFrame(root, fg_color="transparent", border_width=0, corner_radius=0)
    footer.pack(side="bottom", fill="x", padx=PAD, pady=(0, PAD))

    donate_label = ctk.CTkLabel(
        footer,
        text="deeplivecam.net",
        cursor="hand2",
        text_color=CYAN,
        font=ctk.CTkFont(size=11, underline=True),
    )
    donate_label.pack(side="right")
    donate_label.bind(
        "<Button>", lambda event: webbrowser.open("https://deeplivecam.net")
    )

    status_label = ctk.CTkLabel(
        footer,
        text="",
        justify="left",
        anchor="w",
        text_color=TEXT_MUTED,
        font=ctk.CTkFont(size=11),
    )
    status_label.pack(side="left", fill="x", expand=True)

    # ===================== ACTION BAR (above footer) =====================
    action_bar = ctk.CTkFrame(
        root,
        fg_color=BG_ELEV,
        border_width=1,
        border_color=BORDER,
        corner_radius=14,
    )
    action_bar.pack(side="bottom", fill="x", padx=PAD, pady=(0, 10))

    action_inner = ctk.CTkFrame(action_bar, fg_color="transparent", border_width=0)
    action_inner.pack(fill="x", padx=14, pady=12)

    # Left side: camera + Live
    cam_label = ctk.CTkLabel(
        action_inner,
        text=_("Camera"),
        text_color=TEXT_DIM,
        font=ctk.CTkFont(size=11),
    )
    cam_label.pack(side="left", padx=(0, 8))

    global camera_optionmenu_ref, camera_indices_ref, camera_names_ref

    available_cameras = get_available_cameras()
    camera_indices, camera_names = available_cameras
    camera_indices_ref[:] = list(camera_indices)
    camera_names_ref[:] = list(camera_names)

    if not camera_names or camera_names[0] == "No cameras found":
        camera_variable = ctk.StringVar(value="No cameras found")
        camera_optionmenu = ctk.CTkOptionMenu(
            action_inner,
            variable=camera_variable,
            values=["No cameras found"],
            state="disabled",
            width=180,
            height=34,
        )
    else:
        camera_variable = ctk.StringVar(value=camera_names[0])
        camera_optionmenu = ctk.CTkOptionMenu(
            action_inner,
            variable=camera_variable,
            values=camera_names,
            width=180,
            height=34,
        )
    camera_optionmenu.pack(side="left", padx=(0, 6))
    camera_optionmenu_ref = camera_optionmenu
    ToolTip(camera_optionmenu, _("Select which camera to use for live mode"))

    # Camera Refresh button — hot-swap webcam without restarting the app.
    refresh_btn = ctk.CTkButton(
        action_inner,
        text="↻",
        cursor="hand2",
        width=34,
        height=34,
        fg_color=BG_ELEV_2,
        hover_color=BORDER_HI,
        text_color=TEXT,
        border_width=1,
        border_color=BORDER,
        font=ctk.CTkFont(size=14, weight="bold"),
        command=lambda: refresh_cameras(camera_variable),
    )
    refresh_btn.pack(side="left", padx=(0, 8))
    ToolTip(
        refresh_btn,
        _("Re-enumerate connected cameras (hot-swap without restart)"),
    )

    def _selected_camera_index():
        names = camera_names_ref
        if not names or names[0] == "No cameras found":
            return None
        try:
            return camera_indices_ref[names.index(camera_variable.get())]
        except (ValueError, IndexError):
            return camera_indices_ref[0] if camera_indices_ref else None

    live_button = ctk.CTkButton(
        action_inner,
        text="● " + _("Live"),
        cursor="hand2",
        fg_color=EMERALD,
        hover_color=EMERALD_HI,
        text_color=BG,
        height=34,
        width=84,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: webcam_preview(root, _selected_camera_index()),
        state=(
            "normal"
            if camera_names and camera_names[0] != "No cameras found"
            else "disabled"
        ),
    )
    live_button.pack(side="left")
    ToolTip(live_button, _("Start real-time face swap using webcam"))

    # Realtime Video Player — watch a saved video with the swap applied
    # in real time, no rendering step.
    play_video_button = ctk.CTkButton(
        action_inner,
        text="▶ " + _("Play"),
        cursor="hand2",
        fg_color=BG_ELEV_2,
        hover_color=BORDER_HI,
        text_color=TEXT,
        border_width=1,
        border_color=BORDER,
        height=34,
        width=78,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: realtime_video_player(root),
    )
    play_video_button.pack(side="left", padx=(8, 0))
    ToolTip(
        play_video_button,
        _("Realtime Video Player — apply the swap to a saved video as it plays"),
    )

    # Right side: Destroy, Start, Preview
    stop_button = ctk.CTkButton(
        action_inner,
        text=_("Destroy"),
        cursor="hand2",
        fg_color=BG_ELEV_2,
        hover_color=ROSE,
        text_color=TEXT,
        border_width=1,
        border_color=BORDER,
        height=34,
        width=92,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: destroy(),
    )
    stop_button.pack(side="right", padx=(8, 0))
    ToolTip(stop_button, _("Stop processing and close the application"))

    start_button = ctk.CTkButton(
        action_inner,
        text="▶  " + _("Start"),
        cursor="hand2",
        fg_color=VIOLET,
        hover_color=VIOLET_HI,
        text_color=BG,
        height=34,
        width=110,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: analyze_target(start, root),
    )
    start_button.pack(side="right", padx=(8, 0))
    ToolTip(start_button, _("Begin processing the target image/video with selected face"))

    preview_button = ctk.CTkButton(
        action_inner,
        text=_("Preview"),
        cursor="hand2",
        fg_color=BG_ELEV_2,
        hover_color=BORDER_HI,
        text_color=TEXT,
        border_width=1,
        border_color=BORDER,
        height=34,
        width=92,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: toggle_preview(),
    )
    preview_button.pack(side="right", padx=(8, 0))
    ToolTip(preview_button, _("Show/hide a preview of the processed output"))

    # ===================== MAIN SCROLLABLE AREA =====================
    main = ctk.CTkScrollableFrame(
        root, fg_color="transparent", border_width=0, corner_radius=0
    )
    main.pack(side="top", fill="both", expand=True, padx=PAD - 4, pady=(2, 4))

    def make_card(parent, eyebrow, title):
        card = ctk.CTkFrame(
            parent,
            fg_color=BG_ELEV,
            border_width=1,
            border_color=BORDER,
            corner_radius=14,
        )
        card.pack(fill="x", padx=4, pady=(0, 12))
        head = ctk.CTkFrame(card, fg_color="transparent", border_width=0)
        head.pack(fill="x", padx=18, pady=(14, 0))
        eb = ctk.CTkLabel(
            head,
            text=eyebrow.upper(),
            text_color=CYAN,
            font=ctk.CTkFont(size=10, weight="bold"),
            anchor="w",
        )
        eb.pack(anchor="w")
        ttl = ctk.CTkLabel(
            head,
            text=title,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT,
            anchor="w",
        )
        ttl.pack(anchor="w", pady=(2, 0))
        return card

    # ===================== SOURCES CARD =====================
    sources_card = make_card(main, "01 — Inputs", _("Sources"))
    sources_inner = ctk.CTkFrame(sources_card, fg_color="transparent", border_width=0)
    sources_inner.pack(fill="x", padx=18, pady=(12, 18))
    sources_inner.grid_columnconfigure(0, weight=1, uniform="sources")
    sources_inner.grid_columnconfigure(1, weight=0)
    sources_inner.grid_columnconfigure(2, weight=1, uniform="sources")

    # Source preview slot
    source_slot = ctk.CTkFrame(
        sources_inner,
        fg_color=BG_ELEV_2,
        border_width=1,
        border_color=BORDER,
        corner_radius=12,
        height=170,
    )
    source_slot.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    source_slot.grid_propagate(False)
    source_slot.grid_columnconfigure(0, weight=1)
    source_slot.grid_rowconfigure(0, weight=1)

    source_label = ctk.CTkLabel(
        source_slot,
        text=_("No face\nselected"),
        text_color=TEXT_DIM,
        font=ctk.CTkFont(size=12),
    )
    source_label.grid(row=0, column=0, sticky="nsew")

    # Center swap column
    swap_col = ctk.CTkFrame(sources_inner, fg_color="transparent", border_width=0)
    swap_col.grid(row=0, column=1, padx=4, sticky="ns")

    swap_faces_button = ctk.CTkButton(
        swap_col,
        text="⇄",
        cursor="hand2",
        width=42,
        height=42,
        fg_color=BG_ELEV_2,
        hover_color=BORDER_HI,
        text_color=TEXT,
        border_width=1,
        border_color=BORDER,
        font=ctk.CTkFont(size=18, weight="bold"),
        corner_radius=999,
        command=lambda: swap_faces_paths(),
    )
    swap_faces_button.place(relx=0.5, rely=0.5, anchor="center")
    ToolTip(swap_faces_button, _("Swap source and target images"))

    # Target preview slot
    target_slot = ctk.CTkFrame(
        sources_inner,
        fg_color=BG_ELEV_2,
        border_width=1,
        border_color=BORDER,
        corner_radius=12,
        height=170,
    )
    target_slot.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
    target_slot.grid_propagate(False)
    target_slot.grid_columnconfigure(0, weight=1)
    target_slot.grid_rowconfigure(0, weight=1)

    target_label = ctk.CTkLabel(
        target_slot,
        text=_("No target\nselected"),
        text_color=TEXT_DIM,
        font=ctk.CTkFont(size=12),
    )
    target_label.grid(row=0, column=0, sticky="nsew")

    # Buttons row
    btn_row = ctk.CTkFrame(sources_inner, fg_color="transparent", border_width=0)
    btn_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(12, 0))
    btn_row.grid_columnconfigure(0, weight=1)
    btn_row.grid_columnconfigure(2, weight=1)

    select_face_button = ctk.CTkButton(
        btn_row,
        text=_("Select a face"),
        cursor="hand2",
        height=36,
        fg_color=VIOLET,
        hover_color=VIOLET_HI,
        text_color=BG,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: select_source_path(),
    )
    select_face_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ToolTip(
        select_face_button,
        _("Choose the source face image to swap onto the target"),
    )

    random_face_button = ctk.CTkButton(
        btn_row,
        text="🎲",
        cursor="hand2",
        width=44,
        height=36,
        fg_color=BG_ELEV_2,
        hover_color=BORDER_HI,
        text_color=TEXT,
        border_width=1,
        border_color=BORDER,
        command=lambda: fetch_random_face(),
    )
    random_face_button.grid(row=0, column=1, padx=4)
    ToolTip(
        random_face_button,
        _("Get a random face from thispersondoesnotexist.com"),
    )

    select_target_button = ctk.CTkButton(
        btn_row,
        text=_("Select a target"),
        cursor="hand2",
        height=36,
        fg_color=VIOLET,
        hover_color=VIOLET_HI,
        text_color=BG,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: select_target_path(),
    )
    select_target_button.grid(row=0, column=2, sticky="ew", padx=(6, 0))
    ToolTip(
        select_target_button,
        _("Choose the target image or video to apply face swap to"),
    )

    # ===================== PIPELINE CARD =====================
    pipeline_card = make_card(main, "02 — Models", _("Pipeline"))
    pipeline_inner = ctk.CTkFrame(
        pipeline_card, fg_color="transparent", border_width=0
    )
    pipeline_inner.pack(fill="x", padx=18, pady=(12, 18))
    pipeline_inner.grid_columnconfigure(0, weight=1, uniform="pipe")
    pipeline_inner.grid_columnconfigure(1, weight=1, uniform="pipe")

    swapper_options = ["Inswapper-128", "Hyperswap-256"]
    swapper_key_map = {
        "Inswapper-128": "face_swapper",
        "Hyperswap-256": "face_swapper_hyperswap",
    }
    if modules.globals.fp_ui.get("face_swapper_hyperswap", False):
        initial_swapper = "Hyperswap-256"
    else:
        initial_swapper = "Inswapper-128"
    swapper_variable = ctk.StringVar(value=initial_swapper)

    def on_swapper_change(choice: str):
        for key in swapper_key_map.values():
            update_tumbler(key, False)
        selected_key = swapper_key_map.get(choice)
        if selected_key:
            update_tumbler(selected_key, True)
        save_switch_states()

    swapper_label = ctk.CTkLabel(
        pipeline_inner,
        text=_("Face Swap Model"),
        text_color=TEXT_DIM,
        font=ctk.CTkFont(size=11),
        anchor="w",
    )
    swapper_label.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 4))
    swapper_dropdown = ctk.CTkOptionMenu(
        pipeline_inner,
        variable=swapper_variable,
        values=swapper_options,
        command=on_swapper_change,
        height=36,
    )
    swapper_dropdown.grid(row=1, column=0, sticky="ew", padx=(0, 6))
    ToolTip(
        swapper_dropdown,
        _(
            "Inswapper-128 is faster (good for live webcam); "
            "Hyperswap-256 is higher fidelity (good for image/video)."
        ),
    )

    enhancer_options = ["None", "GFPGAN", "GPEN-512", "GPEN-256"]
    enhancer_key_map = {
        "None": None,
        "GFPGAN": "face_enhancer",
        "GPEN-512": "face_enhancer_gpen512",
        "GPEN-256": "face_enhancer_gpen256",
    }
    initial_enhancer = "None"
    if modules.globals.fp_ui.get("face_enhancer", False):
        initial_enhancer = "GFPGAN"
    elif modules.globals.fp_ui.get("face_enhancer_gpen512", False):
        initial_enhancer = "GPEN-512"
    elif modules.globals.fp_ui.get("face_enhancer_gpen256", False):
        initial_enhancer = "GPEN-256"
    enhancer_variable = ctk.StringVar(value=initial_enhancer)

    def on_enhancer_change(choice: str):
        for key in [
            "face_enhancer",
            "face_enhancer_gpen256",
            "face_enhancer_gpen512",
        ]:
            update_tumbler(key, False)
        selected_key = enhancer_key_map.get(choice)
        if selected_key:
            update_tumbler(selected_key, True)
        save_switch_states()

    enhancer_label = ctk.CTkLabel(
        pipeline_inner,
        text=_("Face Enhancer"),
        text_color=TEXT_DIM,
        font=ctk.CTkFont(size=11),
        anchor="w",
    )
    enhancer_label.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4))
    enhancer_dropdown = ctk.CTkOptionMenu(
        pipeline_inner,
        variable=enhancer_variable,
        values=enhancer_options,
        command=on_enhancer_change,
        height=36,
    )
    enhancer_dropdown.grid(row=1, column=1, sticky="ew", padx=(6, 0))
    ToolTip(
        enhancer_dropdown,
        _("Select a face enhancement model (None = no enhancement)"),
    )

    # ===================== OUTPUT OPTIONS CARD =====================
    output_card = make_card(main, "03 — Toggles", _("Output options"))
    output_inner = ctk.CTkFrame(
        output_card, fg_color="transparent", border_width=0
    )
    output_inner.pack(fill="x", padx=18, pady=(12, 18))
    output_inner.grid_columnconfigure(0, weight=1, uniform="opt")
    output_inner.grid_columnconfigure(1, weight=1, uniform="opt")

    keep_fps_value = ctk.BooleanVar(value=modules.globals.keep_fps)
    keep_fps_checkbox = ctk.CTkSwitch(
        output_inner,
        text=_("Keep fps"),
        variable=keep_fps_value,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "keep_fps", keep_fps_value.get()),
            save_switch_states(),
        ),
    )
    keep_fps_checkbox.grid(row=0, column=0, sticky="w", pady=6)
    ToolTip(keep_fps_checkbox, _("Output video keeps the original frame rate"))

    keep_audio_value = ctk.BooleanVar(value=modules.globals.keep_audio)
    keep_audio_switch = ctk.CTkSwitch(
        output_inner,
        text=_("Keep audio"),
        variable=keep_audio_value,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "keep_audio", keep_audio_value.get()),
            save_switch_states(),
        ),
    )
    keep_audio_switch.grid(row=0, column=1, sticky="w", pady=6)
    ToolTip(keep_audio_switch, _("Copy audio track from the source video to output"))

    keep_frames_value = ctk.BooleanVar(value=modules.globals.keep_frames)
    keep_frames_switch = ctk.CTkSwitch(
        output_inner,
        text=_("Keep frames"),
        variable=keep_frames_value,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "keep_frames", keep_frames_value.get()),
            save_switch_states(),
        ),
    )
    keep_frames_switch.grid(row=1, column=0, sticky="w", pady=6)
    ToolTip(keep_frames_switch, _("Keep extracted frames on disk after processing"))

    many_faces_value = ctk.BooleanVar(value=modules.globals.many_faces)
    many_faces_switch = ctk.CTkSwitch(
        output_inner,
        text=_("Many faces"),
        variable=many_faces_value,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "many_faces", many_faces_value.get()),
            save_switch_states(),
        ),
    )
    many_faces_switch.grid(row=1, column=1, sticky="w", pady=6)
    ToolTip(many_faces_switch, _("Swap every detected face, not just the primary one"))

    map_faces = ctk.BooleanVar(value=modules.globals.map_faces)
    map_faces_switch = ctk.CTkSwitch(
        output_inner,
        text=_("Map faces"),
        variable=map_faces,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "map_faces", map_faces.get()),
            save_switch_states(),
            close_mapper_window() if not map_faces.get() else None,
        ),
    )
    map_faces_switch.grid(row=2, column=0, sticky="w", pady=6)
    ToolTip(
        map_faces_switch,
        _("Manually assign which source face maps to which target face"),
    )

    show_fps_value = ctk.BooleanVar(value=modules.globals.show_fps)
    show_fps_switch = ctk.CTkSwitch(
        output_inner,
        text=_("Show FPS"),
        variable=show_fps_value,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "show_fps", show_fps_value.get()),
            save_switch_states(),
        ),
    )
    show_fps_switch.grid(row=2, column=1, sticky="w", pady=6)
    ToolTip(show_fps_switch, _("Display frames-per-second counter on the live preview"))

    poisson_blend_value = ctk.BooleanVar(value=modules.globals.poisson_blend)
    poisson_blend_switch = ctk.CTkSwitch(
        output_inner,
        text=_("Poisson blend"),
        variable=poisson_blend_value,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "poisson_blend", poisson_blend_value.get()),
            save_switch_states(),
        ),
    )
    poisson_blend_switch.grid(row=3, column=0, sticky="w", pady=6)
    ToolTip(
        poisson_blend_switch,
        _("Blend face edges smoothly using Poisson blending"),
    )

    color_correction_value = ctk.BooleanVar(value=modules.globals.color_correction)
    color_correction_switch = ctk.CTkSwitch(
        output_inner,
        text=_("Fix blueish cam"),
        variable=color_correction_value,
        cursor="hand2",
        command=lambda: (
            setattr(modules.globals, "color_correction", color_correction_value.get()),
            save_switch_states(),
        ),
    )
    color_correction_switch.grid(row=3, column=1, sticky="w", pady=6)
    ToolTip(
        color_correction_switch,
        _("Fix blue/green color cast from some webcams"),
    )

    # mouth_mask + show_mouth_mask_box are auto-controlled by the Mouth Mask slider
    mouth_mask_var = ctk.BooleanVar(value=modules.globals.mouth_mask)
    show_mouth_mask_box_var = ctk.BooleanVar(value=modules.globals.show_mouth_mask_box)

    # ===================== TUNING CARD (sliders) =====================
    tuning_card = make_card(main, "04 — Fine controls", _("Tuning"))
    tuning_inner = ctk.CTkFrame(
        tuning_card, fg_color="transparent", border_width=0
    )
    tuning_inner.pack(fill="x", padx=18, pady=(12, 18))
    tuning_inner.grid_columnconfigure(0, minsize=120)
    tuning_inner.grid_columnconfigure(1, weight=1)
    tuning_inner.grid_columnconfigure(2, minsize=52)

    def make_slider_row(parent, row, label_text, var, from_, to, on_change, fmt="{:.0f}"):
        lbl = ctk.CTkLabel(
            parent,
            text=label_text,
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
        )
        lbl.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
        slider = ctk.CTkSlider(
            parent,
            from_=from_,
            to=to,
            variable=var,
            height=14,
        )
        slider.grid(row=row, column=1, sticky="ew", pady=8)
        val_label = ctk.CTkLabel(
            parent,
            text=fmt.format(var.get()),
            text_color=CYAN,
            font=ctk.CTkFont(size=11, weight="bold"),
            anchor="e",
            width=46,
        )
        val_label.grid(row=row, column=2, sticky="e", padx=(12, 0))

        def wrapped(value):
            try:
                val_label.configure(text=fmt.format(float(value)))
            except Exception:
                pass
            on_change(value)

        slider.configure(command=wrapped)
        return slider, val_label

    # Transparency
    transparency_var = ctk.DoubleVar(value=1.0)

    def on_transparency_change(value: float):
        val = float(value)
        modules.globals.opacity = val
        percentage = int(val * 100)
        if percentage == 0:
            modules.globals.fp_ui["face_enhancer"] = False
            update_status("Transparency set to 0% - Face swapping disabled.")
        elif percentage == 100:
            modules.globals.face_swapper_enabled = True
            update_status("Transparency set to 100%.")
        else:
            modules.globals.face_swapper_enabled = True
            update_status(f"Transparency set to {percentage}%")

    transparency_slider, _tl = make_slider_row(
        tuning_inner, 0, _("Transparency"), transparency_var,
        0.0, 1.0, on_transparency_change, fmt="{:.0%}",
    )
    ToolTip(
        transparency_slider,
        _(
            "Blend between original and swapped face "
            "(0% = original, 100% = fully swapped)"
        ),
    )

    # Sharpness
    sharpness_var = ctk.DoubleVar(value=0.0)

    def on_sharpness_change(value: float):
        modules.globals.sharpness = float(value)
        update_status(f"Sharpness set to {float(value):.1f}")

    sharpness_slider, _sl = make_slider_row(
        tuning_inner, 1, _("Sharpness"), sharpness_var,
        0.0, 5.0, on_sharpness_change, fmt="{:.1f}",
    )
    ToolTip(sharpness_slider, _("Sharpen the enhanced face output"))

    # Mouth Mask
    mouth_mask_size_var = ctk.DoubleVar(value=modules.globals.mouth_mask_size)

    def on_mouth_mask_size_change(value: float):
        val = float(value)
        modules.globals.mouth_mask_size = val
        if val > 0:
            modules.globals.mouth_mask = True
            mouth_mask_var.set(True)
        else:
            modules.globals.mouth_mask = False
            mouth_mask_var.set(False)
            modules.globals.show_mouth_mask_box = False

    def on_mouth_mask_slider_release(event):
        modules.globals.show_mouth_mask_box = False

    def on_mouth_mask_slider_press(event):
        if modules.globals.mouth_mask_size > 0:
            modules.globals.show_mouth_mask_box = True

    mouth_mask_slider, _ml = make_slider_row(
        tuning_inner, 2, _("Mouth mask"), mouth_mask_size_var,
        0.0, 100.0, on_mouth_mask_size_change,
    )
    mouth_mask_slider.bind("<ButtonPress-1>", on_mouth_mask_slider_press)
    mouth_mask_slider.bind("<ButtonRelease-1>", on_mouth_mask_slider_release)
    ToolTip(
        mouth_mask_slider,
        _("0 = use swapped mouth, 100 = expose original mouth to chin area"),
    )

    # Quick Lip Mask — fast lip-only paste-back
    quick_lip_size_var = ctk.DoubleVar(value=modules.globals.quick_lip_size)

    def on_quick_lip_size_change(value: float):
        val = float(value)
        modules.globals.quick_lip_size = val
        modules.globals.quick_lip_mask = val > 0
        save_switch_states()

    quick_lip_slider, _qls = make_slider_row(
        tuning_inner, 3, _("Quick lip mask"), quick_lip_size_var,
        0.0, 100.0, on_quick_lip_size_change,
    )
    ToolTip(
        quick_lip_slider,
        _(
            "Fast lip-only paste-back: keeps your original lip motion on "
            "the swap without exposing the whole mouth-to-chin area. "
            "0 = off, 100 = widest lip strip. Conflicts with Mouth mask "
            "(Mouth mask wins if both are set)."
        ),
    )

    # Chin Mask — feathers the jaw boundary
    chin_mask_size_var = ctk.DoubleVar(value=modules.globals.chin_mask_size)

    def on_chin_mask_size_change(value: float):
        val = float(value)
        modules.globals.chin_mask_size = val
        modules.globals.chin_mask = val > 0
        save_switch_states()

    chin_mask_slider, _cms = make_slider_row(
        tuning_inner, 4, _("Chin mask"), chin_mask_size_var,
        0.0, 100.0, on_chin_mask_size_change,
    )
    ToolTip(
        chin_mask_slider,
        _(
            "Soften the jaw boundary by feathering the swapped chin "
            "back toward the original. Eliminates harsh jawline edges. "
            "0 = off, 100 = covers the whole lower face."
        ),
    )

    # Eyes Mask — keeps original eyes
    eyes_mask_size_var = ctk.DoubleVar(value=modules.globals.eyes_mask_size)

    def on_eyes_mask_size_change(value: float):
        val = float(value)
        modules.globals.eyes_mask_size = val
        modules.globals.eyes_mask = val > 0
        save_switch_states()

    eyes_mask_slider, _ems = make_slider_row(
        tuning_inner, 5, _("Eyes mask"), eyes_mask_size_var,
        0.0, 100.0, on_eyes_mask_size_change,
    )
    ToolTip(
        eyes_mask_slider,
        _(
            "Paste your original eyes back over the swap so gaze stays "
            "sharp and expressive. 0 = swap eyes, 100 = widest original-eye coverage."
        ),
    )

    # Eyebrows Mask — preserves original eyebrows
    eyebrows_mask_size_var = ctk.DoubleVar(value=modules.globals.eyebrows_mask_size)

    def on_eyebrows_mask_size_change(value: float):
        val = float(value)
        modules.globals.eyebrows_mask_size = val
        modules.globals.eyebrows_mask = val > 0
        save_switch_states()

    eyebrows_mask_slider, _bms = make_slider_row(
        tuning_inner, 6, _("Eyebrows mask"), eyebrows_mask_size_var,
        0.0, 100.0, on_eyebrows_mask_size_change,
    )
    ToolTip(
        eyebrows_mask_slider,
        _(
            "Preserve original brow detail and arch over the swap. "
            "0 = off, 100 = widest curved-brow coverage."
        ),
    )

    # Forehead Height
    forehead_size_var = ctk.DoubleVar(value=modules.globals.forehead_size)

    def on_forehead_size_change(value: float):
        modules.globals.forehead_size = float(value)
        save_switch_states()

    forehead_size_slider, _fhs = make_slider_row(
        tuning_inner, 7, _("Forehead height"), forehead_size_var,
        0.0, 100.0, on_forehead_size_change,
    )
    ToolTip(
        forehead_size_slider,
        _(
            "Extend the swap upward to match more of the head. "
            "0 = face only (default), 100 = covers the full forehead/hairline."
        ),
    )

    # Forehead Width
    forehead_width_var = ctk.DoubleVar(value=modules.globals.forehead_width)

    def on_forehead_width_change(value: float):
        modules.globals.forehead_width = float(value)
        save_switch_states()

    forehead_width_slider, _fws = make_slider_row(
        tuning_inner, 8, _("Forehead width"), forehead_width_var,
        0.0, 100.0, on_forehead_width_change,
    )
    ToolTip(
        forehead_width_slider,
        _(
            "Widen the head match so the swap covers the temples/sides. "
            "0 = default face oval, 100 = widest head coverage."
        ),
    )

    # Hair Color
    hair_color_var = ctk.DoubleVar(value=modules.globals.hair_color)

    def on_hair_color_change(value: float):
        modules.globals.hair_color = float(value)
        save_switch_states()

    hair_color_slider, _hc = make_slider_row(
        tuning_inner, 9, _("Hair color"), hair_color_var,
        0.0, 100.0, on_hair_color_change,
    )
    ToolTip(
        hair_color_slider,
        _(
            "Recolor your hair toward the source's color. Shape and motion stay "
            "yours so the hair moves naturally with your head. 0 = your color, "
            "100 = full match. Requires bisenet_resnet_18.onnx in models/ "
            "(auto-downloads on first use, ~50MB)."
        ),
    )

    # Hair Texture
    hair_texture_var = ctk.DoubleVar(value=modules.globals.hair_texture)

    def on_hair_texture_change(value: float):
        modules.globals.hair_texture = float(value)
        save_switch_states()

    hair_texture_slider, _ht = make_slider_row(
        tuning_inner, 10, _("Hair texture"), hair_texture_var,
        0.0, 100.0, on_hair_texture_change,
    )
    ToolTip(
        hair_texture_slider,
        _(
            "Match the hair's fine texture (strands, sheen) to the source by "
            "scaling its high-frequency luminance variance. 0 = your texture, "
            "100 = matched. Use with Hair Color for a cohesive look."
        ),
    )

    # Face Enhancer Scaler — dial enhancement intensity up/down.
    enhancer_blend_var = ctk.DoubleVar(value=modules.globals.enhancer_blend)

    def on_enhancer_blend_change(value: float):
        modules.globals.enhancer_blend = float(value)
        save_switch_states()

    enhancer_blend_slider, _ebs = make_slider_row(
        tuning_inner, 11, _("Enhancer scaler"), enhancer_blend_var,
        0.0, 100.0, on_enhancer_blend_change,
    )
    ToolTip(
        enhancer_blend_slider,
        _(
            "Dial the face enhancer (GFPGAN / GPEN-256 / GPEN-512) "
            "intensity. 0 = original face, 100 = fully enhanced."
        ),
    )

    # Realtime enhancer stride — frame skip for live FPS preservation.
    live_skip_var = ctk.DoubleVar(value=float(modules.globals.live_enhance_skip))

    def on_live_skip_change(value: float):
        modules.globals.live_enhance_skip = max(1, int(round(float(value))))
        save_switch_states()

    live_skip_slider, _lss = make_slider_row(
        tuning_inner, 12, _("Realtime skip"), live_skip_var,
        1.0, 6.0, on_live_skip_change, fmt="{:.0f}",
    )
    ToolTip(
        live_skip_slider,
        _(
            "Realtime Face Enhancer stride for live webcam mode. "
            "1 = run every frame (max quality), 2-3 = balanced, "
            "4-6 = lightning-fast (cached enhanced face is re-pasted "
            "between inference runs)."
        ),
    )

    # Interpolation — temporal smoothing between frames.
    interpolation_var = ctk.DoubleVar(value=float(modules.globals.interpolation_weight))

    def on_interpolation_change(value: float):
        val = float(value)
        modules.globals.interpolation_weight = val
        # Slider drives both the weight and the on/off toggle.
        modules.globals.enable_interpolation = val > 0
        save_switch_states()

    interpolation_slider, _ipl = make_slider_row(
        tuning_inner, 13, _("Interpolation"), interpolation_var,
        0.0, 0.95, on_interpolation_change, fmt="{:.2f}",
    )
    ToolTip(
        interpolation_slider,
        _(
            "Smoother frame-to-frame transitions (temporal blend with "
            "the previous result). 0 = off, higher = smoother but adds "
            "ghosting on fast motion. 0.2-0.4 is a good live-cam range."
        ),
    )

    # ===================== CAMERA / OUTPUT / UI CARD =====================
    cam_card = make_card(main, "05 — Camera, output & UI", _("Camera, output & UI"))
    cam_inner = ctk.CTkFrame(cam_card, fg_color="transparent", border_width=0)
    cam_inner.pack(fill="x", padx=18, pady=(12, 18))
    cam_inner.grid_columnconfigure(0, weight=1, uniform="cam")
    cam_inner.grid_columnconfigure(1, weight=1, uniform="cam")

    # --- Resolution Switch ---
    global resolution_optionmenu_ref
    res_label = ctk.CTkLabel(
        cam_inner, text=_("Capture resolution"),
        text_color=TEXT_DIM, font=ctk.CTkFont(size=11), anchor="w",
    )
    res_label.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 4))

    res_var = ctk.StringVar(value=modules.globals.live_resolution)

    def on_resolution_change(choice: str):
        modules.globals.live_resolution = choice
        save_switch_states()
        # Hot-apply if a live preview is currently running
        global _active_capturer
        if _active_capturer is not None:
            try:
                w, h, fps = resolution_map(choice)
                _active_capturer.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                _active_capturer.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                _active_capturer.cap.set(cv2.CAP_PROP_FPS, fps)
                _active_capturer.actual_width = int(
                    _active_capturer.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                )
                _active_capturer.actual_height = int(
                    _active_capturer.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                )
                update_status(
                    f"Resolution set to {choice} "
                    f"({_active_capturer.actual_width}x"
                    f"{_active_capturer.actual_height})"
                )
            except Exception as e:
                update_status(f"Resolution change failed: {e}")
        else:
            update_status(f"Resolution set to {choice}")

    res_dropdown = ctk.CTkOptionMenu(
        cam_inner,
        variable=res_var,
        values=list(resolution_labels()),
        command=on_resolution_change,
        height=34,
    )
    res_dropdown.grid(row=1, column=0, sticky="ew", padx=(0, 6))
    resolution_optionmenu_ref = res_dropdown
    ToolTip(
        res_dropdown,
        _(
            "Resolution Switch / Resolution Changer — picks the camera "
            "capture size. Auto = let the camera pick. Higher = more "
            "detail but lower FPS."
        ),
    )

    # --- GPU Changer ---
    global gpu_optionmenu_ref
    gpu_names, gpu_count = detect_gpus()
    modules.globals.gpu_device_names = list(gpu_names)
    modules.globals.gpu_device_count = max(1, gpu_count)
    gpu_id = max(0, min(modules.globals.gpu_device_id, len(gpu_names) - 1))
    gpu_label_text = _("GPU device")
    gpu_label = ctk.CTkLabel(
        cam_inner, text=gpu_label_text, text_color=TEXT_DIM,
        font=ctk.CTkFont(size=11), anchor="w",
    )
    gpu_label.grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4))

    gpu_display_options = [f"{i}: {name}" for i, name in enumerate(gpu_names)]
    gpu_var = ctk.StringVar(value=gpu_display_options[gpu_id])

    def on_gpu_change(choice: str):
        try:
            idx = int(choice.split(":", 1)[0])
        except ValueError:
            idx = 0
        modules.globals.gpu_device_id = idx
        set_cuda_visible_device(idx)
        save_switch_states()
        update_status(
            f"GPU device {idx} selected — restart Live for the swap "
            f"engine to bind the new device."
        )

    gpu_dropdown = ctk.CTkOptionMenu(
        cam_inner,
        variable=gpu_var,
        values=gpu_display_options,
        command=on_gpu_change,
        height=34,
    )
    gpu_dropdown.grid(row=1, column=1, sticky="ew", padx=(6, 0))
    gpu_optionmenu_ref = gpu_dropdown
    ToolTip(
        gpu_dropdown,
        _(
            "GPU Changer / Multi-GPU support — selects which CUDA device "
            "the swap engine binds to. Restart Live mode after changing."
        ),
    )

    # --- Switches: Virtual Cam, Optimized Rendering, In-Window Preview,
    #     Force Discrete GPU ---
    sw_row = ctk.CTkFrame(cam_inner, fg_color="transparent", border_width=0)
    sw_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 4))
    sw_row.grid_columnconfigure(0, weight=1, uniform="cam_sw")
    sw_row.grid_columnconfigure(1, weight=1, uniform="cam_sw")

    vcam_var = ctk.BooleanVar(value=modules.globals.virtual_cam_enabled)

    def on_vcam_toggle():
        modules.globals.virtual_cam_enabled = vcam_var.get()
        save_switch_states()
        sink = get_virtual_cam()
        if not modules.globals.virtual_cam_enabled:
            sink.close()
            modules.globals.virtual_cam_active = False
            modules.globals.virtual_cam_backend = ""
            if vcam_status_label is not None:
                vcam_status_label.configure(text=_("Virtual cam: off"))
        else:
            if vcam_status_label is not None:
                vcam_status_label.configure(
                    text=_("Virtual cam: opens on next Live frame")
                )

    vcam_switch = ctk.CTkSwitch(
        sw_row, text=_("Virtual camera"),
        variable=vcam_var, cursor="hand2", command=on_vcam_toggle,
    )
    vcam_switch.grid(row=0, column=0, sticky="w", pady=6)
    ToolTip(
        vcam_switch,
        _(
            "Stream the swapped feed to OBS / Zoom / Meet via OBS "
            "Virtual Camera (Win/Mac) or v4l2loopback (Linux). "
            "Requires the pyvirtualcam package."
        ),
    )

    optrend_var = ctk.BooleanVar(value=modules.globals.optimized_rendering)

    def on_optrend_toggle():
        modules.globals.optimized_rendering = optrend_var.get()
        save_switch_states()

    optrend_switch = ctk.CTkSwitch(
        sw_row, text=_("Optimized rendering"),
        variable=optrend_var, cursor="hand2", command=on_optrend_toggle,
    )
    optrend_switch.grid(row=0, column=1, sticky="w", pady=6)
    ToolTip(
        optrend_switch,
        _(
            "Auto-tune detection stride, preview scale and frame skipping "
            "based on measured FPS so live, video and image modes stay "
            "responsive on any hardware."
        ),
    )

    inwin_var = ctk.BooleanVar(value=modules.globals.in_window_preview)

    def on_inwin_toggle():
        modules.globals.in_window_preview = inwin_var.get()
        save_switch_states()
        # If projection mode is active, In-Window wins (mutually exclusive)
        if modules.globals.in_window_preview and modules.globals.projection_mode:
            modules.globals.projection_mode = 0
            close_projection_window()

    inwin_switch = ctk.CTkSwitch(
        sw_row, text=_("In-window preview"),
        variable=inwin_var, cursor="hand2", command=on_inwin_toggle,
    )
    inwin_switch.grid(row=1, column=0, sticky="w", pady=6)
    ToolTip(
        inwin_switch,
        _(
            "Render the live swap inside the main dashboard rather than "
            "in a separate window. Mutually exclusive with Window Projection."
        ),
    )

    fgpu_var = ctk.BooleanVar(value=modules.globals.force_discrete_gpu)

    def on_force_gpu_toggle():
        modules.globals.force_discrete_gpu = fgpu_var.get()
        save_switch_states()
        if modules.globals.force_discrete_gpu:
            apply_force_discrete_gpu()
            update_status(
                "Forced discrete GPU on next launch (laptop dual-GPU systems)"
            )

    fgpu_switch = ctk.CTkSwitch(
        sw_row, text=_("Force discrete GPU"),
        variable=fgpu_var, cursor="hand2", command=on_force_gpu_toggle,
    )
    fgpu_switch.grid(row=1, column=1, sticky="w", pady=6)
    ToolTip(
        fgpu_switch,
        _(
            "Forced GPU usage on laptops — sets NVIDIA Optimus / AMD "
            "switchable graphics hints so the discrete GPU is used instead "
            "of the iGPU. Takes effect on next launch."
        ),
    )

    # --- Window Projection buttons ---
    proj_row = ctk.CTkFrame(cam_inner, fg_color="transparent", border_width=0)
    proj_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 4))
    proj_row.grid_columnconfigure(0, weight=1, uniform="proj")
    proj_row.grid_columnconfigure(1, weight=1, uniform="proj")

    project_btn = ctk.CTkButton(
        proj_row, text=_("Window projection"),
        cursor="hand2", height=34,
        fg_color=BG_ELEV_2, hover_color=BORDER_HI, text_color=TEXT,
        border_width=1, border_color=BORDER,
        command=lambda: toggle_projection_window(borderless=True),
    )
    project_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ToolTip(
        project_btn,
        _(
            "Borderless pop-out preview suitable for OBS window-capture. "
            "Click again to close. Press F inside the window for full screen."
        ),
    )

    fullscreen_btn = ctk.CTkButton(
        proj_row, text=_("Projection: fullscreen"),
        cursor="hand2", height=34,
        fg_color=BG_ELEV_2, hover_color=BORDER_HI, text_color=TEXT,
        border_width=1, border_color=BORDER,
        command=lambda: toggle_projection_window(fullscreen=True),
    )
    fullscreen_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
    ToolTip(
        fullscreen_btn,
        _(
            "Window Projection Full Screen Mode — true fullscreen output "
            "for clean recording / streaming."
        ),
    )

    # --- LUTs ---
    global lut_status_label, vcam_status_label
    lut_row = ctk.CTkFrame(cam_inner, fg_color="transparent", border_width=0)
    lut_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    lut_row.grid_columnconfigure(1, weight=1)

    lut_btn = ctk.CTkButton(
        lut_row, text=_("Load .cube LUT"),
        cursor="hand2", height=34,
        fg_color=VIOLET, hover_color=VIOLET_HI, text_color=BG,
        font=ctk.CTkFont(size=12, weight="bold"),
        command=lambda: load_lut_file(),
    )
    lut_btn.grid(row=0, column=0, sticky="w", padx=(0, 8))
    ToolTip(
        lut_btn,
        _(
            "Load a cinematic .cube LUT and apply it to live, video and "
            "image output as the final color-grading step."
        ),
    )

    lut_status_label = ctk.CTkLabel(
        lut_row, text=f"LUT: {modules.globals.lut_name}",
        text_color=TEXT_MUTED, font=ctk.CTkFont(size=11), anchor="w",
    )
    lut_status_label.grid(row=0, column=1, sticky="ew", padx=(0, 8))

    clear_lut_btn = ctk.CTkButton(
        lut_row, text=_("Clear"),
        cursor="hand2", width=70, height=34,
        fg_color=BG_ELEV_2, hover_color=BORDER_HI, text_color=TEXT,
        border_width=1, border_color=BORDER,
        command=lambda: clear_lut(),
    )
    clear_lut_btn.grid(row=0, column=2, sticky="e")
    ToolTip(clear_lut_btn, _("Clear the active LUT"))

    # LUT strength slider
    lut_strength_var = ctk.DoubleVar(value=modules.globals.lut_strength)

    def on_lut_strength_change(value):
        modules.globals.lut_strength = float(value)
        save_switch_states()

    lut_str_slider, _lstr = make_slider_row(
        cam_inner, 5, _("LUT strength"), lut_strength_var,
        0.0, 100.0, on_lut_strength_change,
    )
    ToolTip(
        lut_str_slider,
        _(
            "Blend strength of the active LUT. 0 = no grade, "
            "100 = full LUT."
        ),
    )

    # Virtual cam status line (shown right below the switches)
    vcam_status_label = ctk.CTkLabel(
        cam_inner,
        text=(
            _("Virtual cam: on (opens on next Live frame)")
            if modules.globals.virtual_cam_enabled
            else _("Virtual cam: off")
        ),
        text_color=TEXT_DIM, font=ctk.CTkFont(size=10), anchor="w",
    )
    vcam_status_label.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    # ===================== IN-WINDOW PREVIEW SLOT =====================
    # Always created; only populated when in_window_preview is on.
    global in_window_preview_label
    in_win_card = make_card(main, "06 — Live monitor", _("In-window preview"))
    in_win_inner = ctk.CTkFrame(in_win_card, fg_color="transparent", border_width=0)
    in_win_inner.pack(fill="both", expand=True, padx=18, pady=(12, 18))
    in_win_inner.grid_columnconfigure(0, weight=1)
    in_win_inner.grid_rowconfigure(0, weight=1)
    in_window_preview_label = ctk.CTkLabel(
        in_win_inner,
        text=_(
            "Toggle “In-window preview” above and click Live to embed "
            "the swap feed here."
        ),
        text_color=TEXT_DIM, font=ctk.CTkFont(size=12),
        height=240,
    )
    in_window_preview_label.grid(row=0, column=0, sticky="nsew")

    return root



def close_mapper_window():
    global POPUP, POPUP_LIVE
    if POPUP and POPUP.winfo_exists():
        POPUP.destroy()
        POPUP = None
    if POPUP_LIVE and POPUP_LIVE.winfo_exists():
        POPUP_LIVE.destroy()
        POPUP_LIVE = None


def analyze_target(start: Callable[[], None], root: ctk.CTk):
    if POPUP != None and POPUP.winfo_exists():
        update_status("Please complete pop-up or close it.")
        return

    if modules.globals.map_faces:
        modules.globals.source_target_map = []

        if is_image(modules.globals.target_path):
            update_status("Getting unique faces")
            get_unique_faces_from_target_image()
        elif is_video(modules.globals.target_path):
            update_status("Getting unique faces")
            get_unique_faces_from_target_video()

        if len(modules.globals.source_target_map) > 0:
            create_source_target_popup(start, root, modules.globals.source_target_map)
        else:
            update_status("No faces found in target")
    else:
        select_output_path(start)


def create_source_target_popup(
        start: Callable[[], None], root: ctk.CTk, map: list
) -> None:
    global POPUP, popup_status_label

    POPUP = ctk.CTkToplevel(root)
    POPUP.title(_("Source x Target Mapper"))
    POPUP.geometry(f"{POPUP_WIDTH}x{POPUP_HEIGHT}")
    POPUP.focus()

    def on_submit_click(start):
        if has_valid_map():
            POPUP.destroy()
            select_output_path(start)
        else:
            update_pop_status("Atleast 1 source with target is required!")

    scrollable_frame = ctk.CTkScrollableFrame(
        POPUP, width=POPUP_SCROLL_WIDTH, height=POPUP_SCROLL_HEIGHT
    )
    scrollable_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")

    def on_button_click(map, button_num):
        map = update_popup_source(scrollable_frame, map, button_num)

    for item in map:
        id = item["id"]

        button = ctk.CTkButton(
            scrollable_frame,
            text=_("Select source image"),
            command=lambda id=id: on_button_click(map, id),
            width=DEFAULT_BUTTON_WIDTH,
            height=DEFAULT_BUTTON_HEIGHT,
        )
        button.grid(row=id, column=0, padx=50, pady=10)

        x_label = ctk.CTkLabel(
            scrollable_frame,
            text=f"X",
            width=MAPPER_PREVIEW_MAX_WIDTH,
            height=MAPPER_PREVIEW_MAX_HEIGHT,
        )
        x_label.grid(row=id, column=2, padx=10, pady=10)

        image = Image.fromarray(gpu_cvt_color(item["target"]["cv2"], cv2.COLOR_BGR2RGB))
        image = image.resize(
            (MAPPER_PREVIEW_MAX_WIDTH, MAPPER_PREVIEW_MAX_HEIGHT), Image.LANCZOS
        )
        tk_image = ctk.CTkImage(image, size=image.size)

        target_image = ctk.CTkLabel(
            scrollable_frame,
            text=f"T-{id}",
            width=MAPPER_PREVIEW_MAX_WIDTH,
            height=MAPPER_PREVIEW_MAX_HEIGHT,
        )
        target_image.grid(row=id, column=3, padx=10, pady=10)
        target_image.configure(image=tk_image)

    popup_status_label = ctk.CTkLabel(POPUP, text=None, justify="center")
    popup_status_label.grid(row=1, column=0, pady=15)

    close_button = ctk.CTkButton(
        POPUP, text=_("Submit"), command=lambda: on_submit_click(start)
    )
    close_button.grid(row=2, column=0, pady=10)


def update_popup_source(
        scrollable_frame: ctk.CTkScrollableFrame, map: list, button_num: int
) -> list:
    global source_label_dict

    source_path = ctk.filedialog.askopenfilename(
        title=_("select an source image"),
        initialdir=RECENT_DIRECTORY_SOURCE,
        filetypes=[img_ft],
    )

    if "source" in map[button_num]:
        map[button_num].pop("source")
        source_label_dict[button_num].destroy()
        del source_label_dict[button_num]

    if source_path == "":
        return map
    else:
        cv2_img = cv2.imread(source_path)
        face = get_one_face(cv2_img)

        if face:
            x_min, y_min, x_max, y_max = face["bbox"]

            map[button_num]["source"] = {
                "cv2": cv2_img[int(y_min): int(y_max), int(x_min): int(x_max)],
                "face": face,
            }

            image = Image.fromarray(
                gpu_cvt_color(map[button_num]["source"]["cv2"], cv2.COLOR_BGR2RGB)
            )
            image = image.resize(
                (MAPPER_PREVIEW_MAX_WIDTH, MAPPER_PREVIEW_MAX_HEIGHT), Image.LANCZOS
            )
            tk_image = ctk.CTkImage(image, size=image.size)

            source_image = ctk.CTkLabel(
                scrollable_frame,
                text=f"S-{button_num}",
                width=MAPPER_PREVIEW_MAX_WIDTH,
                height=MAPPER_PREVIEW_MAX_HEIGHT,
            )
            source_image.grid(row=button_num, column=1, padx=10, pady=10)
            source_image.configure(image=tk_image)
            source_label_dict[button_num] = source_image
        else:
            update_pop_status("Face could not be detected in last upload!")
        return map


def create_preview(parent: ctk.CTkToplevel) -> ctk.CTkToplevel:
    global preview_label, preview_slider

    preview = ctk.CTkToplevel(parent)
    preview.withdraw()
    preview.title(_("Preview"))
    preview.configure()
    preview.protocol("WM_DELETE_WINDOW", lambda: toggle_preview())
    preview.resizable(width=True, height=True)

    preview_label = ctk.CTkLabel(preview, text=None)
    preview_label.pack(fill="both", expand=True)

    preview_slider = ctk.CTkSlider(
        preview, from_=0, to=0, command=lambda frame_value: update_preview(frame_value)
    )

    return preview


def update_status(text: str) -> None:
    status_label.configure(text=_(text))
    ROOT.update()


def update_pop_status(text: str) -> None:
    popup_status_label.configure(text=_(text))


def update_pop_live_status(text: str) -> None:
    popup_status_label_live.configure(text=_(text))


def update_tumbler(var: str, value: bool) -> None:
    modules.globals.fp_ui[var] = value
    save_switch_states()
    # If we're currently in a live preview, update the frame processors
    if PREVIEW.state() == "normal":
        global frame_processors
        frame_processors = get_frame_processors_modules(
            modules.globals.frame_processors
        )


def fetch_random_face() -> None:
    PREVIEW.withdraw()
    try:
        response = requests.get(
            "https://thispersondoesnotexist.com/",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, "deep_live_cam_random_face.jpg")
        with open(temp_path, "wb") as f:
            f.write(response.content)
        modules.globals.source_path = temp_path
        image = render_image_preview(temp_path, (200, 200))
        source_label.configure(image=image, text="")
    except Exception as e:
        print(f"Failed to fetch random face: {e}")


def select_source_path() -> None:
    global RECENT_DIRECTORY_SOURCE, img_ft, vid_ft

    PREVIEW.withdraw()
    source_path = ctk.filedialog.askopenfilename(
        title=_("select an source image"),
        initialdir=RECENT_DIRECTORY_SOURCE,
        filetypes=[img_ft],
    )
    if is_image(source_path):
        modules.globals.source_path = source_path
        RECENT_DIRECTORY_SOURCE = os.path.dirname(modules.globals.source_path)
        image = render_image_preview(modules.globals.source_path, (200, 200))
        source_label.configure(image=image, text="")
    else:
        modules.globals.source_path = None
        source_label.configure(image=None, text=_("No face\nselected"))


def swap_faces_paths() -> None:
    global RECENT_DIRECTORY_SOURCE, RECENT_DIRECTORY_TARGET

    source_path = modules.globals.source_path
    target_path = modules.globals.target_path

    if not is_image(source_path) or not is_image(target_path):
        return

    modules.globals.source_path = target_path
    modules.globals.target_path = source_path

    RECENT_DIRECTORY_SOURCE = os.path.dirname(modules.globals.source_path)
    RECENT_DIRECTORY_TARGET = os.path.dirname(modules.globals.target_path)

    PREVIEW.withdraw()

    source_image = render_image_preview(modules.globals.source_path, (200, 200))
    source_label.configure(image=source_image, text="")

    target_image = render_image_preview(modules.globals.target_path, (200, 200))
    target_label.configure(image=target_image, text="")


def select_target_path() -> None:
    global RECENT_DIRECTORY_TARGET, img_ft, vid_ft

    PREVIEW.withdraw()
    target_path = ctk.filedialog.askopenfilename(
        title=_("select an target image or video"),
        initialdir=RECENT_DIRECTORY_TARGET,
        filetypes=[img_ft, vid_ft],
    )
    if is_image(target_path):
        modules.globals.target_path = target_path
        RECENT_DIRECTORY_TARGET = os.path.dirname(modules.globals.target_path)
        image = render_image_preview(modules.globals.target_path, (200, 200))
        target_label.configure(image=image, text="")
    elif is_video(target_path):
        modules.globals.target_path = target_path
        RECENT_DIRECTORY_TARGET = os.path.dirname(modules.globals.target_path)
        video_frame = render_video_preview(target_path, (200, 200))
        target_label.configure(image=video_frame, text="")
    else:
        modules.globals.target_path = None
        target_label.configure(image=None, text=_("No target\nselected"))


def select_output_path(start: Callable[[], None]) -> None:
    global RECENT_DIRECTORY_OUTPUT, img_ft, vid_ft

    if is_image(modules.globals.target_path):
        output_path = ctk.filedialog.asksaveasfilename(
            title=_("save image output file"),
            filetypes=[img_ft],
            defaultextension=".png",
            initialfile="output.png",
            initialdir=RECENT_DIRECTORY_OUTPUT,
        )
    elif is_video(modules.globals.target_path):
        output_path = ctk.filedialog.asksaveasfilename(
            title=_("save video output file"),
            filetypes=[vid_ft],
            defaultextension=".mp4",
            initialfile="output.mp4",
            initialdir=RECENT_DIRECTORY_OUTPUT,
        )
    else:
        output_path = None
    if output_path:
        modules.globals.output_path = output_path
        RECENT_DIRECTORY_OUTPUT = os.path.dirname(modules.globals.output_path)
        start()


def check_and_ignore_nsfw(target, destroy: Callable = None) -> bool:
    """Check if the target is NSFW.
    TODO: Consider to make blur the target.
    """
    from numpy import ndarray
    from modules.predicter import predict_image, predict_video, predict_frame

    if type(target) is str:  # image/video file path
        check_nsfw = predict_image if has_image_extension(target) else predict_video
    elif type(target) is ndarray:  # frame object
        check_nsfw = predict_frame
    if check_nsfw and check_nsfw(target):
        if destroy:
            destroy(
                to_quit=False
            )  # Do not need to destroy the window frame if the target is NSFW
        update_status("Processing ignored!")
        return True
    else:
        return False


def fit_image_to_size(image, width: int, height: int):
    if width is None and height is None:
        return image
    h, w, _ = image.shape
    ratio_h = 0.0
    ratio_w = 0.0
    if width > height:
        ratio_h = height / h
    else:
        ratio_w = width / w
    ratio = max(ratio_w, ratio_h)
    new_size = (int(ratio * w), int(ratio * h))
    return gpu_resize(image, dsize=new_size)


def render_image_preview(image_path: str, size: Tuple[int, int]) -> ctk.CTkImage:
    image = Image.open(image_path)
    if size:
        image = ImageOps.fit(image, size, Image.LANCZOS)
    return ctk.CTkImage(image, size=image.size)


def render_video_preview(
        video_path: str, size: Tuple[int, int], frame_number: int = 0
) -> ctk.CTkImage:
    capture = cv2.VideoCapture(video_path)
    if frame_number:
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
    has_frame, frame = capture.read()
    if has_frame:
        image = Image.fromarray(gpu_cvt_color(frame, cv2.COLOR_BGR2RGB))
        if size:
            image = ImageOps.fit(image, size, Image.LANCZOS)
        return ctk.CTkImage(image, size=image.size)
    capture.release()
    cv2.destroyAllWindows()


def toggle_preview() -> None:
    if PREVIEW.state() == "normal":
        PREVIEW.withdraw()
    elif modules.globals.source_path and modules.globals.target_path:
        init_preview()
        update_preview()


def init_preview() -> None:
    if is_image(modules.globals.target_path):
        preview_slider.pack_forget()
    if is_video(modules.globals.target_path):
        video_frame_total = get_video_frame_total(modules.globals.target_path)
        preview_slider.configure(to=video_frame_total)
        preview_slider.pack(fill="x")
        preview_slider.set(0)


def update_preview(frame_number: int = 0) -> None:
    if modules.globals.source_path and modules.globals.target_path:
        update_status("Processing...")
        temp_frame = get_video_frame(modules.globals.target_path, frame_number)
        if modules.globals.nsfw_filter and check_and_ignore_nsfw(temp_frame):
            return
        for frame_processor in get_frame_processors_modules(
                modules.globals.frame_processors
        ):
            temp_frame = frame_processor.process_frame(
                get_one_face(cv2.imread(modules.globals.source_path)), temp_frame
            )
        image = Image.fromarray(gpu_cvt_color(temp_frame, cv2.COLOR_BGR2RGB))
        image = ImageOps.contain(
            image, (PREVIEW_MAX_WIDTH, PREVIEW_MAX_HEIGHT), Image.LANCZOS
        )
        image = ctk.CTkImage(image, size=image.size)
        preview_label.configure(image=image)
        update_status("Processing succeed!")
        PREVIEW.deiconify()


def webcam_preview(root: ctk.CTk, camera_index: int):
    global POPUP_LIVE

    if POPUP_LIVE and POPUP_LIVE.winfo_exists():
        update_status("Source x Target Mapper is already open.")
        POPUP_LIVE.focus()
        return

    if not modules.globals.map_faces:
        if modules.globals.source_path is None:
            update_status("Please select a source image first")
            return
        from modules.face_analyser import get_face_analyser
        get_face_analyser()
        # Run pre_check on every active processor — this downloads any
        # missing model (e.g. hyperswap_1a_256.onnx) BEFORE the swap loop
        # starts, otherwise swap_face would spam "Model not found" per frame.
        active = get_frame_processors_modules(modules.globals.frame_processors)
        for fp in active:
            try:
                if not fp.pre_check():
                    update_status(
                        f"{fp.NAME}: pre_check failed (model missing or "
                        "download blocked). Live preview may produce no swap."
                    )
            except Exception as e:
                update_status(f"{fp.NAME}: pre_check error: {e}")
        # Warm whichever swapper is currently selected so the first frame
        # doesn't pay the model-load latency.
        for fp in active:
            if fp.NAME in ("DLC.FACE-SWAPPER", "DLC.FACE-SWAPPER-HYPERSWAP"):
                getter = getattr(fp, "get_face_swapper", None)
                if callable(getter):
                    try:
                        getter()
                    except Exception as e:
                        update_status(f"{fp.NAME}: warmup failed: {e}")
                break
        create_webcam_preview(camera_index)
    else:
        modules.globals.source_target_map = []
        create_source_target_popup_for_webcam(
            root, modules.globals.source_target_map, camera_index
        )



def refresh_cameras(camera_variable: "ctk.StringVar | None" = None) -> None:
    """Re-enumerate connected cameras and update the action-bar dropdown.

    The user can hot-plug a USB webcam and click ↻ — no restart required.
    Falls back gracefully when the dropdown is currently disabled.
    """
    global camera_indices_ref, camera_names_ref, camera_optionmenu_ref
    indices, names = get_available_cameras()
    camera_indices_ref[:] = list(indices)
    camera_names_ref[:] = list(names)
    if camera_optionmenu_ref is None:
        return
    if not names or names[0] == "No cameras found":
        camera_optionmenu_ref.configure(values=["No cameras found"], state="disabled")
        if camera_variable is not None:
            camera_variable.set("No cameras found")
        update_status("No cameras detected")
        return
    camera_optionmenu_ref.configure(values=names, state="normal")
    if camera_variable is not None:
        current = camera_variable.get()
        if current not in names:
            camera_variable.set(names[0])
    update_status(f"Found {len(names)} camera(s)")


def load_lut_file() -> None:
    """Open a file picker, parse the .cube file, and arm the LUT for live use."""
    global _active_lut, _active_lut_path
    path = ctk.filedialog.askopenfilename(
        title=_("Load .cube LUT"),
        filetypes=[("Cube LUT", ("*.cube",)), ("All files", ("*.*",))],
    )
    if not path:
        return
    try:
        lut = load_cube_lut(path)
    except Exception as e:
        update_status(f"LUT load failed: {e}")
        return
    _active_lut = lut
    _active_lut_path = path
    modules.globals.lut_path = path
    modules.globals.lut_name = os.path.basename(path)
    save_switch_states()
    if lut_status_label is not None:
        lut_status_label.configure(text=f"LUT: {modules.globals.lut_name}")
    update_status(
        f"Loaded LUT '{modules.globals.lut_name}' (size={lut.size})"
    )


def clear_lut() -> None:
    global _active_lut, _active_lut_path
    _active_lut = None
    _active_lut_path = None
    modules.globals.lut_path = None
    modules.globals.lut_name = "None"
    save_switch_states()
    if lut_status_label is not None:
        lut_status_label.configure(text="LUT: None")
    update_status("LUT cleared")


def _ensure_projection_window():
    """Create the projection window once.  Returns the (window, label) pair."""
    global projection_window, projection_label
    if projection_window is not None and projection_window.winfo_exists():
        return projection_window, projection_label
    projection_window = ctk.CTkToplevel(ROOT)
    projection_window.title("Phantom Cast — Projection")
    projection_window.configure(fg_color="#000000")
    projection_window.geometry("960x540+120+120")

    projection_label = ctk.CTkLabel(projection_window, text="", fg_color="#000000")
    projection_label.pack(fill="both", expand=True)

    def _on_close():
        global projection_window
        try:
            if projection_window is not None:
                projection_window.destroy()
        except Exception:
            pass
        projection_window = None
        modules.globals.projection_mode = 0

    def _toggle_full(_e=None):
        try:
            cur = bool(projection_window.attributes("-fullscreen"))
            projection_window.attributes("-fullscreen", not cur)
            modules.globals.projection_mode = 2 if not cur else 1
        except Exception:
            pass

    projection_window.protocol("WM_DELETE_WINDOW", _on_close)
    projection_window.bind("<F11>", _toggle_full)
    projection_window.bind("<f>", _toggle_full)
    projection_window.bind("<Escape>", lambda e: _on_close())
    return projection_window, projection_label


def close_projection_window() -> None:
    global projection_window
    if projection_window is not None:
        try:
            projection_window.destroy()
        except Exception:
            pass
        projection_window = None
    modules.globals.projection_mode = 0


def toggle_projection_window(borderless: bool = False, fullscreen: bool = False) -> None:
    """Open or close the projection feed window.

    * ``borderless`` — overrideredirect Toplevel suitable for OBS
      window-capture (no chrome, draggable by Tk's window manager).
    * ``fullscreen`` — true fullscreen mode.

    Calling the same mode twice toggles the window off.
    """
    global projection_window
    requested = 2 if fullscreen else 1
    # If already in this mode, close it.
    if (projection_window is not None
            and projection_window.winfo_exists()
            and modules.globals.projection_mode == requested):
        close_projection_window()
        return

    win, _lbl = _ensure_projection_window()
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass
    if fullscreen:
        try:
            win.attributes("-fullscreen", True)
        except Exception:
            win.geometry(f"{win.winfo_screenwidth()}x{win.winfo_screenheight()}+0+0")
        modules.globals.projection_mode = 2
    else:
        try:
            win.attributes("-fullscreen", False)
        except Exception:
            pass
        if borderless:
            try:
                win.overrideredirect(True)
            except Exception:
                pass
        modules.globals.projection_mode = 1

    if modules.globals.in_window_preview:
        # Mutually exclusive — projection wins once user opens it.
        modules.globals.in_window_preview = False
        save_switch_states()


def realtime_video_player(root: ctk.CTk) -> None:
    """Realtime Video Player — apply the swap to a saved video as it plays.

    No render-to-file step.  Frames are read from the file at the file's
    own FPS, run through the live processing thread, and displayed in the
    same preview targets (popup / in-window / projection) as live mode.
    """
    if modules.globals.source_path is None:
        update_status("Please select a source image first")
        return
    video_path = ctk.filedialog.askopenfilename(
        title=_("Select a video to play with face-swap"),
        filetypes=[vid_ft, ("All files", ("*.*",))],
    )
    if not video_path:
        return
    if not is_video(video_path):
        update_status("Selected file is not a supported video")
        return

    # Warm up models so first-frame latency is hidden behind file open.
    from modules.face_analyser import get_face_analyser
    get_face_analyser()
    active = get_frame_processors_modules(modules.globals.frame_processors)
    for fp in active:
        try:
            fp.pre_check()
        except Exception:
            pass

    create_video_player_preview(video_path)


def create_video_player_preview(video_path: str) -> None:
    """Worker that mirrors create_webcam_preview but pulls frames from a file.

    Reuses :func:`_processing_thread_func` for the swap+enhance work and
    the same preview-target selection as live mode.
    """
    global preview_label, PREVIEW

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        update_status(f"Failed to open video: {video_path}")
        return

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src_fps <= 0 or src_fps > 240:
        src_fps = 30.0

    preview_label.configure(width=PREVIEW_DEFAULT_WIDTH, height=PREVIEW_DEFAULT_HEIGHT)
    PREVIEW.deiconify()

    capture_queue = queue.Queue(maxsize=2)
    processed_queue = queue.Queue(maxsize=2)
    stop_event = threading.Event()

    # File reader thread — paces frames at the source FPS for realtime playback.
    def _file_reader():
        delay = 1.0 / src_fps
        next_t = time.time()
        while not stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                stop_event.set()
                break
            try:
                capture_queue.put_nowait(frame)
            except queue.Full:
                try:
                    capture_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    capture_queue.put_nowait(frame)
                except queue.Full:
                    pass
            next_t += delay
            sleep_for = next_t - time.time()
            if sleep_for > 0:
                time.sleep(min(sleep_for, 0.1))

    reader = threading.Thread(target=_file_reader, daemon=True)
    reader.start()

    proc_thread = threading.Thread(
        target=_processing_thread_func,
        args=(capture_queue, processed_queue, stop_event, src_fps),
        daemon=True,
    )
    proc_thread.start()

    def _cleanup():
        stop_event.set()
        reader.join(timeout=2.0)
        proc_thread.join(timeout=2.0)
        cap.release()
        # Don't withdraw if user routed to in-window — they may want to keep it.
        if not modules.globals.in_window_preview and not modules.globals.projection_mode:
            PREVIEW.withdraw()

    poll_ms = max(1, min(16, int(500 / max(src_fps, 1.0))))

    def _display_next_frame():
        if stop_event.is_set():
            _cleanup()
            return
        try:
            bgr_frame = processed_queue.get_nowait()
        except queue.Empty:
            ROOT.after(poll_ms, _display_next_frame)
            return
        _present_processed_frame(bgr_frame)
        ROOT.after(poll_ms, _display_next_frame)

    ROOT.after(0, _display_next_frame)


def _present_processed_frame(bgr_frame):
    """Route a processed BGR frame to the active preview target(s):
    popup window, in-window preview, projection window — and the virtual
    camera if enabled.

    Applies the LUT here (post-swap, pre-display) so all three targets
    plus the virtual camera see the same graded output.
    """
    if bgr_frame is None:
        return
    # 1) LUT (the only color grade applied after the swap)
    if _active_lut is not None and modules.globals.lut_strength > 0:
        try:
            bgr_frame = apply_lut(
                bgr_frame, _active_lut,
                strength=float(modules.globals.lut_strength) / 100.0,
            )
        except Exception as e:
            print(f"[ui] LUT apply failed: {e}")

    # 2) Virtual camera — full-resolution, ungraded-by-Tk frames
    if modules.globals.virtual_cam_enabled:
        sink = get_virtual_cam()
        if not sink.is_open:
            h, w = bgr_frame.shape[:2]
            ok = sink.open(w, h, fps=30)
            modules.globals.virtual_cam_active = ok
            modules.globals.virtual_cam_backend = sink.backend
            if vcam_status_label is not None:
                if ok:
                    vcam_status_label.configure(
                        text=_("Virtual cam: live ({} {}x{})").format(
                            sink.backend or "OBS", w, h,
                        )
                    )
                else:
                    err = sink.last_error or "unknown error"
                    vcam_status_label.configure(
                        text=_("Virtual cam: unavailable — {}").format(err)
                    )
        if sink.is_open:
            sink.send(bgr_frame)
    elif get_virtual_cam().is_open:
        get_virtual_cam().close()
        modules.globals.virtual_cam_active = False

    # 3) Choose the visible target
    targets = []
    if modules.globals.projection_mode and projection_window is not None \
            and projection_window.winfo_exists():
        targets.append(("projection", projection_label, projection_window))
    if modules.globals.in_window_preview and in_window_preview_label is not None:
        targets.append(("inwin", in_window_preview_label, None))
    if not targets:
        targets.append(("popup", preview_label, PREVIEW))

    for kind, label, win in targets:
        if kind == "popup":
            w = max(PREVIEW.winfo_width(), PREVIEW_DEFAULT_WIDTH)
            h = max(PREVIEW.winfo_height(), PREVIEW_DEFAULT_HEIGHT)
        elif kind == "inwin":
            w = max(in_window_preview_label.winfo_width(), 480)
            h = max(in_window_preview_label.winfo_height(), 270)
        else:
            try:
                w = max(projection_window.winfo_width(), 640)
                h = max(projection_window.winfo_height(), 360)
            except Exception:
                w, h = 960, 540

        sized = fit_image_to_size(bgr_frame, w, h)
        rgb = cv2.cvtColor(sized, cv2.COLOR_BGR2RGB)
        try:
            img = Image.fromarray(rgb)
            ctk_img = ctk.CTkImage(img, size=img.size)
            label.configure(image=ctk_img, text="")
        except Exception:
            pass


def get_available_cameras():
    """Returns a list of available camera names and indices."""
    if platform.system() == "Windows":
        try:
            graph = FilterGraph()
            devices = graph.get_input_devices()

            # Create list of indices and names
            camera_indices = list(range(len(devices)))
            camera_names = devices

            # If no cameras found through DirectShow, try OpenCV fallback
            if not camera_names:
                # Try to open camera with index -1 and 0
                test_indices = [-1, 0]
                working_cameras = []

                for idx in test_indices:
                    cap = cv2.VideoCapture(idx)
                    if cap.isOpened():
                        working_cameras.append(f"Camera {idx}")
                        cap.release()

                if working_cameras:
                    return test_indices[: len(working_cameras)], working_cameras

            # If still no cameras found, return empty lists
            if not camera_names:
                return [], ["No cameras found"]

            return camera_indices, camera_names

        except Exception as e:
            print(f"Error detecting cameras: {str(e)}")
            return [], ["No cameras found"]
    else:
        # Unix-like systems (Linux/Mac) camera detection
        camera_indices = []
        camera_names = []

        if platform.system() == "Darwin":
            # Do NOT probe cameras with cv2.VideoCapture on macOS — probing
            # invalid indices triggers the OBSENSOR backend and causes SIGSEGV.
            # Default to indices 0 and 1 (covers FaceTime + one USB camera).
            # The user can select the correct index from the UI dropdown.
            camera_indices = [0, 1]
            camera_names = ["Camera 0", "Camera 1"]
        else:
            # Linux camera detection - test first 10 indices
            for i in range(10):
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    camera_indices.append(i)
                    camera_names.append(f"Camera {i}")
                    cap.release()

        if not camera_names:
            return [], ["No cameras found"]

        return camera_indices, camera_names


def _capture_thread_func(cap, capture_queue, stop_event):
    """Capture thread: reads frames from camera and puts them into the queue.
    Drops frames when the queue is full to avoid backpressure on the camera."""
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            stop_event.set()
            break
        try:
            capture_queue.put_nowait(frame)
        except queue.Full:
            # Drop the oldest frame and enqueue the new one
            try:
                capture_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                capture_queue.put_nowait(frame)
            except queue.Full:
                pass


def _processing_thread_func(capture_queue, processed_queue, stop_event,
                            camera_fps: float = 30.0):
    """Processing thread: takes raw frames from capture_queue, runs face
    detection (throttled), applies face swap/enhancement, and puts results
    into processed_queue.

    Args:
        camera_fps: Actual camera frame rate — used to compute how many
            frames to skip between face detections (~80ms target).
    """
    frame_processors = get_frame_processors_modules(modules.globals.frame_processors)
    source_image = None
    last_source_path = None
    prev_time = time.time()
    fps_update_interval = 0.5
    frame_count = 0
    fps = 0
    det_count = 0
    cached_target_face = None
    cached_many_faces = None
    # Detect every N frames ≈ 80ms.  At 60fps → every 5 frames (83ms),
    # at 30fps → every 3 frames (100ms), at 15fps → every frame.
    base_det_interval = max(1, round(camera_fps * 0.08))
    det_interval = base_det_interval if modules.globals.optimized_rendering else 1
    # Last measured FPS, used by the optimized-rendering auto-tuner.
    last_tune_fps: float = 0.0

    while not stop_event.is_set():
        try:
            frame = capture_queue.get(timeout=0.05)
        except queue.Empty:
            continue

        temp_frame = frame

        if modules.globals.live_mirror:
            temp_frame = gpu_flip(temp_frame, 1)

        if not modules.globals.map_faces:
            if modules.globals.source_path and modules.globals.source_path != last_source_path:
                last_source_path = modules.globals.source_path
                source_image = get_one_face(cv2.imread(modules.globals.source_path))

            # Run detection every det_interval frames (~80ms).
            # Use fast detection (det-only, no landmark/recognition) for live mode.
            det_count += 1
            if det_count % det_interval == 0:
                if modules.globals.many_faces:
                    cached_target_face = None
                    cached_many_faces = detect_many_faces_fast(temp_frame)
                else:
                    cached_target_face = detect_one_face_fast(temp_frame)
                    cached_many_faces = None

            # Build face list for enhancers from cached detection
            _cached_faces = None
            if cached_many_faces:
                _cached_faces = cached_many_faces
            elif cached_target_face is not None:
                _cached_faces = [cached_target_face]

            for frame_processor in frame_processors:
                if frame_processor.NAME == "DLC.FACE-ENHANCER":
                    if modules.globals.fp_ui["face_enhancer"]:
                        temp_frame = frame_processor.process_frame(
                            None, temp_frame, detected_faces=_cached_faces)
                elif frame_processor.NAME == "DLC.FACE-ENHANCER-GPEN256":
                    if modules.globals.fp_ui.get("face_enhancer_gpen256", False):
                        temp_frame = frame_processor.process_frame(
                            None, temp_frame, detected_faces=_cached_faces)
                elif frame_processor.NAME == "DLC.FACE-ENHANCER-GPEN512":
                    if modules.globals.fp_ui.get("face_enhancer_gpen512", False):
                        temp_frame = frame_processor.process_frame(
                            None, temp_frame, detected_faces=_cached_faces)
                elif frame_processor.NAME in ("DLC.FACE-SWAPPER", "DLC.FACE-SWAPPER-HYPERSWAP"):
                    # Use cached face positions from detection thread
                    swapped_bboxes = []
                    if modules.globals.many_faces and cached_many_faces:
                        result = temp_frame.copy()
                        for t_face in cached_many_faces:
                            result = frame_processor.swap_face(source_image, t_face, result)
                            if hasattr(t_face, 'bbox') and t_face.bbox is not None:
                                swapped_bboxes.append(t_face.bbox.astype(int))
                        temp_frame = result
                    elif cached_target_face is not None:
                        temp_frame = frame_processor.swap_face(source_image, cached_target_face, temp_frame)
                        if hasattr(cached_target_face, 'bbox') and cached_target_face.bbox is not None:
                            swapped_bboxes.append(cached_target_face.bbox.astype(int))
                    # Hair color/texture transfer (single-face mode only).
                    # parse_every=5 amortizes the BiSeNet target parse over
                    # multiple frames to keep live FPS up.
                    if (
                        (getattr(modules.globals, "hair_color", 0.0) > 0
                         or getattr(modules.globals, "hair_texture", 0.0) > 0)
                        and not modules.globals.many_faces
                        and cached_target_face is not None
                        and source_image is not None
                    ):
                        temp_frame = _hair_swap.apply_hair_swap(
                            temp_frame, source_image, cached_target_face,
                            parse_every=5,
                        )
                    # Apply post-processing (sharpening, interpolation)
                    temp_frame = frame_processor.apply_post_processing(temp_frame, swapped_bboxes)
                else:
                    temp_frame = frame_processor.process_frame(source_image, temp_frame)
        else:
            modules.globals.target_path = None
            for frame_processor in frame_processors:
                if frame_processor.NAME == "DLC.FACE-ENHANCER":
                    if modules.globals.fp_ui["face_enhancer"]:
                        temp_frame = frame_processor.process_frame_v2(temp_frame)
                elif frame_processor.NAME in ("DLC.FACE-ENHANCER-GPEN256", "DLC.FACE-ENHANCER-GPEN512"):
                    fp_key = frame_processor.NAME.split(".")[-1].lower().replace("-", "_")
                    if modules.globals.fp_ui.get(fp_key, False):
                        temp_frame = frame_processor.process_frame_v2(temp_frame)
                else:
                    temp_frame = frame_processor.process_frame_v2(temp_frame)

        # Calculate and display FPS
        current_time = time.time()
        frame_count += 1
        if current_time - prev_time >= fps_update_interval:
            fps = frame_count / (current_time - prev_time)
            frame_count = 0
            prev_time = current_time
            # Optimized Rendering: gently auto-tune detection stride so the
            # live pipeline keeps the preview fluid on slower hardware.
            if modules.globals.optimized_rendering:
                target_fps = max(camera_fps * 0.7, 20.0)
                if fps < target_fps and det_interval < base_det_interval * 3:
                    det_interval += 1
                elif fps > target_fps * 1.2 and det_interval > 1:
                    det_interval -= 1
            else:
                det_interval = 1
            last_tune_fps = fps

        if modules.globals.show_fps:
            cv2.putText(
                temp_frame,
                f"FPS: {fps:.1f}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2,
            )

        # Queue the processed frame as BGR; the display thread resizes to the
        # preview window first and then runs cvtColor on the (much smaller)
        # buffer — cheaper than converting the full 1080p frame here.
        try:
            processed_queue.put_nowait(temp_frame)
        except queue.Full:
            try:
                processed_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                processed_queue.put_nowait(temp_frame)
            except queue.Full:
                pass


def create_webcam_preview(camera_index: int):
    global preview_label, PREVIEW, _active_capturer

    cap = VideoCapturer(camera_index)
    # Resolution Switch: honour user-selected target.
    req_w, req_h, req_fps = resolution_map(modules.globals.live_resolution)
    if not cap.start(req_w, req_h, req_fps):
        update_status("Failed to start camera")
        return

    _active_capturer = cap
    camera_fps = cap.actual_fps
    print(f"[webcam] Camera running at {cap.actual_width}x{cap.actual_height}@{camera_fps:.0f}fps")

    preview_label.configure(width=PREVIEW_DEFAULT_WIDTH, height=PREVIEW_DEFAULT_HEIGHT)

    # Decide the visible target: in-window / projection / popup
    show_popup = not (
        modules.globals.in_window_preview or modules.globals.projection_mode
    )
    if show_popup:
        PREVIEW.deiconify()
    if modules.globals.projection_mode:
        _ensure_projection_window()

    # Queues for decoupling capture from processing and processing from display.
    # Small maxsize ensures we always work on recent frames and drop stale ones.
    capture_queue = queue.Queue(maxsize=2)
    processed_queue = queue.Queue(maxsize=2)
    stop_event = threading.Event()

    # Start capture thread
    cap_thread = threading.Thread(
        target=_capture_thread_func,
        args=(cap, capture_queue, stop_event),
        daemon=True,
    )
    cap_thread.start()

    # Start processing thread
    proc_thread = threading.Thread(
        target=_processing_thread_func,
        args=(capture_queue, processed_queue, stop_event, camera_fps),
        daemon=True,
    )
    proc_thread.start()

    # Cleanup helper called from the display loop when preview closes
    def _cleanup():
        global _active_capturer
        stop_event.set()
        cap_thread.join(timeout=2.0)
        proc_thread.join(timeout=2.0)
        cap.release()
        _active_capturer = None
        if show_popup:
            PREVIEW.withdraw()
        # Stop pushing to virtual cam — caller will re-open on next Live frame.
        sink = get_virtual_cam()
        if sink.is_open:
            sink.close()
            modules.globals.virtual_cam_active = False

    # Poll at ~2x camera FPS (Nyquist) so we pick up frames promptly
    # without burning CPU.  Clamped to [1, 16] ms.
    poll_ms = max(1, min(16, int(500 / camera_fps)))

    # Non-blocking display loop using ROOT.after() — avoids blocking the
    # Tk event loop which could cause UI freezes or re-entrancy issues.
    def _display_next_frame():
        # Stop conditions:
        #  - capture thread set the stop event
        #  - popup target was withdrawn AND no other target is active
        if stop_event.is_set():
            _cleanup()
            return
        no_targets_left = (
            not modules.globals.in_window_preview
            and not modules.globals.projection_mode
            and PREVIEW.state() == "withdrawn"
        )
        if no_targets_left:
            _cleanup()
            return

        try:
            bgr_frame = processed_queue.get_nowait()
        except queue.Empty:
            ROOT.after(poll_ms, _display_next_frame)
            return

        _present_processed_frame(bgr_frame)
        ROOT.after(poll_ms, _display_next_frame)

    # Kick off the non-blocking display loop
    ROOT.after(0, _display_next_frame)


def create_source_target_popup_for_webcam(
        root: ctk.CTk, map: list, camera_index: int
) -> None:
    global POPUP_LIVE, popup_status_label_live

    POPUP_LIVE = ctk.CTkToplevel(root)
    POPUP_LIVE.title(_("Source x Target Mapper"))
    POPUP_LIVE.geometry(f"{POPUP_LIVE_WIDTH}x{POPUP_LIVE_HEIGHT}")
    POPUP_LIVE.focus()

    def on_submit_click():
        if has_valid_map():
            simplify_maps()
            update_pop_live_status("Mappings successfully submitted!")
            create_webcam_preview(camera_index)  # Open the preview window
        else:
            update_pop_live_status("At least 1 source with target is required!")

    def on_add_click():
        add_blank_map()
        refresh_data(map)
        update_pop_live_status("Please provide mapping!")

    def on_clear_click():
        clear_source_target_images(map)
        refresh_data(map)
        update_pop_live_status("All mappings cleared!")

    popup_status_label_live = ctk.CTkLabel(POPUP_LIVE, text=None, justify="center")
    popup_status_label_live.grid(row=1, column=0, pady=15)

    add_button = ctk.CTkButton(POPUP_LIVE, text=_("Add"), command=lambda: on_add_click())
    add_button.place(relx=0.1, rely=0.92, relwidth=0.2, relheight=0.05)

    clear_button = ctk.CTkButton(POPUP_LIVE, text=_("Clear"), command=lambda: on_clear_click())
    clear_button.place(relx=0.4, rely=0.92, relwidth=0.2, relheight=0.05)

    close_button = ctk.CTkButton(
        POPUP_LIVE, text=_("Submit"), command=lambda: on_submit_click()
    )
    close_button.place(relx=0.7, rely=0.92, relwidth=0.2, relheight=0.05)



def clear_source_target_images(map: list):
    global source_label_dict_live, target_label_dict_live

    for item in map:
        if "source" in item:
            del item["source"]
        if "target" in item:
            del item["target"]

    for button_num in list(source_label_dict_live.keys()):
        source_label_dict_live[button_num].destroy()
        del source_label_dict_live[button_num]

    for button_num in list(target_label_dict_live.keys()):
        target_label_dict_live[button_num].destroy()
        del target_label_dict_live[button_num]


def refresh_data(map: list):
    global POPUP_LIVE

    scrollable_frame = ctk.CTkScrollableFrame(
        POPUP_LIVE, width=POPUP_LIVE_SCROLL_WIDTH, height=POPUP_LIVE_SCROLL_HEIGHT
    )
    scrollable_frame.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")

    def on_sbutton_click(map, button_num):
        map = update_webcam_source(scrollable_frame, map, button_num)

    def on_tbutton_click(map, button_num):
        map = update_webcam_target(scrollable_frame, map, button_num)

    for item in map:
        id = item["id"]

        button = ctk.CTkButton(
            scrollable_frame,
            text=_("Select source image"),
            command=lambda id=id: on_sbutton_click(map, id),
            width=DEFAULT_BUTTON_WIDTH,
            height=DEFAULT_BUTTON_HEIGHT,
        )
        button.grid(row=id, column=0, padx=30, pady=10)

        x_label = ctk.CTkLabel(
            scrollable_frame,
            text=f"X",
            width=MAPPER_PREVIEW_MAX_WIDTH,
            height=MAPPER_PREVIEW_MAX_HEIGHT,
        )
        x_label.grid(row=id, column=2, padx=10, pady=10)

        button = ctk.CTkButton(
            scrollable_frame,
            text=_("Select target image"),
            command=lambda id=id: on_tbutton_click(map, id),
            width=DEFAULT_BUTTON_WIDTH,
            height=DEFAULT_BUTTON_HEIGHT,
        )
        button.grid(row=id, column=3, padx=20, pady=10)

        if "source" in item:
            image = Image.fromarray(
                gpu_cvt_color(item["source"]["cv2"], cv2.COLOR_BGR2RGB)
            )
            image = image.resize(
                (MAPPER_PREVIEW_MAX_WIDTH, MAPPER_PREVIEW_MAX_HEIGHT), Image.LANCZOS
            )
            tk_image = ctk.CTkImage(image, size=image.size)

            source_image = ctk.CTkLabel(
                scrollable_frame,
                text=f"S-{id}",
                width=MAPPER_PREVIEW_MAX_WIDTH,
                height=MAPPER_PREVIEW_MAX_HEIGHT,
            )
            source_image.grid(row=id, column=1, padx=10, pady=10)
            source_image.configure(image=tk_image)

        if "target" in item:
            image = Image.fromarray(
                gpu_cvt_color(item["target"]["cv2"], cv2.COLOR_BGR2RGB)
            )
            image = image.resize(
                (MAPPER_PREVIEW_MAX_WIDTH, MAPPER_PREVIEW_MAX_HEIGHT), Image.LANCZOS
            )
            tk_image = ctk.CTkImage(image, size=image.size)

            target_image = ctk.CTkLabel(
                scrollable_frame,
                text=f"T-{id}",
                width=MAPPER_PREVIEW_MAX_WIDTH,
                height=MAPPER_PREVIEW_MAX_HEIGHT,
            )
            target_image.grid(row=id, column=4, padx=20, pady=10)
            target_image.configure(image=tk_image)


def update_webcam_source(
        scrollable_frame: ctk.CTkScrollableFrame, map: list, button_num: int
) -> list:
    global source_label_dict_live

    source_path = ctk.filedialog.askopenfilename(
        title=_("select an source image"),
        initialdir=RECENT_DIRECTORY_SOURCE,
        filetypes=[img_ft],
    )

    if "source" in map[button_num]:
        map[button_num].pop("source")
        source_label_dict_live[button_num].destroy()
        del source_label_dict_live[button_num]

    if source_path == "":
        return map
    else:
        cv2_img = cv2.imread(source_path)
        face = get_one_face(cv2_img)

        if face:
            x_min, y_min, x_max, y_max = face["bbox"]

            map[button_num]["source"] = {
                "cv2": cv2_img[int(y_min): int(y_max), int(x_min): int(x_max)],
                "face": face,
            }

            image = Image.fromarray(
                gpu_cvt_color(map[button_num]["source"]["cv2"], cv2.COLOR_BGR2RGB)
            )
            image = image.resize(
                (MAPPER_PREVIEW_MAX_WIDTH, MAPPER_PREVIEW_MAX_HEIGHT), Image.LANCZOS
            )
            tk_image = ctk.CTkImage(image, size=image.size)

            source_image = ctk.CTkLabel(
                scrollable_frame,
                text=f"S-{button_num}",
                width=MAPPER_PREVIEW_MAX_WIDTH,
                height=MAPPER_PREVIEW_MAX_HEIGHT,
            )
            source_image.grid(row=button_num, column=1, padx=10, pady=10)
            source_image.configure(image=tk_image)
            source_label_dict_live[button_num] = source_image
        else:
            update_pop_live_status("Face could not be detected in last upload!")
        return map


def update_webcam_target(
        scrollable_frame: ctk.CTkScrollableFrame, map: list, button_num: int
) -> list:
    global target_label_dict_live

    target_path = ctk.filedialog.askopenfilename(
        title=_("select an target image"),
        initialdir=RECENT_DIRECTORY_SOURCE,
        filetypes=[img_ft],
    )

    if "target" in map[button_num]:
        map[button_num].pop("target")
        target_label_dict_live[button_num].destroy()
        del target_label_dict_live[button_num]

    if target_path == "":
        return map
    else:
        cv2_img = cv2.imread(target_path)
        face = get_one_face(cv2_img)

        if face:
            x_min, y_min, x_max, y_max = face["bbox"]

            map[button_num]["target"] = {
                "cv2": cv2_img[int(y_min): int(y_max), int(x_min): int(x_max)],
                "face": face,
            }

            image = Image.fromarray(
                gpu_cvt_color(map[button_num]["target"]["cv2"], cv2.COLOR_BGR2RGB)
            )
            image = image.resize(
                (MAPPER_PREVIEW_MAX_WIDTH, MAPPER_PREVIEW_MAX_HEIGHT), Image.LANCZOS
            )
            tk_image = ctk.CTkImage(image, size=image.size)

            target_image = ctk.CTkLabel(
                scrollable_frame,
                text=f"T-{button_num}",
                width=MAPPER_PREVIEW_MAX_WIDTH,
                height=MAPPER_PREVIEW_MAX_HEIGHT,
            )
            target_image.grid(row=button_num, column=4, padx=20, pady=10)
            target_image.configure(image=tk_image)
            target_label_dict_live[button_num] = target_image
        else:
            update_pop_live_status("Face could not be detected in last upload!")
        return map

