from types import SimpleNamespace

from PySide6.QtMultimedia import QMediaPlayer

import radiotop_gui as rt
from radiotop_gui import IcyMetadataThread


class _FakeHeaders:
    def __init__(self, values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


class _FakeResponse:
    def __init__(self, headers):
        self.headers = _FakeHeaders(headers)

    def read(self, n=-1):
        return b""

    def close(self):
        pass


# ------------------------------------------------------- IcyMetadataThread
def test_poll_once_emits_station_name_from_icy_name_header(monkeypatch, qapp):
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"icy-name": "Best Radio Ever"}),
    )
    thread = IcyMetadataThread("http://example.com:7700/stream.mp3")
    captured = []
    thread.station_name_ready.connect(lambda n: captured.append(n))
    thread._poll_once()
    assert captured == ["Best Radio Ever"]


def test_poll_once_does_not_emit_when_icy_name_missing(monkeypatch, qapp):
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({}),
    )
    thread = IcyMetadataThread("http://example.com:7700/stream.mp3")
    captured = []
    thread.station_name_ready.connect(lambda n: captured.append(n))
    thread._poll_once()
    assert captured == []


def test_poll_once_only_emits_station_name_once_per_thread(monkeypatch, qapp):
    monkeypatch.setattr(
        "radiotop_gui.urllib.request.urlopen",
        lambda req, timeout=None: _FakeResponse({"icy-name": "Best Radio Ever", "icy-metaint": "16000"}),
    )
    thread = IcyMetadataThread("http://example.com:7700/stream.mp3")
    captured = []
    thread.station_name_ready.connect(lambda n: captured.append(n))
    thread._poll_once()
    thread._poll_once()
    assert captured == ["Best Radio Ever"]


# ------------------------------------------------------- MainWindow rename
def _station(name, url, custom=True):
    return {"name": name, "url": url, "custom": custom}


def test_on_icy_station_name_renames_when_name_was_guessed(main_window_stub):
    url = "http://streams.example.com:7700/stream.mp3"
    main_window_stub.stations = [_station("streams.example.com", url)]  # matches _guess_name(url)
    main_window_stub.current_idx = 0

    rt.MainWindow._on_icy_station_name(main_window_stub, "Best Radio Ever")

    assert main_window_stub.stations[0]["name"] == "Best Radio Ever"
    assert main_window_stub.name_label.text() == "Best Radio Ever"
    assert main_window_stub.station_dialog.refresh_list_calls == 1
    assert main_window_stub.save_custom_stations_calls == 1


def test_on_icy_station_name_does_not_override_explicit_name(main_window_stub):
    url = "http://streams.example.com:7700/stream.mp3"
    main_window_stub.stations = [_station("My Favorite Station", url)]
    main_window_stub.current_idx = 0

    rt.MainWindow._on_icy_station_name(main_window_stub, "Best Radio Ever")

    assert main_window_stub.stations[0]["name"] == "My Favorite Station"
    assert main_window_stub.save_custom_stations_calls == 0


def test_on_icy_station_name_still_updates_status_label_for_explicit_name(main_window_stub):
    # Even when a user-typed station name is left untouched (see the test
    # above), the "Playing on - X" status should reflect the stream's actual
    # reported icy-name, not the stored/typed station name.
    url = "http://streams.example.com:7700/stream.mp3"
    main_window_stub.stations = [_station("My Favorite Station", url)]
    main_window_stub.current_idx = 0
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadedMedia,
    )

    rt.MainWindow._on_icy_station_name(main_window_stub, "Best Radio Ever")

    assert main_window_stub.stations[0]["name"] == "My Favorite Station"
    assert main_window_stub.status_label.text() == "Playing on - Best Radio Ever"


def test_on_icy_station_name_noop_when_nothing_playing(main_window_stub):
    main_window_stub.current_idx = None
    rt.MainWindow._on_icy_station_name(main_window_stub, "Best Radio Ever")
    assert main_window_stub.save_custom_stations_calls == 0


def test_on_icy_station_name_noop_when_name_unchanged(main_window_stub):
    url = "http://streams.example.com:7700/stream.mp3"
    main_window_stub.stations = [_station("streams.example.com", url)]
    main_window_stub.current_idx = 0

    rt.MainWindow._on_icy_station_name(main_window_stub, "streams.example.com")

    assert main_window_stub.save_custom_stations_calls == 0


def test_on_icy_station_name_updates_status_label_when_playing(main_window_stub):
    url = "http://streams.example.com:7700/stream.mp3"
    main_window_stub.stations = [_station("streams.example.com", url)]
    main_window_stub.current_idx = 0
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadedMedia,
    )

    rt.MainWindow._on_icy_station_name(main_window_stub, "Best Radio Ever")

    assert main_window_stub.status_label.text() == "Playing on - Best Radio Ever"


def test_on_icy_station_name_does_not_save_non_custom_station(main_window_stub):
    url = "http://streams.example.com:7700/stream.mp3"
    main_window_stub.stations = [_station("streams.example.com", url, custom=False)]
    main_window_stub.current_idx = 0

    rt.MainWindow._on_icy_station_name(main_window_stub, "Best Radio Ever")

    assert main_window_stub.stations[0]["name"] == "Best Radio Ever"
    assert main_window_stub.save_custom_stations_calls == 0
