"""Stable queue identities and bounded row reuse across large scroll ranges."""

from types import SimpleNamespace

from cyoa_downloader_app.gui import app, virtual_queue
from cyoa_downloader_app.gui.queue_import import QueueImport


def items(count):
    return [{"_queue_id": f"id-{i}", "url": "https://example.test/same/", "filename": str(i), "mode": "auto"}
            for i in range(count)]


def test_model_uses_identity_for_duplicates_and_keeps_list_owner():
    data = items(4)
    model = virtual_queue.QueueIndex(data)
    original = data[0]
    model.states["id-0"] = "error"
    model.swap(0, 3)
    assert model.get("id-0") is original
    assert model.positions["id-0"] == 3
    assert model.remove_ids({"id-1", "id-2"}) == 2
    assert model.items is data
    assert data == [items(4)[3], original]
    assert model.states == {"id-0": "error"}
    model.remove_ids({"id-0"})
    assert not model.states and model.get("id-0") is None


def test_append_allocates_missing_or_duplicate_ids_without_replacing_valid_ids():
    data = items(2)
    model = virtual_queue.QueueIndex(data)
    data.extend([dict(data[0]), {"url": "another"}])
    model.appended()
    assert data[0]["_queue_id"] == "id-0"
    assert len(model.positions) == len(data) == 4
    assert len({item["_queue_id"] for item in data}) == 4
    model.rebuild()
    assert len(model.positions) == 4


def test_clear_cannot_reuse_old_status_for_new_row():
    data = items(3)
    model = virtual_queue.QueueIndex(data)
    model.states["id-0"] = "done"
    model.clear()
    data.append({"url": "https://example.test/same/"})
    model.appended()
    assert len(model.positions) == 1 and not model.states
    assert model.get("id-0") is None


class Canvas:
    def __init__(self):
        self.y = 0
        self.windows = {}
        self.pending = {}
        self.serial = 0

    def canvasy(self, y):
        return self.y + y

    def winfo_height(self):
        return 140

    def winfo_width(self):
        return 900

    def itemconfigure(self, key, **kwargs):
        self.windows.setdefault(key, {}).update(kwargs)

    def coords(self, key, x, y):
        self.windows.setdefault(key, {}).update(x=x, y=y)

    def after(self, delay, callback):
        self.serial += 1
        self.pending[self.serial] = callback
        return self.serial

    def after_cancel(self, key):
        self.pending.pop(key, None)

    def configure(self, **kwargs):
        self.region = kwargs["scrollregion"]

    def focus_set(self):
        pass

    def pump(self):
        while self.pending:
            key = next(iter(self.pending))
            self.pending.pop(key)()


class Row:
    def __init__(self, view):
        self.identity = None
        self.focus = False
        self.window = object()

    def focused(self):
        return self.focus

    def bind_item(self, item):
        self.identity = item["_queue_id"]

    def legacy_tuple(self):
        return (self.window,) * 5


def make_view(monkeypatch, count=10000):
    monkeypatch.setattr(virtual_queue, "VirtualRow", Row)
    view = virtual_queue.VirtualQueue.__new__(virtual_queue.VirtualQueue)
    view.model = virtual_queue.QueueIndex(items(count))
    view.canvas = Canvas()
    view.gui = SimpleNamespace(_queue_rows=[], _update_queue_count=lambda: None,
                               _qlist=SimpleNamespace(_apply_widget_scaling=lambda value: value))
    view.rows = []
    view.closed = False
    view.after_id = None
    view.drag_identity = None
    return view


def test_10000_rows_only_allocate_viewport_pool(monkeypatch):
    view = make_view(monkeypatch)
    for index in (0, 100, 2000, 8000, 9998, 5000, 0):
        view.canvas.y = index * 72
        view.render()
        view.canvas.pump()
        assert len(view.rows) <= 7
        active = [row.identity for row in view.rows if row.identity is not None]
        assert active
        assert f"id-{max(0, index - 2)}" in active
    assert len(view.gui._queue_rows) <= 7


def test_editor_or_dragged_item_is_not_rebound_on_scroll(monkeypatch):
    view = make_view(monkeypatch)
    view.render()
    row = next(row for row in view.rows if row.identity == "id-0")
    row.focus = True
    view.canvas.y = 5000 * 72
    view.render()
    view.canvas.pump()
    assert row.identity == "id-0" and len(view.rows) <= 8
    row.focus = False
    view.drag_identity = "id-0"
    view.canvas.y = 9000 * 72
    view.render()
    assert row.identity == "id-0"
    view.drag_identity = None
    view.render()
    assert row.identity != "id-0"


def test_offscreen_state_survives_scroll_and_deleted_id_is_ignored(monkeypatch):
    view = make_view(monkeypatch)
    view.set_state("id-9000", "error")
    view.canvas.pump()
    assert view.model.states["id-9000"] == "error"
    view.remove_ids({"id-9000"})
    view.set_state("id-9000", "done")
    view.canvas.pump()
    assert "id-9000" not in view.model.states


def test_active_snapshot_status_uses_original_id_after_reorder(monkeypatch):
    view = make_view(monkeypatch, 3)
    pending = []
    gui = SimpleNamespace(_queue_view=view, _queue_data=view.model.items,
                          _active_run_order=("id-0", "id-1", "id-2"), _run_on_ui_thread=pending.append)
    view.swap(0, 2)
    app.CYOADownloaderGUI._set_dot(gui, 0, "running")
    view.remove_ids({"id-1"})
    app.CYOADownloaderGUI._set_dot(gui, 1, "done")
    for callback in pending:
        callback()
    assert view.model.states == {"id-0": "running"}


def test_closed_view_cancels_its_render_timer(monkeypatch):
    view = make_view(monkeypatch)
    view.request_render()
    view.close()
    assert not view.canvas.pending
    view.request_render()
    assert not view.canvas.pending


def test_bulk_import_commits_model_in_slices_without_per_item_widgets(monkeypatch):
    view = make_view(monkeypatch, 0)
    gui = view.gui
    gui._queue_view = view
    gui._queue_data = view.model.items
    gui._mode_var = "auto"
    gui._is_running = False
    gui.root = view.canvas
    gui._set_status = lambda message: None
    gui._make_queue_row = lambda *a: (_ for _ in ()).throw(AssertionError("per-item widget creation"))
    task = QueueImport(gui, "fixture")
    task.accept([{"url": f"https://example.test/{i}"} for i in range(10000)])
    assert 0 < len(gui._queue_data) <= 256
    assert gui._queue_import_busy
    view.canvas.pump()
    assert len(gui._queue_data) == len(view.model.positions) == 10000
    assert not gui._queue_import_busy and len(view.rows) <= 7


def test_clear_during_virtual_import_prevents_late_rows(monkeypatch):
    view = make_view(monkeypatch, 0)
    gui = view.gui
    gui._queue_view = view
    gui._queue_data = view.model.items
    gui._mode_var = "auto"
    gui._is_running = False
    gui.root = view.canvas
    gui._set_status = lambda message: None
    QueueImport(gui, "fixture").accept([{"url": str(i)} for i in range(1000)])
    app.CYOADownloaderGUI._clear_queue(gui)
    view.canvas.pump()
    assert not gui._queue_data and not gui._queue_import_busy
    assert not view.model.positions and not view.model.states


def test_mode_choice_from_old_menu_does_not_update_recycled_badge(monkeypatch):
    view = make_view(monkeypatch, 3)
    gui = SimpleNamespace(_queue_data=view.model.items, _queue_view=view,
                          QUEUE_MODE_OPTIONS=("auto", "website_folder"))
    item = gui._queue_data[0]
    item["auto_detected"] = True
    badge = SimpleNamespace(configure=lambda **kw: (_ for _ in ()).throw(AssertionError("recycled badge changed")))
    app.CYOADownloaderGUI._set_queue_item_mode(gui, item, "website-folder", badge)
    assert item["mode"] == "website_folder" and "auto_detected" not in item
    assert gui._queue_data[1]["mode"] == "auto"


def test_existing_numeric_identity_is_normalized():
    model = virtual_queue.QueueIndex([{"_queue_id": 7, "url": "url"}])
    assert model.items[0]["_queue_id"] == "7"
    assert model.get("7") is model.items[0]
