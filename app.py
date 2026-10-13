"""
Entry point for the Gujarat CCTV Integration Platform.

Run with:
    python app.py

For production use gunicorn + eventlet worker:
    gunicorn --worker-class eventlet -w 1 app:app
"""
import eventlet
eventlet.monkey_patch()  # Must be first — patches stdlib for async I/O

import logging
# Python's root logger defaults to WARNING — that silently swallows every
# logger.info(...) call in this codebase (ANPR "hit" confirmations, YOLO
# model-load confirmation, camera worker status, etc). Without this, the
# app *looks* silent even when everything is working correctly, which is
# very confusing to debug. INFO level makes all of that visible again.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.services.blocking import _patch_logging  # noqa: E402
_patch_logging()  # native logging locks: safe to log from tpool threads

from app import create_app, socketio

app = create_app()

if __name__ == "__main__":
    # use_reloader=False is required here: eventlet's monkey-patching of the
    # stdlib (threading/subprocess) conflicts with Werkzeug's debug-mode
    # auto-restart mechanism, causing intermittent "Restarting with stat"
    # loops and the server silently failing to bind/listen on the port.
    # debug=True is kept (useful tracebacks) — only the reloader is disabled.
    #
    # Security: the Werkzeug debugger (debug=True) can run Python code from the
    # browser, and host 0.0.0.0 exposes the API to everyone on the same Wi-Fi.
    # Defaults are now safe: debug OFF, listen on this laptop only.
    #   FLASK_DEBUG=1        -> tracebacks/debugger (local development only)
    #   SANGAM_HOST=0.0.0.0  -> allow other devices on the network (e.g. a phone)
    import os
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("SANGAM_HOST", "127.0.0.1")
    socketio.run(app, host=host, port=5000, debug=debug, use_reloader=False)
