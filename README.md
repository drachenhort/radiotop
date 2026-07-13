# RadioTop
Caveat: This code has been created through Claude.ai

A simple, native-looking internet radio player for KDE Plasma.

Built with [PySide6](https://doc.qt.io/qtforpython/) (Qt for Python), so it automatically follows your Breeze / system Qt theme, colors, and icon set — no extra styling code needed.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![PySide6](https://img.shields.io/badge/PySide6-Qt%20for%20Python-41cd52)
![Platform](https://img.shields.io/badge/platform-Linux%20%2F%20KDE%20Plasma-blue)

## Features

- 🎵 Play internet radio streams (Shoutcast/Icecast) with play / pause / stop controls
- 📻 Add, edit, search, and remove your own custom stations — remembered between runs
- 🏷️ Live "now playing" track title from the stream's ICY metadata
- 🔎 Automatic track lookup — genre, release year, and album via [MusicBrainz](https://musicbrainz.org/), with optional richer genre tags from [Last.fm](https://www.last.fm/api)
- 🖼️ Artist photos (Discogs → Wikipedia → Last.fm) and album cover art (Cover Art Archive)
- 🔔 Native desktop notifications when a new station or track starts playing (toggleable)
- 🔊 Output device selector, with automatic refresh when devices change
- 🗔 Minimizes to the system tray instead of closing; tray menu for quick play/pause/stop/quit
- 🎨 Zero custom theming — inherits your Breeze / system Qt palette and icons

## Requirements

- Linux with a Qt6 multimedia backend installed (FFmpeg or GStreamer), e.g.:
  ```bash
  # Debian/Ubuntu/KDE neon
  sudo apt install qt6-multimedia-plugins
  # or, for GStreamer backends
  sudo apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-bad
  ```
- Python 3.9+
- [PySide6](https://pypi.org/project/PySide6/)

## Installation

```bash
git clone https://github.com/example/radiotop.git
cd radiotop
pip install --user PySide6
```

## Usage

```bash
python3 radiotop_gui.py
# or, after making it executable:
chmod +x radiotop_gui.py
./radiotop_gui.py
```

Click **Stations...** to open the station list, where you can search existing stations or paste a stream URL to add and play a new one. Closing the main window minimizes RadioTop to the system tray — use the tray menu or **File → Quit** to actually exit.

## Optional API keys

RadioTop works out of the box using MusicBrainz (no key required). Two optional integrations add richer metadata and better artist photo coverage:

| Service | Adds | Get a key |
|---|---|---|
| **Last.fm** | Crowd-tagged genres, alongside MusicBrainz's release year | [last.fm/api/account/create](https://www.last.fm/api/account/create) |
| **Discogs** | Better artist photo coverage, tried before Wikipedia | [discogs.com/settings/developers](https://www.discogs.com/settings/developers) |

Configure both from the **Settings** menu — each dialog has a **Test** button to validate your key/token before saving. Leave either blank to disable it; RadioTop falls back gracefully.

## Desktop notifications

RadioTop shows a native KDE notification when a new station or track starts playing, using your system's own notification service (so it respects your Plasma notification settings for position and behavior). Toggle this on or off via **Settings → Show Desktop Notifications**.

## Configuration & data

RadioTop stores your custom stations, volume, output device, notification preference, and API keys/tokens via `QSettings` (typically `~/.config/radiotop/RadioTop.conf` on Linux). No separate config file to manage by hand.

## Troubleshooting

- **Streams won't play** — make sure a Qt6 multimedia backend is installed (see [Requirements](#requirements)).
- **No track title / metadata** — not all stations send ICY metadata; some streams simply don't support it.
- **No genre / year / album found** — lookup depends on the track being in the MusicBrainz database and the station sending a clean `Artist - Title` string.

## License

[MIT](LICENSE)
