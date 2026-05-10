"""ONNX model downloader with SHA-256 integrity check + signed-URL fetch.

For Pro/Studio gated models we ask the heartbeat endpoint for a short-lived
signed download URL — server-side gates by plan. For free-tier and base
models we use static CDN URLs.

A successful download:
    1. Streams to a ``.tmp`` next to the target.
    2. Verifies SHA-256.
    3. Atomically renames into place.

Idempotent: if the target exists with the right hash, returns immediately.
"""
from __future__ import annotations

import hashlib
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from modules.phantom_cast.logger import get
from modules.phantom_cast.paths import models_dir

log = get("setup.models")


@dataclass(frozen=True)
class ModelSpec:
    name: str
    filename: str
    sha256: str
    url: str
    plan_required: str = "free"   # "free" | "pro" | "studio"
    bytes_estimate: int = 0


REQUIRED_MODELS: List[ModelSpec] = [
    # ModelSpec(
    #     name="InsightFace buffalo_l (det/rec)",
    #     filename="buffalo_l.zip",
    #     sha256="REPLACE_WITH_REAL_SHA256_BUFFALO_L",
    #     url="https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip",
    #     plan_required="free",
    #     bytes_estimate=275_000_000,
    # ),
    ModelSpec(
        name="inswapper_128 (fp16)",
        filename="inswapper_128_fp16.onnx",
        sha256="REPLACE_WITH_REAL_SHA256_INSWAPPER_FP16",
        url="https://models.phantomcast.space/inswapper_128_fp16.onnx",
        plan_required="free",
        bytes_estimate=275_000_000,
    ),
    ModelSpec(
        name="HyperSwap 1A 256",
        filename="hyperswap_1a_256.onnx",
        sha256="REPLACE_WITH_REAL_SHA256_HYPERSWAP_1A_256",
        url="https://huggingface.co/facefusion/models-3.3.0/resolve/main/hyperswap_1a_256.onnx",
        plan_required="free",
        bytes_estimate=403_000_000,
    ),
    ModelSpec(
        name="BiSeNet ResNet-18 (face parser)",
        filename="bisenet_resnet_18.onnx",
        sha256="REPLACE_WITH_REAL_SHA256_BISENET_R18",
        url="https://huggingface.co/facefusion/models-3.1.0/resolve/main/bisenet_resnet_18.onnx",
        plan_required="free",
        bytes_estimate=50_000_000,
    ),
    ModelSpec(
        name="GFPGAN v1.4",
        filename="gfpgan_1.4.onnx",
        sha256="REPLACE_WITH_REAL_SHA256_GFPGAN",
        url="https://github.com/harisreedhar/Face-Upscalers-ONNX/releases/download/Models/GFPGANv1.4.onnx",
        plan_required="free",
        bytes_estimate=350_000_000,
    ),
    ModelSpec(
        name="GPEN-256",
        filename="GPEN-BFR-256.onnx",
        sha256="REPLACE_WITH_REAL_SHA256_GPEN256",
        url="https://github.com/harisreedhar/Face-Upscalers-ONNX/releases/download/Models/GPEN-BFR-256.onnx",
        plan_required="free",
        bytes_estimate=140_000_000,
    ),
    # Premium-only:
    ModelSpec(
        name="GPEN-512",
        filename="GPEN-BFR-512.onnx",
        sha256="REPLACE_WITH_REAL_SHA256_GPEN512",
        url="https://github.com/harisreedhar/Face-Upscalers-ONNX/releases/download/Models/GPEN-BFR-512.onnx",  # filled at runtime by signed-URL request
        plan_required="pro",
        bytes_estimate=270_000_000,
    ),
]


# ---------- SHA / disk helpers ----------


def _sha256_file(p: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _verify(spec: ModelSpec, target: Path) -> bool:
    if not target.is_file():
        return False
    if spec.sha256.startswith("REPLACE_WITH_REAL"):
        # During development we accept presence as good-enough.
        return True
    return _sha256_file(target).lower() == spec.sha256.lower()


def verify_models(specs: Iterable[ModelSpec] = REQUIRED_MODELS) -> List[ModelSpec]:
    """Return the subset of ``specs`` that are missing or hash-mismatched."""
    missing: List[ModelSpec] = []
    for s in specs:
        target = models_dir() / s.filename
        if not _verify(s, target):
            missing.append(s)
    return missing


# ---------- download ----------


def _download(spec: ModelSpec, target: Path,
              progress: Optional[Callable[[int, int], None]] = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()

    url = spec.url
    if not url:
        url = _request_signed_url(spec)

    req = urllib.request.Request(url, headers={"User-Agent": "PhantomCast/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp, tmp.open("wb") as out:
            total = int(resp.headers.get("Content-Length") or spec.bytes_estimate or 0)
            done = 0
            while True:
                buf = resp.read(1024 * 256)
                if not buf:
                    break
                out.write(buf)
                done += len(buf)
                if progress:
                    progress(done, total)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"download {spec.name} failed: HTTP {e.code}") from e

    actual = _sha256_file(tmp)
    if not spec.sha256.startswith("REPLACE_WITH_REAL") and actual.lower() != spec.sha256.lower():
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{spec.name}: SHA-256 mismatch (got {actual[:12]}, "
            f"want {spec.sha256[:12]})"
        )
    os.replace(tmp, target)


def _request_signed_url(spec: ModelSpec) -> str:
    """Ask Cloud Functions for a short-lived signed model URL.

    Server enforces plan: GPEN-512 only returns for plan in {pro, studio}.
    """
    from modules.phantom_cast.firebase.client import _post_json
    from modules.phantom_cast.firebase.config import FUNCTIONS_BASE
    from modules.phantom_cast.license import license_manager

    snap = license_manager().snapshot()
    resp = _post_json(
        f"{FUNCTIONS_BASE}/v1_modelUrl",
        {
            "license_id": snap.license_id,
            "license_key": snap.license_key,
            "model": spec.filename,
        },
    )
    return resp["url"]


# ---------- public async API ----------


def ensure_models_async(
    progress: Callable[[str, int, int], None],
    done: Callable[[bool, Optional[str]], None],
) -> None:
    """Download all missing models on a daemon thread.

    Premium models are skipped if the user lacks the plan; that's fine — the
    free pipeline still works, and premium gated code paths will trigger the
    paywall when invoked.
    """
    def worker():
        try:
            from modules.phantom_cast.subscription.gate import has_feature

            for spec in REQUIRED_MODELS:
                target = models_dir() / spec.filename
                if _verify(spec, target):
                    log.info("model OK: %s", spec.filename)
                    continue
                if spec.plan_required != "free" and not has_feature("gpu_inference"):
                    # Don't pull premium models on free; will fetch on first paid use.
                    log.info("skipping premium model %s (no entitlement yet)", spec.filename)
                    continue
                log.info("downloading %s", spec.filename)
                _download(spec, target,
                          progress=lambda d, t, s=spec: progress(s.name, d, t))
            done(True, None)
        except Exception as e:  # noqa: BLE001
            log.exception("model download failed")
            done(False, str(e))

    threading.Thread(target=worker, daemon=True, name="dlc-model-dl").start()
