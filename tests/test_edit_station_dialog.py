from radiotop_gui import EditStationDialog


def test_values_returns_current_field_contents(qapp):
    dlg = EditStationDialog("My Station", "http://example.com:7700/stream.mp3")
    assert dlg.values() == ("My Station", "http://example.com:7700/stream.mp3")


def test_values_are_stripped_of_surrounding_whitespace(qapp):
    dlg = EditStationDialog("  My Station  ", "  http://example.com/stream.mp3  ")
    name, url = dlg.values()
    assert name == "My Station"
    assert url == "http://example.com/stream.mp3"


def test_values_reflect_edits(qapp):
    dlg = EditStationDialog("Old Name", "http://old.example.com/stream.mp3")
    dlg.name_edit.setText("New Name")
    dlg.url_edit.setText("http://new.example.com/stream.mp3")
    assert dlg.values() == ("New Name", "http://new.example.com/stream.mp3")
