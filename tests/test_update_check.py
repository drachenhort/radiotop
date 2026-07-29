import json

import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

import radiotop_gui as rt
from radiotop_gui import UpdateCheckThread, _parse_version


# ------------------------------------------------------------ _parse_version
@pytest.mark.parametrize(
    "version_str, expected",
    [
        ("0.32", (0, 32)),
        ("0.9", (0, 9)),
        ("v0.23", (0, 23)),
        ("V0.2", (0, 2)),
        ("", ()),
    ],
)
def test_parse_version(version_str, expected):
    assert _parse_version(version_str) == expected


def test_parse_version_orders_numerically_not_lexically():
    assert _parse_version("0.9") < _parse_version("0.10")
    assert _parse_version("0.32") > _parse_version("0.31")


# -------------------------------------------------------- UpdateCheckThread
class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_update_check_thread_reports_available_when_newer(monkeypatch):
    payload = {"tag_name": "0.99", "body": "notes", "html_url": "https://example.invalid/0.99"}
    monkeypatch.setattr(
        "threads.urllib.request.urlopen",
        lambda req, timeout=None, **kwargs: _FakeResponse(payload),
    )
    thread = UpdateCheckThread("0.32")
    results = []
    thread.check_complete.connect(results.append)
    thread.run()
    assert results == [
        {
            "available": True,
            "latest_version": "0.99",
            "notes": "notes",
            "html_url": "https://example.invalid/0.99",
        }
    ]


def test_update_check_thread_reports_unavailable_when_current(monkeypatch):
    payload = {"tag_name": "0.32", "body": "", "html_url": "https://example.invalid/0.32"}
    monkeypatch.setattr(
        "threads.urllib.request.urlopen",
        lambda req, timeout=None, **kwargs: _FakeResponse(payload),
    )
    thread = UpdateCheckThread("0.32")
    results = []
    thread.check_complete.connect(results.append)
    thread.run()
    assert results[0]["available"] is False


def test_update_check_thread_reports_error_on_network_failure(monkeypatch):
    def _raise(req, timeout=None, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr("threads.urllib.request.urlopen", _raise)
    thread = UpdateCheckThread("0.32")
    results = []
    thread.check_complete.connect(results.append)
    thread.run()
    assert "error" in results[0]


# --------------------------------------------------- MainWindow integration
def test_on_update_check_complete_shows_dialog_when_available(main_window_stub, monkeypatch, qapp):
    captured = {}

    def fake_exec(self):
        captured["text"] = self.text()
        captured["buttons"] = [b.text() for b in self.buttons()]
        return 0

    class _ParentlessMessageBox(QMessageBox):
        # MainWindowStub is a plain QObject, not a QWidget, so it can't be
        # used as a QMessageBox parent - substitute None, since only the
        # parenting is affected, not the dialog's own behavior under test.
        def __init__(self, parent=None, *a, **k):
            super().__init__(None, *a, **k)

    # Qt reorders buttons by role for display, so pick "Open Release Page"
    # by label rather than assuming a fixed position.
    def _click_open_release_page(self):
        return next(b for b in self.buttons() if b.text() == "Open Release Page")

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", _click_open_release_page)
    monkeypatch.setattr(rt, "QMessageBox", _ParentlessMessageBox)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: opened.append(url.toString())))

    main_window_stub.update_check_thread = None
    rt.MainWindow._on_update_check_complete(
        main_window_stub,
        {
            "available": True,
            "latest_version": "0.99",
            "notes": "some notes",
            "html_url": "https://example.invalid/release",
        },
        manual=True,
    )
    assert "0.99" in captured["text"]
    assert "some notes" in captured["text"]
    assert "Open Release Page" in captured["buttons"]
    assert opened == ["https://example.invalid/release"]


def test_on_update_check_complete_silent_when_up_to_date_and_automatic(main_window_stub, monkeypatch):
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a))
    main_window_stub.update_check_thread = None
    rt.MainWindow._on_update_check_complete(main_window_stub, {"available": False}, manual=False)
    assert infos == []


def test_on_update_check_complete_notifies_when_up_to_date_and_manual(main_window_stub, monkeypatch):
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a))
    main_window_stub.update_check_thread = None
    rt.MainWindow._on_update_check_complete(main_window_stub, {"available": False}, manual=True)
    assert len(infos) == 1


def test_on_update_check_complete_silent_on_error_when_automatic(main_window_stub, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    main_window_stub.update_check_thread = None
    rt.MainWindow._on_update_check_complete(main_window_stub, {"error": "boom"}, manual=False)
    assert warnings == []


def test_on_update_check_complete_warns_on_error_when_manual(main_window_stub, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    main_window_stub.update_check_thread = None
    rt.MainWindow._on_update_check_complete(main_window_stub, {"error": "boom"}, manual=True)
    assert len(warnings) == 1


def test_check_for_updates_skips_when_already_checking(main_window_stub):
    main_window_stub.update_check_thread = object()  # sentinel: a check is "in flight"
    rt.MainWindow._check_for_updates(main_window_stub, manual=True)
    assert main_window_stub._status_bar.messages  # status message shown, no new thread started
