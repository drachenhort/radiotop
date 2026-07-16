import urllib.error

from radiotop_gui import SimilarTracksThread


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def _json_bytes(obj):
    import json
    return json.dumps(obj).encode("utf-8")


def test_run_resolves_artist_and_returns_radio_tracks(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        if "/search?" in req.full_url:
            return _FakeResponse(_json_bytes({"data": [{"artist": {"id": 42}}]}))
        assert "/artist/42/radio" in req.full_url
        return _FakeResponse(_json_bytes({"data": [
            {"title": "Kid A", "artist": {"name": "Radiohead"}},
            {"title": "Creep", "artist": {"name": "Radiohead"}},
        ]}))

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = SimilarTracksThread("Radiohead", "15 Step")
    captured = []
    thread.results_ready.connect(lambda tracks: captured.append(tracks))
    thread.run()

    assert captured == [[
        {"title": "Kid A", "artist": "Radiohead"},
        {"title": "Creep", "artist": "Radiohead"},
    ]]


def test_run_emits_empty_list_when_search_finds_no_artist(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        return _FakeResponse(_json_bytes({"data": []}))

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = SimilarTracksThread("Nobody", "Nothing")
    captured = []
    thread.results_ready.connect(lambda tracks: captured.append(tracks))
    thread.run()

    assert captured == [[]]


def test_run_emits_empty_list_on_network_error(monkeypatch, qapp):
    def _raise(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    thread = SimilarTracksThread("Radiohead", "15 Step")
    captured = []
    thread.results_ready.connect(lambda tracks: captured.append(tracks))
    thread.run()

    assert captured == [[]]


def test_run_widens_with_related_artists_top_tracks(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        url = req.full_url
        if "/search?" in url:
            return _FakeResponse(_json_bytes({"data": [{"artist": {"id": 1}}]}))
        if "/artist/1/radio" in url:
            return _FakeResponse(_json_bytes({"data": [
                {"title": "A", "artist": {"name": "Main"}},
            ]}))
        if "/artist/1/related" in url:
            return _FakeResponse(_json_bytes({"data": [{"id": 2}, {"id": 3}]}))
        if "/artist/2/top" in url:
            return _FakeResponse(_json_bytes({"data": [{"title": "B", "artist": {"name": "Related2"}}]}))
        if "/artist/3/top" in url:
            return _FakeResponse(_json_bytes({"data": [{"title": "C", "artist": {"name": "Related3"}}]}))
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = SimilarTracksThread("Main", "Song", widen=True)
    captured = []
    thread.results_ready.connect(lambda tracks: captured.append(tracks))
    thread.run()

    assert captured == [[
        {"title": "A", "artist": "Main"},
        {"title": "B", "artist": "Related2"},
        {"title": "C", "artist": "Related3"},
    ]]


def test_run_deduplicates_and_caps_at_max_tracks(monkeypatch, qapp):
    def _urlopen(req, timeout=None):
        if "/search?" in req.full_url:
            return _FakeResponse(_json_bytes({"data": [{"artist": {"id": 1}}]}))
        tracks = [{"title": "Same", "artist": {"name": "X"}} for _ in range(30)]
        return _FakeResponse(_json_bytes({"data": tracks}))

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _urlopen)
    thread = SimilarTracksThread("X", "Y")
    captured = []
    thread.results_ready.connect(lambda tracks: captured.append(tracks))
    thread.run()

    assert captured == [[{"title": "Same", "artist": "X"}]]
