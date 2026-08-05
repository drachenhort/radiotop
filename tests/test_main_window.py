import json
from types import SimpleNamespace

from PySide6.QtMultimedia import QMediaPlayer

import radiotop_gui as rt
from conftest import _LabelStub, _TimerStub


# --------------------------------------------------------------- guess name
def test_guess_name_uses_host(main_window_stub):
    name = rt.MainWindow._guess_name(main_window_stub, "http://streams.example.com:7700/stream.mp3")
    assert name == "streams.example.com"


def test_guess_name_falls_back_when_no_host(main_window_stub):
    name = rt.MainWindow._guess_name(main_window_stub, "not-a-url")
    assert name == "Custom Stream"


# --------------------------------------------------------- custom stations
def test_load_custom_stations_defaults_to_empty_list(main_window_stub):
    assert rt.MainWindow._load_custom_stations(main_window_stub) == []


def test_load_custom_stations_returns_saved_list(main_window_stub):
    stations = [{"name": "A", "url": "http://a.example.com:7700/stream.mp3", "custom": True}]
    main_window_stub.settings.setValue("custom_stations", json.dumps(stations))
    assert rt.MainWindow._load_custom_stations(main_window_stub) == stations


def test_load_custom_stations_ignores_corrupt_json(main_window_stub):
    main_window_stub.settings.setValue("custom_stations", "{not valid json")
    assert rt.MainWindow._load_custom_stations(main_window_stub) == []


def test_load_custom_stations_ignores_non_list_json(main_window_stub):
    main_window_stub.settings.setValue("custom_stations", json.dumps({"not": "a list"}))
    assert rt.MainWindow._load_custom_stations(main_window_stub) == []


def test_save_custom_stations_persists_only_custom_ones(main_window_stub):
    main_window_stub.stations = [
        {"name": "Built-in", "url": "http://a.example.com:7700/stream.mp3", "custom": False},
        {"name": "Mine", "url": "http://b.example.com:7700/stream.mp3", "custom": True},
    ]
    rt.MainWindow._save_custom_stations(main_window_stub)

    saved = json.loads(main_window_stub.settings.value("custom_stations"))
    assert saved == [{"name": "Mine", "url": "http://b.example.com:7700/stream.mp3", "custom": True}]


def test_save_then_load_round_trips(main_window_stub):
    main_window_stub.stations = [{"name": "Mine", "url": "http://b.example.com:7700/stream.mp3", "custom": True}]
    rt.MainWindow._save_custom_stations(main_window_stub)
    assert rt.MainWindow._load_custom_stations(main_window_stub) == main_window_stub.stations


# ---------------------------------------------------------- stations menu
def test_rebuild_stations_menu_lists_stations_and_manage_action(main_window_stub):
    main_window_stub.stations = [
        {"name": "Alpha", "url": "http://a.example.com:7700/stream.mp3", "custom": True},
        {"name": "Beta", "url": "http://b.example.com:7700/stream.mp3", "custom": True},
    ]
    main_window_stub.current_idx = 1
    rt.MainWindow._rebuild_stations_menu(main_window_stub)

    actions = main_window_stub.stations_menu.actions()
    texts = [a.text() for a in actions]
    assert texts == ["Alpha", "Beta", "", "&Manage Stations..."]
    assert actions[3].text() == "&Manage Stations..."


def test_rebuild_stations_menu_checks_current_station(main_window_stub):
    main_window_stub.stations = [
        {"name": "Alpha", "url": "http://a.example.com:7700/stream.mp3", "custom": True},
        {"name": "Beta", "url": "http://b.example.com:7700/stream.mp3", "custom": True},
    ]
    main_window_stub.current_idx = 1
    rt.MainWindow._rebuild_stations_menu(main_window_stub)

    station_actions = [a for a in main_window_stub.stations_menu.actions() if a.isCheckable()]
    assert [a.isChecked() for a in station_actions] == [False, True]


def test_rebuild_stations_menu_no_station_checked_when_stopped(main_window_stub):
    main_window_stub.stations = [{"name": "Alpha", "url": "http://a.example.com:7700/stream.mp3", "custom": True}]
    main_window_stub.current_idx = None
    rt.MainWindow._rebuild_stations_menu(main_window_stub)

    station_actions = [a for a in main_window_stub.stations_menu.actions() if a.isCheckable()]
    assert all(not a.isChecked() for a in station_actions)


def test_rebuild_stations_menu_clicking_station_plays_it(main_window_stub):
    main_window_stub.stations = [
        {"name": "Alpha", "url": "http://a.example.com:7700/stream.mp3", "custom": True},
        {"name": "Beta", "url": "http://b.example.com:7700/stream.mp3", "custom": True},
    ]
    rt.MainWindow._rebuild_stations_menu(main_window_stub)

    main_window_stub.stations_menu.actions()[1].trigger()
    assert main_window_stub.play_index_calls == [1]


def test_rebuild_stations_menu_with_no_stations_only_shows_manage(main_window_stub):
    main_window_stub.stations = []
    rt.MainWindow._rebuild_stations_menu(main_window_stub)
    texts = [a.text() for a in main_window_stub.stations_menu.actions()]
    assert texts == ["&Manage Stations..."]


def test_rebuild_stations_menu_clears_previous_entries(main_window_stub):
    main_window_stub.stations = [{"name": "Alpha", "url": "http://a.example.com:7700/stream.mp3", "custom": True}]
    rt.MainWindow._rebuild_stations_menu(main_window_stub)
    main_window_stub.stations = []
    rt.MainWindow._rebuild_stations_menu(main_window_stub)
    texts = [a.text() for a in main_window_stub.stations_menu.actions()]
    assert texts == ["&Manage Stations..."]


# ------------------------------------------------------------- status label
def _rig_for_update_status(stub, playback_state, media_status=QMediaPlayer.MediaStatus.NoMedia):
    stub.player = SimpleNamespace(playbackState=lambda: playback_state, mediaStatus=lambda: media_status)
    label = SimpleNamespace(text_value="")
    label.setText = lambda t: setattr(label, "text_value", t)
    label.setStyleSheet = lambda s: None
    stub.status_label = label
    stub.play_btn = SimpleNamespace(setIcon=lambda i: None)
    stub.style = lambda: SimpleNamespace(standardIcon=lambda i: None)


def test_update_status_appends_station_name_when_playing(main_window_stub):
    main_window_stub.stations = [{"name": "Cool FM", "url": "http://a.example.com:7700/stream.mp3", "custom": True}]
    main_window_stub.current_idx = 0
    _rig_for_update_status(main_window_stub, QMediaPlayer.PlaybackState.PlayingState)
    rt.MainWindow._update_status(main_window_stub)
    assert main_window_stub.status_label.text_value == "Playing on - Cool FM"


def test_update_status_plain_playing_when_nothing_selected(main_window_stub):
    main_window_stub.stations = []
    main_window_stub.current_idx = None
    _rig_for_update_status(main_window_stub, QMediaPlayer.PlaybackState.PlayingState)
    rt.MainWindow._update_status(main_window_stub)
    assert main_window_stub.status_label.text_value == "Playing"


def test_update_status_does_not_append_name_when_stopped(main_window_stub):
    main_window_stub.stations = [{"name": "Cool FM", "url": "http://a.example.com:7700/stream.mp3", "custom": True}]
    main_window_stub.current_idx = 0
    _rig_for_update_status(main_window_stub, QMediaPlayer.PlaybackState.StoppedState)
    rt.MainWindow._update_status(main_window_stub)
    assert main_window_stub.status_label.text_value == "Stopped"


def test_update_status_does_not_append_name_when_buffering(main_window_stub):
    main_window_stub.stations = [{"name": "Cool FM", "url": "http://a.example.com:7700/stream.mp3", "custom": True}]
    main_window_stub.current_idx = 0
    _rig_for_update_status(
        main_window_stub, QMediaPlayer.PlaybackState.PlayingState, QMediaPlayer.MediaStatus.BufferingMedia
    )
    rt.MainWindow._update_status(main_window_stub)
    assert main_window_stub.status_label.text_value == "Buffering..."


def test_update_status_prefers_live_icy_name_over_stored_name(main_window_stub):
    main_window_stub.stations = [{"name": "My Favorite Station", "url": "http://a.example.com:7700/stream.mp3", "custom": True}]
    main_window_stub.current_idx = 0
    main_window_stub._current_icy_name = "Actual Broadcast Name"
    _rig_for_update_status(main_window_stub, QMediaPlayer.PlaybackState.PlayingState)
    rt.MainWindow._update_status(main_window_stub)
    assert main_window_stub.status_label.text_value == "Playing on - Actual Broadcast Name"


# ------------------------------------------------------------ maybe reconnect
def test_maybe_reconnect_schedules_when_conditions_met(main_window_stub, monkeypatch):
    stub = main_window_stub
    stub.auto_reconnect_enabled = True
    stub.current_idx = 0
    stub._reconnect_attempts_remaining = 3
    stub.reconnect_max_attempts = 3
    stub._playback_generation = 1
    scheduled = []
    monkeypatch.setattr(
        rt.QTimer,
        "singleShot",
        lambda delay, fn: scheduled.append((delay, fn)),
    )
    rt.MainWindow._maybe_reconnect(stub)
    assert stub._reconnect_attempts_remaining == 2
    assert len(scheduled) == 1


def test_maybe_reconnect_does_nothing_when_disabled(main_window_stub, monkeypatch):
    stub = main_window_stub
    stub.auto_reconnect_enabled = False
    stub.current_idx = 0
    stub._reconnect_attempts_remaining = 3
    monkeypatch.setattr(
        rt.QTimer,
        "singleShot",
        lambda delay, fn: (_ for _ in ()).throw(AssertionError("should not schedule")),
    )
    rt.MainWindow._maybe_reconnect(stub)
    assert stub._reconnect_attempts_remaining == 3


# ------------------------------------------------------- credential refresh
class _FakeAcceptedDialog:
    """Stands in for LastfmSettingsDialog/DiscogsSettingsDialog: a real
    QDialog can't be parented to MainWindowStub (a QObject, not a QWidget),
    and _configure_lastfm_key/_configure_discogs_token only touch .exec()
    and the value-accessor method anyway."""

    def __init__(self, current_value, parent=None):
        pass

    def exec(self):
        return rt.QDialog.DialogCode.Accepted


def _rig_for_credential_config(stub, current_artist):
    stub.settings = SimpleNamespace(setValue=lambda k, v: None)
    stub.lastfm_api_key = ""
    stub.discogs_token = ""
    stub.lookup_cache = {"some-title": {}}
    stub.artist_image_cache = {"Some Artist": b"stale-bytes"}
    stub.last_image_artist = current_artist
    stub._status_bar = SimpleNamespace(showMessage=lambda *a, **k: None)
    stub.statusBar = lambda: stub._status_bar
    stub._fetch_artist_image_calls = []
    stub._fetch_artist_image = lambda artist: stub._fetch_artist_image_calls.append(artist)


def test_configure_lastfm_key_refetches_currently_displayed_artist_image(main_window_stub, monkeypatch):
    stub = main_window_stub
    _rig_for_credential_config(stub, current_artist="Some Artist")

    class _FakeDialog(_FakeAcceptedDialog):
        def api_key(self):
            return "new-key"

    monkeypatch.setattr(rt, "LastfmSettingsDialog", _FakeDialog)

    rt.MainWindow._configure_lastfm_key(stub)

    assert stub.artist_image_cache == {}
    assert stub.last_image_artist is None
    assert stub._fetch_artist_image_calls == ["Some Artist"]


def test_configure_discogs_token_refetches_currently_displayed_artist_image(main_window_stub, monkeypatch):
    stub = main_window_stub
    _rig_for_credential_config(stub, current_artist="Some Artist")

    class _FakeDialog(_FakeAcceptedDialog):
        def token(self):
            return "new-token"

    monkeypatch.setattr(rt, "DiscogsSettingsDialog", _FakeDialog)

    rt.MainWindow._configure_discogs_token(stub)

    assert stub.artist_image_cache == {}
    assert stub.last_image_artist is None
    assert stub._fetch_artist_image_calls == ["Some Artist"]


def test_configure_lastfm_key_does_nothing_when_no_track_playing(main_window_stub, monkeypatch):
    stub = main_window_stub
    _rig_for_credential_config(stub, current_artist=None)

    class _FakeDialog(_FakeAcceptedDialog):
        def api_key(self):
            return "new-key"

    monkeypatch.setattr(rt, "LastfmSettingsDialog", _FakeDialog)

    rt.MainWindow._configure_lastfm_key(stub)

    assert stub._fetch_artist_image_calls == []


# --------------------------------------------------- subwave now playing
def _rig_for_subwave_now_playing(stub):
    stub.current_idx = 0
    stub._subwave_detected = False
    stub._current_subwave_track = None
    stub._current_display_title = None
    stub.liked_tracks = set()
    stub.show_label = SimpleNamespace(setText=lambda t: None)
    stub.subwave_detail_label = SimpleNamespace(setText=lambda t: None)
    stub.next_track_label = SimpleNamespace(setText=lambda t: None)
    stub.like_btn = SimpleNamespace(setEnabled=lambda v: None, setText=lambda t: None)
    stub.track_label = SimpleNamespace(setText=lambda t: None)
    stub.track_info_dialog = SimpleNamespace(
        set_subwave_details=lambda bpm, key: None,
        set_now_playing=lambda title: None,
    )
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._liked_key = lambda artist, title: rt.MainWindow._liked_key(artist, title)
    stub._on_track_title = lambda title: rt.MainWindow._on_track_title(stub, title)
    # Let the real _on_track_title run (that's the method under test here -
    # specifically its dedup against _current_display_title) but stub out
    # everything it delegates to, same as the direct _on_track_title tests
    # below.
    stub._set_track_label = lambda title, year=None: None
    stub._lookup_track_info_calls = []
    stub._lookup_track_info = lambda title: stub._lookup_track_info_calls.append(title)
    stub._fetch_artist_image = lambda artist: None
    stub._schedule_track_notification = lambda artist, body: None
    stub._pending_notification_artist = None


def _payload(artist, title):
    return {"now_playing": {"nowPlaying": {"artist": artist, "title": title}}, "state": {}}


def test_subwave_now_playing_drives_track_title(main_window_stub):
    stub = main_window_stub
    _rig_for_subwave_now_playing(stub)

    rt.MainWindow._on_subwave_now_playing(stub, _payload("Some Artist", "Some Track"))

    assert stub._lookup_track_info_calls == ["Some Artist - Some Track"]
    assert stub._current_display_title == "Some Artist - Some Track"


def test_subwave_now_playing_does_not_redrive_title_for_repeated_polls(main_window_stub):
    # Regression test companion: SUB/WAVE is polled every few seconds, so
    # the track-title pipeline must only re-run once the track actually
    # changes - not on every poll of the same track.
    stub = main_window_stub
    _rig_for_subwave_now_playing(stub)
    stub._current_display_title = "Some Artist - Some Track"  # already displayed (e.g. via ICY)

    rt.MainWindow._on_subwave_now_playing(stub, _payload("Some Artist", "Some Track"))

    assert stub._lookup_track_info_calls == []


def test_subwave_now_playing_does_nothing_when_no_track_info(main_window_stub):
    stub = main_window_stub
    _rig_for_subwave_now_playing(stub)

    rt.MainWindow._on_subwave_now_playing(stub, {"now_playing": {}, "state": {}})

    assert stub._lookup_track_info_calls == []
    assert stub._current_display_title is None


def test_on_track_title_ignores_repeat_of_currently_displayed_title(main_window_stub):
    stub = main_window_stub
    stub._current_display_title = "Some Artist - Some Track"
    stub._set_track_label = lambda title, year=None: None
    stub.track_info_dialog = SimpleNamespace(set_now_playing=lambda title: None)
    lookup_calls = []
    stub._lookup_track_info = lambda title: lookup_calls.append(title)

    rt.MainWindow._on_track_title(stub, "Some Artist - Some Track")

    assert lookup_calls == []


def test_on_track_title_applies_a_genuinely_new_title(main_window_stub):
    stub = main_window_stub
    stub._current_display_title = "Old Artist - Old Track"
    stub._set_track_label = lambda title, year=None: None
    stub.track_info_dialog = SimpleNamespace(set_now_playing=lambda title: None)
    lookup_calls = []
    stub._lookup_track_info = lambda title: lookup_calls.append(title)
    stub._fetch_artist_image = lambda artist: None
    stub._schedule_track_notification = lambda artist, body: None
    stub._pending_notification_artist = None

    rt.MainWindow._on_track_title(stub, "New Artist - New Track")

    assert lookup_calls == ["New Artist - New Track"]
    assert stub._current_display_title == "New Artist - New Track"


# --------------------------------------------------- subwave heartbeat dot
def test_set_subwave_heartbeat_dot_hidden(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._set_subwave_heartbeat_dot(stub, "hidden")

    assert stub.subwave_heartbeat_dot.text() == ""


def test_set_subwave_heartbeat_dot_fresh(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._set_subwave_heartbeat_dot(stub, "fresh")

    assert stub.subwave_heartbeat_dot.text() == "●"


def test_set_subwave_heartbeat_dot_stale(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._set_subwave_heartbeat_dot(stub, "stale")

    assert stub.subwave_heartbeat_dot.text() == "●"


# ----------------------------------------------- subwave heartbeat timer
def test_start_subwave_thread_resets_heartbeat_state(main_window_stub, monkeypatch):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 2
    stub.subwave_thread = None
    stub.subwave_api_base = None
    stub._current_subwave_track = None
    stub.subwave_detail_label = _LabelStub()
    stub.next_track_label = _LabelStub()
    stub.show_label = _LabelStub()
    stub.like_btn = SimpleNamespace(setEnabled=lambda v: None, setText=lambda t: None)
    monkeypatch.setattr(rt, "SubwaveNowPlayingThread", lambda api_base: SimpleNamespace(
        now_playing_ready=SimpleNamespace(connect=lambda f: None),
        unavailable=SimpleNamespace(connect=lambda f: None),
        finished=SimpleNamespace(connect=lambda f: None),
        start=lambda: None,
        deleteLater=lambda: None,
    ))

    rt.MainWindow._start_subwave_thread(stub, "http://example.com:8000/stream.mp3")

    assert stub._subwave_heartbeat_missed == 0
    assert stub._subwave_heartbeat_timer.stop_calls == 1
    assert stub.subwave_heartbeat_dot.text() == ""


def test_on_subwave_now_playing_starts_and_resets_heartbeat_timer(main_window_stub):
    stub = main_window_stub
    _rig_for_subwave_now_playing(stub)
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1

    rt.MainWindow._on_subwave_now_playing(stub, _payload("Some Artist", "Some Track"))

    assert stub._subwave_heartbeat_missed == 0
    assert stub._subwave_heartbeat_timer.start_calls == [15000]
    assert stub.subwave_heartbeat_dot.text() == "●"


def test_stop_subwave_thread_stops_heartbeat_timer(main_window_stub):
    stub = main_window_stub
    stub.subwave_thread = None
    stub.subwave_api_base = None
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._stop_subwave_thread(stub)

    assert stub._subwave_heartbeat_timer.stop_calls == 1
    assert stub._subwave_heartbeat_missed == 0
    assert stub.subwave_heartbeat_dot.text() == ""


def test_on_subwave_unavailable_stops_heartbeat_timer(main_window_stub):
    stub = main_window_stub
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1
    stub.subwave_heartbeat_dot = _LabelStub()
    stub.subwave_detail_label = _LabelStub()
    stub.next_track_label = _LabelStub()
    stub.show_label = _LabelStub()
    stub.like_btn = SimpleNamespace(setEnabled=lambda v: None, setText=lambda t: None)

    rt.MainWindow._on_subwave_unavailable(stub)

    assert stub._subwave_heartbeat_timer.stop_calls == 1
    assert stub._subwave_heartbeat_missed == 0
    assert stub.subwave_heartbeat_dot.text() == ""
