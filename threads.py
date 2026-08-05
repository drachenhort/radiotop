"""Background QThread subclasses used by RadioTop for network lookups.

Split out of radiotop_gui.py as the first step of breaking up that file;
see CLAUDE.md for the overall module-split plan. Each class here does one
job and reports back to MainWindow via Qt signals - see radiotop_gui.py's
module docstring / CLAUDE.md for the per-class rundown (ICY polling,
SUB/WAVE now-playing, update checks, MusicBrainz/Last.fm/iTunes track
lookup, artist image, album art, similar tracks).
"""

import json
import re
import socket
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as _wait_futures
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from PySide6.QtCore import QThread, Signal

GITHUB_REPO = "drachenhort/radiotop"


def _ssl_context():
    """Builds an SSLContext pinned to certifi's CA bundle rather than
    letting OpenSSL fall back to its own compiled-in default cert
    path/dir. In a PyInstaller build, the bundled libssl still carries
    the *build machine's* default paths (e.g. a distro's /etc/ssl/certs
    layout), which may not exist - or may be empty - on whatever machine
    the frozen exe actually runs on, causing every HTTPS request to fail
    with "unable to get local issuer certificate" even though the
    running system has its own perfectly good trust store. Falls back to
    ssl's own default context if certifi isn't installed (e.g. running
    from source in an environment without it)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CONTEXT = _ssl_context()

TITLE_RE = re.compile(rb"StreamTitle='([^']*)';")

RADIOTOP_USER_AGENT = "RadioTop/1.0 ( https://github.com/example/radiotop )"


def _parse_version(version_str):
    """Turns a release tag like "0.32" (or, for older tags, "v0.23"/"V0.21")
    into an int tuple for comparison, since plain string comparison would
    sort "0.9" after "0.10"."""
    digits = version_str.lstrip("vV")
    parts = []
    for piece in digits.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts)


def _deezer_artist_track_query(artist_name, track_title):
    """Builds a Deezer advanced-search query string (`artist:"..." track:"..."`)
    for the given artist/title. Deezer's query syntax treats an unescaped `"`
    as a field terminator, so a literal quote in either field (not uncommon in
    track titles, e.g. `He Said "Yes"`) would otherwise truncate that field
    and silently return the wrong (or no) result - stripped here rather than
    escaped, since Deezer's search has no escape syntax for a quote."""
    artist_name = artist_name.replace('"', "")
    track_title = track_title.replace('"', "")
    return f'artist:"{artist_name}" track:"{track_title}"'


# Shared pool for the sole purpose of making urlopen() calls interruptible
# (see _cancellable_urlopen below) - separate from any ThreadPoolExecutor a
# thread class creates for its own concurrent lookups (e.g. TrackLookupThread
# querying MusicBrainz/iTunes/Last.fm at once), since those are a different
# concern with their own lifetime (scoped to one run() call).
_CONNECT_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="radiotop-connect")

# How often _cancellable_urlopen re-checks stop_event while a connect is
# still in flight on the pool. Short enough that stop() is noticed quickly;
# long enough not to busy-loop.
_CONNECT_POLL_INTERVAL = 0.2


def _cancellable_urlopen(req, timeout, stop_event):
    """Like urllib.request.urlopen(), but interruptible: a plain
    urlopen(timeout=10) blocks in one uninterruptible call for up to the
    full timeout, and there's no response object to shut down from another
    thread until it returns - so calling stop() on a thread stuck
    connecting to a stalled/firewalled host (no RST, just silence) has no
    effect until that timeout elapses on its own.

    Runs the actual urlopen() call on a background pool and polls its
    future instead of blocking on it directly, so this function - and
    therefore the QThread calling it - can return as soon as stop_event is
    set, without waiting for the real network call to finish. That call
    keeps running on the pool in the background until it naturally
    completes or times out; if it eventually succeeds after this function
    has already given up, the response is closed rather than left open
    until garbage collection reclaims it (see _close_late_response).
    Returns None if stop_event was set before the call completed;
    otherwise returns the response (or raises whatever exception the real
    urlopen() call raised, exactly as if it had been called directly)."""
    future = _CONNECT_POOL.submit(urllib.request.urlopen, req, timeout=timeout, context=_SSL_CONTEXT)
    while True:
        if stop_event.is_set():
            future.add_done_callback(_close_late_response)
            return None
        # wait() (unlike future.result(timeout=...)) never raises on its
        # own timeout - it just returns an empty done set - so a real
        # socket.timeout from the urlopen() call itself can't be mistaken
        # for "still polling" and swallowed. On Python 3.11+, socket.timeout
        # and concurrent.futures.TimeoutError are BOTH aliases of the same
        # builtin TimeoutError, so catching one to mean "keep polling" used
        # to also catch the other, silently discarding a genuine connection
        # timeout and spinning this loop forever instead of returning it.
        done, _ = _wait_futures((future,), timeout=_CONNECT_POLL_INTERVAL)
        if done:
            return future.result()


def _close_late_response(future):
    """Done-callback for a urlopen() future that _cancellable_urlopen gave
    up waiting on (stop() fired first): if it went on to succeed anyway,
    close the response so its socket doesn't stay open until garbage
    collection reclaims it. A no-op if the call failed or was itself
    already cancelled."""
    try:
        resp = future.result()
    except Exception:
        return
    try:
        resp.close()
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
            resp = _cancellable_urlopen(req, timeout=15, stop_event=self._stop_event)
        except Exception:
            return ""  # transient failure - the outer loop will retry next interval
        if resp is None:
            return ""  # stop() fired while still connecting - outer loop exits on its own

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
        of opening the connection at all if stop() was already called (or
        if stop() fires while still connecting - see _cancellable_urlopen)."""
        if self._stop_event.is_set():
            return None
        resp = _cancellable_urlopen(req, timeout=timeout, stop_event=self._stop_event)
        if resp is None:
            return None
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


class SubwaveNowPlayingThread(_CancellableRequestThread):
    """Polls a SUB/WAVE station's own HTTP API (GET /now-playing + GET
    /state, both unauthenticated) for richer now-playing metadata than ICY
    tags give us - genre, the DJ persona, and the upcoming queue for a
    "next track" display. Polled every POLL_INTERVAL seconds,
    matching the interval SUB/WAVE's own web player polls at. If the first
    couple of polls fail (wrong port, not a SUB/WAVE station, API down),
    unavailable() fires once and the thread exits - there's no point
    hammering a station that was never going to answer."""

    now_playing_ready = Signal(dict)
    unavailable = Signal()

    POLL_INTERVAL = 5  # seconds, matching the SUB/WAVE web player's own poll rate
    MAX_CONSECUTIVE_FAILURES = 2

    def __init__(self, api_base):
        super().__init__()
        self.api_base = api_base

    def run(self):
        failures = 0
        while not self._stop_event.is_set():
            now, state = self._poll_once()
            if self._stop_event.is_set():
                return
            if now is None and state is None:
                failures += 1
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self.unavailable.emit()
                    return
            else:
                failures = 0
                self.now_playing_ready.emit({"now_playing": now or {}, "state": state or {}})
            self._stop_event.wait(self.POLL_INTERVAL)

    def _poll_once(self):
        headers = {"User-Agent": RADIOTOP_USER_AGENT, "Accept": "application/json"}
        now = state = None
        try:
            now = self._fetch_json(
                urllib.request.Request(f"{self.api_base}/now-playing", headers=headers)
            )
        except Exception:
            pass
        try:
            state = self._fetch_json(
                urllib.request.Request(f"{self.api_base}/state", headers=headers)
            )
        except Exception:
            pass
        return now, state


class SubwaveRequestThread(_CancellableRequestThread):
    """Fires a one-shot POST /request to a SUB/WAVE station's API when the
    user likes the current track - SUB/WAVE has no persistent star/favorite
    endpoint, so "like" is a free-text request nudging the DJ's picker
    toward similar material, not a record kept on the station. Fire-and-
    forget: nobody is waiting on the result, so failures are swallowed."""

    def __init__(self, api_base, text):
        super().__init__()
        self.api_base = api_base
        self.text = text

    def run(self):
        body = json.dumps({"text": self.text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_base}/request",
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": RADIOTOP_USER_AGENT,
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            self._fetch_json(req)
        except Exception:
            pass


class UpdateCheckThread(_CancellableRequestThread):
    """One-shot check against GitHub's "latest release" API for a newer
    RadioTop version than the one currently running. Read-only and
    unauthenticated - GitHub's public API rate limit (60/hr per IP) is far
    more than a once-a-day check plus the occasional manual one could ever
    hit."""

    check_complete = Signal(dict)

    def __init__(self, current_version):
        super().__init__()
        self.current_version = current_version

    def run(self):
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            headers={"User-Agent": RADIOTOP_USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        try:
            data = self._fetch_json(req)
        except Exception as exc:
            if not self._stop_event.is_set():
                self.check_complete.emit({"error": str(exc)})
            return
        if data is None:
            return  # stopped
        latest_tag = data.get("tag_name") or ""
        available = _parse_version(latest_tag) > _parse_version(self.current_version)
        self.check_complete.emit({
            "available": available,
            "latest_version": latest_tag,
            "notes": data.get("body") or "",
            "html_url": data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
        })


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
        for sep in (" - ", " – ", " — "):
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
    """Fetches a picture of the current artist/band. Deezer (no key needed)
    is tried first as the primary source, then Discogs (if a token is
    configured), then Wikipedia, then Last.fm as a last resort.

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
        # `not image_bytes` (rather than `is None`) so a 200 OK with an
        # empty/truncated body - seen in the wild from CDNs under load -
        # falls through to the next source instead of being treated as a
        # successful fetch.
        image_bytes = self._fetch_from_deezer()
        if not image_bytes and self.discogs_token and not self._stop_event.is_set():
            image_bytes = self._fetch_from_discogs()
        if not image_bytes and not self._stop_event.is_set():
            image_bytes = self._fetch_from_wikipedia()
        if not image_bytes and self.lastfm_api_key and not self._stop_event.is_set():
            image_bytes = self._fetch_from_lastfm()

        if self._stop_event.is_set():
            return
        if not image_bytes:
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
    """Fetches the album cover. Deezer (no key required) is tried first as
    the primary source via an artist/title track search. Falls back to the
    Cover Art Archive, keyed by the MusicBrainz release ID - more reliable
    than a name search, but only available when MusicBrainz matched a
    release - and finally to the artwork URL returned by an iTunes Search
    API track match when both of those miss."""

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
        query = _deezer_artist_track_query(self.artist_name, self.track_title)
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
        # `not image_bytes` (rather than `is None`) so a 200 OK with an
        # empty/truncated body - seen in the wild from CDNs under load -
        # falls through to the next source instead of being treated as a
        # successful fetch.
        image_bytes = None
        if self.artist_name and self.track_title:
            image_bytes = self._fetch_from_deezer()
        if not image_bytes and self.release_mbid and not self._stop_event.is_set():
            image_bytes = self._fetch_from_cover_art_archive()
        if not image_bytes and self.itunes_artwork_url and not self._stop_event.is_set():
            image_bytes = self._fetch_from_itunes()

        if self._stop_event.is_set():
            return
        if not image_bytes:
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
        query = _deezer_artist_track_query(self.artist_name, self.track_title)
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
