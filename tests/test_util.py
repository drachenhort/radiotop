from util import select_output_device_index, should_attempt_reconnect


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
