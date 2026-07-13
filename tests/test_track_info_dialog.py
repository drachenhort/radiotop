from radiotop_gui import TrackInfoDialog


def test_set_waiting_resets_labels(qapp):
    dlg = TrackInfoDialog()
    dlg.apply_lookup({
        "found": True, "title": "T", "artist": "A", "album": "Al",
        "genre": "G", "year": "2020", "sources": ["MusicBrainz"],
    })
    dlg.set_waiting()
    assert dlg.title_label.text() == "Waiting for stream metadata..."
    assert dlg.artist_label.text() == ""
    assert dlg.album_label.text() == "Album: -"
    assert dlg.genre_value.text() == "-"
    assert dlg.year_value.text() == "-"
    assert dlg.status_label.text() == ""


def test_set_no_track(qapp):
    dlg = TrackInfoDialog()
    assert dlg.title_label.text() == "No track playing"


def test_set_now_playing_shows_raw_title_while_looking_up(qapp):
    dlg = TrackInfoDialog()
    dlg.set_now_playing("Artist - Title")
    assert dlg.title_label.text() == "Artist - Title"
    assert dlg.status_label.text() == "Looking up track details..."


def test_set_now_playing_falls_back_when_raw_title_empty(qapp):
    dlg = TrackInfoDialog()
    dlg.set_now_playing("")
    assert dlg.title_label.text() == "Unknown track"


def test_apply_lookup_not_found_shows_message(qapp):
    dlg = TrackInfoDialog()
    dlg.apply_lookup({"found": False})
    assert dlg.status_label.text() == "No additional details found for this track."


def test_apply_lookup_not_found_includes_lastfm_error(qapp):
    dlg = TrackInfoDialog()
    dlg.apply_lookup({"found": False, "lastfm_error": "Track not found on Last.fm"})
    assert "Last.fm: Track not found on Last.fm" in dlg.status_label.text()


def test_apply_lookup_found_populates_fields(qapp):
    dlg = TrackInfoDialog()
    dlg.apply_lookup({
        "found": True,
        "title": "One More Time",
        "artist": "Daft Punk",
        "album": "Discovery",
        "genre": "House",
        "year": "2001",
        "sources": ["MusicBrainz", "Last.fm"],
    })
    assert dlg.title_label.text() == "One More Time"
    assert dlg.artist_label.text() == "Daft Punk"
    assert dlg.album_label.text() == "Album: Discovery"
    assert dlg.genre_value.text() == "House"
    assert dlg.year_value.text() == "2001"
    assert dlg.status_label.text() == "Source: MusicBrainz, Last.fm"


def test_apply_lookup_widens_dialog_for_long_album_name(qapp):
    dlg = TrackInfoDialog()
    long_album = "A" * (TrackInfoDialog.ALBUM_LENGTH_THRESHOLD + 5)
    dlg.apply_lookup({"found": True, "album": long_album})
    assert dlg.width() >= TrackInfoDialog.WIDE_WIDTH


def test_apply_lookup_missing_fields_show_placeholder(qapp):
    dlg = TrackInfoDialog()
    dlg.apply_lookup({"found": True})
    assert dlg.album_label.text() == "Album: -"
    assert dlg.genre_value.text() == "-"
    assert dlg.year_value.text() == "-"
