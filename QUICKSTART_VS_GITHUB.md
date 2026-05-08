# Deep-Live-Cam: QuickStart (Premium) vs GitHub (Open-Source) Comparison

Comparison of features in the paid **QuickStart** distribution at
<https://deeplivecam.net/index.php/quickstart> versus the open-source GitHub
build at <https://github.com/hacksider/Deep-Live-Cam>.

The local checkout at `Deep-Live-Cam-main/` is the open-source GitHub baseline.

---

## Features the QuickStart (premium) version has that the GitHub version does not

### Packaging & support
- **One-click executable / precompiled portable build** — GitHub version requires manual Git + Python + Pip + CUDA + C++ + FFmpeg setup
- **Pre-bundled dependencies** — plug-and-play, no technical skills required
- **Always ~1.0 version ahead** of GitHub (example: QuickStart 3.1 ≈ GitHub 2.1)
- **Priority Support**

### Models & enhancers
- **HyperSwap (256×256) face-swap model** in addition to Inswapper-128 (GitHub has only Inswapper-128). Listed as "up to 200% boosted deepfake."
- **GPEN-256 and GPEN-512 face enhancers** alongside GFPGAN (GitHub has only GFPGANv1.4)
- **Smarter Model Selection dropdown** to switch between top-performing models
- **Lightning-Fast Face Enhancer** — up to 4× faster than GitHub's GFPGAN path
- **Realtime Face Enhancer** — live, on-the-fly upscaling without killing FPS *(2.7 Beta)*
- **Face Enhancer Scaler** — dial enhancement intensity up or down *(2.7 Beta)*
- **Inswapper Optimizer** — fine-tuned core swap engine *(2.7 Beta)*
- **Interpolation** — smoother frame-to-frame transitions *(2.7 Beta)*

### Masking & blending
- **Forehead sliders** — sliders to match the head (no equivalent in GitHub)
- **Quick Lip Mask** — automated, fast
- **Chin Mask** — eliminates harsh jaw boundaries
- **Eyes Mask** — keeps eyes sharp and expressive
- **Poisson Blending upgrade** *(2.4)* — removes translucent boxes, cleaner ears

### Camera, output & UI
- **Virtual Camera support** (OBS-friendly streaming to any platform) — GitHub has none
- **Camera Refresh button** — hot-swap webcam without restarting the app
- **Optimized Rendering** — automatic optimization across live, video, and image
- **Resolution Switch / Resolution Changer** — auto-pick or change resolution on the fly
- **Realtime Video Player** — watch saved videos with face-swap applied in real time, no rendering step
- **Window Projection** — borderless pop-out feed for OBS/recording *(2.7 Beta)*
- **Window Projection Full Screen Mode** *(2.7 Beta)*
- **In-Window Preview** — monitor swap inside main dashboard *(2.7 Beta)*
- **LUTs for color grading** — built-in cinematic LUTs *(2.7 Beta)*
- **GPU Changer / Multi-GPU support** — distribute load across GPUs *(2.7 Beta)*
- **Forced GPU usage on laptops** *(2.4)*
- **Better UI** — overhauled interface *(2.7 Beta)*

> Note: the comparison table on the QuickStart page contains a couple of apparent
> copy-paste typos where the "Mask" and "Camera Refresh" rows reuse the same
> description. The items above are cross-referenced against the 2.7 Beta and
> 2.6 release notes.

---

## Features the QuickStart team intends to release (roadmap)

From their news posts and teasers:

- **VR support** — tech demo previewed; still under optimization
- **Real-time voice transformation companion software** with 1,000+ voices, included in subscription (teased alongside 2.5)
- **Beginner-friendly virtual camera setup** for easier onboarding
- **Smarter camera handling** across varied setups
- **Major performance optimizations targeting GeForce 20xx and RTX 30xx–50xx GPUs**, claiming nearly double the performance
- **"Movies starring you"** — face-map yourself (and friends) into full movies played back in real time, an extension of the Realtime Video Watching feature
- **2.7 stable release** — currently in Beta (Windows NVIDIA/AMD + Mac); full release pending feedback
- General "more features being brewed" / "powerful new features and enhancements" referenced in subscriber teasers

---

## Sources

- [Deep Live Cam QuickStart page](https://deeplivecam.net/index.php/quickstart)
- [DeepLiveCam Home (news feed)](https://deeplivecam.net/)
- [2.7 Beta release post](https://deeplivecam.net/index.php/news/2-7-beta-released-windows-only-as-of-this-moment)
- [Movies starring you (2.7)](https://deeplivecam.net/index.php/news/maximize-your-deeplivecam-experience-movies-starring-you)
- [Teaser: 2.5 + subscriber surprises](https://deeplivecam.net/index.php/news/teaser-deep-live-cam-2-5-and-plus-more-surprises-for-subscribers)
- [We skipped 2.5… Here comes 2.6](https://deeplivecam.net/index.php/news/we-skipped-2-5-here-comes-2-6)
- [DeepLiveCam 2.4 release](https://deeplivecam.net/index.php/news/deeplivecam-2-4-is-now-released)
- [DeepLiveCam 2.3 release](https://deeplivecam.net/index.php/news/deeplivecam-2-3-released)
- [VR Preview](https://deeplivecam.net/index.php/news/vr-preview)
- [hacksider/Deep-Live-Cam GitHub](https://github.com/hacksider/Deep-Live-Cam)
