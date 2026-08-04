import pytest

from enrichment_mixin import EnrichmentMixin


class _FakeSignal:
    def connect(self, fn):
        pass


class _FakeTrackLookupThread:
    """Stands in for threads.TrackLookupThread so _lookup_track_info's
    cache-miss path can run without spinning a real QThread or hitting the
    network - tests simulate the thread's result by calling
    main._on_lookup_result(...) directly, as the real result_ready signal
    would."""

    def __init__(self, title, lastfm_api_key):
        self.title = title
        self.lastfm_api_key = lastfm_api_key
        self.result_ready = _FakeSignal()
        self.finished = _FakeSignal()

    def start(self):
        pass

    def deleteLater(self):
        pass


@pytest.fixture(autouse=True)
def _fake_lookup_thread(monkeypatch):
    monkeypatch.setattr("enrichment_mixin.TrackLookupThread", _FakeTrackLookupThread)


class _FakeTrackInfoDialog:
    def __init__(self):
        self.applied = []
        self.similar_tracks_calls = []

    def apply_lookup(self, result):
        self.applied.append(result)

    def set_similar_tracks(self, tracks):
        self.similar_tracks_calls.append(tracks)


class _FakeMainWindow(EnrichmentMixin):
    """Stands in for MainWindow so EnrichmentMixin's lookup logic can be
    tested without the real player, caches beyond what's needed, or thread
    machinery - lookup_thread is never actually started in these tests."""

    MAX_CACHE_ENTRIES = 300

    def __init__(self):
        self.lookup_cache = {}
        self.lookup_thread = None
        self.last_lookup_title = None
        self.track_info_dialog = _FakeTrackInfoDialog()
        self.lastfm_api_key = ""
        self.track_label_calls = []
        self.artist_image_calls = []
        self.album_art_calls = []
        self.similar_tracks_calls = []
        self.stop_lookup_thread_calls = 0

    def _set_track_label(self, raw_title, year=None):
        self.track_label_calls.append((raw_title, year))

    def _fetch_artist_image(self, artist):
        self.artist_image_calls.append(artist)

    def _fetch_album_art(self, release_mbid, itunes_artwork_url, artist, title, album):
        self.album_art_calls.append((release_mbid, itunes_artwork_url, artist, title, album))

    def _fetch_similar_tracks(self, artist, title):
        self.similar_tracks_calls.append((artist, title))

    def _set_album_caption(self, name):
        pass

    def _set_album_art_placeholder(self, text):
        pass

    def _stop_lookup_thread(self):
        self.stop_lookup_thread_calls += 1
        self.lookup_thread = None


def _result(raw_title, artist="Radiohead", title="Creep", found=True):
    return {
        "raw_title": raw_title,
        "found": found,
        "year": "1993",
        "artist": artist,
        "title": title,
        "album": "Pablo Honey",
        "release_mbid": "mbid-1",
        "itunes_artwork_url": None,
    }


def test_on_lookup_result_ignores_stale_result_for_a_different_title():
    # Regression test: a slow TrackLookupThread for an old track (either from
    # before a station switch, or superseded by a newer track on the same
    # station) used to unconditionally overwrite the currently-displayed
    # track info when it finally arrived, even though a different title is
    # now current.
    main = _FakeMainWindow()
    main._lookup_track_info("Old Artist - Old Track")
    assert main.last_lookup_title == "Old Artist - Old Track"

    # Simulate the track changing before the old lookup's result arrives.
    main._lookup_track_info("New Artist - New Track")
    assert main.last_lookup_title == "New Artist - New Track"

    # The stale result for the old title now lands.
    main._on_lookup_result(_result("Old Artist - Old Track", artist="Old Artist", title="Old Track"))

    assert main.track_info_dialog.applied == []
    assert main.track_label_calls == []
    assert main.artist_image_calls == []
    assert main.album_art_calls == []
    assert main.similar_tracks_calls == []
    # Still cached for a later cache hit, even though not applied to the UI.
    assert "Old Artist - Old Track" in main.lookup_cache


def test_on_lookup_result_applies_result_matching_current_title():
    main = _FakeMainWindow()
    main._lookup_track_info("Radiohead - Creep")
    main._on_lookup_result(_result("Radiohead - Creep"))

    assert main.track_info_dialog.applied == [_result("Radiohead - Creep")]
    assert main.track_label_calls == [("Radiohead - Creep", "1993")]
    assert main.artist_image_calls == ["Radiohead"]


def test_lookup_track_info_cache_hit_updates_last_lookup_title_and_reapplies():
    # A cache hit for a track that's current again (radio replaying
    # something heard earlier this session) should still update the current
    # UI even though no new thread is started.
    main = _FakeMainWindow()
    main.lookup_cache["Radiohead - Creep"] = _result("Radiohead - Creep")
    main._lookup_track_info("Radiohead - Creep")

    assert main.last_lookup_title == "Radiohead - Creep"
    assert main.track_info_dialog.applied == [_result("Radiohead - Creep")]


def test_lookup_track_info_stops_any_in_flight_thread_before_starting_a_new_one():
    main = _FakeMainWindow()
    main.lookup_thread = object()  # pretend a previous lookup is in flight
    main._lookup_track_info("Radiohead - Creep")
    assert main.stop_lookup_thread_calls == 1
