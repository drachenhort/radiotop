#!/usr/bin/env python3
"""
RadioTop - a simple, native-looking internet radio player.

Built with PySide6 (Qt for Python), so it automatically follows your
system's Qt theme, colors, and icon set (Breeze on KDE Plasma, the native
theme on Windows 10/11, etc.) - no extra styling code needed. Runs on
Linux (KDE Plasma and other desktops) as well as Windows 10/11.

Requires:
    pip install --user PySide6

Run:
    python3 radiotop_gui.py
    # or, on Linux:  chmod +x radiotop_gui.py && ./radiotop_gui.py

Notes:
- Playback uses Qt Multimedia (QMediaPlayer), which uses FFmpeg or
  GStreamer on Linux and Media Foundation on Windows - no extra backend
  install is needed on Windows. On Linux, if streams don't play, make
  sure the relevant Qt6 multimedia backend package is installed
  (e.g. `qt6-multimedia-plugins`, or GStreamer's `good`/`bad` plugin sets).
- Track title comes from the stream's ICY metadata. Genre, year, and album
  are then looked up via the MusicBrainz API (the open metadata database
  ListenBrainz is built on), with the iTunes Search API as a no-key fallback
  - matching depends on the track being findable in one of those and the
  station sending a clean "Artist - Title" string.
- Custom stations you add are remembered between runs (via QSettings,
  which uses an INI-style config file on Linux and the Registry on
  Windows).
- Closing the window prompts to either quit RadioTop or keep it running
  in the system tray; the tray menu or File > Quit always exits directly
  without prompting.
"""

import json
import os
import signal
import sys
import threading
import time

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSettings

from dialogs import (
    DiscogsSettingsDialog,
    EditStationDialog,
    LastfmSettingsDialog,
    StationListDialog,
    TrackInfoDialog,
)
from enrichment_mixin import EnrichmentMixin
from stream_proxy import StreamProxyServer
from threads import (
    IcyMetadataThread,
    SubwaveNowPlayingThread,
    SubwaveRequestThread,
    TrackLookupThread,
    UpdateCheckThread,
    _parse_version,
)
from util import (
    DEFAULT_STREAM_FILENAME,
    DEFAULT_STREAM_PORT,
    _app_icon,
    _normalize_station_url,
    _resource_path,
    _subwave_api_base,
    format_reconnect_message,
    select_output_device_index,
    should_attempt_reconnect,
    should_notify_immediately,
)

APP_ORG = "radiotop"
APP_NAME = "RadioTop"
APP_VERSION = "0.40"  # bumped alongside the CHANGELOG entry at release time

UPDATE_CHECK_INTERVAL_SECS = 24 * 60 * 60  # don't auto-check more than once a day

DEFAULT_STATIONS = []

STATUS_COLORS = {
    "Playing": "#3daee9",     # Breeze highlight blue
    "Buffering...": "#f67400",
    "Paused": "#f67400",
    "Stopped": "#888888",
    "Error": "#da4453",
}


class MainWindow(EnrichmentMixin, QMainWindow):
    # Cap on each of the lookup/artist-image/album-art/similar-tracks caches
    # below, evicting the oldest entry once a cache grows past this size.
    # These caches otherwise have no TTL and are never cleared during a run
    # (only ever added to or, for artist_image_cache/lookup_cache, wiped
    # entirely when Last.fm/Discogs credentials change) - fine for a normal
    # listening session, but this is meant to run 24/7 in the background, so
    # without a bound each cache would grow for as long as the app stays open.
    MAX_CACHE_ENTRIES = 300

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RadioTop")
        self.resize(360, 480)
        self.setWindowIcon(_app_icon())

        self.settings = QSettings(APP_ORG, APP_NAME)
        self.stations = list(DEFAULT_STATIONS) + self._load_custom_stations()
        self.current_idx = None
        self.last_station_url = self.settings.value("last_station_url", "") or ""
        self.auto_connect_last_station = self.settings.value(
            "auto_connect_last_station", True, type=bool
        )
        self._current_icy_name = None
        self._quitting = False
        self.meta_thread = None
        self.lookup_thread = None
        self.lookup_cache = {}
        self.last_lookup_title = None
        self.artist_image_thread = None
        self.artist_image_cache = {}
        self.last_image_artist = None
        self.album_art_thread = None
        self.album_art_cache = {}
        self.last_album_key = None
        self.similar_tracks_thread = None
        self.similar_tracks_cache = {}
        self.last_similar_tracks_artist = None
        self.last_similar_tracks_title = None
        self.lastfm_api_key = self.settings.value("lastfm_api_key", "") or ""
        self.discogs_token = self.settings.value("discogs_token", "") or ""
        self.similar_tracks_widen = self.settings.value("similar_tracks_widen", False, type=bool)
        self.notifications_enabled = self.settings.value("show_notifications", True, type=bool)
        self.auto_reconnect_enabled = self.settings.value("auto_reconnect_enabled", True, type=bool)
        self.reconnect_max_attempts = int(self.settings.value("reconnect_max_attempts", 3))
        self.subwave_thread = None
        self.subwave_api_base = None
        self._current_subwave_track = None
        self._subwave_detected = False
        self._subwave_request_threads = []
        self.liked_tracks = self._load_liked_tracks()
        self.update_check_thread = None
        self._reconnect_attempts_remaining = 0
        self._playback_generation = 0
        self._pending_notification_artist = None
        self._pending_notification_body = None
        self.track_info_dialog = TrackInfoDialog(self)

        # --- media player -------------------------------------------------
        try:
            self.stream_proxy = StreamProxyServer()
        except Exception:
            self.stream_proxy = None  # fall back to direct playback if the proxy fails to start

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        start_volume = int(self.settings.value("volume", 70))
        self.audio_output.setVolume(start_volume / 100.0)

        self.player.mediaStatusChanged.connect(self._update_status)
        self.player.playbackStateChanged.connect(self._update_status)
        self.player.errorOccurred.connect(self._on_error)

        self._build_ui(start_volume)
        self._build_tray()
        self.station_dialog = StationListDialog(self)

        if self.auto_connect_last_station:
            idx = self._find_station_index_by_url(self.last_station_url)
            if idx is not None:
                QTimer.singleShot(0, lambda: self.play_index(idx))

        last_check = float(self.settings.value("last_update_check", 0))
        if time.time() - last_check >= UPDATE_CHECK_INTERVAL_SECS:
            QTimer.singleShot(3000, lambda: self._check_for_updates(manual=False))

    # ------------------------------------------------------------------ UI
    def _build_ui(self, start_volume):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addStretch(1)

        self.name_label = QLabel("Nothing playing")
        name_font = QFont()
        name_font.setPointSize(16)
        name_font.setBold(True)
        self.name_label.setFont(name_font)
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.name_label)

        self.show_label = QLabel("")
        self.show_label.setWordWrap(True)
        self.show_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.show_label.setStyleSheet("color: #888888; font-size: 10px;")
        root.addWidget(self.show_label)

        self.track_label = QLabel("")
        self.track_label.setWordWrap(True)
        self.track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        track_font = QFont()
        track_font.setItalic(True)
        self.track_label.setFont(track_font)
        self.track_label.setStyleSheet("color: #3daee9;")
        root.addWidget(self.track_label)

        self.subwave_detail_label = QLabel("")
        self.subwave_detail_label.setWordWrap(True)
        self.subwave_detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subwave_detail_label.setStyleSheet("color: #888888; font-size: 10px;")
        root.addWidget(self.subwave_detail_label)

        self.next_track_label = QLabel("")
        self.next_track_label.setWordWrap(True)
        self.next_track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_track_label.setStyleSheet("color: #888888; font-size: 10px;")
        root.addWidget(self.next_track_label)

        self.status_label = QLabel("Stopped")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_font = QFont()
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS['Stopped']};")
        root.addWidget(self.status_label)

        root.addSpacing(16)

        transport = QHBoxLayout()
        transport.addStretch(1)
        style = self.style()
        self.play_btn = QPushButton()
        self.play_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.setToolTip("Play / Pause")
        self.play_btn.setFixedSize(48, 48)
        self.play_btn.clicked.connect(self.toggle_play_pause)
        transport.addWidget(self.play_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.setFixedSize(48, 48)
        self.stop_btn.clicked.connect(self.stop_playback)
        transport.addWidget(self.stop_btn)
        transport.addStretch(1)
        root.addLayout(transport)

        info_row = QHBoxLayout()
        info_row.addStretch(1)
        self.info_btn = QPushButton("Track Info")
        self.info_btn.clicked.connect(self._show_track_info_dialog)
        info_row.addWidget(self.info_btn)
        self.like_btn = QPushButton("☆ Like")
        self.like_btn.setToolTip("Nudge the SUB/WAVE DJ toward more like this")
        self.like_btn.setEnabled(False)
        self.like_btn.clicked.connect(self._on_like_clicked)
        info_row.addWidget(self.like_btn)
        info_row.addStretch(1)
        root.addLayout(info_row)

        root.addSpacing(10)

        image_row = QHBoxLayout()
        image_row.addStretch(1)

        artist_col = QVBoxLayout()
        self.artist_image_label = QLabel()
        self.artist_image_label.setFixedSize(130, 130)
        self.artist_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_image_label.setWordWrap(True)
        artist_col.addWidget(self.artist_image_label)
        self.artist_caption = QLabel("Artist")
        self.artist_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_caption.setWordWrap(True)
        self.artist_caption.setFixedWidth(130)
        self.artist_caption.setStyleSheet("color: #888888; font-size: 10px;")
        artist_col.addWidget(self.artist_caption)
        image_row.addLayout(artist_col)

        image_row.addSpacing(14)

        album_col = QVBoxLayout()
        self.album_art_label = QLabel()
        self.album_art_label.setFixedSize(130, 130)
        self.album_art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.album_art_label.setWordWrap(True)
        album_col.addWidget(self.album_art_label)
        self.album_caption = QLabel("Album")
        self.album_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.album_caption.setWordWrap(True)
        self.album_caption.setFixedWidth(130)
        self.album_caption.setStyleSheet("color: #888888; font-size: 10px;")
        album_col.addWidget(self.album_caption)
        image_row.addLayout(album_col)

        image_row.addStretch(1)
        root.addLayout(image_row)
        self._set_album_art_placeholder("No image")
        self._set_artist_image_placeholder("No image")

        root.addSpacing(16)

        vol_row = QHBoxLayout()
        vol_icon = QLabel()
        vol_icon.setPixmap(style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(20, 20))
        vol_row.addWidget(vol_icon)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(start_volume)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.volume_slider, 1)
        self.volume_pct_label = QLabel(f"{start_volume}%")
        self.volume_pct_label.setFixedWidth(36)
        vol_row.addWidget(self.volume_pct_label)
        root.addLayout(vol_row)

        device_row = QHBoxLayout()
        device_icon = QLabel()
        device_icon.setPixmap(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon).pixmap(18, 18))
        device_row.addWidget(device_icon)
        self.device_combo = QComboBox()
        self.device_combo.setToolTip("Audio output device")
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)
        device_row.addWidget(self.device_combo, 1)
        refresh_btn = QPushButton()
        refresh_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh_btn.setToolTip("Refresh output device list")
        refresh_btn.setFixedSize(28, 28)
        refresh_btn.clicked.connect(lambda: self._refresh_output_devices(preserve_selection=True))
        device_row.addWidget(refresh_btn)
        root.addLayout(device_row)

        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(
            lambda: self._refresh_output_devices(preserve_selection=True)
        )
        self._refresh_output_devices(preserve_selection=False)

        root.addStretch(2)

        # ---- menu bar ----
        file_menu = self.menuBar().addMenu("&File")
        quit_action = QAction("&Quit", self)
        quit_action.triggered.connect(self.quit_app)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About RadioTop", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        update_action = QAction("Check for &Updates...", self)
        update_action.triggered.connect(lambda: self._check_for_updates(manual=True))
        help_menu.addAction(update_action)

        view_menu = self.menuBar().addMenu("&View")
        stations_action = QAction("&Station List...", self)
        stations_action.triggered.connect(self._show_station_list_dialog)
        view_menu.addAction(stations_action)
        track_info_action = QAction("&Track Info Window", self)
        track_info_action.triggered.connect(self._show_track_info_dialog)
        view_menu.addAction(track_info_action)

        self.stations_menu = self.menuBar().addMenu("&Stations")
        self._rebuild_stations_menu()

        settings_menu = self.menuBar().addMenu("&Settings")
        lastfm_action = QAction("&Last.fm API Key...", self)
        lastfm_action.triggered.connect(self._configure_lastfm_key)
        settings_menu.addAction(lastfm_action)

        discogs_action = QAction("&Discogs API Token...", self)
        discogs_action.triggered.connect(self._configure_discogs_token)
        settings_menu.addAction(discogs_action)

        settings_menu.addSeparator()
        self.notifications_action = QAction("Show Desktop &Notifications", self)
        self.notifications_action.setCheckable(True)
        self.notifications_action.setChecked(self.notifications_enabled)
        self.notifications_action.toggled.connect(self._on_notifications_toggled)
        settings_menu.addAction(self.notifications_action)

        self.similar_tracks_widen_action = QAction("&Widen Similar Tracks (Related Artists)", self)
        self.similar_tracks_widen_action.setCheckable(True)
        self.similar_tracks_widen_action.setChecked(self.similar_tracks_widen)
        self.similar_tracks_widen_action.toggled.connect(self._on_similar_tracks_widen_toggled)
        settings_menu.addAction(self.similar_tracks_widen_action)

        settings_menu.addSeparator()
        self.auto_reconnect_action = QAction("Automatically &Reconnect on Drop", self)
        self.auto_reconnect_action.setCheckable(True)
        self.auto_reconnect_action.setChecked(self.auto_reconnect_enabled)
        self.auto_reconnect_action.toggled.connect(self._on_auto_reconnect_toggled)
        settings_menu.addAction(self.auto_reconnect_action)

        reconnect_attempts_action = QAction("Reconnect &Attempts...", self)
        reconnect_attempts_action.triggered.connect(self._configure_reconnect_attempts)
        settings_menu.addAction(reconnect_attempts_action)

        self.auto_connect_action = QAction("Connect to &Last Station on Startup", self)
        self.auto_connect_action.setCheckable(True)
        self.auto_connect_action.setChecked(self.auto_connect_last_station)
        self.auto_connect_action.toggled.connect(self._on_auto_connect_toggled)
        settings_menu.addAction(self.auto_connect_action)

        self.setStatusBar(QStatusBar())

    def _rebuild_stations_menu(self):
        """Repopulates the &Stations menu bar entry: one checkable action per
        station (checked = currently playing, click = play it), followed by
        a Manage Stations... action that opens the full search/add/edit
        dialog. Called on every structural change (add/edit/remove) and
        whenever the playing station changes, since a rebuild is cheap for
        the handful of stations this app deals with."""
        self.stations_menu.clear()
        self._stations_action_group = QActionGroup(self)
        self._stations_action_group.setExclusive(True)
        for idx, station in enumerate(self.stations):
            action = QAction(station["name"], self)
            action.setCheckable(True)
            action.setChecked(idx == self.current_idx)
            action.triggered.connect(lambda checked=False, i=idx: self.play_index(i))
            self._stations_action_group.addAction(action)
            self.stations_menu.addAction(action)

        if self.stations:
            self.stations_menu.addSeparator()
        manage_action = QAction("&Manage Stations...", self)
        manage_action.triggered.connect(self._show_station_list_dialog)
        self.stations_menu.addAction(manage_action)

    def _build_tray(self):
        icon = _app_icon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("RadioTop")
        menu = QMenu()
        menu.addAction("Play / Pause", self.toggle_play_pause)
        menu.addAction("Stop", self.stop_playback)
        menu.addSeparator()
        menu.addAction("Show Window", self._show_window)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    # -------------------------------------------------------------- state
    def _load_custom_stations(self):
        raw = self.settings.value("custom_stations", "[]")
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except (TypeError, json.JSONDecodeError):
            pass
        return []

    def _save_custom_stations(self):
        customs = [s for s in self.stations if s.get("custom")]
        self.settings.setValue("custom_stations", json.dumps(customs))

    def _load_liked_tracks(self):
        raw = self.settings.value("liked_tracks", "[]")
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return set(data)
        except (TypeError, json.JSONDecodeError):
            pass
        return set()

    def _save_liked_tracks(self):
        self.settings.setValue("liked_tracks", json.dumps(sorted(self.liked_tracks)))

    @staticmethod
    def _liked_key(artist, title):
        return f"{artist.strip().lower()}||{title.strip().lower()}"

    # ------------------------------------------------------------- list ---
    # ---------------------------------------------------------- playback ---
    def play_index(self, idx, _is_reconnect=False):
        if idx is None or idx < 0 or idx >= len(self.stations):
            return
        station = self.stations[idx]
        self.current_idx = idx
        self.last_station_url = station["url"]
        self.settings.setValue("last_station_url", station["url"])
        if not _is_reconnect:
            self._playback_generation += 1
            self._reconnect_attempts_remaining = self.reconnect_max_attempts
        self._current_icy_name = None
        if self.stream_proxy is not None:
            play_url = self.stream_proxy.local_url(station["url"])
        else:
            play_url = station["url"]
        self.player.setSource(QUrl(play_url))
        self.player.play()
        self.name_label.setText(station["name"])
        self.track_label.setText("")
        self._pending_notification_artist = None
        self._show_notification("RadioTop - Station", station["name"])
        self.track_info_dialog.set_waiting()
        self.last_lookup_title = None
        self._stop_lookup_thread()
        self.last_image_artist = None
        self._stop_artist_image_thread()
        self._set_artist_image_placeholder("Waiting for track info...")
        self._set_artist_caption("")
        self.last_album_key = None
        self._stop_album_art_thread()
        self._set_album_art_placeholder("Waiting for track info...")
        self._set_album_caption("")
        self.last_similar_tracks_artist = None
        self.last_similar_tracks_title = None
        self._stop_similar_tracks_thread()
        self.statusBar().showMessage(f"Connecting to {station['name']}...", 4000)
        self.station_dialog.refresh_list()
        self._rebuild_stations_menu()
        self._start_metadata_thread(station["url"])
        self._start_subwave_thread(station["url"])

    def _start_subwave_thread(self, url):
        self._stop_subwave_thread()
        self._current_subwave_track = None
        self._subwave_detected = False
        self.subwave_detail_label.setText("")
        self.next_track_label.setText("")
        self.show_label.setText("")
        self.like_btn.setEnabled(False)
        self.like_btn.setText("☆ Like")
        self.subwave_api_base = _subwave_api_base(url)
        self.subwave_thread = SubwaveNowPlayingThread(self.subwave_api_base)
        self.subwave_thread.now_playing_ready.connect(self._on_subwave_now_playing)
        self.subwave_thread.unavailable.connect(self._on_subwave_unavailable)
        self.subwave_thread.finished.connect(self._on_subwave_thread_finished)
        self.subwave_thread.finished.connect(self.subwave_thread.deleteLater)
        self.subwave_thread.start()

    def _stop_subwave_thread(self):
        thread = self.subwave_thread
        self.subwave_thread = None
        self.subwave_api_base = None
        if thread is None:
            return
        try:
            thread.now_playing_ready.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.unavailable.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.stop()
            thread.wait(2000)
            if thread.isRunning():
                thread.terminate()
                thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    def _on_subwave_thread_finished(self):
        if self.sender() is self.subwave_thread:
            self.subwave_thread = None

    def _on_subwave_unavailable(self):
        self.subwave_api_base = None
        self._current_subwave_track = None
        self._subwave_detected = False
        self.subwave_detail_label.setText("")
        self.next_track_label.setText("")
        self.show_label.setText("")
        self.like_btn.setEnabled(False)
        self.like_btn.setText("☆ Like")

    def _on_subwave_now_playing(self, payload):
        if self.current_idx is None:
            return
        self._subwave_detected = True
        self._update_status()

        now_response = payload.get("now_playing") or {}
        active_show = now_response.get("activeShow") or {}
        self.show_label.setText(f"On Air: {active_show['name']}" if active_show.get("name") else "")

        now = now_response.get("nowPlaying") or {}
        artist = (now.get("artist") or "").strip()
        title = (now.get("title") or "").strip()
        if artist and title:
            self._current_subwave_track = {"artist": artist, "title": title}
            self.subwave_detail_label.setText(str(now.get("genre") or ""))
            self.like_btn.setEnabled(True)
            liked = self._liked_key(artist, title) in self.liked_tracks
            self.like_btn.setText("★ Liked" if liked else "☆ Like")
            self.track_info_dialog.set_subwave_details(now.get("bpm"), now.get("musicalKey"))
        else:
            self._current_subwave_track = None
            self.subwave_detail_label.setText("")
            self.like_btn.setEnabled(False)
            self.like_btn.setText("☆ Like")
            self.track_info_dialog.set_subwave_details(None, None)

        state = payload.get("state") or {}
        upcoming = state.get("upcoming") or []
        if upcoming:
            nxt = upcoming[0]
            nxt_artist = (nxt.get("artist") or "").strip()
            nxt_title = (nxt.get("title") or "").strip()
            label = " - ".join(p for p in (nxt_artist, nxt_title) if p)
            self.next_track_label.setText(f"Next: {label}" if label else "")
        else:
            self.next_track_label.setText("")

    def _on_like_clicked(self):
        track = self._current_subwave_track
        if not track:
            return
        key = self._liked_key(track["artist"], track["title"])
        if key in self.liked_tracks:
            self.liked_tracks.discard(key)
            self.like_btn.setText("☆ Like")
        else:
            self.liked_tracks.add(key)
            self.like_btn.setText("★ Liked")
            self._send_subwave_like_request(track["artist"], track["title"])
        self._save_liked_tracks()

    def _send_subwave_like_request(self, artist, title):
        if not self.subwave_api_base:
            return
        thread = SubwaveRequestThread(self.subwave_api_base, f"more like {artist} - {title}")
        self._subwave_request_threads.append(thread)

        def _cleanup(t=thread):
            if t in self._subwave_request_threads:
                self._subwave_request_threads.remove(t)

        thread.finished.connect(_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _stop_subwave_request_threads(self):
        threads = list(self._subwave_request_threads)
        self._subwave_request_threads.clear()
        for thread in threads:
            try:
                thread.stop()
                thread.wait(2000)
                if thread.isRunning():
                    thread.terminate()
                    thread.wait(500)
            except RuntimeError:
                pass  # underlying C++ object was already deleted - nothing to do

    def _stop_update_check_thread(self):
        thread = self.update_check_thread
        self.update_check_thread = None
        if thread is None:
            return
        try:
            thread.stop()
            thread.wait(2000)
            if thread.isRunning():
                thread.terminate()
                thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    def _start_metadata_thread(self, url):
        self._stop_metadata_thread()
        self.meta_thread = IcyMetadataThread(url)
        self.meta_thread.title_changed.connect(self._on_track_title)
        self.meta_thread.station_name_ready.connect(self._on_icy_station_name)
        self.meta_thread.finished.connect(self._on_meta_thread_finished)
        self.meta_thread.finished.connect(self.meta_thread.deleteLater)
        self.meta_thread.start()

    def _on_meta_thread_finished(self):
        # The thread may have already been deleteLater'd and replaced by a
        # newer one by the time this runs - only clear our reference if it
        # still points at the thread that just finished.
        if self.sender() is self.meta_thread:
            self.meta_thread = None

    def _on_icy_station_name(self, icy_name):
        """Records the station's own broadcast name (from the icy-name
        response header) so the "Playing on - <name>" status always reflects
        the stream's actual reported name - and separately, adopts it in
        place of the station's stored name if that name is still just the
        placeholder guessed from the URL's hostname when the station was
        added, but never overrides a name the user actually typed in,
        custom or not."""
        if self.current_idx is None:
            return
        self._current_icy_name = icy_name
        station = self.stations[self.current_idx]
        if station["name"] != self._guess_name(station["url"]) or icy_name == station["name"]:
            self._update_status()  # status label picks up self._current_icy_name regardless
            return
        station["name"] = icy_name
        self.name_label.setText(icy_name)
        self._update_status()
        self.statusBar().showMessage(f'Station name from stream: "{icy_name}"', 4000)
        self._show_notification("RadioTop - Station Name Found", f'Now known as "{icy_name}"')
        if station.get("custom"):
            self._save_custom_stations()
        self.station_dialog.refresh_list()
        self._rebuild_stations_menu()

    def _stop_metadata_thread(self):
        thread = self.meta_thread
        self.meta_thread = None
        if thread is None:
            return
        try:
            thread.title_changed.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.stop()
            thread.wait(2000)
            if thread.isRunning():
                thread.terminate()
                thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    def _set_track_label(self, raw_title, year=None):
        text = f"\u266a {raw_title}"
        if year:
            text += f"  ({year})"
        self.track_label.setText(text)

    # How long to give the async artist-photo fetch (Discogs/Wikipedia/
    # Last.fm) a head start before the "now playing" notification gives
    # up on it and falls back to the plain app icon. Long enough to cover
    # most of these lookups, short enough that the notification still
    # feels immediate.
    NOTIFICATION_IMAGE_WAIT_MS = 1500

    def _on_track_title(self, title):
        self._set_track_label(title)
        self.track_info_dialog.set_now_playing(title)
        self._lookup_track_info(title)
        artist, track_name = TrackLookupThread._split_artist_title(title)
        body = f"{artist} \u2014 {track_name}" if artist else (title or "Unknown track")
        if artist:
            self._fetch_artist_image(artist)  # kick this off first so it has maximum head start
        self._schedule_track_notification(artist, body)

    def _schedule_track_notification(self, artist, body):
        icon = self._icon_for_artist(artist) if artist else None
        if should_notify_immediately(artist, icon is not None):
            self._pending_notification_artist = None
            self._show_notification("RadioTop - Now Playing", body, icon)
            return
        # Not cached yet. Hold the notification briefly so it can use the
        # real artist photo once the async fetch finishes, instead of
        # always falling back to the generic app icon on an artist's
        # first play. _on_artist_image_ready/_not_found fire this early
        # if the fetch finishes before the wait window elapses.
        self._pending_notification_artist = artist
        self._pending_notification_body = body
        QTimer.singleShot(
            self.NOTIFICATION_IMAGE_WAIT_MS,
            lambda a=artist: self._fire_pending_notification(a),
        )

    def _fire_pending_notification(self, artist):
        if self._pending_notification_artist != artist:
            return  # already fired early, or superseded by a newer track/station
        self._pending_notification_artist = None
        body = self._pending_notification_body
        icon = self._icon_for_artist(artist)
        self._show_notification("RadioTop - Now Playing", body, icon)

    def _icon_for_artist(self, artist_name):
        cached = self.artist_image_cache.get(artist_name)
        if not cached:
            return None
        pixmap = QPixmap()
        if pixmap.loadFromData(cached) and not pixmap.isNull():
            return QIcon(pixmap)
        return None

    def _refresh_current_artist_image(self):
        # Clearing artist_image_cache alone isn't enough to make a
        # credential change take effect immediately: _fetch_artist_image()
        # no-ops when asked to re-fetch for the artist it's already
        # showing/fetching, so without resetting last_image_artist too, the
        # change stays invisible until the next track change (same fix
        # applied in _on_similar_tracks_widen_toggled).
        artist_name = self.last_image_artist
        self.last_image_artist = None
        if artist_name:
            self._fetch_artist_image(artist_name)

    def _configure_lastfm_key(self):
        dlg = LastfmSettingsDialog(self.lastfm_api_key, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            key = dlg.api_key()
            self.lastfm_api_key = key
            self.settings.setValue("lastfm_api_key", key)
            self.lookup_cache.clear()
            self.artist_image_cache.clear()
            self._refresh_current_artist_image()
            self.statusBar().showMessage(
                "Last.fm API key saved." if key else "Last.fm API key cleared - using MusicBrainz only.",
                4000,
            )

    def _configure_discogs_token(self):
        dlg = DiscogsSettingsDialog(self.discogs_token, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            token = dlg.token()
            self.discogs_token = token
            self.settings.setValue("discogs_token", token)
            self.artist_image_cache.clear()
            self._refresh_current_artist_image()
            self.statusBar().showMessage(
                "Discogs token saved." if token else "Discogs token cleared.",
                4000,
            )

    def _on_notifications_toggled(self, checked):
        self.notifications_enabled = checked
        self.settings.setValue("show_notifications", checked)

    def _on_auto_reconnect_toggled(self, checked):
        self.auto_reconnect_enabled = checked
        self.settings.setValue("auto_reconnect_enabled", checked)

    def _on_auto_connect_toggled(self, checked):
        self.auto_connect_last_station = checked
        self.settings.setValue("auto_connect_last_station", checked)

    def _configure_reconnect_attempts(self):
        value, ok = QInputDialog.getInt(
            self,
            "Reconnect Attempts",
            "Number of reconnect attempts after a dropped connection:",
            self.reconnect_max_attempts,
            1,
            10,
            1,
        )
        if ok:
            self.reconnect_max_attempts = value
            self.settings.setValue("reconnect_max_attempts", value)

    def _on_similar_tracks_widen_toggled(self, checked):
        self.similar_tracks_widen = checked
        self.settings.setValue("similar_tracks_widen", checked)
        self.similar_tracks_cache.clear()
        # _fetch_similar_tracks() no-ops when asked to re-fetch for the
        # artist it's already showing/fetching - which, without resetting
        # last_similar_tracks_artist here too, would make toggling this
        # setting invisible until the next track change. Re-fetch for the
        # currently displayed track (if any) so the new widen setting takes
        # effect immediately.
        artist_name = self.last_similar_tracks_artist
        track_title = self.last_similar_tracks_title
        self.last_similar_tracks_artist = None
        self.last_similar_tracks_title = None
        if artist_name and track_title:
            self._fetch_similar_tracks(artist_name, track_title)

    def _show_notification(self, title, body, icon=None):
        if not self.notifications_enabled:
            return
        # Routed through the system tray icon's native notification call
        # (the desktop's own notification service - KDE Plasma's via
        # D-Bus/knotifications, the Windows Action Center on 10/11, etc.)
        # rather than a self-drawn, self-positioned popup window. Custom
        # top-level windows can't reliably position or even show
        # themselves under Wayland compositors (including Plasma's
        # default Wayland session) - only the desktop's own notification
        # daemon can guarantee correct placement and visibility.
        if icon is None:
            icon = self.windowIcon()
        self.tray.showMessage(title, body, icon, 4000)

    def toggle_play_pause(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        elif self.current_idx is not None:
            self.play_index(self.current_idx)  # resume whatever was last selected
        else:
            idx = self._find_station_index_by_url(self.last_station_url)
            if idx is not None:
                self.play_index(idx)  # nothing selected this run - resume last-played station
            else:
                self._show_station_list_dialog()  # never played anything - prompt for a station

    def _find_station_index_by_url(self, url):
        if not url:
            return None
        for idx, station in enumerate(self.stations):
            if station["url"] == url:
                return idx
        return None

    def _show_station_list_dialog(self):
        self.station_dialog.refresh_list()
        self.station_dialog.show()
        self.station_dialog.raise_()
        self.station_dialog.activateWindow()

    def stop_playback(self):
        self.player.stop()
        self.current_idx = None
        self._playback_generation += 1  # invalidate any pending auto-reconnect retry
        self._stop_metadata_thread()
        self._stop_subwave_thread()
        self._stop_subwave_request_threads()
        self._current_subwave_track = None
        self._subwave_detected = False
        self.subwave_detail_label.setText("")
        self.next_track_label.setText("")
        self.show_label.setText("")
        self.like_btn.setEnabled(False)
        self.like_btn.setText("☆ Like")
        self._stop_lookup_thread()
        self.last_lookup_title = None
        self._stop_artist_image_thread()
        self._pending_notification_artist = None
        self.last_image_artist = None
        self._set_artist_image_placeholder("No image")
        self._set_artist_caption("")
        self._stop_album_art_thread()
        self.last_album_key = None
        self._set_album_art_placeholder("No image")
        self._set_album_caption("")
        self._stop_similar_tracks_thread()
        self.last_similar_tracks_artist = None
        self.last_similar_tracks_title = None
        self.name_label.setText("Nothing playing")
        self.track_label.setText("")
        self.track_info_dialog.set_no_track()
        self.station_dialog.refresh_list()
        self._rebuild_stations_menu()

    # ------------------------------------------------------- status/errors
    def _update_status(self, *_):
        state = self.player.playbackState()
        media_status = self.player.mediaStatus()

        if media_status == QMediaPlayer.MediaStatus.InvalidMedia:
            status = "Error"
        elif state == QMediaPlayer.PlaybackState.PlayingState:
            if media_status in (
                QMediaPlayer.MediaStatus.LoadingMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
                QMediaPlayer.MediaStatus.StalledMedia,
            ):
                status = "Buffering..."
            else:
                status = "Playing"
        elif state == QMediaPlayer.PlaybackState.PausedState:
            status = "Paused"
        else:
            status = "Stopped"

        text = status
        if status == "Playing" and self.current_idx is not None:
            # Prefer the stream's own live icy-name over the stored station
            # name, which may be a name the user typed in and that
            # _on_icy_station_name therefore left untouched.
            display_name = self._current_icy_name or self.stations[self.current_idx]["name"]
            if self._subwave_detected:
                display_name += " (SUB/WAVE)"
            text = f"Playing on - {display_name}"

        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS.get(status, '#888888')};")

        style = self.style()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _on_error(self, error, error_string):
        self.status_label.setText("Error")
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS['Error']};")
        self.statusBar().showMessage(error_string or "Playback error", 6000)
        self._maybe_reconnect()

    def _maybe_reconnect(self):
        if not should_attempt_reconnect(
            self.auto_reconnect_enabled,
            self.current_idx is not None,
            self._reconnect_attempts_remaining,
        ):
            return
        self._reconnect_attempts_remaining -= 1
        idx = self.current_idx
        generation = self._playback_generation
        attempt_number = self.reconnect_max_attempts - self._reconnect_attempts_remaining
        self.statusBar().showMessage(
            format_reconnect_message(attempt_number, self.reconnect_max_attempts),
            4000,
        )
        QTimer.singleShot(3000, lambda: self._do_reconnect(idx, generation))

    def _do_reconnect(self, idx, generation):
        if generation != self._playback_generation:
            return  # station changed or playback was stopped since the error
        self.play_index(idx, _is_reconnect=True)

    # ------------------------------------------------------------ volume ---
    def _on_volume_changed(self, value):
        self.audio_output.setVolume(value / 100.0)
        self.volume_pct_label.setText(f"{value}%")
        self.settings.setValue("volume", value)

    # -------------------------------------------------------- output device
    def _refresh_output_devices(self, preserve_selection=True):
        current_id = None
        if preserve_selection and self.device_combo.count() > 0:
            current_dev = self.device_combo.currentData()
            if current_dev is not None:
                current_id = bytes(current_dev.id())

        devices = QMediaDevices.audioOutputs()
        default_device = QMediaDevices.defaultAudioOutput()

        self.device_combo.blockSignals(True)
        self.device_combo.clear()

        if not devices:
            self.device_combo.addItem("No output devices found", None)
            self.device_combo.blockSignals(False)
            return

        if current_id is None:
            saved_id = self.settings.value("output_device_id", b"")
            if isinstance(saved_id, str):
                saved_id = saved_id.encode("latin-1", errors="ignore")
            current_id = bytes(saved_id) if saved_id else None

        device_ids = []
        for dev in devices:
            label = dev.description()
            if not default_device.isNull() and bytes(dev.id()) == bytes(default_device.id()):
                label += " (Default)"
            self.device_combo.addItem(label, dev)
            device_ids.append(bytes(dev.id()))

        select_idx = select_output_device_index(device_ids, current_id)

        self.device_combo.setCurrentIndex(select_idx)
        self._apply_output_device(self.device_combo.itemData(select_idx))
        self.device_combo.blockSignals(False)

    def _on_device_selected(self, index):
        device = self.device_combo.itemData(index)
        if device is not None:
            self._apply_output_device(device)

    def _apply_output_device(self, device):
        self.audio_output.setDevice(device)
        try:
            self.settings.setValue("output_device_id", bytes(device.id()))
        except Exception:
            pass
        self.statusBar().showMessage(f"Audio output: {device.description()}", 3000)

    # ----------------------------------------------------------- stations --
    def _guess_name(self, url):
        return QUrl(url).host() or "Custom Stream"

    # --------------------------------------------------------------- tray -
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.setVisible(not self.isVisible())
            if self.isVisible():
                self.raise_()
                self.activateWindow()

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_about(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("About RadioTop")
        dlg.setText(
            f"<b>RadioTop</b> v{APP_VERSION}<br>A simple internet radio player.<br>No bloat, just play.<br>"
            "Built with PySide6 / Qt Multimedia."
        )
        logo_path = _resource_path("assets", "radiotop_about_logo.png")
        logo = QPixmap(logo_path)
        if not logo.isNull():
            dlg.setIconPixmap(
                logo.scaled(
                    160, 160,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()

    def _check_for_updates(self, manual=False):
        if self.update_check_thread is not None:
            if manual:
                self.statusBar().showMessage("Already checking for updates...", 3000)
            return
        self.update_check_thread = UpdateCheckThread(APP_VERSION)
        self.update_check_thread.check_complete.connect(
            lambda result: self._on_update_check_complete(result, manual)
        )
        self.update_check_thread.finished.connect(self._on_update_check_thread_finished)
        self.update_check_thread.finished.connect(self.update_check_thread.deleteLater)
        self.update_check_thread.start()

    def _on_update_check_thread_finished(self):
        if self.sender() is self.update_check_thread:
            self.update_check_thread = None

    def _on_update_check_complete(self, result, manual):
        self.settings.setValue("last_update_check", time.time())
        error = result.get("error")
        if error:
            if manual:
                QMessageBox.warning(self, "Check for Updates", f"Couldn't check for updates:\n{error}")
            return
        if not result.get("available"):
            if manual:
                QMessageBox.information(
                    self, "Check for Updates", f"You're up to date (v{APP_VERSION})."
                )
            return

        latest_version = result.get("latest_version", "?")
        notes = result.get("notes", "").strip()
        html_url = result.get("html_url")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Update Available")
        dlg.setIcon(QMessageBox.Icon.Information)
        text = f"RadioTop v{latest_version} is available (you have v{APP_VERSION})."
        if notes:
            text += f"\n\n{notes}"
        dlg.setText(text)
        open_btn = dlg.addButton("Open Release Page", QMessageBox.ButtonRole.ActionRole)
        dlg.addButton(QMessageBox.StandardButton.Close)
        dlg.exec()
        if dlg.clickedButton() is open_btn and html_url:
            QDesktopServices.openUrl(QUrl(html_url))

    def quit_app(self):
        if self._quitting:
            return
        self._quitting = True
        # An active tray icon can keep the process alive on some desktops
        # (e.g. KDE Plasma's DBus-based StatusNotifierItem) even after
        # QApplication.quit() is called - hide it explicitly rather than
        # relying on teardown to do it implicitly. Harmless no-op on
        # platforms (like Windows) where this isn't an issue.
        self.tray.hide()
        self._stop_metadata_thread()
        self._stop_subwave_thread()
        self._stop_subwave_request_threads()
        self._stop_update_check_thread()
        self._stop_lookup_thread()
        self._stop_artist_image_thread()
        self._stop_album_art_thread()
        self._stop_similar_tracks_thread()
        self.player.stop()
        if self.stream_proxy is not None:
            self.stream_proxy.shutdown()
        # Safety net: if anything still prevents a clean shutdown, force
        # the process to actually exit after a short grace period rather
        # than leaving it hanging invisibly in the background. Marked as
        # a daemon thread so it never itself delays a normal clean exit.
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()
        QApplication.quit()

    def closeEvent(self, event):
        if self._quitting or not self.tray.isVisible():
            event.accept()
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Close RadioTop")
        msg.setText("Do you want to quit RadioTop, or keep it running in the background?")
        quit_btn = msg.addButton("Quit", QMessageBox.ButtonRole.DestructiveRole)
        tray_btn = msg.addButton("Minimize to Tray", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.setDefaultButton(tray_btn)
        msg.exec()
        clicked = msg.clickedButton()

        if clicked is quit_btn:
            event.accept()
            self.quit_app()
        elif clicked is tray_btn:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "RadioTop",
                "Still running in the tray. Right-click the tray icon to quit.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        else:
            event.ignore()  # Cancel - leave the window open


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    window.show()

    # Quit cleanly (no blocking "Quit or Minimize to Tray?" dialog) when the
    # OS asks us to close - either via a session manager's logout/reboot
    # sequence (X11 XSMP's commitDataRequest) or a direct SIGTERM/SIGINT.
    # Without this, closeEvent()'s modal can pop up during shutdown with
    # nobody able to click it, which is what leaves the process running and
    # blocks the reboot/logout.
    app.commitDataRequest.connect(lambda _sm: window.quit_app())
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_args: QTimer.singleShot(0, window.quit_app))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
