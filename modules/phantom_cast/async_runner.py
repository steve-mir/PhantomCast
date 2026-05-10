"""Single background worker thread for license / subscription / Firebase calls.

UI threads schedule work via ``run_async(fn, on_done)`` and receive results
back on the Tk main loop with ``root.after(0, ...)`` so we never touch widgets
off-thread.
"""
from __future__ import annotations

import queue
import threading
import traceback
from typing import Any, Callable, Optional

from modules.phantom_cast.logger import get

log = get("async")


class AsyncRunner:
    def __init__(self) -> None:
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="dlc-async")
        self._thread.start()

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fn, on_done, on_error, tk_root = item
            try:
                result = fn()
                cb = (lambda r=result: on_done(r)) if on_done else None
            except Exception as e:  # noqa: BLE001
                log.exception("async task failed: %s", e)
                tb = traceback.format_exc()
                cb = (lambda err=e, tb=tb: on_error(err, tb)) if on_error else None
            if cb is None:
                continue
            if tk_root is not None:
                try:
                    tk_root.after(0, cb)
                except Exception:
                    cb()
            else:
                cb()

    def run(
        self,
        fn: Callable[[], Any],
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[BaseException, str], None]] = None,
        tk_root: Any = None,
    ) -> None:
        self._q.put((fn, on_done, on_error, tk_root))

    def shutdown(self) -> None:
        self._q.put(None)


_singleton: Optional[AsyncRunner] = None


def runner() -> AsyncRunner:
    global _singleton
    if _singleton is None:
        _singleton = AsyncRunner()
    return _singleton
