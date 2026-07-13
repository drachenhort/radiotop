import pytest

from radiotop_gui import DEFAULT_STREAM_FILENAME, DEFAULT_STREAM_PORT, _normalize_station_url


def test_adds_both_port_and_filename_when_missing():
    url, adjusted = _normalize_station_url("http://example.com/")
    assert adjusted is True
    assert url == f"http://example.com:{DEFAULT_STREAM_PORT}/{DEFAULT_STREAM_FILENAME}"


def test_adds_only_missing_filename_when_port_present():
    url, adjusted = _normalize_station_url("http://example.com:8000/")
    assert adjusted is True
    assert url == f"http://example.com:8000/{DEFAULT_STREAM_FILENAME}"


def test_adds_only_missing_port_when_filename_present():
    url, adjusted = _normalize_station_url("http://example.com/stream.mp3")
    assert adjusted is True
    assert url == f"http://example.com:{DEFAULT_STREAM_PORT}/stream.mp3"


def test_leaves_url_untouched_when_both_present():
    original = f"http://example.com:8000/stream.mp3"
    url, adjusted = _normalize_station_url(original)
    assert adjusted is False
    assert url == original


def test_filename_check_is_case_insensitive():
    original = "http://example.com:8000/STREAM.MP3"
    url, adjusted = _normalize_station_url(original)
    assert adjusted is False
    assert url == original


def test_filename_match_anywhere_in_path_counts_as_present():
    original = "http://example.com:8000/live/stream.mp3?foo=bar"
    url, adjusted = _normalize_station_url(original)
    assert adjusted is False
    assert url == original


def test_preserves_userinfo_when_adding_port():
    url, adjusted = _normalize_station_url("http://user:pass@example.com/stream.mp3")
    assert adjusted is True
    assert url == f"http://user:pass@example.com:{DEFAULT_STREAM_PORT}/stream.mp3"


def test_preserves_query_and_fragment():
    url, adjusted = _normalize_station_url("http://example.com/?token=abc#frag")
    assert adjusted is True
    assert url.startswith(f"http://example.com:{DEFAULT_STREAM_PORT}/{DEFAULT_STREAM_FILENAME}")
    assert url.endswith("?token=abc#frag")


def test_path_without_trailing_slash_gets_filename_appended():
    url, adjusted = _normalize_station_url("http://example.com:8000/live")
    assert adjusted is True
    assert url == f"http://example.com:8000/live/{DEFAULT_STREAM_FILENAME}"
