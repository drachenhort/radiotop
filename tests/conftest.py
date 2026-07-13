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

    def setText(self, text):
        self.text_value = text

    def setStyleSheet(self, style):
        pass

    def text(self):
        return self.text_value


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

    def __init__(self, settings=None, stations=None):
        super().__init__()
        self.settings = settings
        self.stations = stations if stations is not None else []
        self.current_idx = None
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

    def play_index(self, idx):
        self.play_index_calls.append(idx)
        self.current_idx = idx

    def _show_station_list_dialog(self):
        self.show_station_list_dialog_calls += 1

    def _guess_name(self, url):
        return rt.MainWindow._guess_name(self, url)

    def _rebuild_stations_menu(self):
        rt.MainWindow._rebuild_stations_menu(self)

    def _update_status(self):
        rt.MainWindow._update_status(self)

    def _save_custom_stations(self):
        self.save_custom_stations_calls += 1
        if self.settings is not None:
            rt.MainWindow._save_custom_stations(self)

    def statusBar(self):
        return self._status_bar


@pytest.fixture
def main_window_stub(isolated_settings, qapp):
    return MainWindowStub(settings=isolated_settings)
