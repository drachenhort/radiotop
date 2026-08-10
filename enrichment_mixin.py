"""Track-enrichment pipeline mixin for MainWindow.

Split out of radiotop_gui.py as part of breaking up that file into
modules (see CLAUDE.md for the overall plan). Bundles the four lookup
stages that fire off of a resolved "now playing" title - MusicBrainz/
Last.fm/iTunes track lookup, artist image, album art, and similar
tracks - since they share the same cache-then-thread-then-signal shape
and are used together (TrackLookupThread's result feeds the other
three). This is purely an organizational split: EnrichmentMixin has no
state of its own and expects to be mixed into MainWindow, which sets up
the caches, dialog, and thread-slot attributes it reads/writes
(self.lookup_cache, self.track_info_dialog, self.artist_image_thread,
etc.) in its own __init__.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from threads import AlbumArtThread, ArtistImageThread, SimilarTracksThread, TrackLookupThread


class EnrichmentMixin:
    def _cache_set(self, cache, key, value):
        """dict[key] = value, then evict the oldest entry (insertion order)
        if that pushes the cache past MAX_CACHE_ENTRIES. Used for the
        lookup/artist-image/album-art/similar-tracks caches so none of them
        grows unbounded over a long-running session."""
        cache[key] = value
        while len(cache) > self.MAX_CACHE_ENTRIES:
            cache.pop(next(iter(cache)))

    def _lookup_track_info(self, title):
        # Set before either branch below runs, so a result for a since-
        # superseded title (from a station switch, or a newer track on the
        # same station arriving while an old lookup is still in flight) is
        # recognized as stale in _on_lookup_result instead of silently
        # overwriting the currently-displayed track info.
        self.last_lookup_title = title
        if title in self.lookup_cache:
            # Route through _on_lookup_result rather than duplicating its
            # logic here, so a cache hit still re-triggers the artist
            # image/album art/similar tracks fetches (each of those has its
            # own dedup/cache, so this is a cheap no-op when already showing
            # the right thing) - otherwise a title repeating later in the
            # session (common for radio) leaves the Similar Tracks panel
            # stuck blank, since set_now_playing() unconditionally clears it
            # and nothing here was refilling it.
            self._on_lookup_result(self.lookup_cache[title])
            return
        self._stop_lookup_thread()
        self.lookup_thread = TrackLookupThread(title, self.lastfm_api_key)
        self.lookup_thread.result_ready.connect(self._on_lookup_result)
        self.lookup_thread.finished.connect(self._on_lookup_thread_finished)
        self.lookup_thread.finished.connect(self.lookup_thread.deleteLater)
        self.lookup_thread.start()

    def _on_lookup_result(self, result):
        if result.get("found"):
            # Only cache successful lookups. A failure (all three sources
            # unreachable/no answer) is often transient - caching it would
            # permanently block retries for this title if it repeats later
            # in the session (common for radio).
            self._cache_set(self.lookup_cache, result["raw_title"], result)
        if result["raw_title"] != self.last_lookup_title:
            return  # superseded by a newer track since this lookup started
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
        album_name = result.get("album") or ""
        if release_mbid or itunes_artwork_url or (track_artist and track_title):
            self._fetch_album_art(release_mbid, itunes_artwork_url, track_artist, track_title, album_name)
        elif result.get("found"):
            self._set_album_caption(album_name)
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

    def _set_artist_caption(self, artist_name):
        self.artist_caption.setText(artist_name or "Artist")

    def _fetch_artist_image(self, artist_name):
        artist_name = (artist_name or "").strip()
        if not artist_name:
            self.last_image_artist = None
            self._stop_artist_image_thread()
            self._set_artist_image_placeholder("No image")
            self._set_artist_caption("")
            return
        self._set_artist_caption(artist_name)
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
        self._cache_set(self.artist_image_cache, artist_name, raw)
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
        self._cache_set(self.artist_image_cache, artist_name, None)
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

    def _set_album_caption(self, album_name):
        self.album_caption.setText(album_name or "Album")

    def _fetch_album_art(self, release_mbid, itunes_artwork_url="", artist_name="", track_title="", album_name=""):
        release_mbid = (release_mbid or "").strip()
        itunes_artwork_url = (itunes_artwork_url or "").strip()
        artist_name = (artist_name or "").strip()
        track_title = (track_title or "").strip()
        album_name = (album_name or "").strip()
        self._set_album_caption(album_name)
        # Cache/dedup key: prefer the MBID (stable, ID-based), then the
        # iTunes artwork URL, and finally the artist/title pair when neither
        # of those was available (Deezer-only lookup). The artist/title case
        # uses a tuple rather than a joined string so two different
        # artist/title pairs can never collide onto the same cache key (e.g.
        # artist "A B" + title "C" vs. artist "A" + title "B C"), and so it
        # can never collide with a release_mbid/itunes_artwork_url string key.
        cache_key = release_mbid or itunes_artwork_url or (
            (artist_name, track_title) if artist_name and track_title else ""
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
        self._cache_set(self.album_art_cache, cache_key, raw)
        if cache_key != self.last_album_key:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(raw)
        if not pixmap.isNull():
            self._set_album_art_pixmap(pixmap)
        else:
            self._set_album_art_placeholder("No cover art")

    def _on_album_art_not_found(self, cache_key):
        self._cache_set(self.album_art_cache, cache_key, None)
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
            self.last_similar_tracks_title = None
            self._stop_similar_tracks_thread()
            self.track_info_dialog.set_similar_tracks([])
            return
        if artist_name == self.last_similar_tracks_artist:
            return  # already showing / fetching similar tracks for this artist
        self.last_similar_tracks_artist = artist_name
        self.last_similar_tracks_title = track_title

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
        self._cache_set(self.similar_tracks_cache, artist_name, tracks)
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
