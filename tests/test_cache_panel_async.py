"""Cache operations must stay outside Tk and outlive closed views safely."""

import queue
import sys
import threading
from types import SimpleNamespace

import pytest

from cyoa_downloader_app.gui import app, final_behaviors
from cyoa_downloader_app.gui import cache_maintenance as maintenance


class Owner:
    def __init__(self):
        self.owner_thread = threading.get_ident()
        self.alive = True
        self.destroyed = None

    def bind(self, event, callback, add):
        assert threading.get_ident() == self.owner_thread
        assert event == "<Destroy>" and add == "+"
        self.destroyed = callback

    def winfo_exists(self):
        assert threading.get_ident() == self.owner_thread, "Worker entered Tk"
        return self.alive

    def destroy(self):
        self.alive = False
        self.destroyed(SimpleNamespace(widget=self))


def make_view(service):
    owner = Owner()
    renders, states = [], []

    def render(*args):
        assert threading.get_ident() == owner.owner_thread
        assert owner.alive
        renders.append(args)

    def controls(busy):
        assert threading.get_ident() == owner.owner_thread
        assert owner.alive
        states.append(busy)

    return service.attach(owner, render, controls), owner, renders, states


def test_views_share_one_operation_and_only_tk_applies_result():
    posted = queue.SimpleQueue()
    service = maintenance.CacheMaintenance(posted.put)
    first, _owner, renders, states = make_view(service)
    second, _other_owner, other_renders, other_states = make_view(service)
    entered, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()

    def work():
        assert threading.get_ident() != main_thread
        entered.set()
        assert release.wait(2)
        return {"stats": {"entries": 1}}

    try:
        assert first.start("refresh", work)
        assert entered.wait(1)
        assert service.busy and not first.ready and not second.ready
        assert not second.start("clear", lambda: pytest.fail("duplicate operation"))
        assert states == other_states == [False, True]
        release.set()
        service.thread.join(2)
        assert not renders and not other_renders
        posted.get_nowait()()
        assert renders == other_renders == [("refresh", {"stats": {"entries": 1}}, None)]
        assert first.ready and second.ready
        assert states == other_states == [False, True, False]
    finally:
        release.set()
        service.thread.join(2)
        service.close()


def test_close_and_reopen_while_busy_does_not_start_another_scan():
    posted = queue.SimpleQueue()
    service = maintenance.CacheMaintenance(posted.put)
    old, owner, old_renders, _ = make_view(service)
    release = threading.Event()
    try:
        assert old.start("clear", lambda: release.wait(2) and {"removed": 4})
        owner.destroy()
        new, _new_owner, new_renders, states = make_view(service)
        assert not old.active and not new.ready
        assert len(service.views) == 1 and states == [True]
        assert not new.start("refresh", lambda: pytest.fail("duplicate scan"))
        release.set()
        service.thread.join(2)
        posted.get_nowait()()
        assert not old_renders
        assert new_renders == [("clear", {"removed": 4}, None)]
        assert new.ready and states == [True, False]
    finally:
        release.set()
        service.thread.join(2)
        service.close()


@pytest.mark.parametrize("close_before_post", [False, True])
def test_shutdown_drops_pending_results_and_rejects_new_work(close_before_post):
    posted = queue.SimpleQueue()
    service = maintenance.CacheMaintenance(posted.put)
    view, _owner, renders, _ = make_view(service)
    release = threading.Event()
    try:
        assert view.start("save", lambda: release.wait(2) and {"limit_mb": 1024})
        if close_before_post:
            service.close()
            release.set()
            service.thread.join(2)
            assert posted.empty()
        else:
            release.set()
            service.thread.join(2)
            service.close()
            posted.get_nowait()()
        assert not renders and not service.views
        assert not view.start("refresh", lambda: None)
        assert not service.start("refresh", lambda: None)
    finally:
        release.set()
        service.thread.join(2)


def test_failure_restores_controls_and_can_be_retried():
    posted = queue.SimpleQueue()
    service = maintenance.CacheMaintenance(posted.put)
    view, _owner, renders, states = make_view(service)

    def fail():
        raise OSError("disk unavailable")

    assert view.start("clear", fail)
    service.thread.join(2)
    posted.get_nowait()()
    assert view.ready and states[-1] is False
    assert renders == [("clear", None, "disk unavailable")]
    assert view.start("refresh", lambda: {"stats": {}})
    service.thread.join(2)
    posted.get_nowait()()
    assert renders[-1] == ("refresh", {"stats": {}}, None)
    service.close()


def test_thread_creation_failure_releases_busy_state(monkeypatch):
    service = maintenance.CacheMaintenance(lambda callback: pytest.fail("posted after start failed"))
    view, _owner, renders, states = make_view(service)
    monkeypatch.setattr(maintenance.threading.Thread, "start", lambda thread: (_ for _ in ()).throw(RuntimeError("no worker")))
    assert view.start("refresh", lambda: None)
    assert not service.busy and not service.is_alive()
    assert renders == [("refresh", None, "no worker")]
    assert states == [False, True, False]
    service.close()


def test_exception_without_message_still_reports_failure():
    posted = queue.SimpleQueue()
    service = maintenance.CacheMaintenance(posted.put)
    view, _owner, renders, _ = make_view(service)

    def fail():
        raise OSError()

    view.start("refresh", fail)
    service.thread.join(2)
    posted.get_nowait()()
    assert renders == [("refresh", None, "OSError")]
    assert view.ready
    service.close()


def test_stale_completion_cannot_release_new_operation():
    posted = queue.SimpleQueue()
    service = maintenance.CacheMaintenance(posted.put)
    view, _owner, renders, _ = make_view(service)
    assert view.start("refresh", lambda: 1)
    service.thread.join(2)
    complete = posted.get_nowait()
    complete()
    release = threading.Event()
    try:
        assert view.start("clear", lambda: release.wait(2))
        complete()
        assert service.busy and len(renders) == 1
        release.set()
        service.thread.join(2)
        posted.get_nowait()()
        assert not service.busy and len(renders) == 2
    finally:
        release.set()
        service.thread.join(2)
        service.close()


def test_destroying_descendant_does_not_detach_parent_view():
    service = maintenance.CacheMaintenance(lambda callback: None)
    view, owner, _, _ = make_view(service)
    owner.destroyed(SimpleNamespace(widget=object()))
    assert view.active
    owner.destroy()
    assert not service.views


def test_application_close_waits_for_maintenance_without_blocking_tk():
    calls = []
    service = SimpleNamespace(close=lambda: calls.append("close"), is_alive=lambda: True)
    gui = SimpleNamespace(_cache_maintenance=service, _v46_finish_close=lambda: None,
                          root=SimpleNamespace(after=lambda delay, callback: calls.append(delay)))
    final_behaviors._v46_finish_close(gui)
    assert calls == ["close", 100]


def test_repeated_close_keeps_only_one_maintenance_timer():
    pending = {}
    sequence = [0]

    def after(delay, callback):
        sequence[0] += 1
        pending[sequence[0]] = callback
        return sequence[0]

    gui = SimpleNamespace(_cache_maintenance=SimpleNamespace(close=lambda: None, is_alive=lambda: True),
                          _v46_finish_close=lambda: None,
                          root=SimpleNamespace(after=after, after_cancel=lambda key: pending.pop(key, None)))
    final_behaviors._v46_finish_close(gui)
    final_behaviors._v46_finish_close(gui)
    assert len(pending) == 1 and gui._cache_close_after_id == 2


def test_each_gui_owns_its_maintenance_service():
    first = SimpleNamespace(_run_on_ui_thread=lambda callback: None)
    second = SimpleNamespace(_run_on_ui_thread=lambda callback: None)
    assert maintenance.cache_maintenance(first) is maintenance.cache_maintenance(first)
    assert maintenance.cache_maintenance(first) is not maintenance.cache_maintenance(second)


class Variable:
    def __init__(self, value=""):
        self.value = value
        self.owner = threading.get_ident()

    def get(self):
        assert threading.get_ident() == self.owner
        return self.value

    def set(self, value):
        assert threading.get_ident() == self.owner
        self.value = value


class Widget(Owner):
    def __init__(self, parent=None, **kwargs):
        super().__init__()
        self.kwargs = kwargs
        self.children = []
        if parent:
            parent.children.append(self)

    def configure(self, **kwargs):
        assert threading.get_ident() == self.owner_thread and self.alive
        self.kwargs.update(kwargs)

    def grid(self, *args, **kwargs):
        pass

    pack = grid
    grid_columnconfigure = grid
    grid_rowconfigure = grid
    grid_propagate = grid
    title = grid
    transient = grid
    grab_set = grid

    def invoke(self):
        if self.kwargs.get("state") != "disabled":
            self.kwargs["command"]()

    def destroy(self):
        for child in self.children:
            child.destroy()
        self.alive = False
        if self.destroyed:
            self.destroyed(SimpleNamespace(widget=self))


class Entry(Widget):
    pass


@pytest.fixture(params=["inline", "dialog"])
def panel(request, monkeypatch):
    fake_ctk = SimpleNamespace(CTkFrame=Widget, CTkLabel=Widget, CTkButton=Widget,
                               CTkEntry=Entry, CTkFont=lambda *a: None, StringVar=Variable)
    monkeypatch.setitem(sys.modules, "customtkinter", fake_ctk)
    monkeypatch.setattr(final_behaviors, "_v25_center_window", lambda *a, **kw: None)
    posted = queue.SimpleQueue()
    window = Widget()
    colors = {key: "color" for key in ("bg", "panel", "surface", "surface2", "border", "fg", "muted",
                                       "muted2", "input_bg", "input_fg", "danger_bg", "danger_hv", "danger_fg")}
    gui = SimpleNamespace(root=Widget(), _language="en", _run_on_ui_thread=posted.put,
                          _p=lambda: colors, _make_singleton_window=lambda key: window,
                          _open_image_cache_folder=lambda: None)
    owner_thread = threading.get_ident()
    calls = []
    limit = [2048]

    def storage(name, result):
        def operation(*args):
            assert threading.get_ident() != owner_thread, "Cache operation ran in Tk"
            calls.append((name, args))
            if name == "save":
                limit[0] = args[0]["image_cache_max_mb"]
            return result() if callable(result) else result
        return operation

    functions = {"_cache_stats": storage("stats", lambda: {"entries": 3, "size_mb": 4, "limit_mb": limit[0]}),
                 "_enforce_cache_limit": storage("enforce", 0),
                 "_clear_image_cache": storage("clear", 3), "_update_settings": storage("save", True)}
    for module in (app, final_behaviors):
        for name, function in functions.items():
            monkeypatch.setattr(module, name, function)
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **kw: True)
    if request.param == "inline":
        app.CYOADownloaderGUI._settings_inline_cache(gui, window, 0, colors, True, Variable(), window)
    else:
        final_behaviors._v27_cache_manager_panel(gui)

    def widgets(parent):
        for child in parent.children:
            yield child
            yield from widgets(child)

    def button(name):
        label = {"save": "Save limit" if request.param == "inline" else "Save",
                 "clear": "Clear cache" if request.param == "inline" else "Clear Cache"}[name]
        return next(widget for widget in widgets(window) if widget.kwargs.get("text") == label)

    def settle():
        gui._cache_maintenance.thread.join(2)
        assert not gui._cache_maintenance.is_alive()
        while not posted.empty():
            posted.get_nowait()()

    settle()
    entry = next(widget for widget in widgets(window) if isinstance(widget, Entry)
                 and widget.kwargs["textvariable"].get() == "2")
    yield SimpleNamespace(gui=gui, window=window, button=button, settle=settle,
                           calls=calls, entry=entry, storage=storage,
                           texts=lambda: [widget.kwargs["textvariable"].get() for widget in widgets(window)
                                          if "textvariable" in widget.kwargs])
    gui._cache_maintenance.close()
    gui._cache_maintenance.thread.join(2)
    window.destroy()


def test_both_panels_save_snapshot_in_worker_and_refresh_controls(panel):
    panel.entry.kwargs["textvariable"].set("2.5")
    panel.button("save").invoke()
    assert panel.entry.kwargs["state"] == "disabled"
    panel.settle()
    assert panel.calls[-3:] == [("save", ({"image_cache_max_mb": 2560},)), ("enforce", ()), ("stats", ())]
    assert panel.entry.kwargs["state"] == "normal"
    assert panel.entry.kwargs["textvariable"].get() == "2.5"


@pytest.mark.parametrize("value", ["nan", "inf", "-1", "0", "invalid"])
def test_invalid_limit_never_reaches_storage(panel, value):
    before = len(panel.calls)
    panel.entry.kwargs["textvariable"].set(value)
    panel.button("save").invoke()
    assert len(panel.calls) == before
    assert not panel.gui._cache_maintenance.busy


def test_clear_keeps_confirmation_and_does_disk_work_off_tk(panel, monkeypatch):
    before = len(panel.calls)
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **kw: False)
    panel.button("clear").invoke()
    assert len(panel.calls) == before
    monkeypatch.setattr("tkinter.messagebox.askyesno", lambda *a, **kw: True)
    panel.button("clear").invoke()
    panel.settle()
    assert panel.calls[-2:] == [("clear", ()), ("stats", ())]


def test_failed_setting_commit_does_not_evict_or_report_success(panel, monkeypatch):
    for module in (app, final_behaviors):
        monkeypatch.setattr(module, "_update_settings", lambda updates: False)
    before = len(panel.calls)
    panel.entry.kwargs["textvariable"].set("0.01")
    panel.button("save").invoke()
    panel.settle()
    assert len(panel.calls) == before
    assert panel.entry.kwargs["state"] == "normal"
    assert not panel.gui._cache_maintenance.busy
    assert any("Could not save cache limit" in value for value in panel.texts())


def test_closed_panel_is_not_updated_after_storage_error(panel, monkeypatch):
    release = threading.Event()

    def failed_clear():
        assert release.wait(2)
        raise OSError("storage unavailable")

    for module in (app, final_behaviors):
        monkeypatch.setattr(module, "_clear_image_cache", failed_clear)
    panel.button("clear").invoke()
    try:
        panel.window.destroy()
        release.set()
        panel.settle()
        assert not panel.gui._cache_maintenance.views
        assert not panel.gui._cache_maintenance.busy
    finally:
        release.set()
