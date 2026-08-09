# Prevent system standby while playing — design

## Problem

RadioTop has no way to stop the OS from sleeping while music is streaming. Users leaving a
station playing (e.g. as background music away from the keyboard) can have the system suspend
mid-stream if the OS's own idle-sleep timer fires, cutting off playback.

## Scope

- Inhibit *system* sleep (suspend) only, not display sleep — the screen turning off while audio
  keeps playing is expected/desired behavior, matching how other media players behave.
- Active only while a station is actually playing (`QMediaPlayer.PlaybackState.PlayingState`).
  Paused/stopped/errored playback does not inhibit sleep.
- User-toggleable, on by default.
- No new runtime dependency — uses OS-native mechanisms already available on each platform.

## Design

### `_SleepInhibitor` (new module-level class)

Added near the other module-level helpers (`_normalize_station_url`, `_resource_path`,
`_app_icon`). Exposes `acquire()` / `release()`, both idempotent (safe to call repeatedly or out
of order — internal `_active` flag guards against double-acquiring or double-releasing).

Platform dispatch happens once, based on `sys.platform`:

- **Windows** (`win32`): `ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS |
  ES_SYSTEM_REQUIRED)` on acquire; `SetThreadExecutionState(ES_CONTINUOUS)` on release (clears
  the flag, restoring normal idle behavior). In-process, no subprocess.
- **macOS** (`darwin`): acquire spawns `caffeinate -i` via `subprocess.Popen`; release calls
  `.terminate()` on that process. `caffeinate` is a standard macOS system binary.
- **Linux**: acquire spawns `systemd-inhibit --what=idle:sleep --who=RadioTop --why="Streaming
  audio" sleep infinity` via `subprocess.Popen`; release calls `.terminate()`. If `systemd-inhibit`
  isn't present (`FileNotFoundError`, e.g. non-systemd distros), acquire logs a warning and becomes
  a no-op — no crash, standby just isn't prevented on that system.

### `MainWindow` wiring

- New persisted setting: `self.prevent_standby_enabled = self.settings.value("prevent_standby",
  True, type=bool)`.
- New checkable `QAction` in the `&Settings` menu, alongside the existing toggles (notifications,
  widen similar tracks, auto-reconnect, auto-connect-last-station):
  `Prevent System &Standby While Playing`, checked state initialized from
  `prevent_standby_enabled`, `toggled` connected to `_on_prevent_standby_toggled`.
- `self._sleep_inhibitor = _SleepInhibitor()` created in `__init__`.
- `_update_status` (already invoked on every `playbackStateChanged` emission) additionally
  evaluates: if `state == PlayingState and self.prevent_standby_enabled`, call
  `self._sleep_inhibitor.acquire()`; otherwise call `self._sleep_inhibitor.release()`.
- `_on_prevent_standby_toggled(checked)`: saves `checked` to both `self.prevent_standby_enabled`
  and `QSettings`, then re-evaluates immediately against the current `self.player.playbackState()`
  (so toggling off mid-play releases right away; toggling on mid-play acquires right away) rather
  than waiting for the next state change.
- App-quit path (existing `closeEvent`) calls `self._sleep_inhibitor.release()` as a safety net,
  so a killed/closed app while playing doesn't leave the OS's sleep inhibited.

## Testing

`_SleepInhibitor`'s platform-specific calls (`subprocess.Popen`, `ctypes.windll`) are monkeypatched
in tests, per this project's existing convention of never hitting real OS/network calls in tests:

- `acquire()`/`release()` idempotency (double-acquire doesn't spawn/call twice; release before
  acquire is a no-op).
- `_on_prevent_standby_toggled` against `MainWindowStub`: toggling off while `PlayingState` calls
  `release()`; toggling on while `PlayingState` calls `acquire()`.
- `_update_status`'s new inhibitor logic against `MainWindowStub`: playing + enabled → acquire;
  playing + disabled → release; paused/stopped → release.
