"""Zero-refresh backend for inline file links.

Clicking a `?open=` Streamlit query link reruns the whole app (the refresh
complaint). Instead, file links point at this tiny localhost server, which
opens/reveals the file and answers `204 No Content` — the browser then stays
exactly where it is: no reload, no new tab, no scroll loss.

Security: binds 127.0.0.1 only, requires a random per-process token, and
re-validates existence + the allowed-paths sandbox on every request.
"""
import hmac
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path

_lock = threading.Lock()
_server = None  # cached (port, token)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            u = urlparse(self.path)
            if u.path != "/open":
                self.send_error(404)
                return
            q = parse_qs(u.query)
            action = (q.get("action", ["open"])[0] or "open").lower()
            raw = q.get("path", [""])[0]
            token = q.get("t", [""])[0]
            if not token or not hmac.compare_digest(token, self.server.token):
                self.send_error(403)
                return
            try:
                p = Path(unquote(raw).strip()).expanduser()
                if not p.is_absolute():
                    p = Path.cwd() / p
                p = p.resolve()
            except Exception:
                p = None
            if p is not None and p.exists() and _is_allowed(p):
                from utils import file_opener
                if action == "reveal":
                    file_opener.reveal_path(str(p))
                else:
                    file_opener.open_path(str(p))
            # 204 either way: browser stays put, nothing refreshes.
            self.send_response(204)
            self.end_headers()
        except Exception:
            try:
                self.send_response(204)
                self.end_headers()
            except Exception:
                pass


def _is_allowed(p: Path) -> bool:
    try:
        from config.settings import load_config
        norm = os.path.normcase(str(p))
        sep = os.path.normcase(os.sep)
        for base in load_config().allowed_paths:
            b = os.path.normcase(str(base))
            prefix = b if b.endswith(sep) else b + sep
            if norm == b or norm.startswith(prefix):
                return True
    except Exception:
        pass
    return False


def ensure_server():
    """Start the link server once per process; return (port, token)."""
    global _server
    if _server is not None:
        return _server
    with _lock:
        if _server is not None:
            return _server
        token = secrets.token_urlsafe(24)
        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        srv.token = token
        port = srv.server_address[1]
        threading.Thread(
            target=srv.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
        ).start()
        _server = (port, token)
        return _server
