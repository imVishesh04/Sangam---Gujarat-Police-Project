"""
Run CPU-heavy C code (OpenCV decode, YOLO, EasyOCR) without freezing the server.

app.py calls eventlet.monkey_patch(), so every "thread" in this process is a
green thread sharing ONE OS thread. Green threads only switch on I/O; a frame
decode or a YOLO pass never yields, so with several cameras running the whole
server (HTTP, Socket.IO, other cameras) stalls behind them.

run_blocking() sends such a call to eventlet's native thread pool (tpool) when
eventlet is active, and simply calls it otherwise (tests, plain scripts).
Only pass work that does NOT use Python sockets (requests, DB, Socket.IO):
those are green and must stay on the hub.
"""
from __future__ import annotations


def _eventlet_active() -> bool:
    try:
        import eventlet.patcher
        return eventlet.patcher.is_monkey_patched("thread")
    except Exception:  # noqa: BLE001
        return False


_logging_patched = False


def _patch_logging():
    """Give the logging module NATIVE locks.

    After monkey_patch() the locks inside logging are green. A log call made
    from a tpool thread (e.g. an OCR error) that meets a green lock held by the
    hub raises "greenlet.error: Cannot switch to a different thread" and can
    kill the camera worker. Native locks work from both sides.
    """
    global _logging_patched
    if _logging_patched:
        return
    import logging
    import eventlet.patcher
    RLock = eventlet.patcher.original("threading").RLock
    logging._lock = RLock()
    logging.Handler.createLock = lambda self: setattr(self, "lock", RLock())
    seen = set()
    loggers = [logging.getLogger()] + [l for l in logging.Logger.manager.loggerDict.values() if isinstance(l, logging.Logger)]
    for lg in loggers:
        for h in lg.handlers:
            if id(h) not in seen:
                seen.add(id(h))
                h.lock = RLock()
    _logging_patched = True


def run_blocking(fn, *args, **kwargs):
    if _eventlet_active():
        _patch_logging()
        from eventlet import tpool
        return tpool.execute(fn, *args, **kwargs)
    return fn(*args, **kwargs)


def native_lock():
    """A lock that works across tpool's native threads (a green lock does not)."""
    try:
        import eventlet.patcher
        return eventlet.patcher.original("threading").Lock()
    except Exception:  # noqa: BLE001
        import threading
        return threading.Lock()
