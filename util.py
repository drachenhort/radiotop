"""Small standalone helpers shared across RadioTop's modules.

Split out of radiotop_gui.py as part of breaking up that file into
modules (see CLAUDE.md for the overall plan): these have no dependency
on each other's modules, so both radiotop_gui.py and dialogs.py import
from here rather than from one another, avoiding a circular import.
"""

import json
import os
import sys
import urllib.request
from urllib.parse import urlparse, urlunparse

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle

from threads import _SSL_CONTEXT


def _fetch_json(req, timeout=10):
    """Request -> urlopen -> JSON-decode, the exact sequence repeated by
    nearly every network call in this file. Passes threads._SSL_CONTEXT
    (pinned to certifi's CA bundle) for the same reason every urlopen()
    call in threads.py does - see CLAUDE.md's "Notes on non-obvious
    behavior"."""
    resp = urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT)
    return json.loads(resp.read().decode("utf-8"))


# Many Shoutcast/Icecast stations only respond correctly if the stream
# address includes an explicit port and a mountpoint/filename - a bare
# "http://host/" often just hangs or errors. Port 7700 is the standard
# port for SUB/Wave Radios stations; used here by _normalize_station_url()
# below to fill in whichever piece a user-entered address is missing.
DEFAULT_STREAM_PORT = 7700
DEFAULT_STREAM_FILENAME = "stream.mp3"


def _normalize_station_url(url):
    """If a station URL is missing a port and/or doesn't reference
    "stream.mp3" anywhere, fill in the default for whichever piece is
    missing (port 7700 - the standard port for SUB/Wave Radios stations -
    and/or the "stream.mp3" filename), since an address lacking both
    often fails to connect. Each piece is checked independently - a URL
    with a port but no filename only gets the filename added, and vice
    versa.

    Returns (possibly-adjusted url, was_adjusted)."""
    parsed = urlparse(url)
    adjusted = False

    netloc = parsed.netloc
    if parsed.port is None:
        userinfo = ""
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            userinfo += "@"
        netloc = f"{userinfo}{parsed.hostname or ''}:{DEFAULT_STREAM_PORT}"
        adjusted = True

    path = parsed.path
    if DEFAULT_STREAM_FILENAME not in path.lower():
        path = path.rstrip("/") + f"/{DEFAULT_STREAM_FILENAME}"
        adjusted = True

    if not adjusted:
        return url, False

    new_url = urlunparse((parsed.scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))
    return new_url, True


def _subwave_api_base(stream_url):
    """A SUB/WAVE station's HTTP API lives on the same origin as its stream
    URL, under /api (the bundled-Caddy production deploy puts the stream,
    web UI, and API behind one origin). Stations that aren't SUB/WAVE simply
    won't answer under that path - SubwaveNowPlayingThread treats that as
    "unavailable" and gives up quietly rather than erroring."""
    parsed = urlparse(stream_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/api", "", "", ""))


def _resource_path(*parts):
    """Resolve a path to a bundled resource (e.g. an icon), working both
    when running from source and when frozen into a standalone executable
    (e.g. via PyInstaller, which unpacks bundled data files to a temp
    directory exposed as sys._MEIPASS at runtime)."""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, *parts)


def _app_icon():
    """The RadioTop app icon, used for the window and system tray.

    Prefers the bundled assets/radiotop.png icon so RadioTop looks the
    same everywhere. On Linux, if that file isn't present for some reason,
    it falls back to the freedesktop icon theme (KDE/Breeze, GNOME, etc.)
    - a lookup that's a no-op on Windows, where QIcon.fromTheme() simply
    finds nothing and the standard Qt icon below is used instead."""
    icon_path = _resource_path("assets", "radiotop.png")
    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
        if not icon.isNull():
            return icon
    fallback = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume)
    return QIcon.fromTheme("audio-x-generic", fallback)
