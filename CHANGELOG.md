# Changelog

All notable changes to RadioTop are documented in this file.

## [0.45] - 2026-08-05

- Fixed: `SubwaveNowPlayingThread` gave up polling a SUB/WAVE station's now-playing API for the
  rest of the session after just 2 consecutive failures, even once it had already succeeded -
  killing the show/song info and Like button on a brief network blip from the station, not just
  when a station genuinely wasn't running SUB/WAVE.
- Added: a two-stage heartbeat watchdog for SUB/WAVE polling - a missed beat marks it stale, a
  second consecutive miss restarts the polling thread outright, so a hung connection recovers
  without needing to switch stations.
- Changed: the heartbeat indicator no longer needs its own dot widget - it's now the color of the
  "(SUB/WAVE)" suffix already shown in the status line (green while polling, grey once a beat's
  been missed).
- Fixed: the genre line under the current track could wrap early and visually overlap the "Next:"
  line below it, because it was still sitting in a centering layout left over from before the dot
  widget existed.
- Changed: clicking the tray icon while the window is already visible now raises/activates it
  instead of hiding it.

## [0.44] - 2026-08-05

- Fixed: a stalled station stream could leave the app unresponsive enough to need a force-close.
  A background lookup thread (ICY metadata, SUB/WAVE, track/artist/album/similar-tracks) whose
  connection attempt genuinely timed out could get stuck spinning at full CPU forever instead of
  returning, because the cancellation logic added in 0.42 mistook that real timeout for its own
  polling interval elapsing (they're the same exception class as of Python 3.11+) and silently
  discarded it.
- Fixed: the local stream proxy had no way to cancel a stuck upstream connection, so Stop or
  switching stations while a stream was stalled didn't take effect until the proxy's own
  connect/read timeout (up to 15s) elapsed on its own.

## [0.43] - 2026-08-04

- Fixed: on SUB/WAVE stations, the displayed track title could occasionally get stuck on an old
  track even though SUB/WAVE's own dashboard already showed the current one - the title only ever
  came from ICY metadata embedded in the audio stream, which is polled less often and can miss an
  update. RadioTop now also uses SUB/WAVE's own now-playing API (already polled for genre/BPM) to
  keep the title current.

## [0.42] - 2026-08-04

- Fixed: switching stations, closing the track-info dialog, or quitting while a background
  metadata/artist-image/album-art/similar-tracks lookup was stuck connecting to a slow or
  unresponsive server could stall for up to the full request timeout (10-15s) instead of
  cancelling right away.

## [0.41] - 2026-08-04

- Fixed: removing a custom station could leave the wrong station selected (and enabled for
  removal) right after the delete, risking a second click deleting the wrong one.
- Fixed: removing an earlier custom station while an auto-reconnect retry was pending could,
  once the retry fired, start playing whatever station had shifted into that slot instead of
  silently no-opping.
- Fixed: a slow-arriving track lookup for a track you'd already skipped past (via a station
  switch, or a new track on the same station) could overwrite the currently displayed
  title/genre/album/year with stale info.
- Fixed: a 200 OK with an empty image response was treated as a successful artist photo/album
  art fetch instead of falling through to the next source, occasionally leaving a blank image
  where a working fallback source was available.
- Fixed: changing the Last.fm key or Discogs token in Settings didn't refresh the artist image
  already on screen - it now updates immediately instead of waiting for the next track change.
- Fixed: editing a station's stream URL without touching its (auto-guessed) name could
  permanently stop RadioTop from adopting that station's real broadcast name later.

## [0.40] - 2026-08-04

- Internal refactor: pulled four decision-logic branches (output-device selection, reconnect
  gating/messaging, notification timing) out of `MainWindow` into pure, directly-tested functions
  in `util.py`. No user-visible behavior change; closes test-coverage gaps.

## [0.39] - 2026-07-30

- Fixed RadioTop sometimes blocking system logout/reboot: closing the main window while the tray
  icon is visible shows a blocking "Quit or Minimize to Tray?" dialog, and a session manager's
  logout/reboot close request could trigger it with nobody able to click it. The app now quits
  cleanly (no dialog) in response to a session manager's close request or a SIGTERM/SIGINT.

## [0.38] - 2026-07-29

- Fixed the 0.37 release build itself: the Windows/macOS/Linux build workflows installed
  `PySide6 pyinstaller` directly instead of `requirements.txt`, so `certifi` was never actually
  bundled into the 0.37 executables and the "unable to get local issuer certificate" fix didn't
  take effect. Workflows now install from `requirements.txt`.

## [0.37] - 2026-07-29

- Fixed HTTPS requests (update checks, MusicBrainz/Last.fm/Discogs/iTunes/Deezer lookups) failing
  with "unable to get local issuer certificate" in the packaged executable on some Linux systems.
  The bundled OpenSSL carried the build machine's compiled-in default CA path, which doesn't exist
  on every machine the frozen exe runs on; every `urlopen()` call now uses an `SSLContext` pinned
  to `certifi`'s CA bundle instead.

## [0.36] - 2026-07-29

- Added a `.desktop` file (`assets/radiotop.desktop`) for KDE/GNOME application menu
  integration on Linux, bundled into `RadioTop-linux.tar.gz` alongside `radiotop.png` so
  users don't need to clone the repo to set up a menu entry. See the README's
  "Linux (prebuilt executable)" section for setup steps.

## [0.35] - 2026-07-29

- No user-facing changes. Split the single-file `radiotop_gui.py` into modules
  (`threads.py`, `stream_proxy.py`, `dialogs.py`, `util.py`, `enrichment_mixin.py`) for
  maintainability, with `radiotop_gui.py` now holding just `MainWindow` and the entry point.
- Added a CI workflow that runs the pytest suite on every push and pull request against `main`.
- Added a Linux build workflow (mirroring the existing Windows/macOS ones) that builds a
  standalone RadioTop executable via PyInstaller and attaches it to GitHub Releases.

## [0.34] - 2026-07-28

- Added BPM and Key to the Track Info window for SUB/WAVE stations, attributed to SUB/WAVE
  as their source since MusicBrainz/Last.fm/iTunes don't supply them.
- Added an "On Air: <show>" label for SUB/WAVE stations currently running a scheduled show.

## [0.33] - 2026-07-28

- Added update checking against GitHub releases: a once-a-day automatic check on startup
  plus **Help → Check for Updates**, showing a dialog with a link to the release page when
  a newer version is out. The app's version now also shows in the About dialog.

## [0.32] - 2026-07-28

- SUB/WAVE detection now shows only in the "Playing on - ..." status line, not duplicated
  on the station name label.

## [0.31] - 2026-07-28

- Dropped BPM/key from the SUB/WAVE now-playing detail line, keeping genre only.

## [0.30] - 2026-07-28

- Added SUB/WAVE station integration: richer now-playing metadata (genre/key/BPM) and a
  "Next" track label pulled from a SUB/WAVE station's own API, plus a Like button that
  nudges the station's DJ toward similar tracks.

## [0.29] - 2026-07-24

- Play now resumes the last-played station by default when nothing is selected yet in the
  current run, instead of always prompting the Stations list.
- Added automatically connecting to the last-played station on startup, toggleable via
  **Settings → Connect to Last Station on Startup**.
- Added automatic reconnection after a dropped stream connection, with the number of retry
  attempts and an on/off toggle configurable from **Settings**.
- Added a Screenshots section to the README.

## [0.28] - 2026-07-17

- Show artist/album names as captions under their images.

## [0.27] - 2026-07-16

- Fixed quote handling in Deezer search queries and bounded the lookup caches.

## [0.26] - 2026-07-16

- Made Deezer the primary source for artist and album pictures.
- Added a Deezer-backed "Similar Tracks" list to the Track Info dialog.
- Added Deezer as an artist/album image source.

## [0.25] - 2026-07-16

- Added a proper logo and tagline ("No bloat, just play.") to the About dialog.
- Closing the window now asks whether to quit or keep running in the system tray.
- Restricted the local stream proxy to http(s) URLs.
- Ran MusicBrainz/Last.fm/iTunes track lookups concurrently.
- Made lookup/artist-image/album-art thread shutdown graceful.
- Deduplicated User-Agent strings, request/JSON boilerplate, and settings dialogs.

## [0.24] - 2026-07-13

- Added the iTunes Search API as a fallback for track/album lookup and art.
- Added a GitHub Actions workflow to run the pytest suite on `windows-latest`.

## [v0.23] - 2026-07-13

- Added `CLAUDE.md` with project architecture and dev guidance.
- Added the pytest test suite for `radiotop_gui.py`.
- Replaced the Stations push-button with a Stations pulldown menu.
- Adopted the station name from the stream's `icy-name` header, with a tray notification and
  live status label update when it's adopted.
- Fixed an `IndexError` when removing a non-last custom station.

## [v0.22] - 2026-07-13

- Auto-fill a missing port/filename in station URLs (default port 7700, standard for SUB/Wave
  Radios), with a user notification when adjusted.
- Granted the release workflow write access so the built `.exe` can attach to GitHub Releases.

## [V0.21] / [V0.2] - 2026-07-13

- Added Windows install support: `install_windows.ps1`, `INSTALL_WINDOWS.md`, the PyInstaller
  spec, and app icon assets.
- Added the GitHub Actions workflow to build `RadioTop.exe` on `windows-latest`.
- Initial public release.
