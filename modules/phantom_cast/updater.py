"""GitHub-Releases-backed in-app updater.

Flow:
    1.  ``check_for_update()`` hits the GitHub Releases API for
        ``steve-mir/PhantomCast`` and parses the latest published release.
    2.  Versions are compared with a tiny semver tuple parser.
    3.  If newer, the caller (UI) prompts the user; on accept, ``download()``
        streams the installer .exe to a temp file with a progress callback.
    4.  ``apply_update()`` launches the installer and exits the app so the
        installer can replace the running .exe (Windows-only flow). On macOS
        and Linux it falls back to opening the file manager / browser.

Dependencies: stdlib only. Network calls are wrapped in narrow try/except so
update failures never block app startup. A user-facing 'skip this version'
preference is persisted in ``%LOCALAPPDATA%/DeepLiveCamPro/updater.json``.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from modules.dlc_pro.logger import get
    log = get("updater")
except Exception:  # pragma: no cover — fallback when logger isn't initialized
    import logging
    log = logging.getLogger("dlc_pro.updater")

# ----------------------------------------------------------------------- config

GITHUB_OWNER = "steve-mir"
GITHUB_REPO  = "PhantomCast"
GITHUB_API   = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Override via env for staging / forks: e.g. DLCPRO_UPDATE_REPO=foo/bar
_REPO_OVERRIDE = os.environ.get("DLCPRO_UPDATE_REPO", "")
if _REPO_OVERRIDE and "/" in _REPO_OVERRIDE:
    GITHUB_OWNER, GITHUB_REPO = _REPO_OVERRIDE.split("/", 1)
    GITHUB_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

REQUEST_TIMEOUT_SECONDS = 10
DOWNLOAD_CHUNK_BYTES = 256 * 1024
USER_AGENT = "DeepLiveCamPro-updater/1.0 (+https://github.com/steve-mir/PhantomCast)"

# Asset matchers in priority order: prefer the installer over the loose .exe.
_ASSET_PATTERNS = (
    re.compile(r".*installer.*\.exe$", re.I),
    re.compile(r".*setup.*\.exe$", re.I),
    re.compile(r".*\.exe$", re.I),
)


def _state_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    p = base / "DeepLiveCamPro"
    p.mkdir(parents=True, exist_ok=True)
    return p / "updater.json"


# ------------------------------------------------------------------ data shapes


@dataclass
class UpdateInfo:
    current: str
    latest: str
    name: str
    body: str
    html_url: str
    asset_url: Optional[str]
    asset_name: Optional[str]
    asset_size: Optional[int]
    published_at: Optional[str]


# ------------------------------------------------------------ version utilities


_VERSION_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?$")


def parse_version(s: str) -> Tuple[int, int, int]:
    """Parse 'v1.2.3', '1.2.3', '1.2', '1' → (major, minor, patch).

    Pre-release or build suffixes ('-rc1', '+build.1') are stripped — for our
    purposes they sort identically to the base version. Bad strings yield
    (0, 0, 0) so a malformed remote tag never *appears* newer than the local
    version (which is well-formed in source).
    """
    if not s:
        return (0, 0, 0)
    m = _VERSION_RE.match(s.strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(g or 0) for g in m.groups())  # type: ignore[return-value]


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def current_version() -> str:
    try:
        from modules.dlc_pro import __version__
        return __version__
    except Exception:
        return "0.0.0"


# -------------------------------------------------------------- network: check


def _http_get_json(url: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                log.debug("github api status=%s url=%s", resp.status, url)
                return None
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log.debug("github api fetch failed: %s", e)
        return None


def _pick_asset(assets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not assets:
        return None
    for pat in _ASSET_PATTERNS:
        for a in assets:
            if pat.match(a.get("name", "")):
                return a
    return None


def check_for_update(current: Optional[str] = None) -> Optional[UpdateInfo]:
    """Returns an UpdateInfo if a newer release exists, else None.

    Network failures, missing releases (404), and rate-limit (403) all return
    None — the caller treats "no update info" identically to "up to date".
    """
    cur = current or current_version()
    j = _http_get_json(GITHUB_API)
    if not j:
        return None
    if j.get("draft") or j.get("prerelease"):
        # Should not be returned by /releases/latest, but defensive in case
        # the user overrode the endpoint to /releases.
        return None

    tag = j.get("tag_name") or j.get("name") or ""
    if not is_newer(tag, cur):
        return None

    asset = _pick_asset(j.get("assets") or [])
    return UpdateInfo(
        current=cur,
        latest=tag,
        name=j.get("name") or tag,
        body=j.get("body") or "",
        html_url=j.get("html_url") or f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest",
        asset_url=(asset or {}).get("browser_download_url"),
        asset_name=(asset or {}).get("name"),
        asset_size=(asset or {}).get("size"),
        published_at=j.get("published_at"),
    )


# ----------------------------------------------------------- network: download


def download(
    url: str,
    target: Path,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Stream ``url`` to ``target``. Calls ``progress(done, total)`` per chunk.

    Returns the final target path on success. Raises on network errors or
    if ``cancel()`` returns True (cooperative cancellation).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                if cancel and cancel():
                    raise RuntimeError("update download cancelled")
                chunk = resp.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    try: progress(done, total)
                    except Exception: pass
    shutil.move(str(tmp), str(target))
    return target


# ----------------------------------------------------------- apply (relaunch)


def apply_update(installer_path: Path, silent: bool = False) -> None:
    """Launch the installer and exit so the new file can replace the running one.

    Windows  → spawn Inno Setup installer with /SILENT (or /VERYSILENT) and exit.
    macOS/Linux → open the .exe path in Finder/file manager and exit. (You're
    presumably running from source on these platforms; the .exe doesn't
    install on them.)
    """
    p = str(installer_path)
    log.info("applying update via %s", p)

    if os.name == "nt":
        flags = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"] if silent \
                else ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
        # DETACHED_PROCESS so the installer survives our exit.
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [p, *flags],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        # Open the file location for the user to handle manually.
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", p])
            else:
                subprocess.Popen(["xdg-open", str(installer_path.parent)])
        except Exception:
            webbrowser.open(f"file://{installer_path.parent}")

    # Give the spawned installer a beat to detach before we go.
    time.sleep(0.4)
    os._exit(0)


def open_release_page(info: UpdateInfo) -> None:
    """Fallback: open the GitHub release page in the user's default browser."""
    webbrowser.open(info.html_url)


# ----------------------------------------------------------- preferences


@dataclass
class UpdaterState:
    skipped_versions: List[str] = field(default_factory=list)
    last_check_ts: float = 0.0
    last_seen_version: str = ""

    @classmethod
    def load(cls) -> "UpdaterState":
        try:
            with open(_state_path(), "r") as f:
                d = json.load(f) or {}
            return cls(
                skipped_versions=list(d.get("skipped_versions", [])),
                last_check_ts=float(d.get("last_check_ts", 0.0)),
                last_seen_version=str(d.get("last_seen_version", "")),
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return cls()

    def save(self) -> None:
        try:
            with open(_state_path(), "w") as f:
                json.dump({
                    "skipped_versions":  self.skipped_versions,
                    "last_check_ts":     self.last_check_ts,
                    "last_seen_version": self.last_seen_version,
                }, f, indent=2)
        except OSError as e:
            log.debug("could not persist updater state: %s", e)

    def skip(self, version: str) -> None:
        if version and version not in self.skipped_versions:
            self.skipped_versions.append(version)
            self.save()

    def is_skipped(self, version: str) -> bool:
        return version in self.skipped_versions


# ------------------------------------------------------ background orchestrator


def check_in_background(
    on_update_available: Callable[[UpdateInfo], None],
    delay_seconds: float = 2.0,
    respect_skip: bool = True,
) -> threading.Thread:
    """Fire ``on_update_available`` from a background thread if a newer release
    is published. The callback is called on the worker thread — marshal back
    to the main thread (e.g. ``root.after(0, ...)``) before touching tkinter.
    """
    state = UpdaterState.load()

    def _worker() -> None:
        try:
            time.sleep(max(0.0, delay_seconds))
            info = check_for_update()
            state.last_check_ts = time.time()
            if not info:
                state.save()
                return
            state.last_seen_version = info.latest
            state.save()
            if respect_skip and state.is_skipped(info.latest):
                log.info("skipping update %s per user preference", info.latest)
                return
            on_update_available(info)
        except Exception as e:  # never block startup on update bugs
            log.warning("update check crashed: %s", e)

    t = threading.Thread(target=_worker, name="dlc-updater", daemon=True)
    t.start()
    return t
