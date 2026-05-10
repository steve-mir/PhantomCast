# Feature Implementation Guide

Companion to [`FEATURE_GAP_ANALYSIS.md`](./FEATURE_GAP_ANALYSIS.md). This is the build manual for the three features the OSS edition is missing vs. the 2.7 Subscribers Edition: **RTX Upscaler**, **Face Aging (Re-Age)**, and **Prompt-Driven Scene Restyle ("FLUX Live")**.

Build order is by ascending risk. You can ship feature 1 standalone, feature 2 standalone, but feature 3 should land after a UX-validation phase using a hosted API.

---

## Architecture orientation

The live pipeline is a chain of frame processors registered in `modules/processors/frame/core.py`. Each processor takes a `Frame` (BGR `np.ndarray`) and returns a `Frame`. The order matters.

**Current chain** (live preview, simplified):
```
camera capture
  → face detection (insightface)
  → [optional] hair_swap (texture/color match)
  → face_swapper (inswapper / hyperswap)
  → face_enhancer (GFPGAN / GPEN)
  → face_masking paste-back (lip / chin / eyes / forehead)
  → output_pipeline (color grade, sharpen)
  → preview canvas + virtual cam + window projection
```

**Target chain** with all three new features:
```
camera capture
  → [NEW] scene_restyle (prompt → regenerated frame)         ← runs FIRST so face-swap pastes onto regenerated body
  → face detection
  → hair_swap
  → face_swapper
  → [NEW] face_aging (FRAN on the swapped face crop)
  → face_enhancer
  → face_masking paste-back
  → output_pipeline
  → [NEW] rtx_upscaler (last stage, before display)
  → preview canvas + virtual cam + window projection
```

Three new files under `modules/processors/frame/`:
- `scene_restyle.py`
- `face_aging.py`
- `rtx_upscaler.py` (or `modules/postprocess/rtx_upscaler.py` since it runs after frame processing)

Three new flags in `modules/globals.py`:
```python
# Scene restyle (FLUX Live)
restyle_enabled: bool = False
restyle_prompt: str = ""
restyle_model: str = "flux-schnell"   # or "lucy-2", "sd-turbo"
restyle_strength: float = 0.6          # img2img denoise strength

# Face aging
face_aging_enabled: bool = False
target_age: int = 50                   # 18..80

# RTX upscaler
rtx_upscaler_enabled: bool = False
rtx_upscaler_scale: float = 1.5        # 1.0..4.0
```

Wire each into `switch_states.json` save/load alongside the existing entries (`modules/ui.py:165`).

---

# Feature 1 — RTX Upscaler

**Goal:** spatial AI upscale of the *final* preview frame using NVIDIA's RTX Video Super Resolution model, gated to RTX GPUs, with a Real-ESRGAN ONNX fallback for AMD/Apple Silicon.

**Effort:** ~1 week.
**Risk:** low. Pure post-processing — can't break the existing pipeline.

## What you need

| Component | Where to get it |
|---|---|
| **NVIDIA RTX Video SDK** (primary, RTX-only) | https://developer.nvidia.com/rtx-video-sdk — request access, accept EULA, download SDK zip (~200 MB). Includes headers, lib, sample apps. |
| **CUDA Toolkit 12.x runtime** | Already required by your project. End user installs via NVIDIA's standard CUDA installer. |
| **Real-ESRGAN x2 ONNX** (fallback, all GPUs) | https://huggingface.co/onnx-community/real-esrgan-x2 — `realesrgan-x2.onnx`, ~67 MB. Place under `models/real_esrgan_x2.onnx`. |
| **realesr-general-x4v3 ONNX** (optional, lighter) | https://github.com/xinntao/Real-ESRGAN/releases — convert via `torch.onnx.export`. |
| **pybind11** | `pip install pybind11` — only if you choose the C++/CUDA binding path for RTX VSR. |

## Implementation steps

### Step 1 — RTX VSR via the SDK (primary path, Windows + RTX GPU)

The SDK ships C++ headers and a lib. Two integration choices:

**Choice A: ctypes shim** (no compilation, fastest to land)
1. Build the SDK's sample `NvVideoEffectsSample` once to get a runtime DLL.
2. Wrap the four key calls — `NvVFX_CreateEffect`, `NvVFX_SetU32`, `NvVFX_Run`, `NvVFX_DestroyEffect` — via `ctypes.WinDLL`.
3. Marshall a `np.ndarray` BGR frame → CUDA device buffer → `NvCVImage` struct → upscale → copy back to host.

**Choice B: pybind11 module** (cleaner, better perf, more setup)
1. Write a small C++ file that wraps the SDK in a `py::class_<RtxVsr>` exposing `__init__(scale)` and `run(np.ndarray) -> np.ndarray`.
2. Build with `setup.py build_ext` for Windows; ship the `.pyd` in your Windows release.
3. Skip on macOS/Linux.

**Recommended:** Choice B for production, Choice A for the first prototype.

### Step 2 — Real-ESRGAN ONNX fallback (cross-platform)

```python
# modules/processors/frame/rtx_upscaler.py (excerpt)
import onnxruntime as ort
import numpy as np

class RealESRGANUpscaler:
    def __init__(self, model_path: str, providers=None):
        self.session = ort.InferenceSession(
            model_path,
            providers=providers or ort.get_available_providers(),
        )
        self.input_name = self.session.get_inputs()[0].name

    def run(self, frame_bgr: np.ndarray) -> np.ndarray:
        x = frame_bgr[:, :, ::-1].astype(np.float32) / 255.0   # BGR→RGB, [0,1]
        x = np.transpose(x, (2, 0, 1))[None, ...]              # NCHW
        y = self.session.run(None, {self.input_name: x})[0]
        y = np.transpose(y[0], (1, 2, 0))                      # HWC
        y = np.clip(y * 255.0, 0, 255).astype(np.uint8)
        return y[:, :, ::-1]                                   # RGB→BGR
```

### Step 3 — Capability detection + picker

```python
def make_upscaler():
    if sys.platform == "win32" and _has_rtx_gpu():
        try:
            from modules.processors.frame._rtx_vsr import RtxVsr
            return RtxVsr(scale=modules.globals.rtx_upscaler_scale)
        except OSError:
            pass
    return RealESRGANUpscaler("models/real_esrgan_x2.onnx")
```

Detect RTX via `pynvml.nvmlDeviceGetName()` — match `r"GeForce RTX|RTX A|RTX \d{4}"`.

### Step 4 — Wire into `output_pipeline.py`

Add a final stage in `modules/output_pipeline.py` after the existing color grade. Keep it gated by `globals.rtx_upscaler_enabled`. Run on the **graded preview frame**, *not* on the virtual-camera frame (you don't want to send a 4K stream to OBS).

### Step 5 — UI

In `modules/ui.py`, add to the existing tuning panel:
- A `CTkSwitch` bound to `globals.rtx_upscaler_enabled`.
- A slider 1.0–4.0 bound to `globals.rtx_upscaler_scale`.
- A tooltip noting "Requires NVIDIA RTX 20-series or newer; falls back to Real-ESRGAN on other GPUs."

### Acceptance test

- 720p preview → 1440p output at ≥30 fps on RTX 3060 with the SDK path.
- Same preview → 1440p at ≥15 fps via the Real-ESRGAN fallback on Apple M-series.
- Toggle on/off mid-preview without freezing.

---

# Feature 2 — Face Aging (Re-Age)

**Goal:** shift the apparent age of the swapped face by a user-set amount, while preserving identity.

**Effort:** 1–2 weeks.
**Risk:** medium. Conversion to ONNX and integrating temporal stability are the hard parts.

## What you need

| Component | Where to get it |
|---|---|
| **FRAN PyTorch implementation** | https://github.com/timroelofs123/face_reaging — ships training code + a Hugging Face weights link in the README. The simplest, has Gradio demo. |
| **FRAN weights (`best_unet_model.pth`)** | https://huggingface.co/timroelofs123/fran — ~110 MB U-Net checkpoint. |
| **Alternate FRAN repo** | https://github.com/ry-lu/pytorch-face-reaging-network — Lightning version, useful if you need to retrain. |
| **(Optional) Fast-AgingGAN** | https://github.com/HasnainRaz/Fast-AgingGAN — simpler binary old/young, 60+ fps, lower fidelity. Use as a fallback for low-end GPUs. |
| **insightface landmarks** | Already in repo via `face_analyser.py` — reuse for the 256×256 aligned face crop. |
| **PyTorch ≥ 2.7** + **onnx ≥ 1.18** | Already in `requirements.txt`. |

## Implementation steps

### Step 1 — Convert FRAN to ONNX

The FRAN U-Net takes a 5-channel input: 3 RGB channels + 2 single-channel age maps (source age, target age). Output is a 3-channel residual added back to the input.

```python
# tools/export_fran_onnx.py
import torch
from model import UNet  # from timroelofs123/face_reaging

ckpt = torch.load("best_unet_model.pth", map_location="cpu")
model = UNet(in_ch=5, out_ch=3, base=64)
model.load_state_dict(ckpt["state_dict"])
model.eval()

dummy = torch.randn(1, 5, 256, 256)
torch.onnx.export(
    model, dummy, "models/fran_reage_256.onnx",
    input_names=["face_age_pair"], output_names=["delta_rgb"],
    dynamic_axes={"face_age_pair": {0: "N"}, "delta_rgb": {0: "N"}},
    opset_version=17,
)
```

Validate the ONNX with `onnx.checker.check_model` and a 256² parity test (PyTorch vs. `onnxruntime`) before shipping.

**Where to host the converted weights:** add `fran_reage_256.onnx` to your existing Hugging Face mirror at `huggingface.co/<your-org>/deep-live-cam`, alongside the inswapper/GFPGAN files. Update `models/instructions.txt` with the URL.

### Step 2 — Build the processor

```python
# modules/processors/frame/face_aging.py (skeleton)
import cv2
import numpy as np
import onnxruntime as ort
from modules.typing import Face, Frame

class FaceAging:
    def __init__(self, model_path: str):
        self.session = ort.InferenceSession(model_path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])

    def __call__(self, frame: Frame, face: Face, source_age: int, target_age: int) -> Frame:
        crop, M = align_face(frame, face, size=256)        # reuse insightface aligner
        rgb = crop[:, :, ::-1].astype(np.float32) / 127.5 - 1.0
        age_src = np.full((1, 1, 256, 256), source_age / 100.0, dtype=np.float32)
        age_tgt = np.full((1, 1, 256, 256), target_age / 100.0, dtype=np.float32)
        x = np.concatenate([rgb.transpose(2, 0, 1)[None], age_src, age_tgt], axis=1)
        delta = self.session.run(None, {"face_age_pair": x})[0][0].transpose(1, 2, 0)
        out = np.clip((rgb + delta) * 127.5 + 127.5, 0, 255).astype(np.uint8)[:, :, ::-1]
        return paste_back(frame, out, M)
```

`align_face` and `paste_back` mirror what `face_swapper.py` already does — refactor those into `modules/face_align.py` so both processors share the same aligner.

### Step 3 — Source-age estimation

FRAN needs a *source* age. Two options:
- **Hardcode** to a sensible default (e.g., 30) and let the user pick *target*. Simplest, matches what the video shows (single toggle).
- **Predict** with a small age estimator. **MiVOLO** ([github.com/WildChlamydia/MiVOLO](https://github.com/WildChlamydia/MiVOLO)) — face age regression, ~25 MB ONNX. Better quality, more work.

Ship Option 1 first; add Option 2 behind a "Detect age" checkbox later.

### Step 4 — Temporal stability

Per-frame FRAN inference flickers between frames because the age maps are constant but the input crop wiggles. Two cheap mitigations:

1. **EMA blending:** keep `last_aged_crop`; mix `0.7 * last + 0.3 * current` before paste-back.
2. **Run every N frames:** infer FRAN every 3rd frame, reuse the previous result for the gap. Faces don't change appearance fast enough for the user to notice.

### Step 5 — UI

In the tuning panel, between "Sharpness" and "Face Masking":
- `CTkSwitch` "Enable Face Aging" → `globals.face_aging_enabled`.
- `CTkSlider` "Target age" 18 → 80, default 50 → `globals.target_age`.
- A small label showing "Inferring every 3 frames" so users understand the framerate cost.

### Step 6 — Pipeline placement

In `processors/frame/core.py`, register `face_aging` to run **after** `face_swapper` but **before** `face_enhancer`. The enhancer then cleans up any FRAN artifacts.

### Acceptance test

- Toggle on with target_age=70 on a 30-year-old source → visible wrinkles, grey hair edge in temple, eye creases.
- target_age=20 on a 50-year-old → smoother skin, fuller cheeks.
- 30 fps live preview holds with FRAN running every 3rd frame on a 3060.
- Identity preserved (insightface cosine similarity vs. pre-aging > 0.85).

---

# Feature 3 — Prompt-Driven Scene Restyle ("FLUX Live")

**Goal:** type a text prompt → the live webcam frame is regenerated with new clothing / hair / scene at ≥20 fps. Face-swap pastes on top so identity is preserved.

**Effort:** Phase A 1 week, Phase B 3–4 weeks.
**Risk:** highest. Real-time diffusion is finicky; temporal flicker is the main failure mode.

**Two-phase rollout. Do Phase A first** to validate UX and the API contract; then decide whether the per-minute cost justifies Phase B.

---

## Phase A — Decart Lucy 2 cloud API

**Why first:** Lucy 2 is built for exactly this UX (live webcam + text prompt + identity-preserving outfit/hair/background swap at 30 fps 1080p). 1 week to working. If your Pro build is *also* a Lucy wrapper, this is the entire feature.

### What you need

| Component | Where to get it |
|---|---|
| **Decart Platform account + API key** | https://platform.decart.ai/ — sign up, generate key, set quota. Pricing per stream-minute. |
| **`lucy-restyle-live` model docs** | https://platform.decart.ai/models/lucy-restyle-live — endpoint URL, prompt schema, frame-format requirements. |
| **WebRTC client** (`aiortc`) | `pip install aiortc==1.9.0` — for streaming frames out and pulling restyled frames back. |
| **(Alternative)** RTMP if Decart accepts it | `pip install python-rtmp` or pipe through `ffmpeg` subprocess. |

### Implementation steps

1. **Add a `scene_restyle.py` processor** that holds an async WebRTC client to the Decart endpoint.
2. **Frame in / frame out queues:** push every captured frame to an `asyncio.Queue`; consume restyled frames from a return queue. Use a `threading.Event` to bridge to the sync ctkinter loop.
3. **Prompt updates:** when the user edits the textbox, send a `{"prompt": "..."}` control message over the data channel. Lucy's API supports live prompt swaps.
4. **Latency budget:** typical is 200–400 ms round-trip. Display the restyled preview but feed the *original* frame to the virtual camera if the user wants OBS sync — or accept the latency.
5. **UI:**
   - Dropdown "Model": `Off`, `Lucy 2 (cloud)`, `Lucy Restyle (cloud)`, later `FLUX Live (local)`.
   - Textbox "Prompt" with debounce (push prompt 300 ms after the user stops typing).
   - Status indicator: `Connected`, `Reconnecting`, `Quota exhausted`.

### Acceptance test

- Type `red leather jacket` → preview shows the jacket within 500 ms.
- Change to `blue hoodie` → smooth crossfade to the new outfit within 1 second.
- Network drop → falls back to pass-through within 2 seconds, shows a clear status.

---

## Phase B — Self-hosted StreamDiffusion + Flux Schnell

**Why:** drops the per-minute API bill, runs offline, ships in your `requirements_full.txt`. **Cost:** real-time diffusion on a single GPU is hard. Expect to spend the bulk of the time on temporal stability and pose lock.

### What you need

| Component | Where to get it |
|---|---|
| **StreamDiffusion** | https://github.com/cumulo-autumn/StreamDiffusion — `pip install streamdiffusion` or clone for the latest. Includes `demo/realtime-img2img` as a reference. |
| **Flux.1 Schnell weights** | https://huggingface.co/black-forest-labs/FLUX.1-schnell — ~24 GB FP16, ~12 GB FP8. License: Apache-2.0. Requires HF login + license acceptance. |
| **(Alternative) SD-Turbo** | https://huggingface.co/stabilityai/sd-turbo — ~3 GB, faster, Apache-2.0, easier to ship. Recommend for the first cut. |
| **(Alternative) SDXL-Lightning** | https://huggingface.co/ByteDance/SDXL-Lightning — 1/2/4/8-step distillations of SDXL. |
| **LCM-LoRA SDv1.5** | https://huggingface.co/latent-consistency/lcm-lora-sdv1-5 — ~67 MB, drops sampling to 4 steps. |
| **TAESD (tiny VAE)** | https://huggingface.co/madebyollin/taesd — ~10 MB, 5–10× faster decode than the standard VAE. |
| **ControlNet Tile (SDv1.5)** | https://huggingface.co/lllyasviel/control_v11f1e_sd15_tile — keeps frame-to-frame structure, kills flicker. ~700 MB. |
| **IP-Adapter Plus** | https://huggingface.co/h94/IP-Adapter — preserves identity/pose between frames. ~300 MB. |
| **TensorRT** | https://developer.nvidia.com/tensorrt — `pip install tensorrt==10.x`. Required for the 30%+ speedup; without it, Flux Schnell won't hit 30 fps even on a 4090. |
| **`xformers`** | `pip install xformers` — already a soft dep of diffusers; flash-attention kernels. |
| **`diffusers`, `transformers`, `accelerate`** | `pip install diffusers==0.30.* transformers==4.44.* accelerate==0.34.*` |
| **`onnx-graphsurgeon` + `polygraphy`** | For TRT engine baking. Comes with TensorRT. |

### Implementation steps

#### B1 — Stand up the realtime-img2img demo
```bash
git clone https://github.com/cumulo-autumn/StreamDiffusion
cd StreamDiffusion
pip install -e .
cd demo/realtime-img2img
python main.py --model_id_or_path stabilityai/sd-turbo
```
Confirm webcam → preview works. **Do not skip this step** — if the upstream demo doesn't run on your machine, your fork won't either.

#### B2 — Bake TensorRT engines
StreamDiffusion ships an `acceleration/tensorrt/` helper. Run once per model + resolution combo:
```bash
python -m streamdiffusion.acceleration.tensorrt.export \
  --model stabilityai/sd-turbo \
  --width 512 --height 512 --batch 1 \
  --output engines/sd_turbo_512.engine
```
Engines are GPU-architecture-specific — bake on the deployment GPU or ship per-arch builds.

#### B3 — Wrap into a processor
```python
# modules/processors/frame/scene_restyle.py (skeleton)
from streamdiffusion import StreamDiffusion
from streamdiffusion.image_utils import postprocess_image

class SceneRestyle:
    def __init__(self, model_id: str, engine_dir: str):
        self.stream = StreamDiffusion(
            pipe=load_pipe(model_id),
            t_index_list=[32, 45],          # 2-step
            torch_dtype=torch.float16,
            width=512, height=512,
        )
        self.stream.load_lcm_lora()
        self.stream.fuse_lora()
        self.stream.use_tiny_vae()
        self.stream.enable_similar_image_filter(0.98)   # SSF — skips redundant frames
        self.stream.prepare(prompt="", negative_prompt="low quality, blurry")

    def update_prompt(self, prompt: str):
        self.stream.update_prompt(prompt)

    def run(self, frame_bgr: np.ndarray) -> np.ndarray:
        out = self.stream(frame_bgr)
        return postprocess_image(out)
```

#### B4 — ControlNet for temporal stability
The single biggest quality win. Without ControlNet-Tile (or HED, or Depth), every frame is a fresh hallucination and the output flickers. With it, the diffusion is conditioned on the input frame's structure → stable.

Add a ControlNet branch to the pipe:
```python
controlnet = ControlNetModel.from_pretrained("lllyasviel/control_v11f1e_sd15_tile")
self.stream = StreamDiffusionControlNet(pipe, controlnet, ...)
```

Tradeoff: ControlNet-Tile adds ~5 ms/frame on a 4090, less prompt influence.

#### B5 — Compositing with face-swap
- Run `scene_restyle` *first* on the raw camera frame.
- Detect the face on the **original** frame (insightface), then re-detect on the restyled frame for paste-back coordinates.
- Run face-swap on the restyled frame using the original-frame's source face.
- Result: regenerated body + clothes + hair + the user's chosen identity, all coherent.

If the diffusion regenerates the face position too aggressively, lock the face region with an inpainting mask (use the existing face mask from `face_masking.py` to keep diffusion *out* of the face area).

#### B6 — Hardware tier fallback

| GPU class | Recommended config |
|---|---|
| RTX 4090 / 5090 | Flux.1 Schnell @ 1024², 4-step, TRT, ControlNet-Tile → 25–30 fps |
| RTX 4070 / 4080 | SD-Turbo @ 768², 1-step, TRT, ControlNet → 30 fps |
| RTX 3060 / 3070 | SD-Turbo @ 512², 1-step, TRT, no ControlNet → 25 fps |
| Apple Silicon | Disable Phase B; force Phase A (Lucy API) only |
| AMD | ONNX/DirectML SD-Turbo @ 512² → 10–15 fps (degraded mode) |

Capability detection: use `pynvml` total VRAM + GPU name to pick the config at startup.

### Phase B acceptance test

- 30 fps preview holds on a 4090 with ControlNet-Tile + TRT + Flux Schnell.
- Prompt change reflects on-screen within ~500 ms (one TRT engine warm-up + 2 frames).
- Frame-to-frame structural similarity (SSIM) on a static scene > 0.92 (i.e., not flickering).
- Face identity post-swap matches source within insightface cosine 0.85.

---

# Cross-cutting work

These touch all three features.

## Model download manager

You currently use `models/instructions.txt` and ask users to download manually. Three new models is two too many to keep doing that. Build a small downloader:

```python
# modules/model_manager.py
MODELS = {
    "real_esrgan_x2":   ("https://huggingface.co/onnx-community/real-esrgan-x2/resolve/main/realesrgan-x2.onnx",            "models/real_esrgan_x2.onnx",   "<sha256>"),
    "fran_reage_256":   ("https://huggingface.co/<your-org>/deep-live-cam/resolve/main/fran_reage_256.onnx",                 "models/fran_reage_256.onnx",   "<sha256>"),
    "sd_turbo_engine":  ("https://huggingface.co/<your-org>/deep-live-cam/resolve/main/sd_turbo_512_sm89.engine",            "models/engines/sd_turbo_512.engine", "<sha256>"),
}

def ensure(model_key: str) -> Path:
    url, dest, sha = MODELS[model_key]
    if Path(dest).exists() and _sha256(dest) == sha:
        return Path(dest)
    _download_with_progress(url, dest)
    assert _sha256(dest) == sha, "checksum mismatch"
    return Path(dest)
```

Hook it into the existing setup wizard at `modules/dlc_pro/ui/setup_wizard.py` — show progress bars for each missing model.

## Settings persistence

Add the new flags to `switch_states.json` save/load. Existing pattern at `modules/ui.py:165`. Don't forget defaults for backward compat.

## License gating (Pro features)

Your Pro layer (`modules/dlc_pro/license/`) already gates inswapper. The three new features are good candidates for the same gate — especially Lucy API access (which costs you per-minute money) and the StreamDiffusion stack (large download). Check `modules.dlc_pro.license.is_active()` before enabling.

## Telemetry / kill switch

The Decart API has a quota; you'll want a kill switch so a runaway stream doesn't burn through the user's plan (or yours, if you're proxying). Plumb a per-session minute counter and auto-disable at 90% quota.

## Documentation

- Update `README.md` "Features" with the three new bullet points.
- Update `docs/SETUP.md` with model download URLs.
- Add a "Hardware requirements" matrix — which GPU classes get which features.

---

# Suggested timeline

| Week | Track A (RTX) | Track B (FRAN) | Track C (Restyle) |
|---|---|---|---|
| 1 | RTX SDK download, Real-ESRGAN ONNX integrated | FRAN PyTorch test on stills | — |
| 2 | RTX SDK ctypes shim shipped | FRAN→ONNX conversion + parity tests | Phase A: Decart account, WebRTC PoC |
| 3 | (done) | Processor + UI + temporal smoothing | Phase A: full UI integration, ship as beta |
| 4 | — | (done) | Phase B kickoff: StreamDiffusion demo running |
| 5 | — | — | Phase B: TRT engine baking, ControlNet integration |
| 6 | — | — | Phase B: compositing with face-swap, tier fallback |
| 7 | — | — | Phase B: temporal stability tuning, beta ship |

Tracks A and B are independent — assign different engineers and run them in parallel. Track C blocks on nothing but is the longest.

---

# Risks & mitigations

| Risk | Mitigation |
|---|---|
| RTX VSR SDK access denied / EULA blocks redistribution | Ship the SDK DLL only when the user has it locally; check at first launch. Real-ESRGAN ONNX is the open fallback. |
| FRAN ONNX export fails (custom ops in newer PyTorch) | Pin PyTorch to the version FRAN was trained on (likely 1.13–2.0); export in a separate conda env. |
| Flux Schnell license drift (BFL has been changing terms) | Default to SD-Turbo (Apache-2.0). Flux is a power-user upgrade. |
| Real-time diffusion flickers despite ControlNet | Add per-frame latent caching + Stochastic Similarity Filter (built into StreamDiffusion). Cap at 20 fps if needed. |
| Decart API pricing changes | Abstract the cloud client behind an interface; add a Replicate or Fal.ai backend as an alt. |
| Diffusion + face-swap interfere — face shifts every frame | Use the existing face mask as a diffusion-exclusion mask via ControlNet-Inpaint. |

---

# Quick-reference: every model & where to download

```
# Feature 1 — RTX Upscaler
models/real_esrgan_x2.onnx
  https://huggingface.co/onnx-community/real-esrgan-x2/resolve/main/realesrgan-x2.onnx
NVIDIA RTX Video SDK (Windows + RTX only)
  https://developer.nvidia.com/rtx-video-sdk

# Feature 2 — Face Aging
models/fran_reage_256.onnx (you convert from PyTorch)
  Source weights:   https://huggingface.co/timroelofs123/fran
  Source code:      https://github.com/timroelofs123/face_reaging
  Alt code:         https://github.com/ry-lu/pytorch-face-reaging-network
  Light fallback:   https://github.com/HasnainRaz/Fast-AgingGAN

# Feature 3 — Scene Restyle
Phase A (cloud):
  https://platform.decart.ai/  (Lucy 2 / Lucy Restyle Live)

Phase B (local):
  StreamDiffusion code:    https://github.com/cumulo-autumn/StreamDiffusion
  SD-Turbo weights:        https://huggingface.co/stabilityai/sd-turbo
  Flux.1 Schnell weights:  https://huggingface.co/black-forest-labs/FLUX.1-schnell
  SDXL-Lightning:          https://huggingface.co/ByteDance/SDXL-Lightning
  LCM-LoRA (SDv1.5):       https://huggingface.co/latent-consistency/lcm-lora-sdv1-5
  TAESD (tiny VAE):        https://huggingface.co/madebyollin/taesd
  ControlNet Tile:         https://huggingface.co/lllyasviel/control_v11f1e_sd15_tile
  IP-Adapter Plus:         https://huggingface.co/h94/IP-Adapter
  TensorRT:                https://developer.nvidia.com/tensorrt
```

Add SHA-256 checksums for everything in `modules/model_manager.py` before shipping — silent corruption of multi-GB diffusion checkpoints is not a debugging session you want to have.
