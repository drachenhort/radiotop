# MainWindow Pure-Helper Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract four pieces of decision logic out of Qt/QSettings-coupled `MainWindow` methods in `radiotop_gui.py` into pure functions in `util.py`, closing test-coverage gaps with zero behavior change.

**Architecture:** Each task adds one pure function to `util.py` (input: plain Python values; output: plain Python value, no Qt/QSettings/network calls), covers it with a direct unit test in `tests/test_util.py`, then wires the corresponding `MainWindow` method in `radiotop_gui.py` to call it in place of the inline logic it replaces.

**Tech Stack:** Python 3, PySide6/Qt, pytest + pytest-qt (see CLAUDE.md Testing section — tests run with `QT_QPA_PLATFORM=offscreen`, no real `MainWindow` instantiation; `tests/conftest.py`'s `MainWindowStub` is used for the integration checks in Task 5).

## Global Constraints

- No behavior change: each extraction is a lift-and-shift of logic that already runs today, verified via the design spec at `docs/superpowers/specs/2026-08-04-mainwindow-pure-helpers-design.md`.
- New functions go in `util.py`, following the existing style there (plain functions, one responsibility, short docstring only where the "why" isn't obvious from the code — see `_normalize_station_url` for the pattern).
- Import new functions into `radiotop_gui.py` via the existing `from util import (...)` block (radiotop_gui.py:83-90), alphabetized within the parens like the current entries.
- New tests go in `tests/test_util.py` (new file) — call functions directly with plain inputs, no `MainWindowStub` needed.
- Run `pytest` after every task; full suite must stay green throughout.

---

### Task 1: `select_output_device_index`

**Files:**
- Modify: `util.py` (add function)
- Modify: `radiotop_gui.py:1032-1067` (`MainWindow._refresh_output_devices`)
- Modify: `radiotop_gui.py:83-90` (import block)
- Test: `tests/test_util.py` (new file)

**Interfaces:**
- Produces: `select_output_device_index(device_ids: list[bytes], target_id: bytes | None) -> int`
  - `target_id` is already resolved by the caller to a single value before the loop runs (see radiotop_gui.py:1033-1054): either the currently-selected device's id, when `preserve_selection` is True, or the id saved in QSettings otherwise — never both.
  - Returns the index of `target_id` in `device_ids` if found, else `0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_util.py`:

```python
from util import select_output_device_index


def test_select_output_device_index_finds_target():
    ids = [b"aaa", b"bbb", b"ccc"]
    assert select_output_device_index(ids, b"bbb") == 1


def test_select_output_device_index_defaults_to_zero_when_not_found():
    ids = [b"aaa", b"bbb"]
    assert select_output_device_index(ids, b"zzz") == 0


def test_select_output_device_index_defaults_to_zero_when_target_none():
    ids = [b"aaa", b"bbb"]
    assert select_output_device_index(ids, None) == 0


def test_select_output_device_index_defaults_to_zero_when_no_ids():
    assert select_output_device_index([], None) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_util.py -v`
Expected: FAIL — `ImportError: cannot import name 'select_output_device_index' from 'util'`

- [ ] **Step 3: Implement in `util.py`**

Add after `_normalize_station_url` (after line 73), before `_subwave_api_base`:

```python
def select_output_device_index(device_ids, target_id):
    """Pick which audio output device index to select when (re)building the
    device combo box. target_id is either the currently-selected device's id
    (when preserving selection across a refresh) or the last device id saved
    to QSettings - MainWindow._refresh_output_devices resolves which one to
    pass in before calling this. Falls back to the first device in the list
    if target_id is None or isn't found."""
    if target_id is not None:
        for i, device_id in enumerate(device_ids):
            if device_id == target_id:
                return i
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_util.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Wire it into `MainWindow._refresh_output_devices`**

In `radiotop_gui.py:83-90`, add `select_output_device_index` to the import block (alphabetized):

```python
from util import (
    DEFAULT_STREAM_FILENAME,
    DEFAULT_STREAM_PORT,
    _app_icon,
    _normalize_station_url,
    _resource_path,
    _subwave_api_base,
    select_output_device_index,
)
```

Replace the selection loop in `_refresh_output_devices` (radiotop_gui.py:1056-1063):

```python
        select_idx = 0
        for i, dev in enumerate(devices):
            label = dev.description()
            if not default_device.isNull() and bytes(dev.id()) == bytes(default_device.id()):
                label += " (Default)"
            self.device_combo.addItem(label, dev)
            if current_id and bytes(dev.id()) == current_id:
                select_idx = i
```

with:

```python
        device_ids = []
        for dev in devices:
            label = dev.description()
            if not default_device.isNull() and bytes(dev.id()) == bytes(default_device.id()):
                label += " (Default)"
            self.device_combo.addItem(label, dev)
            device_ids.append(bytes(dev.id()))

        select_idx = select_output_device_index(device_ids, current_id)
```

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: all pass, including new `tests/test_util.py` tests and existing `tests/test_main_window.py`.

- [ ] **Step 7: Commit**

```bash
git add util.py radiotop_gui.py tests/test_util.py
git commit -m "refactor: extract select_output_device_index into util.py"
```

---

### Task 2: `should_attempt_reconnect`

**Files:**
- Modify: `util.py` (add function)
- Modify: `radiotop_gui.py:1004-1009` (`MainWindow._maybe_reconnect`)
- Test: `tests/test_util.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `should_attempt_reconnect(auto_reconnect_enabled: bool, has_current_station: bool, attempts_remaining: int) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_util.py`:

```python
from util import should_attempt_reconnect


def test_should_attempt_reconnect_true_when_all_conditions_met():
    assert should_attempt_reconnect(True, True, 3) is True


def test_should_attempt_reconnect_false_when_auto_reconnect_disabled():
    assert should_attempt_reconnect(False, True, 3) is False


def test_should_attempt_reconnect_false_when_no_current_station():
    assert should_attempt_reconnect(True, False, 3) is False


def test_should_attempt_reconnect_false_when_no_attempts_remaining():
    assert should_attempt_reconnect(True, True, 0) is False


def test_should_attempt_reconnect_false_when_attempts_negative():
    assert should_attempt_reconnect(True, True, -1) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_util.py -v -k should_attempt_reconnect`
Expected: FAIL — `ImportError: cannot import name 'should_attempt_reconnect' from 'util'`

- [ ] **Step 3: Implement in `util.py`**

Add after `select_output_device_index`:

```python
def should_attempt_reconnect(auto_reconnect_enabled, has_current_station, attempts_remaining):
    """Whether MainWindow._maybe_reconnect should schedule a reconnect
    attempt after a playback error: only if the user has auto-reconnect on,
    a station is actually selected, and there are attempts left in the
    current budget (reset each time a station is picked - see
    MainWindow.play_index)."""
    return auto_reconnect_enabled and has_current_station and attempts_remaining > 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_util.py -v -k should_attempt_reconnect`
Expected: PASS (5 passed)

- [ ] **Step 5: Wire it into `MainWindow._maybe_reconnect`**

Add `should_attempt_reconnect` to the `from util import (...)` block in `radiotop_gui.py` (alphabetized, so it lands right after `select_output_device_index`).

Replace radiotop_gui.py:1004-1008:

```python
    def _maybe_reconnect(self):
        if not self.auto_reconnect_enabled:
            return
        if self.current_idx is None or self._reconnect_attempts_remaining <= 0:
            return
```

with:

```python
    def _maybe_reconnect(self):
        if not should_attempt_reconnect(
            self.auto_reconnect_enabled,
            self.current_idx is not None,
            self._reconnect_attempts_remaining,
        ):
            return
```

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add util.py radiotop_gui.py tests/test_util.py
git commit -m "refactor: extract should_attempt_reconnect into util.py"
```

---

### Task 3: `format_reconnect_message`

**Files:**
- Modify: `util.py` (add function)
- Modify: `radiotop_gui.py:1010-1018` (`MainWindow._maybe_reconnect`)
- Test: `tests/test_util.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–2.
- Produces: `format_reconnect_message(attempt_number: int, max_attempts: int) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_util.py`:

```python
from util import format_reconnect_message


def test_format_reconnect_message_first_attempt():
    assert format_reconnect_message(1, 5) == "Connection dropped, reconnecting (1/5)..."


def test_format_reconnect_message_last_attempt():
    assert format_reconnect_message(5, 5) == "Connection dropped, reconnecting (5/5)..."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_util.py -v -k format_reconnect_message`
Expected: FAIL — `ImportError: cannot import name 'format_reconnect_message' from 'util'`

- [ ] **Step 3: Implement in `util.py`**

Add after `should_attempt_reconnect`:

```python
def format_reconnect_message(attempt_number, max_attempts):
    """Status-bar text shown while MainWindow._maybe_reconnect is retrying
    a dropped connection, e.g. "Connection dropped, reconnecting (2/5)...".
    """
    return f"Connection dropped, reconnecting ({attempt_number}/{max_attempts})..."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_util.py -v -k format_reconnect_message`
Expected: PASS (2 passed)

- [ ] **Step 5: Wire it into `MainWindow._maybe_reconnect`**

Add `format_reconnect_message` to the `from util import (...)` block (alphabetized, before `select_output_device_index`).

Replace radiotop_gui.py:1009-1017:

```python
        self._reconnect_attempts_remaining -= 1
        idx = self.current_idx
        generation = self._playback_generation
        self.statusBar().showMessage(
            f"Connection dropped, reconnecting "
            f"({self.reconnect_max_attempts - self._reconnect_attempts_remaining}/"
            f"{self.reconnect_max_attempts})...",
            4000,
        )
```

with:

```python
        self._reconnect_attempts_remaining -= 1
        idx = self.current_idx
        generation = self._playback_generation
        attempt_number = self.reconnect_max_attempts - self._reconnect_attempts_remaining
        self.statusBar().showMessage(
            format_reconnect_message(attempt_number, self.reconnect_max_attempts),
            4000,
        )
```

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add util.py radiotop_gui.py tests/test_util.py
git commit -m "refactor: extract format_reconnect_message into util.py"
```

---

### Task 4: `should_notify_immediately`

**Files:**
- Modify: `util.py` (add function)
- Modify: `radiotop_gui.py:776-783` (`MainWindow._schedule_track_notification`)
- Test: `tests/test_util.py`

**Interfaces:**
- Consumes: nothing from Tasks 1–3.
- Produces: `should_notify_immediately(artist: str | None, icon_cached: bool) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_util.py`:

```python
from util import should_notify_immediately


def test_should_notify_immediately_true_when_no_artist():
    assert should_notify_immediately(None, icon_cached=False) is True
    assert should_notify_immediately("", icon_cached=False) is True


def test_should_notify_immediately_true_when_icon_cached():
    assert should_notify_immediately("Some Artist", icon_cached=True) is True


def test_should_notify_immediately_false_when_artist_and_no_icon():
    assert should_notify_immediately("Some Artist", icon_cached=False) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_util.py -v -k should_notify_immediately`
Expected: FAIL — `ImportError: cannot import name 'should_notify_immediately' from 'util'`

- [ ] **Step 3: Implement in `util.py`**

Add after `format_reconnect_message`:

```python
def should_notify_immediately(artist, icon_cached):
    """Whether MainWindow._schedule_track_notification should show the
    "now playing" notification right away, versus holding it briefly so it
    can use the real artist photo once the async fetch finishes. True when
    there's no artist to look up at all, or the artist's photo is already
    cached from earlier this session."""
    return not artist or icon_cached
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_util.py -v -k should_notify_immediately`
Expected: PASS (3 passed)

- [ ] **Step 5: Wire it into `MainWindow._schedule_track_notification`**

Add `should_notify_immediately` to the `from util import (...)` block (alphabetized, after `should_attempt_reconnect`).

Replace radiotop_gui.py:776-783:

```python
    def _schedule_track_notification(self, artist, body):
        icon = self._icon_for_artist(artist) if artist else None
        if not artist or icon is not None:
            # No artist to look up, or we already have their photo cached
            # from earlier this session - show right away.
            self._pending_notification_artist = None
            self._show_notification("RadioTop - Now Playing", body, icon)
            return
```

with:

```python
    def _schedule_track_notification(self, artist, body):
        icon = self._icon_for_artist(artist) if artist else None
        if should_notify_immediately(artist, icon is not None):
            self._pending_notification_artist = None
            self._show_notification("RadioTop - Now Playing", body, icon)
            return
```

- [ ] **Step 6: Run full test suite**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add util.py radiotop_gui.py tests/test_util.py
git commit -m "refactor: extract should_notify_immediately into util.py"
```

---

### Task 5: Integration checks in `tests/test_main_window.py`

**Files:**
- Modify: `tests/test_main_window.py`
- Read (context only): `tests/conftest.py` for `MainWindowStub` / `main_window_stub` fixture shape.

**Interfaces:**
- Consumes: `MainWindow._refresh_output_devices`, `MainWindow._maybe_reconnect` (unbound, called against `main_window_stub` per the pattern already used throughout `tests/test_main_window.py`, e.g. `rt.MainWindow._guess_name(stub, url)`).

Add a couple of thin checks confirming `MainWindow` still calls through correctly — not re-testing the pure functions' logic (that's Tasks 1–4's job), just confirming the wiring didn't break. First inspect the `main_window_stub` fixture in `tests/conftest.py` to see what attributes it already provides (e.g. `auto_reconnect_enabled`, `current_idx`, `_reconnect_attempts_remaining`, `reconnect_max_attempts`) — reuse those rather than reinventing the stub.

- [ ] **Step 1: Read `tests/conftest.py` to confirm available stub attributes**

Run: `grep -n "reconnect\|device" tests/conftest.py`

If `auto_reconnect_enabled`, `current_idx`, `_reconnect_attempts_remaining`, `reconnect_max_attempts` aren't already set on the stub, note what's missing — add them to `MainWindowStub`'s relevant attribute list in `tests/conftest.py` following the existing pattern there (read the file first to match style exactly before editing).

- [ ] **Step 2: Write the test**

Append to `tests/test_main_window.py`:

```python
def test_maybe_reconnect_schedules_when_conditions_met(main_window_stub, qtbot, monkeypatch):
    stub = main_window_stub
    stub.auto_reconnect_enabled = True
    stub.current_idx = 0
    stub._reconnect_attempts_remaining = 3
    stub.reconnect_max_attempts = 3
    stub._playback_generation = 1
    scheduled = []
    monkeypatch.setattr(
        "radiotop_gui.QTimer.singleShot",
        lambda delay, fn: scheduled.append((delay, fn)),
    )
    rt.MainWindow._maybe_reconnect(stub)
    assert stub._reconnect_attempts_remaining == 2
    assert len(scheduled) == 1


def test_maybe_reconnect_does_nothing_when_disabled(main_window_stub, monkeypatch):
    stub = main_window_stub
    stub.auto_reconnect_enabled = False
    stub.current_idx = 0
    stub._reconnect_attempts_remaining = 3
    monkeypatch.setattr(
        "radiotop_gui.QTimer.singleShot",
        lambda delay, fn: (_ for _ in ()).throw(AssertionError("should not schedule")),
    )
    rt.MainWindow._maybe_reconnect(stub)
    assert stub._reconnect_attempts_remaining == 3
```

Check the top of `tests/test_main_window.py` for how `rt` is imported (e.g. `import radiotop_gui as rt`) and match that alias — read the file's existing imports before adding these tests to confirm the exact alias and any required fixtures (`qtbot` may not be needed if `QTimer.singleShot` is fully monkeypatched; drop the `qtbot` fixture parameter if unused to avoid an unused-fixture warning).

- [ ] **Step 3: Run the new tests**

Run: `pytest tests/test_main_window.py -v -k maybe_reconnect`
Expected: PASS (2 passed)

- [ ] **Step 4: Run full test suite**

Run: `pytest -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_main_window.py tests/conftest.py
git commit -m "test: add integration checks for MainWindow._maybe_reconnect wiring"
```

---

## Final Verification

- [ ] Run `pytest -v` one more time — full suite green.
- [ ] Run the app manually per CLAUDE.md ("For anything not covered by the test suite, verify by actually running the app"): `python3 radiotop_gui.py`, play a station, let it hit a network drop or toggle auto-reconnect off/on in Settings, switch output devices in the device dropdown, and confirm a track notification appears — behavior should be identical to before this refactor.
