"""Marshal pywebview API calls onto the macOS main thread."""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable


def run_on_main_thread(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """
    pywebview invokes js_api on a worker thread; Cocoa UI must run on the main thread.
    """
    if threading.current_thread() is threading.main_thread():
        return func(*args, **kwargs)

    from PyObjCTools import AppHelper

    result_q: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result_q.put((True, func(*args, **kwargs)))
        except Exception as exc:
            result_q.put((False, exc))

    AppHelper.callAfter(runner)
    ok, value = result_q.get(timeout=600.0)
    if not ok:
        raise value
    return value