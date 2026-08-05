"""Local HTTP proxy that relays station streams with RadioTop's own User-Agent.

Split out of radiotop_gui.py as part of breaking up that file into
modules; see CLAUDE.md for the overall module-split plan. QMediaPlayer's
FFmpeg backend does its own networking directly via libavformat, which
never goes through any Qt API where a custom header could be injected -
routing playback through this local-only (127.0.0.1) proxy means the only
outbound connection to the actual radio server is the one this proxy
makes itself, with a header we control.
"""

import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from threads import RADIOTOP_USER_AGENT, _cancellable_urlopen


class _ActiveUpstream:
    """Tracks one handler's in-flight upstream fetch so
    StreamProxyServer.abort_active() can interrupt it from the GUI thread -
    the same cooperative-cancel trick threads.py's lookup threads use for
    their own network calls (see _cancellable_urlopen), applied here so a
    stalled station doesn't leave QMediaPlayer's local connection to this
    proxy stuck for up to the connect/read timeout when the user hits
    Stop or switches stations mid-stall."""

    def __init__(self):
        self._stop_event = threading.Event()
        self._resp = None
        self._lock = threading.Lock()

    def set_response(self, resp):
        with self._lock:
            self._resp = resp

    def abort(self):
        self._stop_event.set()
        with self._lock:
            resp = self._resp
        if resp is not None:
            # Closing the response wrapper alone doesn't reliably interrupt
            # a blocking read happening on this handler's own thread - shut
            # down the underlying socket directly, same as
            # IcyMetadataThread.stop() / _CancellableRequestThread.stop().
            try:
                resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


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

        conn = _ActiveUpstream()
        registry = self.server.active_connections
        lock = self.server.active_connections_lock
        with lock:
            registry.add(conn)
        try:
            try:
                req = urllib.request.Request(target, headers={"User-Agent": RADIOTOP_USER_AGENT})
                upstream = _cancellable_urlopen(req, timeout=15, stop_event=conn._stop_event)
            except Exception:
                try:
                    self.send_error(502, "Could not reach stream")
                except Exception:
                    pass
                return
            if upstream is None:
                return  # abort_active() fired while still connecting

            conn.set_response(upstream)
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
                    pass  # local client disconnected, or abort_active() shut the socket down
        finally:
            with lock:
                registry.discard(conn)


class StreamProxyServer:
    """A local-only HTTP server (127.0.0.1) that proxies station streams
    through RADIOTOP_USER_AGENT. One instance is started for the life of
    the app and reused for every station played."""

    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StreamProxyHandler)
        self._httpd.daemon_threads = True
        self._httpd.active_connections = set()
        self._httpd.active_connections_lock = threading.Lock()
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def local_url(self, original_url):
        return f"http://127.0.0.1:{self.port}/stream?url={quote(original_url, safe='')}"

    def abort_active(self):
        """Interrupts any in-flight upstream fetch(es) this proxy is
        currently relaying, so a stalled station doesn't leave the local
        connection to QMediaPlayer stuck waiting on it. Called from
        MainWindow.stop_playback() and play_index() so Stop / switching
        stations reacts immediately even mid-stall, rather than waiting
        out the proxy's connect/read timeout."""
        with self._httpd.active_connections_lock:
            conns = list(self._httpd.active_connections)
        for conn in conns:
            conn.abort()

    def shutdown(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass
