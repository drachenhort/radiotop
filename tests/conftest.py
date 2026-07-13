import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PySide6.QtCore import QObject, QSettings
from PySide6.QtWidgets import QMenu

import radiotop_gui as rt


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

    def play_index(self, idx):
        self.play_index_calls.append(idx)
        self.current_idx = idx

    def _show_station_list_dialog(self):
        self.show_station_list_dialog_calls += 1


@pytest.fixture
def main_window_stub(isolated_settings, qapp):
    return MainWindowStub(settings=isolated_settings)
