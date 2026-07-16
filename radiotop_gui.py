#!/usr/bin/env python3
"""
RadioTop - a simple, native-looking internet radio player.

Built with PySide6 (Qt for Python), so it automatically follows your
system's Qt theme, colors, and icon set (Breeze on KDE Plasma, the native
theme on Windows 10/11, etc.) - no extra styling code needed. Runs on
Linux (KDE Plasma and other desktops) as well as Windows 10/11.

Requires:
    pip install --user PySide6

Run:
    python3 radiotop_gui.py
    # or, on Linux:  chmod +x radiotop_gui.py && ./radiotop_gui.py

Notes:
- Playback uses Qt Multimedia (QMediaPlayer), which uses FFmpeg or
  GStreamer on Linux and Media Foundation on Windows - no extra backend
  install is needed on Windows. On Linux, if streams don't play, make
  sure the relevant Qt6 multimedia backend package is installed
  (e.g. `qt6-multimedia-plugins`, or GStreamer's `good`/`bad` plugin sets).
- Track title comes from the stream's ICY metadata. Genre, year, and album
  are then looked up via the MusicBrainz API (the open metadata database
  ListenBrainz is built on), with the iTunes Search API as a no-key fallback
  - matching depends on the track being findable in one of those and the
  station sending a clean "Artist - Title" string.
- Custom stations you add are remembered between runs (via QSettings,
  which uses an INI-style config file on Linux and the Registry on
  Windows).
- Closing the window prompts to either quit RadioTop or keep it running
  in the system tray; the tray menu or File > Quit always exits directly
  without prompting.
"""

import json
import os
import re
import socket
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

from PySide6.QtCore import Qt, QUrl, Signal, QThread, QTimer
from PySide6.QtGui import QAction, QActionGroup, QFont, QIcon, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSettings

APP_ORG = "radiotop"
APP_NAME = "RadioTop"

DEFAULT_STATIONS = []

STATUS_COLORS = {
    "Playing": "#3daee9",     # Breeze highlight blue
    "Buffering...": "#f67400",
    "Paused": "#f67400",
    "Stopped": "#888888",
    "Error": "#da4453",
}


TITLE_RE = re.compile(rb"StreamTitle='([^']*)';")

RADIOTOP_USER_AGENT = "RadioTop/1.0 ( https://github.com/example/radiotop )"


def _fetch_json(req, timeout=10):
    """Request -> urlopen -> JSON-decode, the exact sequence repeated by
    nearly every network call in this file."""
    resp = urllib.request.urlopen(req, timeout=timeout)
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


class _StreamProxyHandler(BaseHTTPRequestHandler):
    """Fetches the real station URL with our own User-Agent and relays the
    raw audio bytes to the local client. QMediaPlayer's FFmpeg backend
    does its own networking directly via libavformat - it never goes
    through any Qt API where a custom header could be injected, and by
    default identifies itself to the remote server as "Lavf" (FFmpeg's
    generic default). Routing playback through this local proxy means
    the only outbound connection to the actual radio server is the one
    this proxy makes itself, with a header we control."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # silence default per-request stderr logging

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        target = query.get("url", [None])[0]
        if not target:
            self.send_error(400, "Missing url parameter")
            return
        # target is already fully percent-decoded by parse_qs() above -
        # do NOT unquote() it again here, or any percent-encoded byte in
        # the original stream URL (e.g. %20, %2B) gets decoded twice and
        # corrupted before being sent upstream.

        # Restrict to http(s) - urlopen() also accepts file://, ftp://, and
        # data: URLs, and since this server listens on 127.0.0.1, any local
        # process (or a webpage's fetch()/<img> to this port) could
        # otherwise use it to read local files or reach internal-network
        # addresses this proxy was never meant to touch.
        if urlparse(target).scheme not in ("http", "https"):
            self.send_error(400, "Unsupported url scheme")
            return

        try:
            req = urllib.request.Request(target, headers={"User-Agent": RADIOTOP_USER_AGENT})
            upstream = urllib.request.urlopen(req, timeout=15)
        except Exception:
            try:
                self.send_error(502, "Could not reach stream")
            except Exception:
                pass
            return

        with upstream:
            try:
                content_type = upstream.headers.get("Content-Type", "audio/mpeg")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # local client (QMediaPlayer) disconnected - normal on stop/switch


class StreamProxyServer:
    """A local-only HTTP server (127.0.0.1) that proxies station streams
    through RADIOTOP_USER_AGENT. One instance is started for the life of
    the app and reused for every station played."""

    def __init__(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StreamProxyHandler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def local_url(self, original_url):
        return f"http://127.0.0.1:{self.port}/stream?url={quote(original_url, safe='')}"

    def shutdown(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass


class IcyMetadataThread(QThread):
    """Periodically opens a brief connection to a Shoutcast/Icecast stream
    purely to read one ICY metadata block (the 'now playing' song title),
    then disconnects - rather than holding a second full-bitrate stream
    open for the whole session. Qt Multimedia doesn't reliably expose ICY
    title updates across backends, so this is the fallback, but a
    permanently-open duplicate connection shows up as a second real
    listener on the server (double bandwidth, inflated listener count),
    which a brief poll avoids: the connection is only alive for about
    one metadata interval's worth of audio (typically a few KB) every
    POLL_INTERVAL seconds, not continuously."""

    title_changed = Signal(str)
    station_name_ready = Signal(str)
    unsupported = Signal()

    POLL_INTERVAL = 20  # seconds between metadata polls

    def __init__(self, url):
        super().__init__()
        self.url = url
        self._stop_event = threading.Event()
        self._resp = None
        self._resp_lock = threading.Lock()
        self._station_name_emitted = False

    def run(self):
        last_title = None
        while not self._stop_event.is_set():
            title = self._poll_once()
            if title is None:
                return  # stream doesn't support ICY metadata, or a hard error - give up
            if title and title != last_title:
                last_title = title
                self.title_changed.emit(title)
            self._stop_event.wait(self.POLL_INTERVAL)

    def _poll_once(self):
        """Connects, reads exactly one metadata block, disconnects.
        Returns the title string (possibly empty if no track info in this
        block), or None if metadata isn't supported / a fatal error
        occurred (in which case polling should stop entirely)."""
        headers = {"Icy-MetaData": "1", "User-Agent": RADIOTOP_USER_AGENT}
        req = urllib.request.Request(self.url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=15)
        except Exception:
            return ""  # transient failure - the outer loop will retry next interval

        with self._resp_lock:
            self._resp = resp
        try:
            if not self._station_name_emitted:
                # icy-name is the station's own broadcast name, sent as a
                # plain response header (unlike the title, which is embedded
                # in the audio stream itself) - available on the very first
                # successful connection, so only worth checking once per
                # thread rather than every poll.
                icy_name = (resp.headers.get("icy-name") or "").strip()
                self._station_name_emitted = True
                if icy_name:
                    self.station_name_ready.emit(icy_name)

            metaint_raw = resp.headers.get("icy-metaint")
            if not metaint_raw:
                self.unsupported.emit()
                return None
            try:
                metaint = int(metaint_raw)
            except ValueError:
                return None

            to_read = metaint
            while to_read > 0 and not self._stop_event.is_set():
                chunk = resp.read(min(4096, to_read))
                if not chunk:
                    return ""
                to_read -= len(chunk)
            if self._stop_event.is_set():
                return ""

            length_byte = resp.read(1)
            if not length_byte:
                return ""
            meta_len = length_byte[0] * 16
            if meta_len == 0:
                return ""
            meta = resp.read(meta_len)
            match = TITLE_RE.search(meta)
            if match:
                return match.group(1).decode("utf-8", errors="replace").strip()
            return ""
        except Exception:
            return ""
        finally:
            with self._resp_lock:
                self._resp = None
            try:
                resp.close()
            except Exception:
                pass

    def stop(self):
        self._stop_event.set()
        with self._resp_lock:
            resp = self._resp
        if resp is not None:
            # Closing the high-level response wrapper does not reliably
            # interrupt a blocking socket read happening on another
            # thread - shut down the underlying socket directly so a
            # poll stuck mid-read (e.g. a stalled/slow server) is forced
            # to return immediately instead of waiting out the timeout.
            try:
                resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                resp.close()
            except Exception:
                pass


class _CancellableRequestThread(QThread):
    """QThread base for threads that make one or more blocking urlopen()
    calls, possibly concurrently (e.g. from a ThreadPoolExecutor within
    run()). Provides the same cooperative-shutdown mechanism as
    IcyMetadataThread.stop(): closing the response wrapper alone doesn't
    reliably interrupt a blocking read happening on another thread, so
    stop() shuts down the underlying socket of every open response
    directly. This lets callers wait briefly for a graceful exit instead
    of reaching for QThread.terminate(), which can kill the thread
    mid-syscall and leave a socket or lock in a bad state."""

    def __init__(self):
        super().__init__()
        self._stop_event = threading.Event()
        self._open_resps = set()
        self._resp_lock = threading.Lock()

    def _urlopen(self, req, timeout=10):
        """Drop-in replacement for urllib.request.urlopen() that registers
        the response so stop() can interrupt it, and returns None instead
        of opening the connection at all if stop() was already called."""
        if self._stop_event.is_set():
            return None
        resp = urllib.request.urlopen(req, timeout=timeout)
        with self._resp_lock:
            if self._stop_event.is_set():
                resp.close()
                return None
            self._open_resps.add(resp)
        return resp

    def _release(self, resp):
        with self._resp_lock:
            self._open_resps.discard(resp)

    def _fetch_json(self, req, timeout=10):
        """_urlopen() + read + JSON-decode + release, bundled since this
        exact sequence repeats across nearly every network call below.
        Returns None if stop() was already called."""
        resp = self._urlopen(req, timeout=timeout)
        if resp is None:
            return None
        try:
            return json.loads(resp.read().decode("utf-8"))
        finally:
            self._release(resp)

    def _fetch_bytes(self, req, timeout=10):
        """Same as _fetch_json(), but for raw responses (image downloads)."""
        resp = self._urlopen(req, timeout=timeout)
        if resp is None:
            return None
        try:
            return resp.read()
        finally:
            self._release(resp)

    def stop(self):
        self._stop_event.set()
        with self._resp_lock:
            resps = list(self._open_resps)
        for resp in resps:
            try:
                resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                resp.close()
            except Exception:
                pass


class TrackLookupThread(_CancellableRequestThread):
    """Looks up genre / release year / album for a 'now playing' title.

    Release year and album come from MusicBrainz (the open metadata
    database ListenBrainz itself is built on) - no API key required.

    Genre, when a Last.fm API key is configured, comes from Last.fm's
    community tags instead, since they tend to be more descriptive /
    familiar for genre than MusicBrainz's own genre field. Without a
    Last.fm key, genre falls back to MusicBrainz's genre/tag data, and
    the app works exactly as before."""

    result_ready = Signal(dict)

    LASTFM_ENDPOINT = "https://ws.audioscrobbler.com/2.0/"
    ITUNES_ENDPOINT = "https://itunes.apple.com/search"

    def __init__(self, raw_title, lastfm_api_key=""):
        super().__init__()
        self.raw_title = raw_title
        self.lastfm_api_key = lastfm_api_key

    @staticmethod
    def _split_artist_title(raw):
        for sep in (" - ", " \u2013 ", " \u2014 "):
            if sep in raw:
                artist, title = raw.split(sep, 1)
                return artist.strip(), title.strip()
        return "", raw.strip()

    def _query_musicbrainz(self, artist, title):
        if artist:
            query = f'recording:"{title}" AND artist:"{artist}"'
        else:
            query = f'recording:"{title}"'
        url = "https://musicbrainz.org/ws/2/recording/?" + urlencode({
            "query": query,
            "fmt": "json",
            "limit": 1,
            "inc": "genres+tags",
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = self._fetch_json(req)
            if data is None:
                return None
        except Exception:
            return None

        recordings = data.get("recordings") or []
        if not recordings:
            return None

        rec = recordings[0]
        artist_credit = rec.get("artist-credit") or []
        artist_name = "".join(
            (ac.get("name", "") + ac.get("joinphrase", "")) for ac in artist_credit
        ).strip() or artist

        releases = rec.get("releases") or []
        album = releases[0].get("title", "") if releases else ""
        release_mbid = releases[0].get("id", "") if releases else ""
        years = sorted({
            rel["date"][:4] for rel in releases
            if rel.get("date") and len(rel["date"]) >= 4 and rel["date"][:4].isdigit()
        })
        year = years[0] if years else ""

        genres = rec.get("genres") or []
        tags = rec.get("tags") or []
        genre = ""
        if genres:
            genre = genres[0].get("name", "").title()
        elif tags:
            top_tag = sorted(tags, key=lambda t: t.get("count", 0), reverse=True)[0]
            genre = top_tag.get("name", "").title()

        return {
            "artist": artist_name,
            "title": rec.get("title", title),
            "album": album,
            "genre": genre,
            "year": year,
            "release_mbid": release_mbid,
        }

    def _query_lastfm(self, artist, title):
        if not self.lastfm_api_key:
            return None, None
        if not artist:
            return None, "No artist detected in stream title (station didn't send 'Artist - Title')"

        url = self.LASTFM_ENDPOINT + "?" + urlencode({
            "method": "track.getInfo",
            "api_key": self.lastfm_api_key,
            "artist": artist,
            "track": title,
            "format": "json",
            "autocorrect": 1,
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = self._fetch_json(req)
            if data is None:
                return None, None
        except urllib.error.HTTPError as e:
            return None, f"Last.fm HTTP error {e.code}"
        except Exception as e:
            return None, f"Last.fm request failed: {e}"

        if data.get("error"):
            return None, data.get("message", f"Last.fm error {data.get('error')}")

        track = data.get("track")
        if not track:
            return None, "Track not found on Last.fm"

        genre = ""
        toptags = (track.get("toptags") or {}).get("tag") or []
        if toptags:
            genre = toptags[0].get("name", "").title()

        album = (track.get("album") or {}).get("title", "")
        return {"genre": genre, "album": album}, None

    def _query_itunes(self, artist, title):
        term = f"{artist} {title}".strip() if artist else title
        url = self.ITUNES_ENDPOINT + "?" + urlencode({
            "term": term,
            "media": "music",
            "entity": "song",
            "limit": 1,
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = self._fetch_json(req)
            if data is None:
                return None
        except Exception:
            return None

        results = data.get("results") or []
        if not results:
            return None

        track = results[0]
        release_date = track.get("releaseDate", "")
        year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""

        # iTunes artwork URLs point at a small thumbnail by default (e.g.
        # ".../100x100bb.jpg") - request a larger size instead.
        artwork_url = track.get("artworkUrl100", "")
        if artwork_url:
            artwork_url = artwork_url.replace("100x100bb", "600x600bb")

        return {
            "artist": track.get("artistName", "") or artist,
            "title": track.get("trackName", "") or title,
            "album": track.get("collectionName", ""),
            "genre": track.get("primaryGenreName", ""),
            "year": year,
            "artwork_url": artwork_url,
        }

    def run(self):
        artist, title = self._split_artist_title(self.raw_title)
        if not title:
            self.result_ready.emit({"raw_title": self.raw_title, "found": False})
            return

        # Run the three lookups concurrently rather than one after another -
        # each is an independent blocking request with its own timeout, so
        # doing them in sequence could multiply the worst-case wait (e.g. a
        # slow/unreachable MusicBrainz) by three before the UI sees anything.
        with ThreadPoolExecutor(max_workers=3) as pool:
            mb_future = pool.submit(self._query_musicbrainz, artist, title)
            lfm_future = pool.submit(self._query_lastfm, artist, title)
            itunes_future = pool.submit(self._query_itunes, artist, title)
            mb = mb_future.result()
            lfm, lfm_error = lfm_future.result()
            itunes = itunes_future.result()

        if self._stop_event.is_set():
            return

        if mb is None and lfm is None and itunes is None:
            self.result_ready.emit({
                "raw_title": self.raw_title,
                "found": False,
                "lastfm_error": lfm_error,
            })
            return

        sources = []
        if mb:
            sources.append("MusicBrainz")
        if lfm:
            sources.append("Last.fm")
        if itunes:
            sources.append("iTunes")

        genre = (
            (lfm.get("genre") if lfm else "")
            or (mb.get("genre") if mb else "")
            or (itunes.get("genre") if itunes else "")
        )
        album = (
            (mb.get("album") if mb else "")
            or (lfm.get("album") if lfm else "")
            or (itunes.get("album") if itunes else "")
        )
        year = (mb.get("year") if mb else "") or (itunes.get("year") if itunes else "")

        self.result_ready.emit({
            "raw_title": self.raw_title,
            "found": True,
            "artist": (mb.get("artist") if mb else "") or (itunes.get("artist") if itunes else "") or artist,
            "title": (mb.get("title") if mb else "") or (itunes.get("title") if itunes else "") or title,
            "album": album,
            "genre": genre,
            "lastfm_error": lfm_error,
            "year": year,
            "release_mbid": (mb.get("release_mbid") if mb else "") or "",
            "itunes_artwork_url": (itunes.get("artwork_url") if itunes else "") or "",
            "sources": sources,
        })


class ArtistImageThread(_CancellableRequestThread):
    """Fetches a picture of the current artist/band. Tries sources in order
    of typical photo quality/coverage: Discogs first (if a token is
    configured), then Deezer, then Wikipedia, then Last.fm as a last resort.

    Note: Last.fm deprecated real photos in their API some time ago -
    artist.getInfo now returns a generic gray placeholder image for
    almost every artist rather than an actual picture. That placeholder
    is detected and skipped so it doesn't get displayed as if it were
    real artwork."""

    image_ready = Signal(bytes)
    not_found = Signal()

    LASTFM_ENDPOINT = "https://ws.audioscrobbler.com/2.0/"
    DISCOGS_ENDPOINT = "https://api.discogs.com"
    DEEZER_ENDPOINT = "https://api.deezer.com"
    # Hash Last.fm uses for its "no image available" placeholder graphic.
    LASTFM_PLACEHOLDER_HASH = "2a96cbd8b46e442fc41c2b86b821562f"
    # MD5-of-empty-string hash Deezer embeds in its "no photo" placeholder URL.
    DEEZER_PLACEHOLDER_HASH = "d41d8cd98f00b204e9800998ecf8427e"
    LASTFM_SIZE_RANK = {"small": 0, "medium": 1, "large": 2, "extralarge": 3, "mega": 4}

    def __init__(self, artist_name, lastfm_api_key="", discogs_token=""):
        super().__init__()
        self.artist_name = artist_name
        self.lastfm_api_key = lastfm_api_key
        self.discogs_token = discogs_token

    def run(self):
        image_bytes = None
        if self.discogs_token:
            image_bytes = self._fetch_from_discogs()
        if image_bytes is None and not self._stop_event.is_set():
            image_bytes = self._fetch_from_deezer()
        if image_bytes is None and not self._stop_event.is_set():
            image_bytes = self._fetch_from_wikipedia()
        if image_bytes is None and self.lastfm_api_key and not self._stop_event.is_set():
            image_bytes = self._fetch_from_lastfm()

        if self._stop_event.is_set():
            return
        if image_bytes is None:
            self.not_found.emit()
            return
        self.image_ready.emit(image_bytes)

    def _discogs_headers(self):
        return {
            "User-Agent": RADIOTOP_USER_AGENT,
            "Authorization": f"Discogs token={self.discogs_token}",
        }

    def _fetch_from_discogs(self):
        search_url = self.DISCOGS_ENDPOINT + "/database/search?" + urlencode({
            "q": self.artist_name,
            "type": "artist",
            "per_page": 1,
        })
        try:
            req = urllib.request.Request(search_url, headers=self._discogs_headers())
            data = self._fetch_json(req)
            if data is None:
                return None
        except Exception:
            return None

        results = data.get("results") or []
        if not results:
            return None

        image_url = None
        artist_id = results[0].get("id")
        if artist_id:
            try:
                artist_req = urllib.request.Request(
                    f"{self.DISCOGS_ENDPOINT}/artists/{artist_id}", headers=self._discogs_headers()
                )
                artist_data = self._fetch_json(artist_req)
                if artist_data is None:
                    return None
            except Exception:
                artist_data = {}
            images = artist_data.get("images") or []
            for img in images:
                if img.get("type") == "primary":
                    image_url = img.get("uri") or img.get("resource_url")
                    break
            if not image_url and images:
                image_url = images[0].get("uri") or images[0].get("resource_url")

        if not image_url:
            image_url = results[0].get("cover_image") or results[0].get("thumb")
        if not image_url or "spacer.gif" in image_url:
            return None

        try:
            img_req = urllib.request.Request(image_url, headers=self._discogs_headers())
            return self._fetch_bytes(img_req)
        except Exception:
            return None

    def _fetch_from_deezer(self):
        url = self.DEEZER_ENDPOINT + "/search/artist?" + urlencode({
            "q": self.artist_name,
            "limit": 1,
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = self._fetch_json(req)
            if data is None:
                return None
        except Exception:
            return None

        results = data.get("data") or []
        if not results:
            return None

        image_url = (
            results[0].get("picture_xl")
            or results[0].get("picture_big")
            or results[0].get("picture_medium")
        )
        if not image_url or self.DEEZER_PLACEHOLDER_HASH in image_url:
            return None

        try:
            img_req = urllib.request.Request(image_url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            return self._fetch_bytes(img_req)
        except Exception:
            return None

    def _fetch_from_lastfm(self):
        url = self.LASTFM_ENDPOINT + "?" + urlencode({
            "method": "artist.getinfo",
            "artist": self.artist_name,
            "api_key": self.lastfm_api_key,
            "format": "json",
            "autocorrect": 1,
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = self._fetch_json(req)
            if data is None:
                return None
        except Exception:
            return None

        artist = data.get("artist")
        if not artist or data.get("error"):
            return None

        image_url = None
        best_rank = -1
        for img in artist.get("image") or []:
            u = img.get("#text", "")
            if not u or self.LASTFM_PLACEHOLDER_HASH in u:
                continue  # Last.fm's generic "no photo" graphic - skip it
            rank = self.LASTFM_SIZE_RANK.get(img.get("size", ""), 0)
            if rank > best_rank:
                best_rank = rank
                image_url = u
        if not image_url:
            return None

        try:
            img_req = urllib.request.Request(image_url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            return self._fetch_bytes(img_req)
        except Exception:
            return None

    def _fetch_from_wikipedia(self):
        title = self.artist_name.strip().replace(" ", "_")
        summary_url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title)
        try:
            req = urllib.request.Request(summary_url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = self._fetch_json(req)
            if data is None:
                return None
        except Exception:
            return None

        thumb = data.get("thumbnail") or {}
        image_url = thumb.get("source")
        if not image_url:
            return None

        try:
            img_req = urllib.request.Request(image_url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            return self._fetch_bytes(img_req)
        except Exception:
            return None


class AlbumArtThread(_CancellableRequestThread):
    """Fetches the album cover, preferring the Cover Art Archive, keyed by
    the MusicBrainz release ID (no key required) - ID-based, so it's more
    reliable than a name search. Falls back to the artwork URL returned by
    an iTunes Search API track match when either MusicBrainz found no
    release or the Cover Art Archive has no cover on file for it, and
    finally to a Deezer track search (also no key required) when both of
    those miss."""

    image_ready = Signal(bytes)
    not_found = Signal()

    DEEZER_ENDPOINT = "https://api.deezer.com"

    def __init__(self, release_mbid, itunes_artwork_url="", artist_name="", track_title=""):
        super().__init__()
        self.release_mbid = release_mbid
        self.itunes_artwork_url = itunes_artwork_url
        self.artist_name = artist_name
        self.track_title = track_title

    def _fetch_from_cover_art_archive(self):
        url = f"https://coverartarchive.org/release/{self.release_mbid}/front-500"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            return self._fetch_bytes(req)
        except Exception:
            return None

    def _fetch_from_itunes(self):
        try:
            req = urllib.request.Request(self.itunes_artwork_url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            return self._fetch_bytes(req)
        except Exception:
            return None

    def _fetch_from_deezer(self):
        query = f'artist:"{self.artist_name}" track:"{self.track_title}"'
        url = self.DEEZER_ENDPOINT + "/search/track?" + urlencode({"q": query, "limit": 1})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = self._fetch_json(req)
            if data is None:
                return None
        except Exception:
            return None

        results = data.get("data") or []
        if not results:
            return None

        album = results[0].get("album") or {}
        image_url = album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium")
        if not image_url:
            return None

        try:
            img_req = urllib.request.Request(image_url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            return self._fetch_bytes(img_req)
        except Exception:
            return None

    def run(self):
        image_bytes = None
        if self.release_mbid:
            image_bytes = self._fetch_from_cover_art_archive()
        if image_bytes is None and self.itunes_artwork_url and not self._stop_event.is_set():
            image_bytes = self._fetch_from_itunes()
        if (
            image_bytes is None
            and self.artist_name
            and self.track_title
            and not self._stop_event.is_set()
        ):
            image_bytes = self._fetch_from_deezer()

        if self._stop_event.is_set():
            return
        if image_bytes is None:
            self.not_found.emit()
            return
        self.image_ready.emit(image_bytes)


class SimilarTracksThread(_CancellableRequestThread):
    """Fetches a short "similar tracks" list from Deezer for the current
    track's artist. Deezer has no "similar tracks by track ID" endpoint, so
    this resolves the track to a Deezer artist ID via search, then uses
    that artist's Deezer "radio" (smart mix) as the similar-tracks pool -
    optionally widened with a few top tracks from a couple of related
    artists, since an artist's own radio mix leans heavily on that same
    artist and can otherwise look repetitive."""

    results_ready = Signal(list)

    DEEZER_ENDPOINT = "https://api.deezer.com"
    MAX_TRACKS = 15
    RELATED_ARTIST_LIMIT = 2
    TRACKS_PER_RELATED_ARTIST = 3

    def __init__(self, artist_name, track_title, widen=False):
        super().__init__()
        self.artist_name = artist_name
        self.track_title = track_title
        self.widen = widen

    def _get_json(self, url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            return self._fetch_json(req)
        except Exception:
            return None

    def _resolve_artist_id(self):
        query = f'artist:"{self.artist_name}" track:"{self.track_title}"'
        url = self.DEEZER_ENDPOINT + "/search?" + urlencode({"q": query, "limit": 1})
        data = self._get_json(url)
        if not data:
            return None
        results = data.get("data") or []
        if not results:
            return None
        return (results[0].get("artist") or {}).get("id")

    def _fetch_artist_radio(self, artist_id):
        data = self._get_json(f"{self.DEEZER_ENDPOINT}/artist/{artist_id}/radio")
        return (data or {}).get("data") or []

    def _fetch_related_artist_ids(self, artist_id):
        data = self._get_json(f"{self.DEEZER_ENDPOINT}/artist/{artist_id}/related")
        related = (data or {}).get("data") or []
        return [a["id"] for a in related[: self.RELATED_ARTIST_LIMIT] if a.get("id")]

    def _fetch_artist_top_tracks(self, artist_id):
        url = f"{self.DEEZER_ENDPOINT}/artist/{artist_id}/top?" + urlencode({
            "limit": self.TRACKS_PER_RELATED_ARTIST,
        })
        data = self._get_json(url)
        return (data or {}).get("data") or []

    @staticmethod
    def _to_result(track):
        return {
            "title": track.get("title", ""),
            "artist": (track.get("artist") or {}).get("name", ""),
        }

    def run(self):
        artist_id = self._resolve_artist_id()
        if not artist_id or self._stop_event.is_set():
            self.results_ready.emit([])
            return

        tracks = self._fetch_artist_radio(artist_id)

        if self.widen:
            for related_id in self._fetch_related_artist_ids(artist_id):
                if self._stop_event.is_set():
                    break
                tracks += self._fetch_artist_top_tracks(related_id)

        if self._stop_event.is_set():
            return

        seen = set()
        results = []
        for track in tracks:
            result = self._to_result(track)
            key = (result["title"], result["artist"])
            if not result["title"] or key in seen:
                continue
            seen.add(key)
            results.append(result)
            if len(results) >= self.MAX_TRACKS:
                break

        self.results_ready.emit(results)


class TrackInfoDialog(QDialog):
    """A small non-modal window showing details about the currently
    playing track: title, artist, album, genre, and release year."""

    ALBUM_LENGTH_THRESHOLD = 28  # chars past which the dialog widens for the album name
    DEFAULT_WIDTH = 380
    WIDE_WIDTH = 560
    SIMILAR_LIST_HEIGHT = 110

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Track Info")
        self.resize(self.DEFAULT_WIDTH, 340)

        layout = QVBoxLayout(self)

        self.title_label = QLabel("No track playing")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.artist_label = QLabel("")
        self.artist_label.setWordWrap(True)
        layout.addWidget(self.artist_label)

        layout.addSpacing(10)

        # Album gets its own full-width row (rather than a form field next to
        # a fixed-width label column) since album names can run long and a
        # narrow value column forces awkward, cramped wrapping.
        self.album_label = QLabel("Album: -")
        self.album_label.setWordWrap(True)
        layout.addWidget(self.album_label)

        layout.addSpacing(4)

        form = QFormLayout()
        self.genre_value = QLabel("-")
        self.year_value = QLabel("-")
        for lbl in (self.genre_value, self.year_value):
            lbl.setWordWrap(True)
        form.addRow("Genre:", self.genre_value)
        form.addRow("Year:", self.year_value)
        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888888; font-style: italic;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Similar Tracks:"))
        self.similar_list = QListWidget()
        self.similar_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.similar_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.similar_list.setMaximumHeight(self.SIMILAR_LIST_HEIGHT)
        layout.addWidget(self.similar_list)

        layout.addStretch(1)

    def _reset_width(self):
        # Start each new track at the compact width; apply_lookup widens it
        # again if the album name turns out to need the extra room.
        self.resize(self.DEFAULT_WIDTH, self.height())

    def set_waiting(self):
        self.title_label.setText("Waiting for stream metadata...")
        self.artist_label.setText("")
        self.album_label.setText("Album: -")
        self.genre_value.setText("-")
        self.year_value.setText("-")
        self.status_label.setText("")
        self.similar_list.clear()
        self._reset_width()

    def set_no_track(self):
        self.title_label.setText("No track playing")
        self.artist_label.setText("")
        self.album_label.setText("Album: -")
        self.genre_value.setText("-")
        self.year_value.setText("-")
        self.status_label.setText("")
        self.similar_list.clear()
        self._reset_width()

    def set_now_playing(self, raw_title):
        self.title_label.setText(raw_title or "Unknown track")
        self.artist_label.setText("")
        self.album_label.setText("Album: -")
        self.genre_value.setText("-")
        self.year_value.setText("-")
        self.status_label.setText("Looking up track details...")
        self.similar_list.clear()
        self._reset_width()

    def set_similar_tracks_loading(self):
        self.similar_list.clear()
        item = QListWidgetItem("Loading...")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.similar_list.addItem(item)

    def set_similar_tracks(self, tracks):
        self.similar_list.clear()
        if not tracks:
            item = QListWidgetItem("No similar tracks found.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.similar_list.addItem(item)
            return
        for track in tracks:
            title = track.get("title", "")
            artist = track.get("artist", "")
            text = f"{title} — {artist}" if artist else title
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.similar_list.addItem(item)

    def apply_lookup(self, result):
        if not result.get("found"):
            msg = "No additional details found for this track."
            if result.get("lastfm_error"):
                msg += f"  (Last.fm: {result['lastfm_error']})"
            self.status_label.setText(msg)
            return
        title = result.get("title") or self.title_label.text()
        self.title_label.setText(title)
        self.artist_label.setText(result.get("artist") or "")

        album = result.get("album") or "-"
        self.album_label.setText(f"Album: {album}")
        if album != "-" and len(album) > self.ALBUM_LENGTH_THRESHOLD:
            self.resize(max(self.width(), self.WIDE_WIDTH), self.height())

        self.genre_value.setText(result.get("genre") or "-")
        self.year_value.setText(result.get("year") or "-")
        sources = result.get("sources") or []
        status_parts = []
        if sources:
            status_parts.append(f"Source: {', '.join(sources)}")
        if result.get("lastfm_error"):
            status_parts.append(f"Last.fm: {result['lastfm_error']}")
        self.status_label.setText("   |   ".join(status_parts))


class _ApiCredentialDialog(QDialog):
    """Shared UI for a dialog that collects a single API key/token: an
    info blurb, a line edit with a "Test" button, a result label, and
    OK/Cancel. Subclasses supply the window chrome via class attributes
    and the actual validation call via _check()."""

    WINDOW_TITLE = ""
    WINDOW_SIZE = (400, 200)
    INFO_TEXT = ""
    PLACEHOLDER = ""
    EMPTY_MESSAGE = "Enter a value first."

    def __init__(self, current_value, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.WINDOW_TITLE)
        self.resize(*self.WINDOW_SIZE)

        layout = QVBoxLayout(self)
        info = QLabel(self.INFO_TEXT)
        info.setWordWrap(True)
        layout.addWidget(info)

        self.value_edit = QLineEdit(current_value)
        self.value_edit.setPlaceholderText(self.PLACEHOLDER)
        value_row = QHBoxLayout()
        value_row.addWidget(self.value_edit, 1)
        test_btn = QPushButton("Test")
        test_btn.clicked.connect(self._test_value)
        value_row.addWidget(test_btn)
        layout.addLayout(value_row)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _test_value(self):
        value = self.value_edit.text().strip()
        if not value:
            self.result_label.setStyleSheet("color: #f67400;")
            self.result_label.setText(self.EMPTY_MESSAGE)
            return
        self.result_label.setStyleSheet("color: #888888;")
        self.result_label.setText("Testing...")
        QApplication.processEvents()
        ok, message = self._check(value)
        self.result_label.setStyleSheet(
            "color: #3daee9;" if ok else "color: #da4453;"
        )
        self.result_label.setText(message)

    def value(self):
        return self.value_edit.text().strip()

    @staticmethod
    def _check(value):
        raise NotImplementedError


class LastfmSettingsDialog(_ApiCredentialDialog):
    """Small dialog for entering/updating the user's Last.fm API key."""

    WINDOW_TITLE = "Last.fm API Key"
    WINDOW_SIZE = (400, 200)
    INFO_TEXT = (
        "Last.fm can supply richer, crowd-tagged genres for the "
        "currently playing track (used alongside MusicBrainz, which "
        "always supplies the release year). Get a free API key at "
        "last.fm/api/account/create, then paste it below. Leave blank "
        "to disable Last.fm and use MusicBrainz only."
    )
    PLACEHOLDER = "Last.fm API key"
    EMPTY_MESSAGE = "Enter a key first."

    @staticmethod
    def _check(key):
        # auth.getToken is a lightweight read-only call - good for validating
        # a key without needing a real artist/track match.
        url = "https://ws.audioscrobbler.com/2.0/?" + urlencode({
            "method": "auth.gettoken",
            "api_key": key,
            "format": "json",
        })
        try:
            req = urllib.request.Request(url, headers={"User-Agent": RADIOTOP_USER_AGENT})
            data = _fetch_json(req)
        except urllib.error.HTTPError as e:
            return False, f"HTTP error {e.code} - key may be invalid."
        except Exception as e:
            return False, f"Network error: {e}"
        if data.get("error"):
            return False, data.get("message", f"Last.fm error {data.get('error')}")
        return True, "Key is valid."

    _check_key = _check  # keep the historical name available too

    def api_key(self):
        return self.value()


class DiscogsSettingsDialog(_ApiCredentialDialog):
    """Small dialog for entering/updating the user's Discogs API token."""

    WINDOW_TITLE = "Discogs API Token"
    WINDOW_SIZE = (420, 220)
    INFO_TEXT = (
        "Discogs often has better artist photo coverage than Wikipedia, "
        "especially for working musicians without a Wikipedia page. When "
        "set, Discogs is tried first for artist photos, then Wikipedia, "
        "then Last.fm. Get a free personal access token at "
        "discogs.com/settings/developers, then paste it below. Leave "
        "blank to disable Discogs."
    )
    PLACEHOLDER = "Discogs personal access token"
    EMPTY_MESSAGE = "Enter a token first."

    @staticmethod
    def _check(token):
        # oauth/identity is a lightweight authenticated call - good for
        # validating a token without needing a real search match.
        url = "https://api.discogs.com/oauth/identity"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": RADIOTOP_USER_AGENT,
                "Authorization": f"Discogs token={token}",
            })
            data = _fetch_json(req)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Invalid token."
            return False, f"HTTP error {e.code}"
        except Exception as e:
            return False, f"Network error: {e}"
        username = data.get("username", "")
        return True, f"Token is valid (authenticated as {username})." if username else "Token is valid."

    _check_token = _check  # keep the historical name available too

    def token(self):
        return self.value()


class EditStationDialog(QDialog):
    """Dialog for editing a station's name and stream URL."""

    def __init__(self, name, url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Station")
        self.resize(420, 150)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name)
        self.url_edit = QLineEdit(url)
        form.addRow("Name:", self.name_edit)
        form.addRow("URL:", self.url_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return self.name_edit.text().strip(), self.url_edit.text().strip()


class StationListDialog(QDialog):
    """Popup for searching, adding, editing, removing, and picking a
    station to play. Kept separate from the main window so the main
    window itself can stay focused on playback controls."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window
        self.setWindowTitle("Stations")
        self.resize(440, 480)

        layout = QVBoxLayout(self)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search stations...")
        self.search_edit.textChanged.connect(lambda _: self.refresh_list())
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.currentItemChanged.connect(self._update_button_states)
        layout.addWidget(self.list_widget, 1)

        name_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Station name...")
        self.name_edit.returnPressed.connect(self._add_station)
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Paste a stream URL (http/https)...")
        self.url_edit.returnPressed.connect(self._add_station)
        url_row.addWidget(self.url_edit, 1)
        add_btn = QPushButton("Add && Play")
        add_btn.clicked.connect(self._add_station)
        url_row.addWidget(add_btn)
        layout.addLayout(url_row)

        manage_row = QHBoxLayout()
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setEnabled(False)
        self.edit_btn.clicked.connect(self._edit_station)
        manage_row.addWidget(self.edit_btn)
        self.remove_btn = QPushButton("Remove Selected Station")
        self.remove_btn.setEnabled(False)
        self.remove_btn.clicked.connect(self._remove_station)
        manage_row.addWidget(self.remove_btn, 1)
        layout.addLayout(manage_row)

        self.refresh_list()

    def refresh_list(self):
        previously_selected = self._selected_station_idx()
        filt = self.search_edit.text().strip().lower()
        self.list_widget.clear()
        for idx, st in enumerate(self.main.stations):
            if filt and filt not in st["name"].lower() and filt not in st["url"].lower():
                continue
            item = QListWidgetItem(f"{st['name']}\n{st['url']}")
            item.setData(Qt.ItemDataRole.UserRole, idx)
            font = item.font()
            if idx == self.main.current_idx:
                font.setBold(True)
                item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            else:
                font.setBold(False)
            item.setFont(font)
            self.list_widget.addItem(item)
        if previously_selected is not None:
            self._select_row_for_station(previously_selected)
        self._update_button_states()

    def _selected_station_idx(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _update_button_states(self, *_):
        idx = self._selected_station_idx()
        # While refresh_list() is mid-rebuild, list_widget.clear() can
        # transiently re-fire currentItemChanged with a row still carrying a
        # UserRole index from the pre-mutation station list (e.g. right
        # after a station is removed) - guard against that stale index
        # briefly pointing past the end of the now-shorter station list.
        valid = idx is not None and 0 <= idx < len(self.main.stations)
        self.edit_btn.setEnabled(valid)
        enabled = valid and self.main.stations[idx].get("custom", False)
        self.remove_btn.setEnabled(enabled)

    def _select_row_for_station(self, idx):
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).data(Qt.ItemDataRole.UserRole) == idx:
                self.list_widget.setCurrentRow(row)
                return

    def _on_item_double_clicked(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        self.main.play_index(idx)
        self.close()

    def _edit_station(self):
        idx = self._selected_station_idx()
        if idx is None:
            return
        station = self.main.stations[idx]
        original_name = station["name"]
        prefill_name = original_name
        if idx == self.main.current_idx and self.main._current_icy_name:
            # Show the stream's own live icy-name rather than the stored
            # name (which may be one the user typed in and that
            # _on_icy_station_name therefore never overwrote) - accepting
            # the dialog unchanged then saves it as the station's name.
            prefill_name = self.main._current_icy_name
        dlg = EditStationDialog(prefill_name, station["url"], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, url = dlg.values()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid http:// or https:// stream URL.")
            return
        url, was_adjusted = _normalize_station_url(url)
        if not name:
            name = self.main._guess_name(url)
        elif name == prefill_name and station["name"] != original_name:
            # The background icy-name lookup (_on_icy_station_name) adopted a
            # freshly-discovered name into the station while this dialog was
            # open - don't clobber it with the stale pre-filled value the
            # user left untouched.
            name = station["name"]

        station["name"] = name
        url_changed = url != station["url"]
        station["url"] = url
        if station.get("custom"):
            self.main._save_custom_stations()
        self.refresh_list()
        self._select_row_for_station(idx)
        self.main._rebuild_stations_menu()

        if idx == self.main.current_idx:
            if url_changed and self.main.player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                self.main.play_index(idx)  # reload with the new address
            else:
                self.main.name_label.setText(name)

        if was_adjusted:
            self._notify_url_adjusted(url)

    def _add_station(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid http:// or https:// stream URL.")
            return
        url, was_adjusted = _normalize_station_url(url)
        name = self.name_edit.text().strip() or self.main._guess_name(url)
        station = {"name": name, "url": url, "custom": True}
        self.main.stations.append(station)
        self.main._save_custom_stations()
        self.refresh_list()
        idx = len(self.main.stations) - 1
        self._select_row_for_station(idx)
        self.main.play_index(idx)
        self.name_edit.clear()
        self.url_edit.clear()
        self.close()
        if was_adjusted:
            self._notify_url_adjusted(url)

    def _notify_url_adjusted(self, adjusted_url):
        QMessageBox.information(
            self,
            "Stream address adjusted",
            "This address didn't include a port and/or \"stream.mp3\", which most "
            f"stream servers need to connect properly - RadioTop filled in the "
            f"default(s) for whichever was missing (port {DEFAULT_STREAM_PORT}, "
            "the standard port for SUB/Wave Radios stations, and/or "
            f"\"{DEFAULT_STREAM_FILENAME}\"):\n\n{adjusted_url}\n\n"
            "If the station still doesn't play, edit it and enter the exact "
            "stream address your station provides instead.",
        )

    def _remove_station(self):
        idx = self._selected_station_idx()
        if idx is None or not self.main.stations[idx].get("custom"):
            return
        if idx == self.main.current_idx:
            self.main.stop_playback()
        elif self.main.current_idx is not None and idx < self.main.current_idx:
            # Everything after the removed row shifts down by one, so the
            # currently-playing station's index needs to shift too, or it
            # ends up pointing at the wrong station.
            self.main.current_idx -= 1
        del self.main.stations[idx]
        self.main._save_custom_stations()
        self.refresh_list()
        self.main._rebuild_stations_menu()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RadioTop")
        self.resize(360, 480)
        self.setWindowIcon(_app_icon())

        self.settings = QSettings(APP_ORG, APP_NAME)
        self.stations = list(DEFAULT_STATIONS) + self._load_custom_stations()
        self.current_idx = None
        self._current_icy_name = None
        self._quitting = False
        self.meta_thread = None
        self.lookup_thread = None
        self.lookup_cache = {}
        self.artist_image_thread = None
        self.artist_image_cache = {}
        self.last_image_artist = None
        self.album_art_thread = None
        self.album_art_cache = {}
        self.last_album_key = None
        self.similar_tracks_thread = None
        self.similar_tracks_cache = {}
        self.last_similar_tracks_artist = None
        self.lastfm_api_key = self.settings.value("lastfm_api_key", "") or ""
        self.discogs_token = self.settings.value("discogs_token", "") or ""
        self.similar_tracks_widen = self.settings.value("similar_tracks_widen", False, type=bool)
        self.notifications_enabled = self.settings.value("show_notifications", True, type=bool)
        self._pending_notification_artist = None
        self._pending_notification_body = None
        self.track_info_dialog = TrackInfoDialog(self)

        # --- media player -------------------------------------------------
        try:
            self.stream_proxy = StreamProxyServer()
        except Exception:
            self.stream_proxy = None  # fall back to direct playback if the proxy fails to start

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        start_volume = int(self.settings.value("volume", 70))
        self.audio_output.setVolume(start_volume / 100.0)

        self.player.mediaStatusChanged.connect(self._update_status)
        self.player.playbackStateChanged.connect(self._update_status)
        self.player.errorOccurred.connect(self._on_error)

        self._build_ui(start_volume)
        self._build_tray()
        self.station_dialog = StationListDialog(self)

    # ------------------------------------------------------------------ UI
    def _build_ui(self, start_volume):
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addStretch(1)

        self.name_label = QLabel("Nothing playing")
        name_font = QFont()
        name_font.setPointSize(16)
        name_font.setBold(True)
        self.name_label.setFont(name_font)
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.name_label)

        self.track_label = QLabel("")
        self.track_label.setWordWrap(True)
        self.track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        track_font = QFont()
        track_font.setItalic(True)
        self.track_label.setFont(track_font)
        self.track_label.setStyleSheet("color: #3daee9;")
        root.addWidget(self.track_label)

        self.status_label = QLabel("Stopped")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_font = QFont()
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS['Stopped']};")
        root.addWidget(self.status_label)

        root.addSpacing(16)

        transport = QHBoxLayout()
        transport.addStretch(1)
        style = self.style()
        self.play_btn = QPushButton()
        self.play_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.setToolTip("Play / Pause")
        self.play_btn.setFixedSize(48, 48)
        self.play_btn.clicked.connect(self.toggle_play_pause)
        transport.addWidget(self.play_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_btn.setToolTip("Stop")
        self.stop_btn.setFixedSize(48, 48)
        self.stop_btn.clicked.connect(self.stop_playback)
        transport.addWidget(self.stop_btn)
        transport.addStretch(1)
        root.addLayout(transport)

        info_row = QHBoxLayout()
        info_row.addStretch(1)
        self.info_btn = QPushButton("Track Info")
        self.info_btn.clicked.connect(self._show_track_info_dialog)
        info_row.addWidget(self.info_btn)
        info_row.addStretch(1)
        root.addLayout(info_row)

        root.addSpacing(10)

        image_row = QHBoxLayout()
        image_row.addStretch(1)

        artist_col = QVBoxLayout()
        self.artist_image_label = QLabel()
        self.artist_image_label.setFixedSize(130, 130)
        self.artist_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_image_label.setWordWrap(True)
        artist_col.addWidget(self.artist_image_label)
        artist_caption = QLabel("Artist")
        artist_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        artist_caption.setStyleSheet("color: #888888; font-size: 10px;")
        artist_col.addWidget(artist_caption)
        image_row.addLayout(artist_col)

        image_row.addSpacing(14)

        album_col = QVBoxLayout()
        self.album_art_label = QLabel()
        self.album_art_label.setFixedSize(130, 130)
        self.album_art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.album_art_label.setWordWrap(True)
        album_col.addWidget(self.album_art_label)
        album_caption = QLabel("Album")
        album_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        album_caption.setStyleSheet("color: #888888; font-size: 10px;")
        album_col.addWidget(album_caption)
        image_row.addLayout(album_col)

        image_row.addStretch(1)
        root.addLayout(image_row)
        self._set_album_art_placeholder("No image")
        self._set_artist_image_placeholder("No image")

        root.addSpacing(16)

        vol_row = QHBoxLayout()
        vol_icon = QLabel()
        vol_icon.setPixmap(style.standardIcon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(20, 20))
        vol_row.addWidget(vol_icon)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(start_volume)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.volume_slider, 1)
        self.volume_pct_label = QLabel(f"{start_volume}%")
        self.volume_pct_label.setFixedWidth(36)
        vol_row.addWidget(self.volume_pct_label)
        root.addLayout(vol_row)

        device_row = QHBoxLayout()
        device_icon = QLabel()
        device_icon.setPixmap(style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon).pixmap(18, 18))
        device_row.addWidget(device_icon)
        self.device_combo = QComboBox()
        self.device_combo.setToolTip("Audio output device")
        self.device_combo.currentIndexChanged.connect(self._on_device_selected)
        device_row.addWidget(self.device_combo, 1)
        refresh_btn = QPushButton()
        refresh_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh_btn.setToolTip("Refresh output device list")
        refresh_btn.setFixedSize(28, 28)
        refresh_btn.clicked.connect(lambda: self._refresh_output_devices(preserve_selection=True))
        device_row.addWidget(refresh_btn)
        root.addLayout(device_row)

        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(
            lambda: self._refresh_output_devices(preserve_selection=True)
        )
        self._refresh_output_devices(preserve_selection=False)

        root.addStretch(2)

        # ---- menu bar ----
        file_menu = self.menuBar().addMenu("&File")
        quit_action = QAction("&Quit", self)
        quit_action.triggered.connect(self.quit_app)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About RadioTop", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        view_menu = self.menuBar().addMenu("&View")
        stations_action = QAction("&Station List...", self)
        stations_action.triggered.connect(self._show_station_list_dialog)
        view_menu.addAction(stations_action)
        track_info_action = QAction("&Track Info Window", self)
        track_info_action.triggered.connect(self._show_track_info_dialog)
        view_menu.addAction(track_info_action)

        self.stations_menu = self.menuBar().addMenu("&Stations")
        self._rebuild_stations_menu()

        settings_menu = self.menuBar().addMenu("&Settings")
        lastfm_action = QAction("&Last.fm API Key...", self)
        lastfm_action.triggered.connect(self._configure_lastfm_key)
        settings_menu.addAction(lastfm_action)

        discogs_action = QAction("&Discogs API Token...", self)
        discogs_action.triggered.connect(self._configure_discogs_token)
        settings_menu.addAction(discogs_action)

        settings_menu.addSeparator()
        self.notifications_action = QAction("Show Desktop &Notifications", self)
        self.notifications_action.setCheckable(True)
        self.notifications_action.setChecked(self.notifications_enabled)
        self.notifications_action.toggled.connect(self._on_notifications_toggled)
        settings_menu.addAction(self.notifications_action)

        self.similar_tracks_widen_action = QAction("&Widen Similar Tracks (Related Artists)", self)
        self.similar_tracks_widen_action.setCheckable(True)
        self.similar_tracks_widen_action.setChecked(self.similar_tracks_widen)
        self.similar_tracks_widen_action.toggled.connect(self._on_similar_tracks_widen_toggled)
        settings_menu.addAction(self.similar_tracks_widen_action)

        self.setStatusBar(QStatusBar())

    def _rebuild_stations_menu(self):
        """Repopulates the &Stations menu bar entry: one checkable action per
        station (checked = currently playing, click = play it), followed by
        a Manage Stations... action that opens the full search/add/edit
        dialog. Called on every structural change (add/edit/remove) and
        whenever the playing station changes, since a rebuild is cheap for
        the handful of stations this app deals with."""
        self.stations_menu.clear()
        self._stations_action_group = QActionGroup(self)
        self._stations_action_group.setExclusive(True)
        for idx, station in enumerate(self.stations):
            action = QAction(station["name"], self)
            action.setCheckable(True)
            action.setChecked(idx == self.current_idx)
            action.triggered.connect(lambda checked=False, i=idx: self.play_index(i))
            self._stations_action_group.addAction(action)
            self.stations_menu.addAction(action)

        if self.stations:
            self.stations_menu.addSeparator()
        manage_action = QAction("&Manage Stations...", self)
        manage_action.triggered.connect(self._show_station_list_dialog)
        self.stations_menu.addAction(manage_action)

    def _build_tray(self):
        icon = _app_icon()
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("RadioTop")
        menu = QMenu()
        menu.addAction("Play / Pause", self.toggle_play_pause)
        menu.addAction("Stop", self.stop_playback)
        menu.addSeparator()
        menu.addAction("Show Window", self._show_window)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    # -------------------------------------------------------------- state
    def _load_custom_stations(self):
        raw = self.settings.value("custom_stations", "[]")
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except (TypeError, json.JSONDecodeError):
            pass
        return []

    def _save_custom_stations(self):
        customs = [s for s in self.stations if s.get("custom")]
        self.settings.setValue("custom_stations", json.dumps(customs))

    # ------------------------------------------------------------- list ---
    # ---------------------------------------------------------- playback ---
    def play_index(self, idx):
        if idx is None or idx < 0 or idx >= len(self.stations):
            return
        station = self.stations[idx]
        self.current_idx = idx
        self._current_icy_name = None
        if self.stream_proxy is not None:
            play_url = self.stream_proxy.local_url(station["url"])
        else:
            play_url = station["url"]
        self.player.setSource(QUrl(play_url))
        self.player.play()
        self.name_label.setText(station["name"])
        self.track_label.setText("")
        self._pending_notification_artist = None
        self._show_notification("RadioTop - Station", station["name"])
        self.track_info_dialog.set_waiting()
        self.last_image_artist = None
        self._stop_artist_image_thread()
        self._set_artist_image_placeholder("Waiting for track info...")
        self.last_album_key = None
        self._stop_album_art_thread()
        self._set_album_art_placeholder("Waiting for track info...")
        self.last_similar_tracks_artist = None
        self._stop_similar_tracks_thread()
        self.statusBar().showMessage(f"Connecting to {station['name']}...", 4000)
        self.station_dialog.refresh_list()
        self._rebuild_stations_menu()
        self._start_metadata_thread(station["url"])

    def _start_metadata_thread(self, url):
        self._stop_metadata_thread()
        self.meta_thread = IcyMetadataThread(url)
        self.meta_thread.title_changed.connect(self._on_track_title)
        self.meta_thread.station_name_ready.connect(self._on_icy_station_name)
        self.meta_thread.finished.connect(self._on_meta_thread_finished)
        self.meta_thread.finished.connect(self.meta_thread.deleteLater)
        self.meta_thread.start()

    def _on_meta_thread_finished(self):
        # The thread may have already been deleteLater'd and replaced by a
        # newer one by the time this runs - only clear our reference if it
        # still points at the thread that just finished.
        if self.sender() is self.meta_thread:
            self.meta_thread = None

    def _on_icy_station_name(self, icy_name):
        """Records the station's own broadcast name (from the icy-name
        response header) so the "Playing on - <name>" status always reflects
        the stream's actual reported name - and separately, adopts it in
        place of the station's stored name if that name is still just the
        placeholder guessed from the URL's hostname when the station was
        added, but never overrides a name the user actually typed in,
        custom or not."""
        if self.current_idx is None:
            return
        self._current_icy_name = icy_name
        station = self.stations[self.current_idx]
        if station["name"] != self._guess_name(station["url"]) or icy_name == station["name"]:
            self._update_status()  # status label picks up self._current_icy_name regardless
            return
        station["name"] = icy_name
        self.name_label.setText(icy_name)
        self._update_status()
        self.statusBar().showMessage(f'Station name from stream: "{icy_name}"', 4000)
        self._show_notification("RadioTop - Station Name Found", f'Now known as "{icy_name}"')
        if station.get("custom"):
            self._save_custom_stations()
        self.station_dialog.refresh_list()
        self._rebuild_stations_menu()

    def _stop_metadata_thread(self):
        thread = self.meta_thread
        self.meta_thread = None
        if thread is None:
            return
        try:
            thread.title_changed.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            thread.stop()
            thread.wait(2000)
            if thread.isRunning():
                thread.terminate()
                thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    def _set_track_label(self, raw_title, year=None):
        text = f"\u266a {raw_title}"
        if year:
            text += f"  ({year})"
        self.track_label.setText(text)

    # How long to give the async artist-photo fetch (Discogs/Wikipedia/
    # Last.fm) a head start before the "now playing" notification gives
    # up on it and falls back to the plain app icon. Long enough to cover
    # most of these lookups, short enough that the notification still
    # feels immediate.
    NOTIFICATION_IMAGE_WAIT_MS = 1500

    def _on_track_title(self, title):
        self._set_track_label(title)
        self.track_info_dialog.set_now_playing(title)
        self._lookup_track_info(title)
        artist, track_name = TrackLookupThread._split_artist_title(title)
        body = f"{artist} \u2014 {track_name}" if artist else (title or "Unknown track")
        if artist:
            self._fetch_artist_image(artist)  # kick this off first so it has maximum head start
        self._schedule_track_notification(artist, body)

    def _schedule_track_notification(self, artist, body):
        icon = self._icon_for_artist(artist) if artist else None
        if not artist or icon is not None:
            # No artist to look up, or we already have their photo cached
            # from earlier this session - show right away.
            self._pending_notification_artist = None
            self._show_notification("RadioTop - Now Playing", body, icon)
            return
        # Not cached yet. Hold the notification briefly so it can use the
        # real artist photo once the async fetch finishes, instead of
        # always falling back to the generic app icon on an artist's
        # first play. _on_artist_image_ready/_not_found fire this early
        # if the fetch finishes before the wait window elapses.
        self._pending_notification_artist = artist
        self._pending_notification_body = body
        QTimer.singleShot(
            self.NOTIFICATION_IMAGE_WAIT_MS,
            lambda a=artist: self._fire_pending_notification(a),
        )

    def _fire_pending_notification(self, artist):
        if self._pending_notification_artist != artist:
            return  # already fired early, or superseded by a newer track/station
        self._pending_notification_artist = None
        body = self._pending_notification_body
        icon = self._icon_for_artist(artist)
        self._show_notification("RadioTop - Now Playing", body, icon)

    def _icon_for_artist(self, artist_name):
        cached = self.artist_image_cache.get(artist_name)
        if not cached:
            return None
        pixmap = QPixmap()
        if pixmap.loadFromData(cached) and not pixmap.isNull():
            return QIcon(pixmap)
        return None

    def _lookup_track_info(self, title):
        if title in self.lookup_cache:
            cached = self.lookup_cache[title]
            self.track_info_dialog.apply_lookup(cached)
            if cached.get("found") and cached.get("year"):
                self._set_track_label(title, cached["year"])
            return
        self._stop_lookup_thread()
        self.lookup_thread = TrackLookupThread(title, self.lastfm_api_key)
        self.lookup_thread.result_ready.connect(self._on_lookup_result)
        self.lookup_thread.finished.connect(self._on_lookup_thread_finished)
        self.lookup_thread.finished.connect(self.lookup_thread.deleteLater)
        self.lookup_thread.start()

    def _on_lookup_result(self, result):
        self.lookup_cache[result["raw_title"]] = result
        self.track_info_dialog.apply_lookup(result)
        if result.get("found") and result.get("year"):
            self._set_track_label(result["raw_title"], result["year"])
        confirmed_artist = result.get("artist")
        if confirmed_artist:
            self._fetch_artist_image(confirmed_artist)
        release_mbid = result.get("release_mbid")
        itunes_artwork_url = result.get("itunes_artwork_url")
        track_artist = result.get("artist") or ""
        track_title = result.get("title") or ""
        if release_mbid or itunes_artwork_url or (track_artist and track_title):
            self._fetch_album_art(release_mbid, itunes_artwork_url, track_artist, track_title)
        elif result.get("found"):
            self._set_album_art_placeholder("No cover art")
        if track_artist and track_title:
            self._fetch_similar_tracks(track_artist, track_title)
        elif result.get("found"):
            self.track_info_dialog.set_similar_tracks([])

    def _on_lookup_thread_finished(self):
        if self.sender() is self.lookup_thread:
            self.lookup_thread = None

    def _stop_lookup_thread(self):
        thread = self.lookup_thread
        self.lookup_thread = None
        if thread is None:
            return
        try:
            thread.result_ready.disconnect(self._on_lookup_result)
        except (TypeError, RuntimeError):
            pass
        try:
            thread.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            if thread.isRunning():
                # stop() interrupts any in-flight request by shutting down
                # its socket directly, so this wait usually returns almost
                # immediately rather than blocking for the full timeout -
                # terminate() can kill the thread mid-syscall, leaving a
                # socket or lock in a bad state, so it's only a last resort.
                thread.stop()
                thread.wait(1500)
                if thread.isRunning():
                    thread.terminate()
                    thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    def _show_track_info_dialog(self):
        self.track_info_dialog.show()
        self.track_info_dialog.raise_()
        self.track_info_dialog.activateWindow()

    # ------------------------------------------------------ artist image ---
    def _set_artist_image_placeholder(self, text):
        self.artist_image_label.setPixmap(QPixmap())
        self.artist_image_label.setText(text)
        self.artist_image_label.setStyleSheet(
            "border: 1px solid #555; border-radius: 4px; background: #222; "
            "color: #777; font-style: italic;"
        )

    def _set_artist_image_pixmap(self, pixmap):
        self.artist_image_label.setText("")
        scaled = pixmap.scaled(
            130, 130, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.artist_image_label.setPixmap(scaled)
        self.artist_image_label.setStyleSheet("border: 1px solid #555; border-radius: 4px; background: #222;")

    def _fetch_artist_image(self, artist_name):
        artist_name = (artist_name or "").strip()
        if not artist_name:
            self.last_image_artist = None
            self._stop_artist_image_thread()
            self._set_artist_image_placeholder("No image")
            return
        if artist_name == self.last_image_artist:
            return  # already showing / fetching this artist
        self.last_image_artist = artist_name

        if artist_name in self.artist_image_cache:
            cached = self.artist_image_cache[artist_name]
            if cached is None:
                self._set_artist_image_placeholder("No image found")
            else:
                pixmap = QPixmap()
                pixmap.loadFromData(cached)
                if not pixmap.isNull():
                    self._set_artist_image_pixmap(pixmap)
                else:
                    self._set_artist_image_placeholder("No image found")
            return

        self._set_artist_image_placeholder("Loading...")
        self._stop_artist_image_thread()
        self.artist_image_thread = ArtistImageThread(artist_name, self.lastfm_api_key, self.discogs_token)
        self.artist_image_thread.image_ready.connect(
            lambda data, name=artist_name: self._on_artist_image_ready(name, data)
        )
        self.artist_image_thread.not_found.connect(
            lambda name=artist_name: self._on_artist_image_not_found(name)
        )
        self.artist_image_thread.finished.connect(self._on_artist_image_thread_finished)
        self.artist_image_thread.finished.connect(self.artist_image_thread.deleteLater)
        self.artist_image_thread.start()

    def _on_artist_image_ready(self, artist_name, data):
        raw = bytes(data)
        self.artist_image_cache[artist_name] = raw
        if self._pending_notification_artist == artist_name:
            self._fire_pending_notification(artist_name)
        if artist_name != self.last_image_artist:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(raw)
        if not pixmap.isNull():
            self._set_artist_image_pixmap(pixmap)
        else:
            self._set_artist_image_placeholder("No image found")

    def _on_artist_image_not_found(self, artist_name):
        self.artist_image_cache[artist_name] = None
        if self._pending_notification_artist == artist_name:
            self._fire_pending_notification(artist_name)
        if artist_name == self.last_image_artist:
            self._set_artist_image_placeholder("No image found")

    def _on_artist_image_thread_finished(self):
        if self.sender() is self.artist_image_thread:
            self.artist_image_thread = None

    def _stop_artist_image_thread(self):
        thread = self.artist_image_thread
        self.artist_image_thread = None
        if thread is None:
            return
        for signal_name in ("image_ready", "not_found", "finished"):
            try:
                getattr(thread, signal_name).disconnect()
            except (TypeError, RuntimeError):
                pass
        try:
            if thread.isRunning():
                # stop() interrupts any in-flight request by shutting down
                # its socket directly, so this wait usually returns almost
                # immediately rather than blocking for the full timeout -
                # terminate() can kill the thread mid-syscall, leaving a
                # socket or lock in a bad state, so it's only a last resort.
                thread.stop()
                thread.wait(1500)
                if thread.isRunning():
                    thread.terminate()
                    thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    # -------------------------------------------------------- album art ---
    def _set_album_art_placeholder(self, text):
        self.album_art_label.setPixmap(QPixmap())
        self.album_art_label.setText(text)
        self.album_art_label.setStyleSheet(
            "border: 1px solid #555; border-radius: 4px; background: #222; "
            "color: #777; font-style: italic;"
        )

    def _set_album_art_pixmap(self, pixmap):
        self.album_art_label.setText("")
        scaled = pixmap.scaled(
            130, 130, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.album_art_label.setPixmap(scaled)
        self.album_art_label.setStyleSheet("border: 1px solid #555; border-radius: 4px; background: #222;")

    def _fetch_album_art(self, release_mbid, itunes_artwork_url="", artist_name="", track_title=""):
        release_mbid = (release_mbid or "").strip()
        itunes_artwork_url = (itunes_artwork_url or "").strip()
        artist_name = (artist_name or "").strip()
        track_title = (track_title or "").strip()
        # Cache/dedup key: prefer the MBID (stable, ID-based), then the
        # iTunes artwork URL, and finally the artist/title pair when neither
        # of those was available (Deezer-only lookup).
        cache_key = release_mbid or itunes_artwork_url or (
            f"{artist_name} {track_title}" if artist_name and track_title else ""
        )
        if not cache_key:
            self.last_album_key = None
            self._stop_album_art_thread()
            self._set_album_art_placeholder("No image")
            return
        if cache_key == self.last_album_key:
            return  # already showing / fetching this release
        self.last_album_key = cache_key

        if cache_key in self.album_art_cache:
            cached = self.album_art_cache[cache_key]
            if cached is None:
                self._set_album_art_placeholder("No cover art")
            else:
                pixmap = QPixmap()
                pixmap.loadFromData(cached)
                if not pixmap.isNull():
                    self._set_album_art_pixmap(pixmap)
                else:
                    self._set_album_art_placeholder("No cover art")
            return

        self._set_album_art_placeholder("Loading...")
        self._stop_album_art_thread()
        self.album_art_thread = AlbumArtThread(release_mbid, itunes_artwork_url, artist_name, track_title)
        self.album_art_thread.image_ready.connect(
            lambda data, key=cache_key: self._on_album_art_ready(key, data)
        )
        self.album_art_thread.not_found.connect(
            lambda key=cache_key: self._on_album_art_not_found(key)
        )
        self.album_art_thread.finished.connect(self._on_album_art_thread_finished)
        self.album_art_thread.finished.connect(self.album_art_thread.deleteLater)
        self.album_art_thread.start()

    def _on_album_art_ready(self, cache_key, data):
        raw = bytes(data)
        self.album_art_cache[cache_key] = raw
        if cache_key != self.last_album_key:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(raw)
        if not pixmap.isNull():
            self._set_album_art_pixmap(pixmap)
        else:
            self._set_album_art_placeholder("No cover art")

    def _on_album_art_not_found(self, cache_key):
        self.album_art_cache[cache_key] = None
        if cache_key == self.last_album_key:
            self._set_album_art_placeholder("No cover art")

    def _on_album_art_thread_finished(self):
        if self.sender() is self.album_art_thread:
            self.album_art_thread = None

    def _stop_album_art_thread(self):
        thread = self.album_art_thread
        self.album_art_thread = None
        if thread is None:
            return
        for signal_name in ("image_ready", "not_found", "finished"):
            try:
                getattr(thread, signal_name).disconnect()
            except (TypeError, RuntimeError):
                pass
        try:
            if thread.isRunning():
                # stop() interrupts any in-flight request by shutting down
                # its socket directly, so this wait usually returns almost
                # immediately rather than blocking for the full timeout -
                # terminate() can kill the thread mid-syscall, leaving a
                # socket or lock in a bad state, so it's only a last resort.
                thread.stop()
                thread.wait(1500)
                if thread.isRunning():
                    thread.terminate()
                    thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    # --------------------------------------------------- similar tracks ---
    def _fetch_similar_tracks(self, artist_name, track_title):
        artist_name = (artist_name or "").strip()
        track_title = (track_title or "").strip()
        if not artist_name or not track_title:
            self.last_similar_tracks_artist = None
            self._stop_similar_tracks_thread()
            self.track_info_dialog.set_similar_tracks([])
            return
        if artist_name == self.last_similar_tracks_artist:
            return  # already showing / fetching similar tracks for this artist
        self.last_similar_tracks_artist = artist_name

        if artist_name in self.similar_tracks_cache:
            self.track_info_dialog.set_similar_tracks(self.similar_tracks_cache[artist_name])
            return

        self.track_info_dialog.set_similar_tracks_loading()
        self._stop_similar_tracks_thread()
        self.similar_tracks_thread = SimilarTracksThread(artist_name, track_title, self.similar_tracks_widen)
        self.similar_tracks_thread.results_ready.connect(
            lambda tracks, name=artist_name: self._on_similar_tracks_ready(name, tracks)
        )
        self.similar_tracks_thread.finished.connect(self._on_similar_tracks_thread_finished)
        self.similar_tracks_thread.finished.connect(self.similar_tracks_thread.deleteLater)
        self.similar_tracks_thread.start()

    def _on_similar_tracks_ready(self, artist_name, tracks):
        self.similar_tracks_cache[artist_name] = tracks
        if artist_name == self.last_similar_tracks_artist:
            self.track_info_dialog.set_similar_tracks(tracks)

    def _on_similar_tracks_thread_finished(self):
        if self.sender() is self.similar_tracks_thread:
            self.similar_tracks_thread = None

    def _stop_similar_tracks_thread(self):
        thread = self.similar_tracks_thread
        self.similar_tracks_thread = None
        if thread is None:
            return
        for signal_name in ("results_ready", "finished"):
            try:
                getattr(thread, signal_name).disconnect()
            except (TypeError, RuntimeError):
                pass
        try:
            if thread.isRunning():
                thread.stop()
                thread.wait(1500)
                if thread.isRunning():
                    thread.terminate()
                    thread.wait(500)
        except RuntimeError:
            pass  # underlying C++ object was already deleted - nothing to do

    def _configure_lastfm_key(self):
        dlg = LastfmSettingsDialog(self.lastfm_api_key, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            key = dlg.api_key()
            self.lastfm_api_key = key
            self.settings.setValue("lastfm_api_key", key)
            self.lookup_cache.clear()
            self.artist_image_cache.clear()
            self.statusBar().showMessage(
                "Last.fm API key saved." if key else "Last.fm API key cleared - using MusicBrainz only.",
                4000,
            )

    def _configure_discogs_token(self):
        dlg = DiscogsSettingsDialog(self.discogs_token, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            token = dlg.token()
            self.discogs_token = token
            self.settings.setValue("discogs_token", token)
            self.artist_image_cache.clear()
            self.statusBar().showMessage(
                "Discogs token saved." if token else "Discogs token cleared.",
                4000,
            )

    def _on_notifications_toggled(self, checked):
        self.notifications_enabled = checked
        self.settings.setValue("show_notifications", checked)

    def _on_similar_tracks_widen_toggled(self, checked):
        self.similar_tracks_widen = checked
        self.settings.setValue("similar_tracks_widen", checked)
        self.similar_tracks_cache.clear()

    def _show_notification(self, title, body, icon=None):
        if not self.notifications_enabled:
            return
        # Routed through the system tray icon's native notification call
        # (the desktop's own notification service - KDE Plasma's via
        # D-Bus/knotifications, the Windows Action Center on 10/11, etc.)
        # rather than a self-drawn, self-positioned popup window. Custom
        # top-level windows can't reliably position or even show
        # themselves under Wayland compositors (including Plasma's
        # default Wayland session) - only the desktop's own notification
        # daemon can guarantee correct placement and visibility.
        if icon is None:
            icon = self.windowIcon()
        self.tray.showMessage(title, body, icon, 4000)

    def toggle_play_pause(self):
        state = self.player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self.player.play()
        elif self.current_idx is not None:
            self.play_index(self.current_idx)  # resume whatever was last selected
        else:
            self._show_station_list_dialog()  # nothing chosen yet - prompt for a station

    def _show_station_list_dialog(self):
        self.station_dialog.refresh_list()
        self.station_dialog.show()
        self.station_dialog.raise_()
        self.station_dialog.activateWindow()

    def stop_playback(self):
        self.player.stop()
        self.current_idx = None
        self._stop_metadata_thread()
        self._stop_lookup_thread()
        self._stop_artist_image_thread()
        self._pending_notification_artist = None
        self.last_image_artist = None
        self._set_artist_image_placeholder("No image")
        self._stop_album_art_thread()
        self.last_album_key = None
        self._set_album_art_placeholder("No image")
        self._stop_similar_tracks_thread()
        self.last_similar_tracks_artist = None
        self.name_label.setText("Nothing playing")
        self.track_label.setText("")
        self.track_info_dialog.set_no_track()
        self.station_dialog.refresh_list()
        self._rebuild_stations_menu()

    # ------------------------------------------------------- status/errors
    def _update_status(self, *_):
        state = self.player.playbackState()
        media_status = self.player.mediaStatus()

        if media_status == QMediaPlayer.MediaStatus.InvalidMedia:
            status = "Error"
        elif state == QMediaPlayer.PlaybackState.PlayingState:
            if media_status in (
                QMediaPlayer.MediaStatus.LoadingMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
                QMediaPlayer.MediaStatus.StalledMedia,
            ):
                status = "Buffering..."
            else:
                status = "Playing"
        elif state == QMediaPlayer.PlaybackState.PausedState:
            status = "Paused"
        else:
            status = "Stopped"

        text = status
        if status == "Playing" and self.current_idx is not None:
            # Prefer the stream's own live icy-name over the stored station
            # name, which may be a name the user typed in and that
            # _on_icy_station_name therefore left untouched.
            display_name = self._current_icy_name or self.stations[self.current_idx]["name"]
            text = f"Playing on - {display_name}"

        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS.get(status, '#888888')};")

        style = self.style()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_btn.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _on_error(self, error, error_string):
        self.status_label.setText("Error")
        self.status_label.setStyleSheet(f"color: {STATUS_COLORS['Error']};")
        self.statusBar().showMessage(error_string or "Playback error", 6000)

    # ------------------------------------------------------------ volume ---
    def _on_volume_changed(self, value):
        self.audio_output.setVolume(value / 100.0)
        self.volume_pct_label.setText(f"{value}%")
        self.settings.setValue("volume", value)

    # -------------------------------------------------------- output device
    def _refresh_output_devices(self, preserve_selection=True):
        current_id = None
        if preserve_selection and self.device_combo.count() > 0:
            current_dev = self.device_combo.currentData()
            if current_dev is not None:
                current_id = bytes(current_dev.id())

        devices = QMediaDevices.audioOutputs()
        default_device = QMediaDevices.defaultAudioOutput()

        self.device_combo.blockSignals(True)
        self.device_combo.clear()

        if not devices:
            self.device_combo.addItem("No output devices found", None)
            self.device_combo.blockSignals(False)
            return

        if current_id is None:
            saved_id = self.settings.value("output_device_id", b"")
            if isinstance(saved_id, str):
                saved_id = saved_id.encode("latin-1", errors="ignore")
            current_id = bytes(saved_id) if saved_id else None

        select_idx = 0
        for i, dev in enumerate(devices):
            label = dev.description()
            if not default_device.isNull() and bytes(dev.id()) == bytes(default_device.id()):
                label += " (Default)"
            self.device_combo.addItem(label, dev)
            if current_id and bytes(dev.id()) == current_id:
                select_idx = i

        self.device_combo.setCurrentIndex(select_idx)
        self._apply_output_device(self.device_combo.itemData(select_idx))
        self.device_combo.blockSignals(False)

    def _on_device_selected(self, index):
        device = self.device_combo.itemData(index)
        if device is not None:
            self._apply_output_device(device)

    def _apply_output_device(self, device):
        self.audio_output.setDevice(device)
        try:
            self.settings.setValue("output_device_id", bytes(device.id()))
        except Exception:
            pass
        self.statusBar().showMessage(f"Audio output: {device.description()}", 3000)

    # ----------------------------------------------------------- stations --
    def _guess_name(self, url):
        return QUrl(url).host() or "Custom Stream"

    # --------------------------------------------------------------- tray -
    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.setVisible(not self.isVisible())
            if self.isVisible():
                self.raise_()
                self.activateWindow()

    def _show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_about(self):
        dlg = QMessageBox(self)
        dlg.setWindowTitle("About RadioTop")
        dlg.setText(
            "<b>RadioTop</b><br>A simple internet radio player.<br>No bloat, just play.<br>"
            "Built with PySide6 / Qt Multimedia."
        )
        logo_path = _resource_path("assets", "radiotop_about_logo.png")
        logo = QPixmap(logo_path)
        if not logo.isNull():
            dlg.setIconPixmap(
                logo.scaled(
                    160, 160,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        dlg.setStandardButtons(QMessageBox.StandardButton.Ok)
        dlg.exec()

    def quit_app(self):
        self._quitting = True
        # An active tray icon can keep the process alive on some desktops
        # (e.g. KDE Plasma's DBus-based StatusNotifierItem) even after
        # QApplication.quit() is called - hide it explicitly rather than
        # relying on teardown to do it implicitly. Harmless no-op on
        # platforms (like Windows) where this isn't an issue.
        self.tray.hide()
        self._stop_metadata_thread()
        self._stop_lookup_thread()
        self._stop_artist_image_thread()
        self._stop_album_art_thread()
        self._stop_similar_tracks_thread()
        self.player.stop()
        if self.stream_proxy is not None:
            self.stream_proxy.shutdown()
        # Safety net: if anything still prevents a clean shutdown, force
        # the process to actually exit after a short grace period rather
        # than leaving it hanging invisibly in the background. Marked as
        # a daemon thread so it never itself delays a normal clean exit.
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()
        QApplication.quit()

    def closeEvent(self, event):
        if self._quitting or not self.tray.isVisible():
            event.accept()
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Close RadioTop")
        msg.setText("Do you want to quit RadioTop, or keep it running in the background?")
        quit_btn = msg.addButton("Quit", QMessageBox.ButtonRole.DestructiveRole)
        tray_btn = msg.addButton("Minimize to Tray", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.setDefaultButton(tray_btn)
        msg.exec()
        clicked = msg.clickedButton()

        if clicked is quit_btn:
            event.accept()
            self.quit_app()
        elif clicked is tray_btn:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "RadioTop",
                "Still running in the tray. Right-click the tray icon to quit.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
        else:
            event.ignore()  # Cancel - leave the window open


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
