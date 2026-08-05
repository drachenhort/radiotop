# SUB/WAVE heartbeat status — design

## Problem

`SubwaveNowPlayingThread` polls a SUB/WAVE station's `/now-playing` and `/state` endpoints every
5s and self-heals from transient failures (see commit `2d1151f`, "Fix SUB/WAVE now-playing thread
giving up permanently on transient failures"). But nothing in the UI tells the user whether that
polling is currently succeeding — if the connection goes stale, `subwave_detail_label` just keeps
showing the last-known genre/track text indefinitely, with no indication it's out of date.

## Scope

SUB/WAVE stations only. Regular ICY-metadata stations are out of scope for now.

## Design

### Visual: heartbeat dot

- New `QLabel("●")` placed next to `subwave_detail_label`, colored via stylesheet:
  - **hidden** — no SUB/WAVE station detected (mirrors current `subwave_detail_label` empty state)
  - **green** — an update was received within the last 15s
  - **grey** — no update received in over 15s (stale)

### Timing: restart-on-heartbeat timer

- `MainWindow` owns one single-shot `QTimer`, `_subwave_heartbeat_timer`, interval 15000ms.
- Restarted (`.start(15000)`) every time `_on_subwave_now_playing` fires.
- On timeout (no update arrived in time): dot flips to stale/grey.
- Stopped/reset alongside the existing `_stop_subwave_thread()` cleanup paths (station switch,
  SUB/WAVE becomes unavailable, app quit) so it doesn't fire after the thread is gone.

**Known false-positive risk:** each poll cycle does two sequential HTTP fetches
(`now-playing` + `state`), each with a 10s timeout (`_CancellableRequestThread._fetch_json`
default). Worst case, one legitimate poll cycle can take 20s+ under a slow-but-fine connection,
which would flash the dot stale before the next successful update arrives. Accepted as-is: the
dot is advisory and self-heals on the next successful poll, and a genuinely slow SUB/WAVE API is
arguably worth flagging as "stale" to the user anyway.

### Watchdog: two-stage reaction

1. **At 15s stale (first `_subwave_heartbeat_timer` timeout):** visual only — dot flips grey, plus
   a debug log line. No thread action, since `SubwaveNowPlayingThread` already retries every 5s
   internally once it has ever succeeded.
2. **At 30s stale (a second consecutive 15s timeout with no update in between):** treat this as
   evidence the thread may be wedged rather than just experiencing normal transient failures, and
   force a restart via the existing `_start_subwave_thread(url)` / `_stop_subwave_thread()` pair.

Implementation-wise, stage 2 is just: on each heartbeat timeout, check a `_subwave_stale_since`
flag/counter — first timeout sets it and restarts the 15s timer again; second consecutive timeout
(no `_on_subwave_now_playing` in between) triggers the thread restart and resets the counter.

### Non-goals

- No heartbeat/staleness handling for ICY-based stations.
- No user-configurable thresholds — 15s/30s are fixed constants alongside
  `SubwaveNowPlayingThread.POLL_INTERVAL`.

## Testing

- Unit test the heartbeat state transitions directly against `MainWindowStub` (per existing test
  conventions in `tests/conftest.py`): simulate `_on_subwave_now_playing` calls and timer
  timeouts, assert dot state and whether a restart was triggered, without a real Qt event loop
  timer firing (use `QTimer` methods directly / call the timeout handler synchronously as tests
  already do for other thread callbacks).
- No new network code is introduced — existing lookup-thread test patterns (monkeypatched
  `urllib.request.urlopen`) don't apply here.
