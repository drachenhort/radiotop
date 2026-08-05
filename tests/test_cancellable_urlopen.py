import socket
import threading
import time

import pytest

from threads import _CancellableRequestThread, _cancellable_urlopen


def _slow_urlopen(delay):
    def _fake(req, timeout=None, **kwargs):
        time.sleep(delay)
        return "should never be observed - stop() should have returned first"

    return _fake


def test_cancellable_urlopen_returns_none_promptly_when_stopped_mid_connect(monkeypatch):
    # Regression test: a plain urllib.request.urlopen(timeout=10) blocks in
    # one uninterruptible call for up to the full timeout - there's no
    # response object to shut down from another thread until it returns, so
    # stop() has no effect on a thread stuck connecting to a stalled/
    # firewalled host until that timeout elapses on its own.
    # _cancellable_urlopen runs the real call on a background pool and polls
    # it instead of blocking directly, so it (and the QThread calling it)
    # can return as soon as stop_event is set - well before the slow call
    # underneath actually finishes.
    monkeypatch.setattr("threads.urllib.request.urlopen", _slow_urlopen(delay=3.0))
    stop_event = threading.Event()
    threading.Timer(0.1, stop_event.set).start()

    start = time.monotonic()
    result = _cancellable_urlopen(object(), timeout=10, stop_event=stop_event)
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed < 1.0  # nowhere near the 3s the underlying call takes, or the 10s timeout


def test_cancellable_urlopen_returns_result_when_not_stopped(monkeypatch):
    monkeypatch.setattr("threads.urllib.request.urlopen", lambda req, timeout=None, **kwargs: "the-response")
    stop_event = threading.Event()

    result = _cancellable_urlopen(object(), timeout=10, stop_event=stop_event)

    assert result == "the-response"


def test_cancellable_urlopen_propagates_real_errors(monkeypatch):
    def _raise(req, timeout=None, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr("threads.urllib.request.urlopen", _raise)
    stop_event = threading.Event()

    with pytest.raises(ValueError):
        _cancellable_urlopen(object(), timeout=10, stop_event=stop_event)


def test_cancellable_urlopen_propagates_socket_timeout_promptly(monkeypatch):
    # Regression test: on Python 3.11+, socket.timeout and
    # concurrent.futures.TimeoutError are both aliases of the same builtin
    # TimeoutError. _cancellable_urlopen's poll loop used to catch
    # "TimeoutError" to mean "the 0.2s poll interval elapsed, keep
    # waiting" - which also caught a genuine socket.timeout raised by the
    # real urlopen() call once its own timeout fired, silently discarding
    # it and spinning the poll loop forever instead of ever returning.
    # This must propagate the real timeout instead of hanging.
    def _raise_socket_timeout(req, timeout=None, **kwargs):
        time.sleep(0.05)
        raise socket.timeout("timed out")

    monkeypatch.setattr("threads.urllib.request.urlopen", _raise_socket_timeout)
    stop_event = threading.Event()  # never set - nothing should be relying on it here

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        _cancellable_urlopen(object(), timeout=10, stop_event=stop_event)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # must not spin forever re-polling an already-failed future


def test_cancellable_urlopen_closes_late_response_after_giving_up(monkeypatch):
    # If stop_event fires before the real urlopen() call finishes,
    # _cancellable_urlopen returns None without waiting for it - but that
    # call keeps running in the background. When it eventually succeeds,
    # its response must still get closed instead of leaking an open socket
    # until garbage collection.
    closed = threading.Event()

    class _FakeResponse:
        def close(self):
            closed.set()

    def _delayed_response(req, timeout=None, **kwargs):
        time.sleep(0.3)
        return _FakeResponse()

    monkeypatch.setattr("threads.urllib.request.urlopen", _delayed_response)
    stop_event = threading.Event()
    threading.Timer(0.05, stop_event.set).start()

    result = _cancellable_urlopen(object(), timeout=10, stop_event=stop_event)
    assert result is None

    assert closed.wait(timeout=2.0)


def test_urlopen_stop_returns_promptly_even_mid_connect(monkeypatch, qapp):
    # Same regression, exercised through _CancellableRequestThread._urlopen
    # (the actual seam every lookup thread uses) rather than the helper
    # function directly.
    monkeypatch.setattr("threads.urllib.request.urlopen", _slow_urlopen(delay=3.0))
    thread = _CancellableRequestThread()

    start = time.monotonic()
    threading.Timer(0.1, thread.stop).start()
    result = thread._urlopen(object(), timeout=10)
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed < 1.0
