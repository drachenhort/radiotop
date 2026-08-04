from util import select_output_device_index


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
