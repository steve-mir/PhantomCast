"""Live output recorder.

Saves every processed frame the live pipeline produces to an MP4 on
disk so users can review the swap result later without the real-time
preview lag they may see on slower hardware (e.g. Apple Silicon Macs).

Design:

* The video preview FPS is whatever the swap pipeline can sustain
  (often 5-10fps on a Mac), but ``cv2.VideoWriter`` needs its FPS at
  open time. We buffer the first N frames, time them, then open the
  writer at the measured rate so playback runs at real-time speed.

* Mic audio is captured in parallel via ``sounddevice`` into a temp
  WAV. On stop, ffmpeg muxes audio + video into the final MP4. If
  sounddevice is missing, the user denies the macOS mic permission, or
  ffmpeg is unavailable, we degrade gracefully to video-only.

* Files land in ``state_dir() / 'recordings'`` — per-user writable for
  both source checkouts and frozen installer builds.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from modules.phantom_cast.paths import state_dir

try:
    import sounddevice as sd  # type: ignore
    _HAS_SOUNDDEVICE = True
except Exception:
    sd = None  # type: ignore
    _HAS_SOUNDDEVICE = False


# Buffer the first ~30 frames (or ~1s, whichever is longer) before
# opening the VideoWriter so we can lock its FPS to the real measured
# pipeline rate. Big enough to be stable, small enough to keep memory
# bounded (~30 * 1080p BGR = ~180MB worst case; typical preview is far
# smaller because this lives downstream of fit-to-window).
_BUFFER_MIN_FRAMES = 30
_BUFFER_MIN_SECONDS = 1.0
_BUFFER_MAX_SECONDS = 5.0   # hard cap — fall through and open at whatever fps

_AUDIO_SR = 44100
_AUDIO_CH = 1


def recordings_dir() -> Path:
    p = state_dir() / "recordings"
    p.mkdir(parents=True, exist_ok=True)
    return p


class LiveRecorder:
    """Records processed BGR frames + mic audio to an MP4.

    Usage::

        rec = LiveRecorder(record_audio=True)
        rec.start()
        ...
        rec.add_frame(bgr)         # called per processed frame
        ...
        path = rec.stop()          # finalised MP4 (or None on failure)
    """

    def __init__(
        self,
        out_dir: Optional[Path] = None,
        *,
        record_audio: bool = True,
        codec: str = "mp4v",
    ):
        self.out_dir = Path(out_dir) if out_dir else recordings_dir()
        self.record_audio = bool(record_audio and _HAS_SOUNDDEVICE)
        self.codec = codec
        self.audio_unavailable_reason: Optional[str] = (
            None if _HAS_SOUNDDEVICE else "sounddevice not installed"
        )

        self._writer: Optional[cv2.VideoWriter] = None
        self._fps: float = 0.0
        self._size: Optional[tuple[int, int]] = None
        self._buffer: list[np.ndarray] = []
        self._first_frame_t: Optional[float] = None

        self._lock = threading.Lock()
        self._started = False
        self._stopped = False
        self._frame_count = 0

        self._video_path: Optional[Path] = None
        self._audio_path: Optional[Path] = None
        self._final_path: Optional[Path] = None

        self._audio_thread: Optional[threading.Thread] = None
        self._audio_stop = threading.Event()
        self._audio_q: "queue.Queue[bytes]" = queue.Queue()
        self._audio_error: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self._started and not self._stopped

    @property
    def measured_fps(self) -> float:
        return self._fps

    @property
    def frames_written(self) -> int:
        return self._frame_count

    @property
    def final_path(self) -> Optional[Path]:
        return self._final_path

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopped = False
        self._buffer = []
        self._first_frame_t = None
        self._frame_count = 0

        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._video_path = self.out_dir / f"live_{ts}.video.mp4"
        self._final_path = self.out_dir / f"live_{ts}.mp4"

        if self.record_audio:
            self._audio_path = self.out_dir / f"live_{ts}.audio.wav"
            self._audio_stop.clear()
            self._audio_thread = threading.Thread(
                target=self._audio_loop, daemon=True
            )
            self._audio_thread.start()

    def add_frame(self, bgr: np.ndarray) -> None:
        if not self._started or self._stopped or bgr is None:
            return
        with self._lock:
            now = time.time()
            if self._first_frame_t is None:
                self._first_frame_t = now

            # Phase 1: buffer until we can estimate FPS reliably.
            if self._writer is None:
                self._buffer.append(bgr.copy())
                elapsed = now - self._first_frame_t
                enough = (
                    (len(self._buffer) >= _BUFFER_MIN_FRAMES
                     and elapsed >= _BUFFER_MIN_SECONDS)
                    or elapsed >= _BUFFER_MAX_SECONDS
                )
                if enough:
                    fps = max((len(self._buffer) - 1) / max(elapsed, 1e-3), 1.0)
                    self._open_writer(self._buffer[0], fps)
                    for f in self._buffer:
                        self._writer.write(f)
                        self._frame_count += 1
                    self._buffer = []
                return

            # Phase 2: streaming. Resize if size shifts mid-recording
            # (e.g. user changes resolution / camera).
            if (bgr.shape[1], bgr.shape[0]) != self._size:
                bgr = cv2.resize(bgr, self._size)
            self._writer.write(bgr)
            self._frame_count += 1

    def stop(self) -> Optional[Path]:
        if not self._started or self._stopped:
            return self._final_path
        self._stopped = True

        with self._lock:
            # Buffer never crossed the threshold (very short clip).
            # Open the writer with whatever FPS we can estimate.
            if self._writer is None and self._buffer:
                if (
                    self._first_frame_t is not None
                    and len(self._buffer) > 1
                ):
                    elapsed = time.time() - self._first_frame_t
                    fps = max((len(self._buffer) - 1) / max(elapsed, 1e-3), 5.0)
                else:
                    fps = 10.0
                try:
                    self._open_writer(self._buffer[0], fps)
                    for f in self._buffer:
                        self._writer.write(f)
                        self._frame_count += 1
                except Exception as e:
                    print(f"[recorder] failed to open writer on flush: {e}")
                self._buffer = []

            if self._writer is not None:
                self._writer.release()
                self._writer = None

        if self._audio_thread is not None:
            self._audio_stop.set()
            self._audio_thread.join(timeout=3.0)
            self._audio_thread = None

        return self._finalise()

    def _open_writer(self, frame: np.ndarray, fps: float) -> None:
        h, w = frame.shape[:2]
        self._size = (w, h)
        fourcc = cv2.VideoWriter_fourcc(*self.codec)
        assert self._video_path is not None
        self._writer = cv2.VideoWriter(
            str(self._video_path), fourcc, float(fps), (w, h)
        )
        self._fps = float(fps)
        if not self._writer.isOpened():
            raise RuntimeError(
                f"cv2.VideoWriter could not open {self._video_path}"
            )

    def _audio_loop(self) -> None:
        if sd is None:
            self._audio_error = "sounddevice not installed"
            return
        try:
            assert self._audio_path is not None
            wf = wave.open(str(self._audio_path), "wb")
            wf.setnchannels(_AUDIO_CH)
            wf.setsampwidth(2)
            wf.setframerate(_AUDIO_SR)

            def cb(indata, frames, time_info, status):  # noqa: ARG001
                int16 = (np.clip(indata, -1.0, 1.0) * 32767.0).astype(np.int16)
                try:
                    self._audio_q.put_nowait(int16.tobytes())
                except queue.Full:
                    pass

            with sd.InputStream(
                samplerate=_AUDIO_SR,
                channels=_AUDIO_CH,
                dtype="float32",
                callback=cb,
            ):
                while not self._audio_stop.is_set():
                    try:
                        chunk = self._audio_q.get(timeout=0.2)
                        wf.writeframes(chunk)
                    except queue.Empty:
                        continue
            while True:
                try:
                    wf.writeframes(self._audio_q.get_nowait())
                except queue.Empty:
                    break
            wf.close()
        except Exception as e:
            self._audio_error = str(e)

    def _finalise(self) -> Optional[Path]:
        if not self._video_path or not self._video_path.exists():
            return None

        have_audio = (
            self.record_audio
            and self._audio_path is not None
            and self._audio_path.exists()
            and self._audio_path.stat().st_size > 0
            and not self._audio_error
        )
        ffmpeg = shutil.which("ffmpeg") if have_audio else None

        if have_audio and ffmpeg:
            try:
                self._mux(ffmpeg)
                for tmp in (self._video_path, self._audio_path):
                    try:
                        if tmp and tmp.exists():
                            tmp.unlink()
                    except OSError:
                        pass
                return self._final_path
            except Exception as e:
                print(f"[recorder] mux failed: {e}")

        # Video-only fallback: rename intermediate to final.
        try:
            if self._final_path and self._final_path != self._video_path:
                if self._final_path.exists():
                    self._final_path.unlink()
                self._video_path.rename(self._final_path)
        except OSError:
            self._final_path = self._video_path
        # Clean up the audio temp if it exists but couldn't be muxed.
        try:
            if self._audio_path and self._audio_path.exists():
                self._audio_path.unlink()
        except OSError:
            pass
        return self._final_path

    def _mux(self, ffmpeg: str) -> None:
        assert self._video_path is not None
        assert self._audio_path is not None
        assert self._final_path is not None
        cmd = [
            ffmpeg, "-y",
            "-i", str(self._video_path),
            "-i", str(self._audio_path),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(self._final_path),
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
