import urllib.error

from radiotop_gui import AlbumArtThread


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def test_run_uses_cover_art_archive_when_mbid_present(monkeypatch, qapp):
    calls = []

    def _urlopen(req, timeout=None):
        calls.append(req.full_url)
        return _FakeResponse(b"cover-art-bytes")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = AlbumArtThread("mbid-123", "https://example.com/itunes.jpg")
    captured = []
    thread.image_ready.connect(lambda data: captured.append(data))
    thread.run()

    assert captured == [b"cover-art-bytes"]
    assert calls == ["https://coverartarchive.org/release/mbid-123/front-500"]  # itunes not touched


def test_run_falls_back_to_itunes_when_cover_art_archive_misses(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        if "coverartarchive.org" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
        return _FakeResponse(b"itunes-art-bytes")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = AlbumArtThread("mbid-123", "https://example.com/itunes.jpg")
    captured = []
    thread.image_ready.connect(lambda data: captured.append(data))
    thread.run()

    assert captured == [b"itunes-art-bytes"]


def test_run_uses_itunes_when_no_mbid(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        assert req.full_url == "https://example.com/itunes.jpg"
        return _FakeResponse(b"itunes-art-bytes")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = AlbumArtThread("", "https://example.com/itunes.jpg")
    captured = []
    thread.image_ready.connect(lambda data: captured.append(data))
    thread.run()

    assert captured == [b"itunes-art-bytes"]


def test_run_emits_not_found_when_all_sources_miss(monkeypatch, qapp):
    def _raise(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    thread = AlbumArtThread("mbid-123", "https://example.com/itunes.jpg")
    not_found_calls = []
    thread.not_found.connect(lambda: not_found_calls.append(True))
    thread.run()

    assert not_found_calls == [True]


def test_run_emits_not_found_when_no_sources_available(qapp):
    thread = AlbumArtThread("", "")
    not_found_calls = []
    thread.not_found.connect(lambda: not_found_calls.append(True))
    thread.run()

    assert not_found_calls == [True]
