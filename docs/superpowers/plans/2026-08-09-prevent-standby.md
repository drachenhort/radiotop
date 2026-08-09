# Prevent System Standby While Playing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-toggleable Settings-menu option (on by default) that stops the OS from
suspending while a RadioTop station is actively playing.

**Architecture:** A new `_SleepInhibitor` class in `util.py` wraps OS-native sleep-inhibit
mechanisms (Windows: `ctypes` `SetThreadExecutionState`; macOS: `caffeinate` subprocess; Linux:
`systemd-inhibit` subprocess) behind a simple idempotent `acquire()`/`release()` interface.
`MainWindow` owns one instance, calls `acquire()`/`release()` from its existing
`_update_status()` playback-state handler and from a new Settings-menu toggle handler, and
releases on app quit as a safety net.

**Tech Stack:** PySide6 (QSettings, QAction), Python stdlib `ctypes` / `subprocess` / `sys`.

## Global Constraints

- No new runtime dependency (spec: "No new runtime dependency — uses OS-native mechanisms
  already available on each platform").
- Inhibit *system* sleep only, not display sleep.
- Active only while `QMediaPlayer.PlaybackState.PlayingState`; paused/stopped/errored does not
  inhibit.
- Toggle defaults to **on** (`True`) when no `QSettings` value exists yet.
- Follow existing test convention: never hit real OS/subprocess/ctypes calls in tests — monkeypatch
  them (see `CLAUDE.md` "Testing" section and `tests/conftest.py`'s `_CancellableRequestThread`
  approach of monkeypatching `urllib.request.urlopen`).

---

### Task 1: `_SleepInhibitor` in `util.py`

**Files:**
- Modify: `util.py` (add imports at top, add class at end, after `_app_icon()` at line 148)
- Test: `tests/test_util.py` (add tests at end of file)

**Interfaces:**
- Produces: `_SleepInhibitor` class with `acquire()` and `release()` methods, both taking no
  arguments and returning `None`. Internal `_active: bool` attribute (starts `False`).
  Platform dispatch is read from `sys.platform` once, at `acquire()` time is fine (no need to
  cache at `__init__`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_util.py`:

```python
import subprocess
from unittest.mock import MagicMock, patch

from util import _SleepInhibitor


def test_sleep_inhibitor_windows_acquire_calls_set_thread_execution_state():
    inhibitor = _SleepInhibitor()
    fake_kernel32 = MagicMock()
    with patch("util.sys.platform", "win32"), patch("util.ctypes") as fake_ctypes:
        fake_ctypes.windll.kernel32 = fake_kernel32
        inhibitor.acquire()
    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED = 0x80000000 | 0x00000001
    fake_kernel32.SetThreadExecutionState.assert_called_once_with(0x80000001)
    assert inhibitor._active is True


def test_sleep_inhibitor_windows_release_restores_es_continuous():
    inhibitor = _SleepInhibitor()
    inhibitor._active = True
    fake_kernel32 = MagicMock()
    with patch("util.sys.platform", "win32"), patch("util.ctypes") as fake_ctypes:
        fake_ctypes.windll.kernel32 = fake_kernel32
        inhibitor.release()
    fake_kernel32.SetThreadExecutionState.assert_called_once_with(0x80000000)
    assert inhibitor._active is False


def test_sleep_inhibitor_macos_acquire_spawns_caffeinate():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    with patch("util.sys.platform", "darwin"), patch(
        "util.subprocess.Popen", return_value=fake_proc
    ) as fake_popen:
        inhibitor.acquire()
    fake_popen.assert_called_once_with(["caffeinate", "-i"])
    assert inhibitor._process is fake_proc
    assert inhibitor._active is True


def test_sleep_inhibitor_macos_release_terminates_process():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    inhibitor._active = True
    inhibitor._process = fake_proc
    with patch("util.sys.platform", "darwin"):
        inhibitor.release()
    fake_proc.terminate.assert_called_once()
    assert inhibitor._process is None
    assert inhibitor._active is False


def test_sleep_inhibitor_linux_acquire_spawns_systemd_inhibit():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    with patch("util.sys.platform", "linux"), patch(
        "util.subprocess.Popen", return_value=fake_proc
    ) as fake_popen:
        inhibitor.acquire()
    fake_popen.assert_called_once_with(
        [
            "systemd-inhibit",
            "--what=idle:sleep",
            "--who=RadioTop",
            "--why=Streaming audio",
            "sleep",
            "infinity",
        ]
    )
    assert inhibitor._process is fake_proc
    assert inhibitor._active is True


def test_sleep_inhibitor_linux_release_terminates_process():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    inhibitor._active = True
    inhibitor._process = fake_proc
    with patch("util.sys.platform", "linux"):
        inhibitor.release()
    fake_proc.terminate.assert_called_once()
    assert inhibitor._process is None
    assert inhibitor._active is False


def test_sleep_inhibitor_linux_acquire_missing_systemd_inhibit_is_noop():
    inhibitor = _SleepInhibitor()
    with patch("util.sys.platform", "linux"), patch(
        "util.subprocess.Popen", side_effect=FileNotFoundError
    ):
        inhibitor.acquire()  # must not raise
    assert inhibitor._active is False
    assert inhibitor._process is None


def test_sleep_inhibitor_acquire_is_idempotent():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    with patch("util.sys.platform", "darwin"), patch(
        "util.subprocess.Popen", return_value=fake_proc
    ) as fake_popen:
        inhibitor.acquire()
        inhibitor.acquire()
    fake_popen.assert_called_once()  # second acquire() is a no-op


def test_sleep_inhibitor_release_before_acquire_is_noop():
    inhibitor = _SleepInhibitor()
    with patch("util.sys.platform", "darwin") as _:
        inhibitor.release()  # must not raise, no process to terminate
    assert inhibitor._active is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_util.py -k sleep_inhibitor -v`
Expected: FAIL with `ImportError: cannot import name '_SleepInhibitor' from 'util'`

- [ ] **Step 3: Write the implementation**

Add imports at the top of `util.py` (after the existing `import` block, i.e. after line 13's
`from urllib.parse import urlparse, urlunparse`, before the `PySide6` imports):

```python
import ctypes
import logging
import subprocess
```

Add at the end of `util.py`, after `_app_icon()`:

```python
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class _SleepInhibitor:
    """Prevents the OS from suspending the system while active. Platform-specific,
    idempotent acquire()/release() - repeated or out-of-order calls are safe no-ops.

    Only inhibits *system* sleep, not display sleep, matching how other media
    players behave: the screen can still turn off while audio keeps playing.
    """

    def __init__(self):
        self._active = False
        self._process = None  # macOS/Linux: the caffeinate/systemd-inhibit subprocess

    def acquire(self):
        if self._active:
            return
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
            )
        elif sys.platform == "darwin":
            self._process = subprocess.Popen(["caffeinate", "-i"])
        else:
            try:
                self._process = subprocess.Popen(
                    [
                        "systemd-inhibit",
                        "--what=idle:sleep",
                        "--who=RadioTop",
                        "--why=Streaming audio",
                        "sleep",
                        "infinity",
                    ]
                )
            except FileNotFoundError:
                logging.warning(
                    "systemd-inhibit not found; standby prevention unavailable on this system"
                )
                return
        self._active = True

    def release(self):
        if not self._active:
            return
        if sys.platform == "win32":
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        elif self._process is not None:
            self._process.terminate()
            self._process = None
        self._active = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_util.py -k sleep_inhibitor -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `pytest`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add util.py tests/test_util.py
git commit -m "feat: add _SleepInhibitor for OS-native system-sleep prevention"
```

---

### Task 2: Wire `_SleepInhibitor` into `MainWindow`

**Files:**
- Modify: `radiotop_gui.py`:
  - imports (line 90 area, `from util import (...)`)
  - `__init__` (settings block around line 161, `self.notifications_enabled = ...`)
  - `_build_menu` / settings menu (around line 423, after the `similar_tracks_widen_action` block)
  - `_on_notifications_toggled` area (around line 915, add new handler alongside it)
  - `_update_status` (end of method, after line 1086)
  - `quit_app` (around line 1255, after `self.player.stop()`)
- Modify: `tests/conftest.py` (`MainWindowStub`, add `prevent_standby_enabled`, `_sleep_inhibitor`
  stub, `sleep_calls` list, `_on_prevent_standby_toggled` delegating method)
- Test: `tests/test_main_window.py` (add tests at end of file)

**Interfaces:**
- Consumes: `_SleepInhibitor` (from Task 1) — `acquire()` / `release()`, both no-arg.
- Produces: `MainWindow.prevent_standby_enabled: bool`, `MainWindow._sleep_inhibitor:
  _SleepInhibitor`, `MainWindow._on_prevent_standby_toggled(checked: bool) -> None`,
  `MainWindow.prevent_standby_action: QAction`.

- [ ] **Step 1: Add the import**

In `radiotop_gui.py`, modify the `from util import (` block to add `_SleepInhibitor` (alphabetical,
before `_app_icon`):

```python
from util import (
    DEFAULT_STREAM_FILENAME,
    DEFAULT_STREAM_PORT,
    _app_icon,
    _normalize_station_url,
    _resource_path,
    _SleepInhibitor,
    _subwave_api_base,
    format_reconnect_message,
    select_output_device_index,
    should_attempt_reconnect,
    should_notify_immediately,
)
```

- [ ] **Step 2: Add settings state and the inhibitor instance in `__init__`**

In `radiotop_gui.py`, right after line 162 (`self.auto_reconnect_enabled = ...`), add:

```python
        self.prevent_standby_enabled = self.settings.value("prevent_standby", True, type=bool)
        self._sleep_inhibitor = _SleepInhibitor()
```

- [ ] **Step 3: Add the Settings-menu toggle**

In `radiotop_gui.py`, right after the `similar_tracks_widen_action` block (after line 423,
`settings_menu.addAction(self.similar_tracks_widen_action)`), add:

```python
        self.prevent_standby_action = QAction("Prevent System &Standby While Playing", self)
        self.prevent_standby_action.setCheckable(True)
        self.prevent_standby_action.setChecked(self.prevent_standby_enabled)
        self.prevent_standby_action.toggled.connect(self._on_prevent_standby_toggled)
        settings_menu.addAction(self.prevent_standby_action)
```

- [ ] **Step 4: Add the toggle handler**

In `radiotop_gui.py`, right after `_on_notifications_toggled` (after line 917), add:

```python
    def _on_prevent_standby_toggled(self, checked):
        self.prevent_standby_enabled = checked
        self.settings.setValue("prevent_standby", checked)
        if checked and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._sleep_inhibitor.acquire()
        else:
            self._sleep_inhibitor.release()
```

- [ ] **Step 5: Hook into `_update_status`**

In `radiotop_gui.py`, at the end of `_update_status` (after line 1086, the `else:
self.play_btn.setIcon(...)` block), add:

```python
        if state == QMediaPlayer.PlaybackState.PlayingState and self.prevent_standby_enabled:
            self._sleep_inhibitor.acquire()
        else:
            self._sleep_inhibitor.release()
```

- [ ] **Step 6: Release on quit as a safety net**

In `radiotop_gui.py`'s `quit_app`, right after `self.player.stop()`, add:

```python
        self._sleep_inhibitor.release()
```

- [ ] **Step 7: Update `MainWindowStub` in `tests/conftest.py`**

In `tests/conftest.py`, inside `MainWindowStub.__init__`, right after
`self.auto_reconnect_enabled = True` (line 100), add:

```python
        self.prevent_standby_enabled = True
        self.sleep_calls = []
        self._sleep_inhibitor = SimpleNamespace(
            acquire=lambda: self.sleep_calls.append("acquire"),
            release=lambda: self.sleep_calls.append("release"),
        )
```

And add a delegating method alongside `_update_status` (after line 139-140):

```python
    def _on_prevent_standby_toggled(self, checked):
        rt.MainWindow._on_prevent_standby_toggled(self, checked)
```

- [ ] **Step 8: Write the failing tests**

Add to `tests/test_main_window.py` (mirroring the existing `test_update_status_*` tests' pattern
of setting `main_window_stub.player` via `SimpleNamespace` before calling `_update_status()`;
`SimpleNamespace` and `QMediaPlayer` are already imported at the top of this file):

```python
def test_update_status_acquires_sleep_inhibitor_when_playing_and_enabled(main_window_stub):
    main_window_stub.prevent_standby_enabled = True
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadedMedia,
    )
    main_window_stub._update_status()
    assert main_window_stub.sleep_calls == ["acquire"]


def test_update_status_releases_sleep_inhibitor_when_playing_and_disabled(main_window_stub):
    main_window_stub.prevent_standby_enabled = False
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadedMedia,
    )
    main_window_stub._update_status()
    assert main_window_stub.sleep_calls == ["release"]


def test_update_status_releases_sleep_inhibitor_when_stopped(main_window_stub):
    main_window_stub.prevent_standby_enabled = True
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.StoppedState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.NoMedia,
    )
    main_window_stub._update_status()
    assert main_window_stub.sleep_calls == ["release"]


def test_update_status_releases_sleep_inhibitor_when_paused(main_window_stub):
    main_window_stub.prevent_standby_enabled = True
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PausedState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadedMedia,
    )
    main_window_stub._update_status()
    assert main_window_stub.sleep_calls == ["release"]


def test_prevent_standby_toggled_off_while_playing_releases(main_window_stub):
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadedMedia,
    )
    main_window_stub._on_prevent_standby_toggled(False)
    assert main_window_stub.prevent_standby_enabled is False
    assert main_window_stub.sleep_calls == ["release"]


def test_prevent_standby_toggled_on_while_playing_acquires(main_window_stub):
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadedMedia,
    )
    main_window_stub._on_prevent_standby_toggled(True)
    assert main_window_stub.prevent_standby_enabled is True
    assert main_window_stub.sleep_calls == ["acquire"]


def test_prevent_standby_toggled_while_stopped_does_not_acquire(main_window_stub):
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.StoppedState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.NoMedia,
    )
    main_window_stub._on_prevent_standby_toggled(True)
    assert main_window_stub.sleep_calls == ["release"]


def test_prevent_standby_toggled_persists_to_settings(main_window_stub):
    main_window_stub.player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.StoppedState,
        mediaStatus=lambda: QMediaPlayer.MediaStatus.NoMedia,
    )
    main_window_stub._on_prevent_standby_toggled(False)
    assert main_window_stub.settings.value("prevent_standby", True, type=bool) is False
```

- [ ] **Step 9: Run tests to verify they fail**

This task applied its implementation (Steps 1-7) before its tests (Step 8) because the change is
one small cross-file wiring — inseparable into a meaningful red/green split without duplicating
the whole diff. To still get a red signal: temporarily comment out Step 7's `conftest.py` block
(the `prevent_standby_enabled`/`sleep_calls`/`_sleep_inhibitor`/`_on_prevent_standby_toggled`
additions), then run:

Run: `pytest tests/test_main_window.py -k "sleep_inhibitor or prevent_standby" -v`
Expected: FAIL with `AttributeError: 'MainWindowStub' object has no attribute 'sleep_calls'`

Then restore Step 7's `conftest.py` block.

- [ ] **Step 10: Run tests to verify they pass**

Run: `pytest tests/test_main_window.py -k "sleep_inhibitor or prevent_standby" -v`
Expected: PASS (8 tests)

- [ ] **Step 11: Run full test suite to check for regressions**

Run: `pytest`
Expected: PASS, no regressions

- [ ] **Step 12: Manual smoke test in the real running app**

Run: `python3 radiotop_gui.py`
- Open **Settings** menu, confirm "Prevent System Standby While Playing" appears, checked by
  default.
- Play a station; on Linux, run `systemctl list-jobs` won't show it, but `ps aux | grep
  systemd-inhibit` (or `systemd-inhibit --list`) should show a RadioTop-owned inhibit while
  playing.
- Stop playback; confirm the `systemd-inhibit` process is gone (`ps aux | grep systemd-inhibit`
  no longer shows it).
- Toggle the setting off while playing; confirm the inhibit process is released immediately.
- Quit the app while playing with the toggle on; confirm no orphaned `systemd-inhibit` process is
  left running (`ps aux | grep systemd-inhibit`).

- [ ] **Step 13: Commit**

```bash
git add radiotop_gui.py tests/conftest.py tests/test_main_window.py
git commit -m "feat: add Settings toggle to prevent system standby while playing"
```
