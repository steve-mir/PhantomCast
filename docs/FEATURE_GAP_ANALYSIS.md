# Feature Gap Analysis — Open-source baseline vs. premium Subscribers Edition

**Source:** 40-second screen recording (1110×856, 30 fps) of the closed-source "Subscribers Edition" build, demonstrating prompt-driven appearance edits on a live webcam.
**Analyzed against:** this repository, branch `main`, commit `9463cce`.

---

## TL;DR

The Subscribers Edition demos **prompt-driven real-time scene regeneration**. The user types `red tank top` → shirt becomes a red tank top. `black leather jacket` → leather jacket. `long yellow hair` → entire upper body re-rendered with blonde hair, white t-shirt, and slightly feminized features. Throughout, the Elon source-face swap stays locked.

You already ship ~85% of the visible UI. Three real engineering gaps remain:

1. **Prompt-driven scene editor** ("FLUX Live" model + prompt textbox).
2. **Aging / Re-Age** section with "Enable Face Aging" toggle.
3. **RTX Upscaler** toggle + scale slider (NVIDIA RTX VSR).

Cosmetic-only deltas (resolution preset *buttons* vs. dropdown, "Subscribers Edition" branding) are out of scope.

---

## Side-by-side feature audit

| UI element in the video | Status in repo | Reference |
|---|---|---|
| Face Swapper toggles (Map/Many faces, Poisson, Fix-blueish, Optimize Inswapper, Safety Check) | Present | `modules/globals.py:30`, `modules/ui.py:948` |
| Lip / Forehead / Chin / Eyes mask sliders | Present | `modules/ui.py:1108–1216`, `modules/processors/frame/face_masking.py` |
| Sharpness / Opacity / Face Fader sliders | Present | `modules/globals.py:57`, `modules/ui.py:1063` |
| Face Enhancer (GFPGAN / GPEN-256 / GPEN-512) | Present | `modules/processors/frame/face_enhancer*.py` |
| Hair color / texture transfer | Present | `modules/processors/frame/hair_swap.py` |
| Resolution switch | Present (dropdown; Pro has preset buttons 320p–1440p — cosmetic) | `modules/ui.py:1343` |
| Virtual Camera | Present | `modules/ui.py:1470` |
| Window Projection | Present | `modules/ui.py:1551` |
| Random/AI source face (✨ button) | Present (uses thispersondoesnotexist.com) | `modules/ui.py:1879` |
| **Model dropdown "FLUX Live" + Prompt textbox** | **MISSING** | — |
| **Aging / Re-Age → "Enable Face Aging"** | **MISSING** | — |
| **RTX Upscaler toggle + Scale slider** | **MISSING** | — |

---

## Frame-by-frame evidence (what the video proves)

| Time (s) | Prompt typed | Result on screen |
|---|---|---|
| 0–4 | (empty) | Webcam pass-through with face-swap (Elon) only |
| 5–9 | `red tank top` | Original grey tank top recolored to red |
| 10–14 | (none) | Brief monochrome frame — likely the model "warming up" or reset |
| 15–24 | `black leather jacket` | Tank top fully replaced by black leather jacket; folds, sheen, collar all coherent |
| 25–40 | `long yellow hair` | Hair regenerated as long blonde; wardrobe morphs to white tee; face subtly feminized — the entire upper-body region is regenerated, not just hair |

The fact that `long yellow hair` also changes the t-shirt and softens facial features confirms this is **whole-frame img2img diffusion**, not a hair-mask recolor or local inpainting.

---

## What the missing UI controls imply technically

### 1. "FLUX Live" model + prompt textbox
- Live diffusion model running per-frame (or every Nth frame with temporal smoothing).
- Text encoder + cross-attention conditioning.
- Must hit ≥20 fps to feel "live" — points at distilled 1–4-step models (Flux.1 Schnell, SD-Turbo, SDXL-Lightning) or LCM-LoRA pipelines.
- Composited *under* the existing face-swap so identity is preserved while the body/clothes/hair are regenerated.

### 2. Aging / Re-Age toggle
- Single switch (no slider visible in the captured frames, though one likely appears when expanded).
- Operates on the swapped face crop, so it reads the post-swap face and shifts apparent age.
- Most production-grade open option: Disney's **FRAN** (Face Re-Aging Network), U-Net-based, identity-preserving.

### 3. RTX Upscaler + Scale slider
- Toggle plus a `Scale:` slider — clearly an upscaler stage, not denoise/sharpen (those exist already).
- Branding "RTX" points at the **NVIDIA RTX Video SDK** (RTX VSR + RTX Video HDR), which is the only thing that earns that label.

---

## What's already in `modules/phantom_cast/`

The repository ships a Pro layer (`modules/phantom_cast/`) covering license activation, Firebase backend, paywall dialog, setup wizard, and updater. The **inference and UI** for the three missing features are not present in `phantom_cast/ui/` either — i.e., this isn't a paywalled-but-implemented feature. They need to be built.

---

## Build-order recommendation

Lowest-risk → highest-risk:

1. **RTX Upscaler** — smallest scope, no training, immediate visible win. ~1 week.
2. **Face Aging (FRAN → ONNX)** — one self-contained processor, slots cleanly into the existing pipeline. 1–2 weeks.
3. **Prompt-driven restyle** — biggest. Recommend a 2-phase rollout: Decart Lucy API first (validates UX in 1 week), then optional self-hosted StreamDiffusion stack (3–4 weeks) if you want to drop the per-minute API bill.

See [`FEATURE_IMPLEMENTATION_GUIDE.md`](./FEATURE_IMPLEMENTATION_GUIDE.md) for the full build plan, model URLs, and integration points.

---

## Open question worth resolving before building

Is the Pro build's "FLUX Live" actually a wrapper around **Decart Lucy 2** (commercial API), or is it a self-hosted StreamDiffusion / Flux Schnell pipeline? Capturing the Pro build's outbound network traffic would settle this in 5 minutes and could save weeks of work — if it's a Decart wrapper, your fastest match is also a Decart wrapper.

---

## Sources

- [Decart — Lucy 2 announcement](https://decart.ai/publications/lucy-2-introducing-sota-video-generation-in-realtime)
- [Decart — Lucy Restyle Live API](https://platform.decart.ai/models/lucy-restyle-live)
- [the-decoder — Lucy 2 transforms live video via text prompts](https://the-decoder.com/decarts-lucy-2-0-transforms-live-video-in-real-time-using-text-prompts/)
- [StreamDiffusion (cumulo-autumn)](https://github.com/cumulo-autumn/StreamDiffusion)
- [StreamDiffusion paper — arXiv 2312.12491](https://arxiv.org/html/2312.12491v2)
- [Stability AI — SDXL Turbo](https://stability.ai/news/stability-ai-sdxl-turbo)
- [Disney Research — Production-Ready Face Re-Aging (FRAN)](https://studios.disneyresearch.com/2022/11/30/production-ready-face-re-aging-for-visual-effects/)
- [timroelofs123/face_reaging (FRAN PyTorch + HF weights)](https://github.com/timroelofs123/face_reaging)
- [ry-lu/pytorch-face-reaging-network](https://github.com/ry-lu/pytorch-face-reaging-network)
- [HasnainRaz/Fast-AgingGAN](https://github.com/HasnainRaz/Fast-AgingGAN)
- [yuval-alaluf/SAM — Style-based Age Manipulation](https://github.com/yuval-alaluf/SAM)
- [NVIDIA RTX Video SDK](https://developer.nvidia.com/rtx-video-sdk)
- [NVIDIA Developer Blog — Enhancing video with RTX Video SDK](https://developer.nvidia.com/blog/enhancing-low-resolution-sdr-video-with-the-nvidia-rtx-video-sdk/)
- [NVIDIA — RTX Video FAQ](https://nvidia.custhelp.com/app/answers/detail/a_id/5448/~/rtx-video-faq)
