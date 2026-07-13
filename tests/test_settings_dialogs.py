import json
import urllib.error

from radiotop_gui import DiscogsSettingsDialog, LastfmSettingsDialog


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


# ------------------------------------------------------------- last.fm key
def test_lastfm_check_key_success(monkeypatch):
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"token": "abc"}),
    )
    ok, message = LastfmSettingsDialog._check_key("valid-key")
    assert ok is True
    assert "valid" in message.lower()


def test_lastfm_check_key_api_error(monkeypatch):
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"error": 10, "message": "Invalid API key"}),
    )
    ok, message = LastfmSettingsDialog._check_key("bad-key")
    assert ok is False
    assert message == "Invalid API key"


def test_lastfm_check_key_http_error(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("http://x", 403, "Forbidden", None, None)

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    ok, message = LastfmSettingsDialog._check_key("bad-key")
    assert ok is False
    assert "403" in message


# ------------------------------------------------------------ discogs token
def test_discogs_check_token_success(monkeypatch):
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"username": "someuser"}),
    )
    ok, message = DiscogsSettingsDialog._check_token("valid-token")
    assert ok is True
    assert "someuser" in message


def test_discogs_check_token_invalid(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("http://x", 401, "Unauthorized", None, None)

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    ok, message = DiscogsSettingsDialog._check_token("bad-token")
    assert ok is False
    assert message == "Invalid token."


def test_discogs_check_token_other_http_error(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("http://x", 500, "Server Error", None, None)

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    ok, message = DiscogsSettingsDialog._check_token("some-token")
    assert ok is False
    assert "500" in message
