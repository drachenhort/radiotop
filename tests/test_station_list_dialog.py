from types import SimpleNamespace

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QDialog, QMessageBox

from radiotop_gui import EditStationDialog, StationListDialog


class _StubMainWindow:
    """Stands in for MainWindow so StationListDialog's list/search/add/edit/
    remove logic can be tested without the real player, tray, or network
    proxy machinery."""

    def __init__(self, stations=None):
        self.stations = stations if stations is not None else []
        self.current_idx = None
        self._current_icy_name = None
        self.play_index_calls = []
        self.save_calls = 0
        self.rebuild_menu_calls = 0
        self.stop_playback_calls = 0
        self.name_label = SimpleNamespace(setText=lambda t: None)
        self.player = SimpleNamespace(playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState)

    def _guess_name(self, url):
        return QUrl(url).host() or "Custom Stream"

    def _save_custom_stations(self):
        self.save_calls += 1

    def _rebuild_stations_menu(self):
        self.rebuild_menu_calls += 1

    def play_index(self, idx):
        self.play_index_calls.append(idx)
        self.current_idx = idx

    def stop_playback(self):
        self.stop_playback_calls += 1
        self.current_idx = None


def _station(name, url, custom=True):
    return {"name": name, "url": url, "custom": custom}


# --------------------------------------------------------------- filtering
def test_refresh_list_shows_all_stations_by_default(qapp):
    main = _StubMainWindow([_station("Alpha FM", "http://a.example.com:7700/stream.mp3"),
                             _station("Beta FM", "http://b.example.com:7700/stream.mp3")])
    dlg = StationListDialog(main)
    assert dlg.list_widget.count() == 2


def test_refresh_list_filters_by_name(qapp):
    main = _StubMainWindow([_station("Alpha FM", "http://a.example.com:7700/stream.mp3"),
                             _station("Beta FM", "http://b.example.com:7700/stream.mp3")])
    dlg = StationListDialog(main)
    dlg.search_edit.setText("alpha")
    assert dlg.list_widget.count() == 1
    assert "Alpha FM" in dlg.list_widget.item(0).text()


def test_refresh_list_filters_by_url(qapp):
    main = _StubMainWindow([_station("Alpha FM", "http://a.example.com:7700/stream.mp3"),
                             _station("Beta FM", "http://b.example.com:7700/stream.mp3")])
    dlg = StationListDialog(main)
    dlg.search_edit.setText("b.example.com")
    assert dlg.list_widget.count() == 1
    assert "Beta FM" in dlg.list_widget.item(0).text()


# ------------------------------------------------------------ button state
def test_remove_button_disabled_for_non_custom_station(qapp):
    main = _StubMainWindow([_station("Built-in", "http://a.example.com:7700/stream.mp3", custom=False)])
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    assert dlg.edit_btn.isEnabled() is True
    assert dlg.remove_btn.isEnabled() is False


def test_remove_button_enabled_for_custom_station(qapp):
    main = _StubMainWindow([_station("Custom", "http://a.example.com:7700/stream.mp3", custom=True)])
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    assert dlg.remove_btn.isEnabled() is True


# ---------------------------------------------------------------- add flow
def test_add_station_appends_and_plays(qapp):
    main = _StubMainWindow([])
    dlg = StationListDialog(main)
    dlg.name_edit.setText("New Station")
    dlg.url_edit.setText("http://new.example.com:7700/stream.mp3")
    dlg._add_station()

    assert len(main.stations) == 1
    assert main.stations[0]["name"] == "New Station"
    assert main.stations[0]["url"] == "http://new.example.com:7700/stream.mp3"
    assert main.stations[0]["custom"] is True
    assert main.save_calls == 1
    assert main.play_index_calls == [0]


def test_add_station_guesses_name_when_blank(qapp):
    main = _StubMainWindow([])
    dlg = StationListDialog(main)
    dlg.url_edit.setText("http://guessme.example.com:7700/stream.mp3")
    dlg._add_station()
    assert main.stations[0]["name"] == "guessme.example.com"


def test_add_station_rejects_non_http_url(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    main = _StubMainWindow([])
    dlg = StationListDialog(main)
    dlg.url_edit.setText("not-a-url")
    dlg._add_station()
    assert main.stations == []
    assert len(warnings) == 1


def test_add_station_normalizes_and_notifies_when_url_adjusted(qapp, monkeypatch):
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a))
    main = _StubMainWindow([])
    dlg = StationListDialog(main)
    dlg.url_edit.setText("http://bare.example.com/")
    dlg._add_station()
    assert main.stations[0]["url"] == "http://bare.example.com:7700/stream.mp3"
    assert len(infos) == 1


# --------------------------------------------------------------- edit flow
def test_edit_station_updates_name_and_reloads_if_playing(qapp, monkeypatch):
    def fake_exec(self):
        self.name_edit.setText("Renamed")
        self.url_edit.setText("http://renamed.example.com:7700/stream.mp3")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EditStationDialog, "exec", fake_exec)

    main = _StubMainWindow([_station("Old Name", "http://old.example.com:7700/stream.mp3")])
    main.current_idx = 0
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._edit_station()

    assert main.stations[0]["name"] == "Renamed"
    assert main.stations[0]["url"] == "http://renamed.example.com:7700/stream.mp3"
    assert main.save_calls == 1
    assert main.play_index_calls == [0]  # reloaded since the playing station's URL changed


def test_edit_station_does_not_reload_when_not_playing(qapp, monkeypatch):
    def fake_exec(self):
        self.url_edit.setText("http://renamed.example.com:7700/stream.mp3")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EditStationDialog, "exec", fake_exec)

    main = _StubMainWindow([_station("Old Name", "http://old.example.com:7700/stream.mp3")])
    main.current_idx = None
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._edit_station()

    assert main.stations[0]["url"] == "http://renamed.example.com:7700/stream.mp3"
    assert main.play_index_calls == []


def test_edit_station_keeps_icy_adopted_name_over_stale_prefill(qapp, monkeypatch):
    # Simulate the background icy-name lookup (_on_icy_station_name) renaming
    # the station while the Edit dialog is still open with its original
    # (now-stale) pre-filled name. The dialog's OK click carries that stale
    # name unmodified, so it must not clobber the freshly-adopted one.
    station = _station("old.example.com", "http://old.example.com:7700/stream.mp3")

    def fake_exec(self):
        station["name"] = "Icy Adopted Name"  # background thread updates it mid-dialog
        return QDialog.DialogCode.Accepted  # name_edit still holds the stale "old.example.com"

    monkeypatch.setattr(EditStationDialog, "exec", fake_exec)

    main = _StubMainWindow([station])
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._edit_station()

    assert main.stations[0]["name"] == "Icy Adopted Name"


def test_edit_station_prefills_with_live_icy_name_for_playing_station(qapp, monkeypatch):
    # A station with a user-typed name never gets that name auto-adopted
    # from the stream (see test_icy_station_name.py), but the Edit dialog
    # should still offer the real broadcast name as the pre-filled value so
    # accepting it unchanged saves the station under its actual name.
    captured_prefill = []

    def fake_exec(self):
        captured_prefill.append(self.name_edit.text())
        return QDialog.DialogCode.Accepted  # accepted unchanged

    monkeypatch.setattr(EditStationDialog, "exec", fake_exec)

    main = _StubMainWindow([_station("My Favorite Station", "http://old.example.com:7700/stream.mp3")])
    main.current_idx = 0
    main._current_icy_name = "Real Icecast Broadcast Name"
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._edit_station()

    assert captured_prefill == ["Real Icecast Broadcast Name"]
    assert main.stations[0]["name"] == "Real Icecast Broadcast Name"


def test_edit_station_does_not_prefill_icy_name_for_a_different_station(qapp, monkeypatch):
    # The live icy-name only applies to the currently *playing* station -
    # editing some other station in the list must still show its own
    # stored name, not the currently-playing station's icy-name.
    captured_prefill = []

    def fake_exec(self):
        captured_prefill.append(self.name_edit.text())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EditStationDialog, "exec", fake_exec)

    main = _StubMainWindow([
        _station("Playing Station", "http://playing.example.com:7700/stream.mp3"),
        _station("Other Station", "http://other.example.com:7700/stream.mp3"),
    ])
    main.current_idx = 0
    main._current_icy_name = "Real Icecast Broadcast Name"
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(1)
    dlg._edit_station()

    assert captured_prefill == ["Other Station"]
    assert main.stations[1]["name"] == "Other Station"


def test_edit_station_applies_user_rename_even_if_matches_old_guess(qapp, monkeypatch):
    # If the station's name never changed in the background, a user-submitted
    # name is applied normally, whether or not it happens to be unchanged.
    def fake_exec(self):
        return QDialog.DialogCode.Accepted  # name_edit left as-is: "Old Name"

    monkeypatch.setattr(EditStationDialog, "exec", fake_exec)

    main = _StubMainWindow([_station("Old Name", "http://old.example.com:7700/stream.mp3")])
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._edit_station()

    assert main.stations[0]["name"] == "Old Name"


def test_edit_station_cancelled_leaves_station_unchanged(qapp, monkeypatch):
    monkeypatch.setattr(EditStationDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    main = _StubMainWindow([_station("Old Name", "http://old.example.com:7700/stream.mp3")])
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._edit_station()

    assert main.stations[0]["name"] == "Old Name"
    assert main.save_calls == 0


# ------------------------------------------------------------- remove flow
def test_remove_station_deletes_custom_station(qapp):
    main = _StubMainWindow([_station("Custom", "http://a.example.com:7700/stream.mp3", custom=True)])
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._remove_station()
    assert main.stations == []
    assert main.save_calls == 1


def test_remove_station_ignores_non_custom_station(qapp):
    main = _StubMainWindow([_station("Built-in", "http://a.example.com:7700/stream.mp3", custom=False)])
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._remove_station()
    assert len(main.stations) == 1
    assert main.save_calls == 0


def test_remove_station_stops_playback_if_currently_playing(qapp):
    main = _StubMainWindow([_station("Custom", "http://a.example.com:7700/stream.mp3", custom=True)])
    main.current_idx = 0
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)
    dlg._remove_station()
    assert main.stop_playback_calls == 1


def test_remove_station_shifts_current_idx_down(qapp):
    main = _StubMainWindow([
        _station("Custom A", "http://a.example.com:7700/stream.mp3", custom=True),
        _station("Currently Playing", "http://b.example.com:7700/stream.mp3", custom=False),
    ])
    main.current_idx = 1
    dlg = StationListDialog(main)
    dlg.list_widget.setCurrentRow(0)  # select "Custom A" (index 0), which precedes the playing station
    dlg._remove_station()
    assert main.current_idx == 0  # shifted down since index 0 was removed
    assert main.stop_playback_calls == 0
