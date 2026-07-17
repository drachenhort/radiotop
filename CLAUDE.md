# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RadioTop is a single-file PySide6 (Qt for Python) desktop internet radio player. Nearly the entire
application — UI, playback, metadata lookup, image fetching, settings — lives in `radiotop_gui.py`
(~2700 lines). There is no package structure and no linter/formatter config; treat the file as the
whole codebase (tests live separately under `tests/`, see Testing below).

## Running

```bash
pip install --user PySide6
python3 radiotop_gui.py
```

Linux additionally needs a Qt6 multimedia backend for playback to work (FFmpeg or GStreamer plugins,
e.g. `qt6-multimedia-plugins`). Windows needs nothing extra (uses Media Foundation).

There's no lint/format tooling and no CI checks that run on every push — the only CI job is the
Windows `.exe` build (see below). For anything not covered by the test suite, verify by actually
running the app.

## Testing

```bash
pip install -r requirements-dev.txt   # pytest, pytest-qt
pytest
```

Tests run against `QT_QPA_PLATFORM=offscreen` (set automatically by `tests/conftest.py`), so no
display is required — CI/headless-safe. `pytest.ini` pins `qt_api = pyside6` for pytest-qt.

Tests avoid instantiating the real `MainWindow` (it starts a live local stream proxy server, system
tray icon, and audio output — heavyweight and not what most logic tests need). Instead
`tests/conftest.py`'s `MainWindowStub` is a minimal real `QObject` carrying just the attributes a
given `MainWindow` method touches; unbound methods are called directly against it, e.g.
`rt.MainWindow._guess_name(stub, url)`. This works because Shiboken only requires `self` to be a
properly `__init__`-ed `QObject` when the method parents a `QAction`/`QMenu` to it — a plain
`MainWindow.__new__(MainWindow)` bypass does *not* satisfy that and raises
`RuntimeError: __init__ method of object's base class not called`.

Modal dialogs (`QMessageBox.warning/information`, `EditStationDialog.exec()`) are monkeypatched in
tests that exercise the code paths triggering them, since a real `.exec()` call would block
indefinitely waiting for interactive input.

For QThread subclasses (`TrackLookupThread` etc.), tests call `.run()` directly rather than
`.start()`, to execute the work synchronously on the test thread instead of racing a background
thread — with `urllib.request.urlopen` monkeypatched so nothing hits the network.

## Building the Windows executable

```bash
pip install pyinstaller
pyinstaller radiotop.spec
```

Must be run on Windows (PyInstaller doesn't cross-compile); this is also why
`.github/workflows/build-windows.yml` runs on `windows-latest`. That workflow triggers on pushes to
`main` that touch `radiotop_gui.py`, `radiotop.spec`, or `assets/**`, on manual dispatch, and on
GitHub Release publish (in which case the built `RadioTop.exe` is attached to the release).

## Architecture

Everything is in `radiotop_gui.py`, organized top-to-bottom as:

- **Module-level helpers** — `_normalize_station_url()` (auto-fills a missing port/`stream.mp3`
  filename on station URLs a user enters, since bare addresses often fail to connect),
  `_resource_path()` (resolves bundled assets both from source and from a PyInstaller-frozen exe via
  `sys._MEIPASS`), `_app_icon()`.
- **`StreamProxyServer` / `_StreamProxyHandler`** — a local-only (`127.0.0.1`) HTTP proxy started once
  for the app's lifetime. All station playback is routed through it rather than directly to the
  station URL, because QMediaPlayer's FFmpeg backend does its own networking via libavformat and
  can't have a custom `User-Agent` injected through any Qt API. The proxy is the only thing that
  actually talks to the remote station server.
- **Background `QThread` subclasses**, each doing one job and reporting back via Qt signals:
  - `IcyMetadataThread` — polls a stream briefly every 20s purely to read one ICY "now playing"
    metadata block, then disconnects, rather than holding a second full-bitrate connection open (to
    avoid inflating the station's listener count / bandwidth).
  - `_CancellableRequestThread` — shared base for the four lookup threads below. Wraps
    `urllib.request.urlopen()` so every in-flight response is tracked; `stop()` shuts down each
    tracked socket directly rather than just closing it, the same cooperative-shutdown trick
    `IcyMetadataThread.stop()` uses on its own single connection (see Notes below). Also provides
    `_fetch_json()` / `_fetch_bytes()` helpers used throughout the subclasses.
  - `TrackLookupThread` — resolves genre/year/album for a track title via MusicBrainz (no key
    needed), optionally overriding genre with Last.fm tags if a Last.fm API key is configured, with
    the iTunes Search API (also no key needed) queried as a fallback source for album/year/genre and
    for an artwork URL used by `AlbumArtThread`.
  - `ArtistImageThread` — artist photo, tried in order: Deezer (primary; no key needed) → Discogs (if
    a token is configured) → Wikipedia → Last.fm.
  - `AlbumArtThread` — cover art, tried in order: Deezer (primary; an artist/title track search, no
    key needed) → the Cover Art Archive, keyed by MusicBrainz release ID → the iTunes artwork URL
    (from `TrackLookupThread`).
  - `SimilarTracksThread` — a short "similar tracks" list for the `TrackInfoDialog`, sourced from
    Deezer (no key needed): resolves the current track to a Deezer artist ID via search, then uses
    that artist's Deezer "radio" (smart mix) as the pool, optionally widened with a few top tracks
    from related artists if `Settings > Widen Similar Tracks` is enabled.
- **Dialogs** (`QDialog` subclasses) — `TrackInfoDialog` (also shows the `SimilarTracksThread`
  results); `LastfmSettingsDialog` / `DiscogsSettingsDialog`, both built on the shared
  `_ApiCredentialDialog` base (key/token field + "Test" button that validates before saving, with
  subclasses only supplying window chrome and a `_check()` method); `EditStationDialog`;
  `StationListDialog`.
- **`MainWindow`** — the main window, system tray integration, and the glue that owns the media
  player, wires thread signals to UI updates, and manages a cache for each of the four lookup
  threads (`lookup_cache`, `artist_image_cache`, `album_art_cache`, `similar_tracks_cache`) so repeat
  lookups for the same track/artist/release don't re-hit the network.

## Persistence

All persistent state (custom stations, volume, output device, notification toggle, similar-tracks
widen toggle, Last.fm/Discogs credentials) goes through `QSettings(APP_ORG, APP_NAME)` — an INI file at
`~/.config/radiotop/RadioTop.conf` on Linux, or the registry
(`HKEY_CURRENT_USER\Software\radiotop\RadioTop`) on Windows. There is no separate config file format
to maintain; add new persisted fields as additional `QSettings` keys read/written directly where
they're used (see `MainWindow.__init__`, `_load_custom_stations` / `_save_custom_stations`).

## Notes on non-obvious behavior

- In `_StreamProxyHandler.do_GET`, the `url` query param must **not** be unquoted a second time —
  `parse_qs()` already percent-decodes it once; decoding again corrupts any percent-encoded byte in
  the original stream URL.
- `IcyMetadataThread.stop()` and `_CancellableRequestThread.stop()` (base for the four lookup
  threads) both shut down the underlying socket directly (`resp.fp.raw._sock.shutdown`) rather than
  just closing the response wrapper, because closing alone doesn't reliably interrupt a blocking read
  on a stalled connection from another thread.
- Station URLs are normalized (port + filename defaults) independently per-field — a URL missing only
  the port gets just the port added, and vice versa — see `_normalize_station_url()`.
