import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PySide6.QtCore import QObject, QSettings
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QMenu

import radiotop_gui as rt


class _StatusBarStub:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, timeout=0):
        self.messages.append(text)


class _StationDialogStub:
    def __init__(self):
        self.refresh_list_calls = 0

    def refresh_list(self):
        self.refresh_list_calls += 1


class _LabelStub:
    def __init__(self):
        self.text_value = ""
        self.style_value = ""

    def setText(self, text):
        self.text_value = text

    def setStyleSheet(self, style):
        self.style_value = style

    def text(self):
        return self.text_value


class _TimerStub:
    def __init__(self):
        self.start_calls = []
        self.stop_calls = 0
        self.active = False

    def start(self, ms):
        self.start_calls.append(ms)
        self.active = True

    def stop(self):
        self.stop_calls += 1
        self.active = False

    def isActive(self):
        return self.active


@pytest.fixture
def isolated_settings(tmp_path):
    """A QSettings instance backed by a throwaway INI file, so tests never
    touch the real ~/.config/radiotop (Linux) or registry (Windows) state."""
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


class MainWindowStub(QObject):
    """A real (but minimal) QObject carrying just the attributes that
    individual MainWindow methods need, so those methods can be exercised
    via e.g. `rt.MainWindow._guess_name(stub, url)` without constructing
    the real MainWindow - which spins up a live audio player, system tray,
    and local stream proxy server, none of which these tests care about.
    Must subclass QObject (not a plain stub) because methods like
    _rebuild_stations_menu parent QActions/QActionGroups to `self`, which
    Shiboken requires to be a properly-constructed QObject.
    """

    # Class attributes matching MainWindow for method access
    _SUBWAVE_HEARTBEAT_OK_COLOR = "#2ecc71"
    _SUBWAVE_HEARTBEAT_STALE_COLOR = "#888888"
    SUBWAVE_DETECT_MAX_RETRIES = 5

    def __init__(self, settings=None, stations=None):
        super().__init__()
        self.settings = settings
        self.stations = stations if stations is not None else []
        self.current_idx = None
        self._current_icy_name = None
        self._subwave_detected = False
        self._subwave_heartbeat_timer = None
        self._subwave_heartbeat_missed = 0
        self._subwave_heartbeat_ok = False
        self.subwave_thread = None
        self.subwave_api_base = None
        self._subwave_detect_retries_left = 0
        self.auto_reconnect_enabled = True
        self.prevent_standby_enabled = True
        self.sleep_calls = []
        self._sleep_inhibitor = SimpleNamespace(
            acquire=lambda: self.sleep_calls.append("acquire"),
            release=lambda: self.sleep_calls.append("release"),
        )
        self.reconnect_max_attempts = 3
        self._reconnect_attempts_remaining = 0
        self._playback_generation = 0
        self.stations_menu = QMenu()
        self.play_index_calls = []
        self.show_station_list_dialog_calls = 0
        self.name_label = _LabelStub()
        self.station_dialog = _StationDialogStub()
        self._status_bar = _StatusBarStub()
        self.save_custom_stations_calls = 0
        # Defaults so _update_status() can run without extra rigging; tests
        # exercising it directly override player/mediaStatus as needed.
        self.player = SimpleNamespace(
            playbackState=lambda: QMediaPlayer.PlaybackState.StoppedState,
            mediaStatus=lambda: QMediaPlayer.MediaStatus.NoMedia,
        )
        self.status_label = _LabelStub()
        self.play_btn = SimpleNamespace(setIcon=lambda i: None)
        self.style = lambda: SimpleNamespace(standardIcon=lambda i: None)
        self.notification_calls = []

    def play_index(self, idx):
        self.play_index_calls.append(idx)
        self.current_idx = idx

    def _show_notification(self, title, body, icon=None):
        self.notification_calls.append((title, body))

    def _show_station_list_dialog(self):
        self.show_station_list_dialog_calls += 1

    def _guess_name(self, url):
        return rt.MainWindow._guess_name(self, url)

    def _rebuild_stations_menu(self):
        rt.MainWindow._rebuild_stations_menu(self)

    def _update_status(self):
        rt.MainWindow._update_status(self)

    def _on_prevent_standby_toggled(self, checked):
        rt.MainWindow._on_prevent_standby_toggled(self, checked)

    def _refresh_current_artist_image(self):
        rt.MainWindow._refresh_current_artist_image(self)

    def _set_subwave_heartbeat_ok(self, ok):
        rt.MainWindow._set_subwave_heartbeat_ok(self, ok)

    def _stop_subwave_thread(self):
        rt.MainWindow._stop_subwave_thread(self)

    def _on_subwave_now_playing(self, payload):
        rt.MainWindow._on_subwave_now_playing(self, payload)

    def _on_subwave_unavailable(self):
        rt.MainWindow._on_subwave_unavailable(self)

    def _on_subwave_thread_finished(self):
        rt.MainWindow._on_subwave_thread_finished(self)

    def _maybe_retry_subwave_detection(self):
        rt.MainWindow._maybe_retry_subwave_detection(self)

    def _save_custom_stations(self):
        self.save_custom_stations_calls += 1
        if self.settings is not None:
            rt.MainWindow._save_custom_stations(self)

    def statusBar(self):
        return self._status_bar


@pytest.fixture
def main_window_stub(isolated_settings, qapp):
    return MainWindowStub(settings=isolated_settings)
