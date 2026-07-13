# RadioTop
I needed a slim, non-intrusive, non-overbloated (in my opinion) player program for SUB/Wave Radio (https://getsubwave.com) - so i set Claude on it. 
This is the Result. For everyone to enjoy.

Caveat: This code has been created through Claude.ai

A simple, native-looking internet radio player.

Built with [PySide6](https://doc.qt.io/qtforpython/) (Qt for Python), so it automatically follows your system's Qt theme, colors, and icon set — Breeze on KDE Plasma, the native theme on Windows 10/11 — no extra styling code needed.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![PySide6](https://img.shields.io/badge/PySide6-Qt%20for%20Python-41cd52)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%2010%2F11-blue)

## Features

- 🎵 Play internet radio streams (Shoutcast/Icecast) with play / pause / stop controls
- 📻 Add, edit, search, and remove your own custom stations — remembered between runs
- 🏷️ Live "now playing" track title from the stream's ICY metadata
- 🔎 Automatic track lookup — genre, release year, and album via [MusicBrainz](https://musicbrainz.org/), with optional richer genre tags from [Last.fm](https://www.last.fm/api)
- 🖼️ Artist photos (Discogs → Wikipedia → Last.fm) and album cover art (Cover Art Archive)
- 🔔 Native desktop notifications when a new station or track starts playing (toggleable)
- 🔊 Output device selector, with automatic refresh when devices change
- 🗔 Minimizes to the system tray instead of closing; tray menu for quick play/pause/stop/quit
- 🎨 Zero custom theming — inherits your system's Qt palette and icons on both Linux and Windows

## Requirements

- Python 3.9+
- [PySide6](https://pypi.org/project/PySide6/)
- **Linux only:** a Qt6 multimedia backend (FFmpeg or GStreamer), e.g.:
  ```bash
  # Debian/Ubuntu/KDE neon
  sudo apt install qt6-multimedia-plugins
  # or, for GStreamer backends
  sudo apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
  ```
- **Windows:** nothing extra — Qt Multimedia uses the built-in Windows Media Foundation backend, no separate package to install.

## Installation

### Linux / Windows (running from source)

```bash
git clone https://github.com/example/radiotop.git
cd radiotop
pip install --user PySide6
```

### Windows 10/11

See **[INSTALL_WINDOWS.md](INSTALL_WINDOWS.md)** for the full walkthrough (prebuilt `.exe`, automated `install_windows.ps1` script, or manual setup). Quick version, using the automated script:

```powershell
git clone https://github.com/example/radiotop.git
cd radiotop
powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Desktop
```

This sets up an isolated Python environment, installs PySide6, and creates Start Menu / Desktop shortcuts — no manual `pip install` needed. To build a standalone `.exe` instead, see [Building the .exe](INSTALL_WINDOWS.md#building-the-exe-optional) in that guide.

## Usage

```bash
python3 radiotop_gui.py
# or, on Linux, after making it executable:
chmod +x radiotop_gui.py
./radiotop_gui.py
```

Use the **Stations** menu to pick a station to play, or choose **Manage Stations...** to search existing stations, edit them, or paste a stream URL to add and play a new one. Closing the main window minimizes RadioTop to the system tray — use the tray menu or **File → Quit** to actually exit.

## Optional API keys

RadioTop works out of the box using MusicBrainz (no key required). Two optional integrations add richer metadata and better artist photo coverage:

| Service | Adds | Get a key |
|---|---|---|
| **Last.fm** | Crowd-tagged genres, alongside MusicBrainz's release year | [last.fm/api/account/create](https://www.last.fm/api/account/create) |
| **Discogs** | Better artist photo coverage, tried before Wikipedia | [discogs.com/settings/developers](https://www.discogs.com/settings/developers) |

Configure both from the **Settings** menu — each dialog has a **Test** button to validate your key/token before saving. Leave either blank to disable it; RadioTop falls back gracefully.

## Desktop notifications

RadioTop shows a native desktop notification when a new station or track starts playing, using your system's own notification service (Plasma's notification daemon on KDE, the Action Center on Windows 10/11) — so it respects your existing notification settings for position and behavior. Toggle this on or off via **Settings → Show Desktop Notifications**.

## Configuration & data

RadioTop stores your custom stations, volume, output device, notification preference, and API keys/tokens via `QSettings` — an INI-style config file at `~/.config/radiotop/RadioTop.conf` on Linux, or the Registry (`HKEY_CURRENT_USER\Software\radiotop\RadioTop`) on Windows. No separate config file to manage by hand either way.

## Troubleshooting

- **Streams won't play (Linux)** — make sure a Qt6 multimedia backend is installed (see [Requirements](#requirements)).
- **Streams won't play (Windows)** — check that Windows Media Feature Pack components haven't been removed (rare, mainly affects "N" editions of Windows); installing the [Media Feature Pack](https://www.microsoft.com/en-us/software-download/mediafeaturepack) fixes this.
- **No track title / metadata** — not all stations send ICY metadata; some streams simply don't support it.
- **No genre / year / album found** — lookup depends on the track being in the MusicBrainz database and the station sending a clean `Artist - Title` string.
- **Firewall prompt on first run (Windows)** — RadioTop opens a small local-only proxy server on `127.0.0.1` to set a proper User-Agent when fetching streams; it's safe to allow.

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests run headless (`QT_QPA_PLATFORM=offscreen`, set automatically), so no display is required.

## Project layout

```
radiotop_gui.py       # the app
assets/
  radiotop.png         # runtime app/tray icon (cross-platform)
  radiotop.ico         # Windows .exe icon, used by radiotop.spec
radiotop.spec          # PyInstaller build spec for a standalone Windows .exe
install_windows.ps1    # automated Windows 10/11 install/uninstall script
INSTALL_WINDOWS.md      # detailed Windows install guide
tests/                 # pytest test suite (run headless, see "Running tests")
requirements-dev.txt   # test-only dependencies (pytest, pytest-qt)
```

## License

[MIT](LICENSE)
