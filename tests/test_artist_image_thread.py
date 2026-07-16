import json
import urllib.error

from radiotop_gui import ArtistImageThread


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def _json_response(obj):
    return _FakeResponse(json.dumps(obj).encode("utf-8"))


def test_run_uses_deezer_first_even_with_discogs_token(monkeypatch, qapp):
    calls = []

    def _urlopen(req, timeout=None):
        calls.append(req.full_url)
        if "api.deezer.com/search/artist" in req.full_url:
            return _json_response({"data": [{"picture_xl": "https://deezer.example/artist.jpg"}]})
        return _FakeResponse(b"deezer-photo-bytes")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = ArtistImageThread("Radiohead", lastfm_api_key="lfmkey", discogs_token="dtoken")
    captured = []
    thread.image_ready.connect(lambda data: captured.append(data))
    thread.run()

    assert captured == [b"deezer-photo-bytes"]
    assert not any("discogs.com" in url for url in calls)  # Discogs never queried


def test_run_falls_back_to_discogs_when_deezer_misses(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        if "api.deezer.com" in req.full_url:
            return _json_response({"data": []})
        if "discogs.com/database/search" in req.full_url:
            return _json_response({"results": [{"id": 1, "cover_image": "https://discogs.example/cover.jpg"}]})
        if "discogs.com/artists" in req.full_url:
            return _json_response({"images": []})
        return _FakeResponse(b"discogs-photo-bytes")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = ArtistImageThread("Radiohead", discogs_token="dtoken")
    captured = []
    thread.image_ready.connect(lambda data: captured.append(data))
    thread.run()

    assert captured == [b"discogs-photo-bytes"]


def test_run_falls_back_to_wikipedia_when_deezer_and_discogs_miss(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        if "api.deezer.com" in req.full_url:
            return _json_response({"data": []})
        if "wikipedia.org" in req.full_url:
            return _json_response({"thumbnail": {"source": "https://wiki.example/photo.jpg"}})
        return _FakeResponse(b"wikipedia-photo-bytes")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = ArtistImageThread("Radiohead")
    captured = []
    thread.image_ready.connect(lambda data: captured.append(data))
    thread.run()

    assert captured == [b"wikipedia-photo-bytes"]


def test_run_emits_not_found_when_all_sources_miss(monkeypatch, qapp):
    def _raise(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    thread = ArtistImageThread("Radiohead", lastfm_api_key="lfmkey", discogs_token="dtoken")
    not_found_calls = []
    thread.not_found.connect(lambda: not_found_calls.append(True))
    thread.run()

    assert not_found_calls == [True]
