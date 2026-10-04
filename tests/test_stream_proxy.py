import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from stream_proxy import StreamProxyServer


class _NotFoundHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_error(404, "Not Found")

    def log_message(self, *args):
        pass


@pytest.fixture
def proxy():
    server = StreamProxyServer()
    yield server
    server.shutdown()


@pytest.fixture
def not_found_url():
    httpd = HTTPServer(("127.0.0.1", 0), _NotFoundHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/stream.mp3"
    httpd.shutdown()
    httpd.server_close()


def _closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/stream.mp3"


def _fetch_through(proxy, url):
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(proxy.local_url(url), timeout=10)
    return info.value


def test_last_error_records_upstream_http_status(proxy, not_found_url):
    err = _fetch_through(proxy, not_found_url)
    assert err.code == 502
    assert proxy.last_error(not_found_url) == "Station server returned HTTP 404 (Not Found)"


def test_last_error_records_refused_connection(proxy):
    url = _closed_port_url()
    _fetch_through(proxy, url)
    assert proxy.last_error(url) == "Station refused the connection"


def test_last_error_is_none_for_unknown_url(proxy):
    assert proxy.last_error("http://never.example.com/") is None
