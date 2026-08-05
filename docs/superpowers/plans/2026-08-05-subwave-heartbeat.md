# SUB/WAVE Heartbeat Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a heartbeat dot in the UI that reflects whether `SubwaveNowPlayingThread` is
currently receiving updates, and auto-restart the thread if it goes stale for two consecutive
windows.

**Architecture:** A single-shot `QTimer` on `MainWindow`, restarted every time
`_on_subwave_now_playing` fires. First timeout (15s of silence) flips a dot label to "stale" and
logs a debug line. A second consecutive timeout (30s total silence) treats the thread as wedged
and force-restarts it via the existing `_start_subwave_thread`/`_stop_subwave_thread` pair.

**Tech Stack:** PySide6 (`QLabel`, `QTimer`), existing `SubwaveNowPlayingThread` in `threads.py`
(unmodified), pytest + pytest-qt for tests.

## Global Constraints

- SUB/WAVE stations only — no changes to ICY-metadata station handling.
- Stale threshold: 15000ms per window (spec: `docs/superpowers/specs/2026-08-05-subwave-heartbeat-design.md`).
- Restart threshold: two consecutive 15000ms windows with no update (30s total).
- No user-facing configuration for these thresholds — fixed constants.
- Single-file app convention: all production code changes go in `radiotop_gui.py` (per
  `CLAUDE.md`); `threads.py` is not touched by this feature.
- Tests follow existing conventions in `tests/conftest.py` / `tests/test_main_window.py`: call
  unbound `MainWindow` methods directly against `MainWindowStub` or a locally-built
  `SimpleNamespace`, no real Qt timers firing — handlers are invoked directly, never via a live
  15s wait.

---

### Task 1: Heartbeat dot widget + style helper

**Files:**
- Modify: `radiotop_gui.py` (near `self.subwave_detail_label` construction, ~line 233-237, and
  imports at top if `QHBoxLayout` isn't already imported)
- Test: `tests/test_main_window.py`

**Interfaces:**
- Produces: `MainWindow._set_subwave_heartbeat_dot(self, state)` where `state` is one of
  `"hidden"`, `"fresh"`, `"stale"`. Sets `self.subwave_heartbeat_dot.setText("●")` /
  `setText("")` and a color via `setStyleSheet`.
- Produces: `self.subwave_heartbeat_dot` — a `QLabel` widget, placed in a row next to
  `self.subwave_detail_label`.

Currently `self.subwave_detail_label` is added directly to the root `QVBoxLayout` (`root`) as its
own full-width row:

```python
self.subwave_detail_label = QLabel("")
self.subwave_detail_label.setWordWrap(True)
self.subwave_detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
self.subwave_detail_label.setStyleSheet("color: #888888; font-size: 10px;")
root.addWidget(self.subwave_detail_label)
```

- [ ] **Step 1: Write the failing test for `_set_subwave_heartbeat_dot`**

Add to `tests/test_main_window.py`, near the existing `# --- subwave now playing` section:

```python
# --------------------------------------------------- subwave heartbeat dot
def test_set_subwave_heartbeat_dot_hidden(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._set_subwave_heartbeat_dot(stub, "hidden")

    assert stub.subwave_heartbeat_dot.text() == ""


def test_set_subwave_heartbeat_dot_fresh(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._set_subwave_heartbeat_dot(stub, "fresh")

    assert stub.subwave_heartbeat_dot.text() == "●"


def test_set_subwave_heartbeat_dot_stale(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._set_subwave_heartbeat_dot(stub, "stale")

    assert stub.subwave_heartbeat_dot.text() == "●"
```

`_LabelStub` (in `tests/conftest.py`) already has `setText`/`text`/`setStyleSheet`, so no fixture
changes are needed — import it at the top of `tests/test_main_window.py` if not already imported
(check the existing `from conftest import ...` or equivalent import line at the top of the file
first; add `_LabelStub` to that import if missing).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_window.py -k test_set_subwave_heartbeat_dot -v`
Expected: FAIL with `AttributeError: type object 'MainWindow' has no attribute '_set_subwave_heartbeat_dot'`

- [ ] **Step 3: Add the widget and implement the helper**

In `radiotop_gui.py`, replace the `subwave_detail_label` block (~line 233-237) with a row that
also holds the dot:

```python
subwave_row = QHBoxLayout()
subwave_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
self.subwave_heartbeat_dot = QLabel("")
self.subwave_heartbeat_dot.setStyleSheet("font-size: 10px;")
subwave_row.addWidget(self.subwave_heartbeat_dot)
self.subwave_detail_label = QLabel("")
self.subwave_detail_label.setWordWrap(True)
self.subwave_detail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
self.subwave_detail_label.setStyleSheet("color: #888888; font-size: 10px;")
subwave_row.addWidget(self.subwave_detail_label)
root.addLayout(subwave_row)
```

Confirm `QHBoxLayout` is already imported from `PySide6.QtWidgets` at the top of the file (it is —
used for `info_row`/`vol_row` elsewhere); if not, add it to the existing `QtWidgets` import line.

Add the helper method near `_on_subwave_unavailable` (the other SUB/WAVE UI-state methods):

```python
_SUBWAVE_DOT_FRESH_COLOR = "#2ecc71"
_SUBWAVE_DOT_STALE_COLOR = "#888888"

def _set_subwave_heartbeat_dot(self, state):
    if state == "hidden":
        self.subwave_heartbeat_dot.setText("")
        return
    color = self._SUBWAVE_DOT_FRESH_COLOR if state == "fresh" else self._SUBWAVE_DOT_STALE_COLOR
    self.subwave_heartbeat_dot.setStyleSheet(f"color: {color}; font-size: 10px;")
    self.subwave_heartbeat_dot.setText("●")
```

Place `_SUBWAVE_DOT_FRESH_COLOR`/`_SUBWAVE_DOT_STALE_COLOR` as class attributes on `MainWindow`
(alongside other class-level constants, if any exist near the top of the class — otherwise define
them directly above `_set_subwave_heartbeat_dot`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_window.py -k test_set_subwave_heartbeat_dot -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add radiotop_gui.py tests/test_main_window.py
git commit -m "feat: add SUB/WAVE heartbeat dot widget"
```

---

### Task 2: Heartbeat timer lifecycle (start/reset/stop)

**Files:**
- Modify: `radiotop_gui.py`:
  - `MainWindow.__init__` (near where other `QTimer`-adjacent state is initialized, and near
    `self.subwave_thread = None` at line 159)
  - `_start_subwave_thread` (line 549)
  - `_stop_subwave_thread` (line 566)
  - `_on_subwave_unavailable` (line 597)
  - `_on_subwave_now_playing` (line 607)
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `MainWindow._set_subwave_heartbeat_dot(self, state)` from Task 1.
- Produces: `self._subwave_heartbeat_timer` (a `QTimer`, `setSingleShot(True)`), connected to
  `self._on_subwave_heartbeat_timeout` (implemented in Task 3, stubbed here as a no-op-safe
  connection target — Task 3 fills in the body).
- Produces: `self._subwave_heartbeat_missed` (int counter), reset to `0` whenever a fresh update
  arrives or the thread (re)starts/stops.

A fake timer stub is needed for tests, since we must not wait on a real 15s `QTimer` firing.
Add this to `tests/conftest.py`, next to `_LabelStub`:

```python
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
```

And add `self._subwave_heartbeat_timer = None` and `self._subwave_heartbeat_missed = 0` to
`MainWindowStub.__init__` in the same file, alongside the existing `self._subwave_detected = False`
line (~line 73).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main_window.py`:

```python
def test_start_subwave_thread_resets_heartbeat_state(main_window_stub, monkeypatch):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 2
    stub.subwave_thread = None
    stub.subwave_api_base = None
    stub._current_subwave_track = None
    stub.subwave_detail_label = _LabelStub()
    stub.next_track_label = _LabelStub()
    stub.show_label = _LabelStub()
    stub.like_btn = SimpleNamespace(setEnabled=lambda v: None, setText=lambda t: None)
    monkeypatch.setattr(rt, "SubwaveNowPlayingThread", lambda api_base: SimpleNamespace(
        now_playing_ready=SimpleNamespace(connect=lambda f: None),
        unavailable=SimpleNamespace(connect=lambda f: None),
        finished=SimpleNamespace(connect=lambda f: None),
        start=lambda: None,
    ))

    rt.MainWindow._start_subwave_thread(stub, "http://example.com:8000/stream.mp3")

    assert stub._subwave_heartbeat_missed == 0
    assert stub._subwave_heartbeat_timer.stop_calls == 1
    assert stub.subwave_heartbeat_dot.text() == ""


def test_on_subwave_now_playing_starts_and_resets_heartbeat_timer(main_window_stub):
    stub = main_window_stub
    _rig_for_subwave_now_playing(stub)
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1

    rt.MainWindow._on_subwave_now_playing(stub, _payload("Some Artist", "Some Track"))

    assert stub._subwave_heartbeat_missed == 0
    assert stub._subwave_heartbeat_timer.start_calls == [15000]
    assert stub.subwave_heartbeat_dot.text() == "●"


def test_stop_subwave_thread_stops_heartbeat_timer(main_window_stub):
    stub = main_window_stub
    stub.subwave_thread = None
    stub.subwave_api_base = None
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1
    stub.subwave_heartbeat_dot = _LabelStub()

    rt.MainWindow._stop_subwave_thread(stub)

    assert stub._subwave_heartbeat_timer.stop_calls == 1
    assert stub._subwave_heartbeat_missed == 0
    assert stub.subwave_heartbeat_dot.text() == ""


def test_on_subwave_unavailable_stops_heartbeat_timer(main_window_stub):
    stub = main_window_stub
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1
    stub.subwave_heartbeat_dot = _LabelStub()
    stub.subwave_detail_label = _LabelStub()
    stub.next_track_label = _LabelStub()
    stub.show_label = _LabelStub()
    stub.like_btn = SimpleNamespace(setEnabled=lambda v: None, setText=lambda t: None)

    rt.MainWindow._on_subwave_unavailable(stub)

    assert stub._subwave_heartbeat_timer.stop_calls == 1
    assert stub._subwave_heartbeat_missed == 0
    assert stub.subwave_heartbeat_dot.text() == ""
```

Import `_LabelStub` and `_TimerStub` from `conftest` at the top of `tests/test_main_window.py` if
not already available (check how `_LabelStub` is currently imported/used elsewhere in the file —
follow the same pattern for `_TimerStub`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_window.py -k "heartbeat" -v`
Expected: FAIL (missing `_subwave_heartbeat_timer` handling / `AttributeError`)

- [ ] **Step 3: Implement the timer lifecycle**

In `MainWindow.__init__`, near `self.subwave_thread = None` (line 159-163):

```python
self.subwave_thread = None
self.subwave_api_base = None
self._current_subwave_track = None
self._subwave_detected = False
self._subwave_request_threads = []
self._subwave_heartbeat_timer = QTimer(self)
self._subwave_heartbeat_timer.setSingleShot(True)
self._subwave_heartbeat_timer.timeout.connect(self._on_subwave_heartbeat_timeout)
self._subwave_heartbeat_missed = 0
```

In `_start_subwave_thread` (line 549), add heartbeat reset alongside the other reset lines:

```python
def _start_subwave_thread(self, url):
    self._stop_subwave_thread()
    self._current_subwave_track = None
    self._subwave_detected = False
    self.subwave_detail_label.setText("")
    self.next_track_label.setText("")
    self.show_label.setText("")
    self.like_btn.setEnabled(False)
    self.like_btn.setText("☆ Like")
    self._subwave_heartbeat_missed = 0
    self._set_subwave_heartbeat_dot("hidden")
    self.subwave_api_base = _subwave_api_base(url)
    ...
```

(`_stop_subwave_thread()` at the top already stops the timer per the change below, so no separate
`stop()` call is needed here — the assertion in Step 1's first test checks `stop_calls == 1`,
which comes from that nested `_stop_subwave_thread()` call.)

In `_stop_subwave_thread` (line 566), add timer stop + counter reset + dot hide right after the
`thread = self.subwave_thread` / `self.subwave_thread = None` lines, before the `if thread is
None: return`:

```python
def _stop_subwave_thread(self):
    thread = self.subwave_thread
    self.subwave_thread = None
    self.subwave_api_base = None
    self._subwave_heartbeat_timer.stop()
    self._subwave_heartbeat_missed = 0
    self._set_subwave_heartbeat_dot("hidden")
    if thread is None:
        return
    ...
```

In `_on_subwave_unavailable` (line 597), add the same three lines:

```python
def _on_subwave_unavailable(self):
    self.subwave_api_base = None
    self._current_subwave_track = None
    self._subwave_detected = False
    self.subwave_detail_label.setText("")
    self.next_track_label.setText("")
    self.show_label.setText("")
    self.like_btn.setEnabled(False)
    self.like_btn.setText("☆ Like")
    self._subwave_heartbeat_timer.stop()
    self._subwave_heartbeat_missed = 0
    self._set_subwave_heartbeat_dot("hidden")
```

In `_on_subwave_now_playing` (line 607), add heartbeat reset right after
`self._subwave_detected = True`:

```python
def _on_subwave_now_playing(self, payload):
    if self.current_idx is None:
        return
    self._subwave_detected = True
    self._subwave_heartbeat_missed = 0
    self._set_subwave_heartbeat_dot("fresh")
    self._subwave_heartbeat_timer.start(15000)
    self._update_status()
    ...
```

Add a placeholder `_on_subwave_heartbeat_timeout` method (Task 3 fills in the real body) so the
`__init__` connection and the tests from Task 1/2 don't break:

```python
def _on_subwave_heartbeat_timeout(self):
    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_window.py -k "heartbeat" -v`
Expected: PASS (4 tests from this task, plus the 3 from Task 1)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest`
Expected: PASS, no regressions in existing `subwave`/`_start_subwave_thread`/`_stop_subwave_thread`
tests.

- [ ] **Step 6: Commit**

```bash
git add radiotop_gui.py tests/conftest.py tests/test_main_window.py
git commit -m "feat: wire SUB/WAVE heartbeat timer lifecycle"
```

---

### Task 3: Two-stage watchdog (stale flip, then restart)

**Files:**
- Modify: `radiotop_gui.py` (`_on_subwave_heartbeat_timeout`, added as a placeholder in Task 2)
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `self._subwave_heartbeat_missed` (int), `self._subwave_heartbeat_timer` (real or
  `_TimerStub`), `self._set_subwave_heartbeat_dot` (Task 1), `self._start_subwave_thread` (Task 2),
  `self.current_idx`, `self.stations` (existing `MainWindow` state — a list of dicts with a
  `"url"` key, see line 735: `station = self.stations[self.current_idx]`).
- Produces: final `_on_subwave_heartbeat_timeout` behavior — first call marks stale and re-arms
  the timer for a second window; second consecutive call (i.e. `_subwave_heartbeat_missed`
  reaching 2) restarts the SUB/WAVE thread via `_start_subwave_thread`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main_window.py`:

```python
def test_heartbeat_timeout_first_miss_marks_stale_and_rearms(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 0
    stub.current_idx = 0
    stub.stations = [{"url": "http://example.com:8000/stream.mp3", "name": "Test"}]
    stub._start_subwave_thread_calls = []
    stub._start_subwave_thread = lambda url: stub._start_subwave_thread_calls.append(url)

    rt.MainWindow._on_subwave_heartbeat_timeout(stub)

    assert stub._subwave_heartbeat_missed == 1
    assert stub.subwave_heartbeat_dot.text() == "●"
    assert stub._subwave_heartbeat_timer.start_calls == [15000]
    assert stub._start_subwave_thread_calls == []


def test_heartbeat_timeout_second_miss_restarts_thread(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1  # already missed once
    stub.current_idx = 0
    stub.stations = [{"url": "http://example.com:8000/stream.mp3", "name": "Test"}]
    stub._start_subwave_thread_calls = []
    stub._start_subwave_thread = lambda url: stub._start_subwave_thread_calls.append(url)

    rt.MainWindow._on_subwave_heartbeat_timeout(stub)

    assert stub._start_subwave_thread_calls == ["http://example.com:8000/stream.mp3"]


def test_heartbeat_timeout_second_miss_no_current_station_does_not_crash(main_window_stub):
    stub = main_window_stub
    stub.subwave_heartbeat_dot = _LabelStub()
    stub._subwave_heartbeat_timer = _TimerStub()
    stub._subwave_heartbeat_missed = 1
    stub.current_idx = None
    stub.stations = []
    stub._start_subwave_thread_calls = []
    stub._start_subwave_thread = lambda url: stub._start_subwave_thread_calls.append(url)

    rt.MainWindow._on_subwave_heartbeat_timeout(stub)

    assert stub._start_subwave_thread_calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_window.py -k "heartbeat_timeout" -v`
Expected: FAIL (current placeholder body is `pass`, so `_subwave_heartbeat_missed` never
increments and no restart happens).

- [ ] **Step 3: Implement the watchdog**

Replace the Task 2 placeholder in `radiotop_gui.py`:

```python
def _on_subwave_heartbeat_timeout(self):
    self._subwave_heartbeat_missed += 1
    if self._subwave_heartbeat_missed == 1:
        self._set_subwave_heartbeat_dot("stale")
        logging.debug("SUB/WAVE heartbeat missed once, marking stale")
        self._subwave_heartbeat_timer.start(15000)
        return
    logging.debug("SUB/WAVE heartbeat missed twice, restarting thread")
    if self.current_idx is not None:
        self._start_subwave_thread(self.stations[self.current_idx]["url"])
```

Check whether `logging` is already imported at the top of `radiotop_gui.py` (it's used elsewhere
for background-thread diagnostics); if not, add `import logging` to the existing import block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_window.py -k "heartbeat" -v`
Expected: PASS (all heartbeat tests from Tasks 1-3)

- [ ] **Step 5: Run the full test suite**

Run: `pytest`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add radiotop_gui.py tests/test_main_window.py
git commit -m "feat: two-stage SUB/WAVE heartbeat watchdog (stale, then restart)"
```

---

### Task 4: Manual verification

**Files:** None (manual QA, no code changes)

- [ ] **Step 1: Run the app against a live SUB/WAVE station**

Run: `python3 radiotop_gui.py`, connect to a known SUB/WAVE station (one with a reachable
`/now-playing` + `/state` API — check `_subwave_api_base()` in `radiotop_gui.py` for how the API
base is derived from the station URL if unsure which of your configured stations qualifies).
Confirm the dot appears green next to the SUB/WAVE detail label once track info shows up.

- [ ] **Step 2: Simulate staleness**

Disconnect network access briefly (e.g. toggle Wi-Fi off, or block outbound traffic to the
station's host) for 15-35 seconds while a SUB/WAVE station is playing. Confirm the dot flips
grey around 15s, and if the outage extends past 30s, confirm (via terminal output, since
`logging.debug` calls need `logging.basicConfig(level=logging.DEBUG)` or `-v`/similar to be
visible — check how the app currently configures logging level, if at all) that the restart log
line appears and the dot goes back to fresh once connectivity returns.

- [ ] **Step 3: Confirm no regression on station switch**

Switch between a SUB/WAVE station and a non-SUB/WAVE station a few times. Confirm the dot hides
correctly (no stale leftover dot) when on a non-SUB/WAVE station.
