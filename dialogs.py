"""Dialog windows used by RadioTop.

Split out of radiotop_gui.py as part of breaking up that file into
modules; see CLAUDE.md for the overall module-split plan. StationListDialog
takes the MainWindow instance as its `main` constructor argument and calls
back into it (play_index, stations list, etc.) rather than importing
MainWindow itself, so this module has no dependency on radiotop_gui.py.
"""

import urllib.error
import urllib.request
from urllib.parse import urlencode

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
)

from threads import RADIOTOP_USER_AGENT
from util import DEFAULT_STREAM_FILENAME, DEFAULT_STREAM_PORT, _fetch_json, _normalize_station_url


class TrackInfoDialog(QDialog):
    """A small non-modal window showing details about the currently
    playing track: title, artist, album, genre, and release year."""

    ALBUM_LENGTH_THRESHOLD = 28  # chars past which the dialog widens for the album name
    DEFAULT_WIDTH = 380
    WIDE_WIDTH = 560
    SIMILAR_LIST_HEIGHT = 110

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Track Info")
        self.resize(self.DEFAULT_WIDTH, 340)

        layout = QVBoxLayout(self)

        self.title_label = QLabel("No track playing")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.artist_label = QLabel("")
        self.artist_label.setWordWrap(True)
        layout.addWidget(self.artist_label)

        layout.addSpacing(10)

        # Album gets its own full-width row (rather than a form field next to
        # a fixed-width label column) since album names can run long and a
        # narrow value column forces awkward, cramped wrapping.
        self.album_label = QLabel("Album: -")
        self.album_label.setWordWrap(True)
        layout.addWidget(self.album_label)

        layout.addSpacing(4)

        form = QFormLayout()
        self.genre_value = QLabel("-")
        self.year_value = QLabel("-")
        self.bpm_value = QLabel("-")
        self.key_value = QLabel("-")
        for lbl in (self.genre_value, self.year_value, self.bpm_value, self.key_value):
            lbl.setWordWrap(True)
        form.addRow("Genre:", self.genre_value)
        form.addRow("Year:", self.year_value)
        form.addRow("BPM:", self.bpm_value)
        form.addRow("Key:", self.key_value)
        layout.addLayout(form)

        self.subwave_note_label = QLabel("")
        self.subwave_note_label.setStyleSheet("color: #888888; font-style: italic; font-size: 10px;")
        layout.addWidget(self.subwave_note_label)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888888; font-style: italic;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Similar Tracks:"))
        self.similar_list = QListWidget()
        self.similar_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.similar_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.similar_list.setMaximumHeight(self.SIMILAR_LIST_HEIGHT)
        layout.addWidget(self.similar_list)

        layout.addStretch(1)

    def _reset_width(self):
        # Start each new track at the compact width; apply_lookup widens it
        # again if the album name turns out to need the extra room.
        self.resize(self.DEFAULT_WIDTH, self.height())

    def set_waiting(self):
        self.title_label.setText("Waiting for stream metadata...")
        self.artist_label.setText("")
        self.album_label.setText("Album: -")
        self.genre_value.setText("-")
        self.year_value.setText("-")
        self.bpm_value.setText("-")
        self.key_value.setText("-")
        self.subwave_note_label.setText("")
        self.status_label.setText("")
        self.similar_list.clear()
        self._reset_width()

    def set_no_track(self):
        self.title_label.setText("No track playing")
        self.artist_label.setText("")
        self.album_label.setText("Album: -")
        self.genre_value.setText("-")
        self.year_value.setText("-")
        self.bpm_value.setText("-")
        self.key_value.setText("-")
        self.subwave_note_label.setText("")
        self.status_label.setText("")
        self.similar_list.clear()
        self._reset_width()

    def set_now_playing(self, raw_title):
        self.title_label.setText(raw_title or "Unknown track")
        self.artist_label.setText("")
        self.album_label.setText("Album: -")
        self.genre_value.setText("-")
        self.year_value.setText("-")
        self.bpm_value.setText("-")
        self.key_value.setText("-")
        self.subwave_note_label.setText("")
        self.status_label.setText("Looking up track details...")
        self.similar_list.clear()
        self._reset_width()

    def set_subwave_details(self, bpm, musical_key):
        """BPM/key aren't available from MusicBrainz/Last.fm/iTunes - only a
        SUB/WAVE station's own API supplies them (from its acoustic
        analysis of the track), so the note makes that source explicit
        rather than presenting them as just another lookup field."""
        self.bpm_value.setText(str(bpm) if bpm else "-")
        self.key_value.setText(str(musical_key) if musical_key else "-")
        self.subwave_note_label.setText("BPM/Key supplied by SUB/WAVE" if (bpm or musical_key) else "")

    def set_similar_tracks_loading(self):
        self.similar_list.clear()
        item = QListWidgetItem("Loading...")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.similar_list.addItem(item)

    def set_similar_tracks(self, tracks):
        self.similar_list.clear()
        if not tracks:
            item = QListWidgetItem("No similar tracks found.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.similar_list.addItem(item)
            return
        for track in tracks:
            title = track.get("title", "")
            artist = track.get("artist", "")
            text = f"{title} — {artist}" if artist else title
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.similar_list.addItem(item)

    def apply_lookup(self, result):
        if not result.get("found"):
            msg = "No additional details found for this track."
            if result.get("lastfm_error"):
                msg += f"  (Last.fm: {result['lastfm_error']})"
            self.status_label.setText(msg)
            return
        title = result.get("title") or self.title_label.text()
        self.title_label.setText(title)
        self.artist_label.setText(result.get("artist") or "")

        album = result.get("album") or "-"
        self.album_label.setText(f"Album: {album}")
        if album != "-" and len(album) > self.ALBUM_LENGTH_THRESHOLD:
            self.resize(max(self.width(), self.WIDE_WIDTH), self.height())

        self.genre_value.setText(result.get("genre") or "-")
        self.year_value.setText(result.get("year") or "-")
        sources = result.get("sources") or []
        status_parts = []
        if sources:
            status_parts.append(f"Source: {', '.join(sources)}")
        if result.get("lastfm_error"):
            status_parts.append(f"Last.fm: {result['lastfm_error']}")
        self.status_label.setText("   |   ".join(status_parts))


class _ApiCredentialDialog(QDialog):
    """Shared UI for a dialog that collects a single API key/token: an
    info blurb, a line edit with a "Test" button, a result label, and
    OK/Cancel. Subclasses supply the window chrome via class attributes
    and the actual validation call via _check()."""

    WINDOW_TITLE = ""
    WINDOW_SIZE = (400, 200)
    INFO_TEXT = ""
    PLACEHOLDER = ""
    EMPTY_MESSAGE = "Enter a value first."

    def __init__(self, current_value, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.resize(*self.WINDOW_SIZE)

        layout = QVBoxLayout(self)
        info = QLabel(self.INFO_TEXT)
        info.setWordWrap(True)
        layout.addWidget(info)

        self.value_edit = QLineEdit(current_value)
        self.value_edit.setPlaceholderText(self.PLACEHOLDER)
        value_row = QHBoxLayout()
        value_row.addWidget(self.value_edit, 1)
        test_btn = QPushButton("Test")
        test_btn.clicked.connect(self._test_value)
        value_row.addWidget(test_btn)
        layout.addLayout(value_row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _test_value(self):
        value = self.value_edit.text().strip()
        if not value:
            self.result_label.setStyleSheet("color: #f67400;")
            self.result_label.setText(self.EMPTY_MESSAGE)
            return
        self.result_label.setStyleSheet("color: #888888;")
        self.result_label.setText("Testing...")
        QApplication.processEvents()
        ok, message = self._check(value)
        self.result_label.setStyleSheet(
            "color: #3daee9;" if ok else "color: #da4453;"
        )
        self.result_label.setText(message)

    def value(self):
        return self.value_edit.text().strip()

    @staticmethod
    def _check(value):
        raise NotImplementedError


class LastfmSettingsDialog(_ApiCredentialDialog):
    """Small dialog for entering/updating the user's Last.fm API key."""

    WINDOW_TITLE = "Last.fm API Key"
    WINDOW_SIZE = (400, 200)
    INFO_TEXT = (
        "Last.fm can supply richer, crowd-tagged genres for the "
        "currently playing track (used alongside MusicBrainz, which "
        "always supplies the release year). Get a free API key at "
        "last.fm/api/account/create, then paste it below. Leave blank "
        "to disable Last.fm and use MusicBrainz only."
    )
    PLACEHOLDER = "Last.fm API key"
    EMPTY_MESSAGE = "Enter a key first."

    @staticmethod
    def _check(key):
        # auth.getToken is a lightweight read-only call - good for validating
        # a key without needing a real artist/track match.
        url = "https://ws.audioscrobbler.com/2.0/?" + urlencode({
            "method": "auth.gettoken",
            "api_key": key,
            "format": "json",
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = _fetch_json(req)
        except urllib.error.HTTPError as e:
            return False, f"HTTP error {e.code} - key may be invalid."
        except Exception as e:
            return False, f"Network error: {e}"
        if data.get("error"):
            return False, data.get("message", f"Last.fm error {data.get('error')}")
        return True, "Key is valid."

    _check_key = _check  # keep the historical name available too

    def api_key(self):
        return self.value()


class DiscogsSettingsDialog(_ApiCredentialDialog):
    """Small dialog for entering/updating the user's Discogs API token."""

    WINDOW_TITLE = "Discogs API Token"
    WINDOW_SIZE = (420, 220)
    INFO_TEXT = (
        "Discogs often has better artist photo coverage than Wikipedia, "
        "especially for working musicians without a Wikipedia page. When "
        "set, Discogs is tried first for artist photos, then Wikipedia, "
        "then Last.fm. Get a free personal access token at "
        "discogs.com/settings/developers, then paste it below. Leave "
        "blank to disable Discogs."
    )
    PLACEHOLDER = "Discogs personal access token"
    EMPTY_MESSAGE = "Enter a token first."

    @staticmethod
    def _check(token):
        # oauth/identity is a lightweight authenticated call - good for
        # validating a token without needing a real search match.
        url = "https://api.discogs.com/oauth/identity"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": RADIOTOP_USER_AGENT,
                "Authorization": f"Discogs token={token}",
            })
            data = _fetch_json(req)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Invalid token."
            return False, f"HTTP error {e.code}"
        except Exception as e:
            return False, f"Network error: {e}"
        username = data.get("username", "")
        return True, f"Token is valid (authenticated as {username})." if username else "Token is valid."

    _check_token = _check  # keep the historical name available too

    def token(self):
        return self.value()


class EditStationDialog(QDialog):
    """Dialog for editing a station's name and stream URL."""

    def __init__(self, name, url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Station")
        self.resize(420, 150)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        self.url_edit = QLineEdit(url)
        form.addRow("Name:", self.name_edit)
        form.addRow("URL:", self.url_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return self.name_edit.text().strip(), self.url_edit.text().strip()


class StationListDialog(QDialog):
    """Popup for searching, adding, editing, removing, and picking a
    station to play. Kept separate from the main window so the main
    window itself can stay focused on playback controls."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        self.setWindowTitle("Stations")
        self.resize(440, 480)

        layout = QVBoxLayout(self)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search stations...")
        self.search_edit.textChanged.connect(lambda _: self.refresh_list())
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.currentItemChanged.connect(self._update_button_states)
        layout.addWidget(self.list_widget, 1)

        name_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Station name...")
        self.name_edit.returnPressed.connect(self._add_station)
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Paste a stream URL (http/https)...")
        self.url_edit.returnPressed.connect(self._add_station)
        url_row.addWidget(self.url_edit, 1)
        add_btn = QPushButton("Add && Play")
        add_btn.clicked.connect(self._add_station)
        url_row.addWidget(add_btn)
        layout.addLayout(url_row)

        manage_row = QHBoxLayout()
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self._edit_station)
        manage_row.addWidget(self.edit_btn)
        self.remove_btn = QPushButton("Remove Selected Station")
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self._remove_station)
        manage_row.addWidget(self.remove_btn, 1)
        layout.addLayout(manage_row)

        self.refresh_list()

    def refresh_list(self):
        previously_selected = self._selected_station_idx()
        filt = self.search_edit.text().strip().lower()
        self.list_widget.clear()
        for idx, st in enumerate(self.main.stations):
            if filt and filt not in st["name"].lower() and filt not in st["url"].lower():
                continue
            item = QListWidgetItem(f"{st['name']}\n{st['url']}")
            item.setData(Qt.ItemDataRole.UserRole, idx)
            font = item.font()
            if idx == self.main.current_idx:
                font.setBold(True)
                item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            else:
                font.setBold(False)
            item.setFont(font)
            self.list_widget.addItem(item)
        if previously_selected is not None:
            self._select_row_for_station(previously_selected)
        self._update_button_states()

    def _selected_station_idx(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _update_button_states(self, *_):
        idx = self._selected_station_idx()
        # While refresh_list() is mid-rebuild, list_widget.clear() can
        # transiently re-fire currentItemChanged with a row still carrying a
        # UserRole index from the pre-mutation station list (e.g. right
        # after a station is removed) - guard against that stale index
        # briefly pointing past the end of the now-shorter station list.
        valid = idx is not None and 0 <= idx < len(self.main.stations)
        self.edit_btn.setEnabled(valid)
        enabled = valid and self.main.stations[idx].get("custom", False)
        self.remove_btn.setEnabled(enabled)

    def _select_row_for_station(self, idx):
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).data(Qt.ItemDataRole.UserRole) == idx:
                self.list_widget.setCurrentRow(row)
                return

    def _on_item_double_clicked(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        self.main.play_index(idx)
        self.close()

    def _edit_station(self):
        idx = self._selected_station_idx()
        if idx is None:
            return
        station = self.main.stations[idx]
        original_name = station["name"]
        prefill_name = original_name
        if idx == self.main.current_idx and self.main._current_icy_name:
            # Show the stream's own live icy-name rather than the stored
            # name (which may be one the user typed in and that
            # _on_icy_station_name therefore never overwrote) - accepting
            # the dialog unchanged then saves it as the station's name.
            prefill_name = self.main._current_icy_name
        dlg = EditStationDialog(prefill_name, station["url"], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, url = dlg.values()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid http:// or https:// stream URL.")
            return
        url, was_adjusted = _normalize_station_url(url)
        if not name:
            name = self.main._guess_name(url)
        elif name == prefill_name and station["name"] != original_name:
            # The background icy-name lookup (_on_icy_station_name) adopted a
            # freshly-discovered name into the station while this dialog was
            # open - don't clobber it with the stale pre-filled value the
            # user left untouched.
            name = station["name"]

        station["name"] = name
        url_changed = url != station["url"]
        station["url"] = url
        if station.get("custom"):
            self.main._save_custom_stations()
        self.refresh_list()
        self._select_row_for_station(idx)
        self.main._rebuild_stations_menu()

        if idx == self.main.current_idx:
            if url_changed and self.main.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                self.main.play_index(idx)  # reload with the new address
            else:
                self.main.name_label.setText(name)

        if was_adjusted:
            self._notify_url_adjusted(url)

    def _add_station(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid http:// or https:// stream URL.")
            return
        url, was_adjusted = _normalize_station_url(url)
        name = self.name_edit.text().strip() or self.main._guess_name(url)
        station = {"name": name, "url": url, "custom": True}
        self.main.stations.append(station)
        self.main._save_custom_stations()
        self.refresh_list()
        idx = len(self.main.stations) - 1
        self._select_row_for_station(idx)
        self.main.play_index(idx)
        self.name_edit.clear()
        self.url_edit.clear()
        self.close()
        if was_adjusted:
            self._notify_url_adjusted(url)

    def _notify_url_adjusted(self, adjusted_url):
        QMessageBox.information(
            self,
            "Stream address adjusted",
            "This address didn't include a port and/or \"stream.mp3\", which most "
            f"stream servers need to connect properly - RadioTop filled in the "
            f"default(s) for whichever was missing (port {DEFAULT_STREAM_PORT}, "
            "the standard port for SUB/Wave Radios stations, and/or "
            f"\"{DEFAULT_STREAM_FILENAME}\"):\n\n{adjusted_url}\n\n"
            "If the station still doesn't play, edit it and enter the exact "
            "stream address your station provides instead.",
        )

    def _remove_station(self):
        idx = self._selected_station_idx()
        if idx is None or not self.main.stations[idx].get("custom"):
            return
        if idx == self.main.current_idx:
            self.main.stop_playback()
        elif self.main.current_idx is not None and idx < self.main.current_idx:
            # Everything after the removed row shifts down by one, so the
            # currently-playing station's index needs to shift too, or it
            # ends up pointing at the wrong station.
            self.main.current_idx -= 1
        del self.main.stations[idx]
        self.main._save_custom_stations()
        self.refresh_list()
        self.main._rebuild_stations_menu()
