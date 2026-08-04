# MainWindow pure-helper extraction — design

Date: 2026-08-04

## Goal

Improve test coverage of `MainWindow` logic by pulling decision logic out of
Qt/QSettings-coupled methods into pure functions in `util.py`, following the
existing pattern set by `_normalize_station_url`. No behavior change — this
is a lift-and-shift of logic that already runs today.

## Scope

Four extractions, each replacing inline logic in `radiotop_gui.py` with a
call to a new pure function in `util.py`:

1. **`select_output_device_index(device_ids, preserved_id, saved_id) -> int`**
   Replaces the id-matching loop in `MainWindow._refresh_output_devices`
   (radiotop_gui.py:1032-1067). Takes plain `bytes` device ids (not Qt
   `QAudioDevice` objects) and returns the index to select: prefer
   `preserved_id` (the currently-selected device, when `preserve_selection`
   is True), else `saved_id` (from QSettings), else `0`. `MainWindow` still
   builds the combo box entries and Qt device objects; it just calls this
   function to pick the index instead of doing the matching inline.

2. **`should_attempt_reconnect(auto_reconnect_enabled, has_current_station, attempts_remaining) -> bool`**
   Replaces the guard at the top of `MainWindow._maybe_reconnect`
   (radiotop_gui.py:1004-1009): returns `False` if auto-reconnect is
   disabled, there's no current station, or no attempts remain; `True`
   otherwise.

3. **`format_reconnect_message(attempt_number, max_attempts) -> str`**
   Replaces the inline f-string built in `_maybe_reconnect`
   (radiotop_gui.py:1012-1017), e.g. `"Connection dropped, reconnecting
   (2/5)..."`.

4. **`should_notify_immediately(artist, icon_cached) -> bool`**
   Replaces the branch in `MainWindow._schedule_track_notification`
   (radiotop_gui.py:778): returns `True` when there's no artist to look up,
   or the artist's photo is already cached; `False` when the notification
   should wait for the async image fetch.

## Non-goals

- No mixin split, no restructuring of `MainWindow` beyond the four call
  sites above.
- No change to `_liked_key` or `_find_station_index_by_url` — already pure
  and simple enough not to need it.
- No change to `UpdateCheckThread`'s version-compare logic — already
  covered by `tests/test_update_check.py`.

## Testing

Add a new `tests/test_util.py` (or extend `tests/test_normalize_station_url.py`
if the maintainer prefers keeping util tests in one file) covering the four
functions directly with plain inputs/outputs — no `MainWindowStub` needed
for these cases. Existing `tests/test_main_window.py` behavior should be
unaffected; add a couple of thin integration checks there only if useful to
confirm `MainWindow` still calls through to the new functions correctly.

## Risk

Low. Each extraction is a pure refactor of existing logic with identical
inputs/outputs; no new dependencies, no persistence format changes.
