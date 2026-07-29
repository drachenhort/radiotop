"""Local HTTP proxy that relays station streams with RadioTop's own User-Agent.

Split out of radiotop_gui.py as part of breaking up that file into
modules; see CLAUDE.md for the overall module-split plan. QMediaPlayer's
FFmpeg backend does its own networking directly via libavformat, which
never goes through any Qt API where a custom header could be injected -
routing playback through this local-only (127.0.0.1) proxy means the only
outbound connection to the actual radio server is the one this proxy
makes itself, with a header we control.
"""

import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from threads import RADIOTOP_USER_AGENT


class _StreamProxyHandler(BaseHTTPRequestHandler):
    """Fetches the real station URL with our own User-Agent and relays the
    raw audio bytes to the local client. QMediaPlayer's FFmpeg backend
    does its own networking directly via libavformat - it never goes
    through any Qt API where a custom header could be injected, and by
    default identifies itself to the remote server as "Lavf" (FFmpeg's
    generic default). Routing playback through this local proxy means
    the only outbound connection to the actual radio server is the one
    this proxy makes itself, with a header we control."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # silence default per-request stderr logging

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        target = query.get("url", [None])[0]
        if not target:
            self.send_error(400, "Missing url parameter")
            return
        # target is already fully percent-decoded by parse_qs() above -
        # do NOT unquote() it again here, or any percent-encoded byte in
        # the original stream URL (e.g. %20, %2B) gets decoded twice and
        # corrupted before being sent upstream.

        # Restrict to http(s) - urlopen() also accepts file://, ftp://, and
        # data: URLs, and since this server listens on 127.0.0.1, any local
        # process (or a webpage's fetch()/<img> to this port) could
        # otherwise use it to read local files or reach internal-network
        # addresses this proxy was never meant to touch.
        if urlparse(target).scheme not in ("http", "https"):
            self.send_error(400, "Unsupported url scheme")
            return

        try:
            req = urllib.request.Request(target, headers={"User-Agent": RADIOTOP_USER_AGENT})
            upstream = urllib.request.urlopen(req, timeout=15)
        except Exception:
            try:
                self.send_error(502, "Could not reach stream")
            except Exception:
                pass
            return

        with upstream:
            try:
                content_type = upstream.headers.get("Content-Type", "audio/mpeg")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # local client (QMediaPlayer) disconnected - normal on stop/switch


class StreamProxyServer:
    """A local-only HTTP server (127.0.0.1) that proxies station streams
    through RADIOTOP_USER_AGENT. One instance is started for the life of
    the app and reused for every station played."""

    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StreamProxyHandler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def local_url(self, original_url):
        return f"http://127.0.0.1:{self.port}/stream?url={quote(original_url, safe='')}"

    def shutdown(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass
