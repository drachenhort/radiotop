# Changelog

All notable changes to RadioTop are documented in this file.

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
