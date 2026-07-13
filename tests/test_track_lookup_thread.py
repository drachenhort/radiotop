import json
import urllib.error

import pytest

from radiotop_gui import TrackLookupThread


# --------------------------------------------------------------- splitting
@pytest.mark.parametrize(
    "raw, expected_artist, expected_title",
    [
        ("Daft Punk - One More Time", "Daft Punk", "One More Time"),
        ("Daft Punk – One More Time", "Daft Punk", "One More Time"),  # en dash
        ("Daft Punk — One More Time", "Daft Punk", "One More Time"),  # em dash
        ("Just A Title", "", "Just A Title"),
        ("  Spaced Artist  -  Spaced Title  ", "Spaced Artist", "Spaced Title"),
        ("", "", ""),
    ],
)
def test_split_artist_title(raw, expected_artist, expected_title):
    artist, title = TrackLookupThread._split_artist_title(raw)
    assert artist == expected_artist
    assert title == expected_title


# ------------------------------------------------------------ musicbrainz
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_query_musicbrainz_parses_best_match(monkeypatch):
    payload = {
        "recordings": [
            {
                "title": "One More Time",
                "artist-credit": [{"name": "Daft", "joinphrase": " "}, {"name": "Punk", "joinphrase": ""}],
                "releases": [
                    {"title": "Discovery", "id": "mbid-123", "date": "2001-03-07"},
                    {"title": "Discovery (Reissue)", "id": "mbid-456", "date": "1999-01-01"},
                ],
                "genres": [{"name": "house"}],
                "tags": [{"name": "electronic", "count": 5}],
            }
        ]
    }
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(payload),
    )
    thread = TrackLookupThread("Daft Punk - One More Time")
    result = thread._query_musicbrainz("Daft Punk", "One More Time")

    assert result["artist"] == "Daft Punk"
    assert result["title"] == "One More Time"
    assert result["album"] == "Discovery"
    assert result["release_mbid"] == "mbid-123"
    assert result["year"] == "1999"  # earliest release year, sorted
    assert result["genre"] == "House"  # genres field takes priority over tags


def test_query_musicbrainz_falls_back_to_top_tag_when_no_genres(monkeypatch):
    payload = {
        "recordings": [
            {
                "title": "Track",
                "artist-credit": [{"name": "Someone", "joinphrase": ""}],
                "releases": [],
                "genres": [],
                "tags": [
                    {"name": "rock", "count": 2},
                    {"name": "indie", "count": 9},
                ],
            }
        ]
    }
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(payload),
    )
    thread = TrackLookupThread("Someone - Track")
    result = thread._query_musicbrainz("Someone", "Track")
    assert result["genre"] == "Indie"  # highest-count tag wins
    assert result["album"] == ""
    assert result["year"] == ""


def test_query_musicbrainz_returns_none_when_no_recordings(monkeypatch):
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"recordings": []}),
    )
    thread = TrackLookupThread("Artist - Title")
    assert thread._query_musicbrainz("Artist", "Title") is None


def test_query_musicbrainz_returns_none_on_request_failure(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    thread = TrackLookupThread("Artist - Title")
    assert thread._query_musicbrainz("Artist", "Title") is None


# ---------------------------------------------------------------- last.fm
def test_query_lastfm_returns_none_without_api_key():
    thread = TrackLookupThread("Artist - Title", lastfm_api_key="")
    result, error = thread._query_lastfm("Artist", "Title")
    assert result is None
    assert error is None


def test_query_lastfm_errors_without_artist():
    thread = TrackLookupThread("Title only", lastfm_api_key="key123")
    result, error = thread._query_lastfm("", "Title only")
    assert result is None
    assert "Artist" in error or "artist" in error


def test_query_lastfm_parses_success(monkeypatch):
    payload = {
        "track": {
            "toptags": {"tag": [{"name": "synthpop"}, {"name": "electronic"}]},
            "album": {"title": "Discovery"},
        }
    }
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(payload),
    )
    thread = TrackLookupThread("Daft Punk - One More Time", lastfm_api_key="key123")
    result, error = thread._query_lastfm("Daft Punk", "One More Time")
    assert error is None
    assert result == {"genre": "Synthpop", "album": "Discovery"}


def test_query_lastfm_reports_api_error(monkeypatch):
    payload = {"error": 6, "message": "Track not found"}
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse(payload),
    )
    thread = TrackLookupThread("Artist - Title", lastfm_api_key="key123")
    result, error = thread._query_lastfm("Artist", "Title")
    assert result is None
    assert error == "Track not found"


def test_query_lastfm_reports_http_error(monkeypatch):
    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("http://x", 403, "Forbidden", None, None)

    monkeypatch.setattr("radiotop_gui.urllib.request.urlopen", _raise)
    thread = TrackLookupThread("Artist - Title", lastfm_api_key="key123")
    result, error = thread._query_lastfm("Artist", "Title")
    assert result is None
    assert "403" in error


# --------------------------------------------------------------------- run
def test_run_emits_not_found_when_title_is_empty(qapp):
    thread = TrackLookupThread("")
    captured = []
    thread.result_ready.connect(lambda r: captured.append(r))
    thread.run()
    assert captured == [{"raw_title": "", "found": False}]


def test_run_combines_musicbrainz_and_lastfm(monkeypatch, qapp):
    thread = TrackLookupThread("Daft Punk - One More Time", lastfm_api_key="key123")
    monkeypatch.setattr(
        thread,
        "_query_musicbrainz",
        lambda artist, title: {
            "artist": "Daft Punk",
            "title": "One More Time",
            "album": "Discovery",
            "genre": "House",
            "year": "2001",
            "release_mbid": "mbid-123",
        },
    )
    monkeypatch.setattr(
        thread,
        "_query_lastfm",
        lambda artist, title: ({"genre": "Synthpop", "album": ""}, None),
    )
    captured = []
    thread.result_ready.connect(lambda r: captured.append(r))
    thread.run()

    assert len(captured) == 1
    result = captured[0]
    assert result["found"] is True
    assert result["genre"] == "Synthpop"  # last.fm genre takes priority
    assert result["album"] == "Discovery"  # musicbrainz album used since last.fm's was empty
    assert result["sources"] == ["MusicBrainz", "Last.fm"]


def test_run_emits_not_found_when_both_sources_fail(monkeypatch, qapp):
    thread = TrackLookupThread("Artist - Title")
    monkeypatch.setattr(thread, "_query_musicbrainz", lambda artist, title: None)
    monkeypatch.setattr(thread, "_query_lastfm", lambda artist, title: (None, None))
    captured = []
    thread.result_ready.connect(lambda r: captured.append(r))
    thread.run()
    assert captured[0]["found"] is False
