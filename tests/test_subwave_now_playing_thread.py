from threads import SubwaveNowPlayingThread


def _drive_run(thread, poll_results, stop_after=None):
    """Runs thread.run() synchronously, feeding one (now, state) pair per
    poll from poll_results and returning instantly instead of sleeping
    POLL_INTERVAL seconds between polls. Stops the loop once poll_results
    is exhausted (or after stop_after polls, if given)."""
    calls = []

    def fake_poll_once():
        calls.append(None)
        if len(calls) > len(poll_results) or (stop_after and len(calls) > stop_after):
            thread._stop_event.set()
            return None, None
        return poll_results[len(calls) - 1]

    def fake_wait(timeout):
        if len(calls) >= len(poll_results) or (stop_after and len(calls) >= stop_after):
            thread._stop_event.set()
        return thread._stop_event.is_set()

    thread._poll_once = fake_poll_once
    thread._stop_event.wait = fake_wait
    thread.run()
    return calls


def test_gives_up_after_max_consecutive_failures_before_any_success(qapp):
    thread = SubwaveNowPlayingThread("http://example.com:8080")
    unavailable = []
    now_playing = []
    thread.unavailable.connect(lambda: unavailable.append(True))
    thread.now_playing_ready.connect(lambda p: now_playing.append(p))

    _drive_run(thread, [(None, None)] * thread.MAX_CONSECUTIVE_FAILURES)

    assert unavailable == [True]
    assert now_playing == []


def test_transient_failures_after_a_success_do_not_give_up(qapp):
    # A station that answered once (proving it really does run a SUB/WAVE
    # API) then hit a couple of failed polls in a row - e.g. a brief wifi
    # drop - should keep polling rather than permanently declaring itself
    # unavailable, since a real success already ruled out "wrong port /
    # not a SUB/WAVE station" as the explanation for the failures.
    thread = SubwaveNowPlayingThread("http://example.com:8080")
    unavailable = []
    now_playing = []
    thread.unavailable.connect(lambda: unavailable.append(True))
    thread.now_playing_ready.connect(lambda p: now_playing.append(p))

    success = ({"nowPlaying": {"artist": "A", "title": "T"}}, {})
    failures = [(None, None)] * (thread.MAX_CONSECUTIVE_FAILURES + 3)

    _drive_run(thread, [success] + failures + [success])

    assert unavailable == []
    assert len(now_playing) == 2
