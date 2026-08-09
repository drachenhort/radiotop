from util import (
    format_reconnect_message,
    select_output_device_index,
    should_attempt_reconnect,
    should_notify_immediately,
)


def test_select_output_device_index_finds_target():
    ids = [b"aaa", b"bbb", b"ccc"]
    assert select_output_device_index(ids, b"bbb") == 1


def test_select_output_device_index_defaults_to_zero_when_not_found():
    ids = [b"aaa", b"bbb"]
    assert select_output_device_index(ids, b"zzz") == 0


def test_select_output_device_index_defaults_to_zero_when_target_none():
    ids = [b"aaa", b"bbb"]
    assert select_output_device_index(ids, None) == 0


def test_select_output_device_index_defaults_to_zero_when_no_ids():
    assert select_output_device_index([], None) == 0


def test_select_output_device_index_defaults_to_zero_when_target_empty_bytes():
    ids = [b"aaa", b"bbb", b""]
    assert select_output_device_index(ids, b"") == 0


def test_should_attempt_reconnect_true_when_all_conditions_met():
    assert should_attempt_reconnect(True, True, 3) is True


def test_should_attempt_reconnect_false_when_auto_reconnect_disabled():
    assert should_attempt_reconnect(False, True, 3) is False


def test_should_attempt_reconnect_false_when_no_current_station():
    assert should_attempt_reconnect(True, False, 3) is False


def test_should_attempt_reconnect_false_when_no_attempts_remaining():
    assert should_attempt_reconnect(True, True, 0) is False


def test_should_attempt_reconnect_false_when_attempts_negative():
    assert should_attempt_reconnect(True, True, -1) is False


def test_format_reconnect_message_first_attempt():
    assert format_reconnect_message(1, 5) == "Connection dropped, reconnecting (1/5)..."


def test_format_reconnect_message_last_attempt():
    assert format_reconnect_message(5, 5) == "Connection dropped, reconnecting (5/5)..."


def test_should_notify_immediately_true_when_no_artist():
    assert should_notify_immediately(None, icon_cached=False) is True
    assert should_notify_immediately("", icon_cached=False) is True


def test_should_notify_immediately_true_when_icon_cached():
    assert should_notify_immediately("Some Artist", icon_cached=True) is True


def test_should_notify_immediately_false_when_artist_and_no_icon():
    assert should_notify_immediately("Some Artist", icon_cached=False) is False


import subprocess
from unittest.mock import MagicMock, patch

from util import _SleepInhibitor


def test_sleep_inhibitor_windows_acquire_calls_set_thread_execution_state():
    inhibitor = _SleepInhibitor()
    fake_kernel32 = MagicMock()
    with patch("util.sys.platform", "win32"), patch("util.ctypes") as fake_ctypes:
        fake_ctypes.windll.kernel32 = fake_kernel32
        inhibitor.acquire()
    # ES_CONTINUOUS | ES_SYSTEM_REQUIRED = 0x80000000 | 0x00000001
    fake_kernel32.SetThreadExecutionState.assert_called_once_with(0x80000001)
    assert inhibitor._active is True


def test_sleep_inhibitor_windows_release_restores_es_continuous():
    inhibitor = _SleepInhibitor()
    inhibitor._active = True
    fake_kernel32 = MagicMock()
    with patch("util.sys.platform", "win32"), patch("util.ctypes") as fake_ctypes:
        fake_ctypes.windll.kernel32 = fake_kernel32
        inhibitor.release()
    fake_kernel32.SetThreadExecutionState.assert_called_once_with(0x80000000)
    assert inhibitor._active is False


def test_sleep_inhibitor_macos_acquire_spawns_caffeinate():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    with patch("util.sys.platform", "darwin"), patch(
        "util.subprocess.Popen", return_value=fake_proc
    ) as fake_popen:
        inhibitor.acquire()
    fake_popen.assert_called_once_with(["caffeinate", "-i"])
    assert inhibitor._process is fake_proc
    assert inhibitor._active is True


def test_sleep_inhibitor_macos_release_terminates_process():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    inhibitor._active = True
    inhibitor._process = fake_proc
    with patch("util.sys.platform", "darwin"):
        inhibitor.release()
    fake_proc.terminate.assert_called_once()
    assert inhibitor._process is None
    assert inhibitor._active is False


def test_sleep_inhibitor_linux_acquire_spawns_systemd_inhibit():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    with patch("util.sys.platform", "linux"), patch(
        "util.subprocess.Popen", return_value=fake_proc
    ) as fake_popen:
        inhibitor.acquire()
    fake_popen.assert_called_once_with(
        [
            "systemd-inhibit",
            "--what=idle:sleep",
            "--who=RadioTop",
            "--why=Streaming audio",
            "sleep",
            "infinity",
        ]
    )
    assert inhibitor._process is fake_proc
    assert inhibitor._active is True


def test_sleep_inhibitor_linux_release_terminates_process():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    inhibitor._active = True
    inhibitor._process = fake_proc
    with patch("util.sys.platform", "linux"):
        inhibitor.release()
    fake_proc.terminate.assert_called_once()
    assert inhibitor._process is None
    assert inhibitor._active is False


def test_sleep_inhibitor_linux_acquire_missing_systemd_inhibit_is_noop():
    inhibitor = _SleepInhibitor()
    with patch("util.sys.platform", "linux"), patch(
        "util.subprocess.Popen", side_effect=FileNotFoundError
    ):
        inhibitor.acquire()  # must not raise
    assert inhibitor._active is False
    assert inhibitor._process is None


def test_sleep_inhibitor_acquire_is_idempotent():
    inhibitor = _SleepInhibitor()
    fake_proc = MagicMock()
    with patch("util.sys.platform", "darwin"), patch(
        "util.subprocess.Popen", return_value=fake_proc
    ) as fake_popen:
        inhibitor.acquire()
        inhibitor.acquire()
    fake_popen.assert_called_once()  # second acquire() is a no-op


def test_sleep_inhibitor_release_before_acquire_is_noop():
    inhibitor = _SleepInhibitor()
    with patch("util.sys.platform", "darwin") as _:
        inhibitor.release()  # must not raise, no process to terminate
    assert inhibitor._active is False
