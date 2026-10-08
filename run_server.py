import os
import sys

# Ensure repository root, vendor/python and src are in sys.path
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(REPO_ROOT, "vendor", "python")
SRC_DIR = os.path.join(REPO_ROOT, "src")

if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)
if os.path.isdir(SRC_DIR) and SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "12000"))

print(f"[BuildScope Python] Starting backend server on {HOST}:{PORT}...", flush=True)

# 1. Try running under Gunicorn (multi-threaded WSGI server)
try:
    from gunicorn.app.base import BaseApplication

    class GunicornStandalone(BaseApplication):
        def __init__(self, options=None):
            self.options = options or {}
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                if key in self.cfg.settings and value is not None:
                    self.cfg.set(key.lower(), value)

        def load(self):
            from oppintel.app.wsgi import create_app
            from oppintel.app.config import load_config
            return create_app(load_config())

    options = {
        "bind": f"{HOST}:{PORT}",
        "workers": 2,
        "threads": 4,
        "timeout": 120,
    }
    print("[BuildScope Python] Running with Gunicorn WSGI...", flush=True)
    GunicornStandalone(options).run()
    sys.exit(0)
except Exception as exc:
    print(f"[BuildScope Python] Gunicorn unavailable or exited ({exc}). Falling back to Werkzeug...", flush=True)

# 2. Fallback to Flask's built-in WSGI server (threaded)
try:
    from oppintel.app.wsgi import create_app
    from oppintel.app.config import load_config
    app = create_app(load_config())
    print(f"[BuildScope Python] Running with Flask WSGI on {HOST}:{PORT}...", flush=True)
    app.run(host=HOST, port=PORT, threaded=True, debug=False)
    sys.exit(0)
except Exception as exc:
    print(f"[BuildScope Python] Flask WSGI server failed ({exc}). Falling back to wsgiref...", flush=True)

# 3. Fallback to standard library wsgiref
try:
    from wsgiref.simple_server import make_server
    from oppintel.app.wsgi import create_app
    from oppintel.app.config import load_config
    app = create_app(load_config())
    print(f"[BuildScope Python] Running with standard library wsgiref on {HOST}:{PORT}...", flush=True)
    httpd = make_server(HOST, PORT, app)
    httpd.serve_forever()
except Exception as exc:
    print(f"[BuildScope Python] FATAL: All WSGI server implementations failed: {exc}", flush=True)
    sys.exit(1)
