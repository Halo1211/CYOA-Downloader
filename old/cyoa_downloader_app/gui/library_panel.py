"""Asynchronous, paged CYOA Manager library selector."""

from __future__ import annotations

import customtkinter as ctk

from .paged_cards import PagedCards
from .panel_jobs import PanelTask
from .queue_import import QueueImport


def queue_records(gui, records):
    """Use the queue's bounded commit path for bulk panel actions."""
    if not records or getattr(gui, "_queue_import_busy", False):
        return False
    QueueImport(gui, "library/history panel").accept(records)
    return True


def open_library_panel(gui, parent, load, prepare, serve_enabled, set_status=None):
    win = gui._make_singleton_window("cyoa_manager_library")
    if win is None:
        return
    p = gui._p()
    is_en = getattr(gui, "_language", "id") == "en"
    win.title("CYOA Manager Library")
    win.geometry("760x580")
    win.minsize(640, 460)
    win.configure(fg_color=p["bg"])
    win.transient(parent)
    win.grab_set()
    task = win._panel_task = PanelTask(win)
    projects, selected, matching = [], set(), []
    title = ctk.CTkLabel(win, text="CYOA Manager Library", font=ctk.CTkFont("Segoe UI", 14, "bold"))
    title.pack(anchor="w", padx=16, pady=(14, 4))
    status = ctk.CTkLabel(win, text="Loading…" if is_en else "Memuat…", text_color=p["muted"], anchor="w")
    status.pack(fill="x", padx=16, pady=(0, 6))
    search = ctk.StringVar()
    entry = ctk.CTkEntry(win, textvariable=search, placeholder_text="Search name, URL or file…" if is_en else "Cari nama, URL atau file…")
    entry.pack(fill="x", padx=16, pady=(0, 8))
    font_name = ctk.CTkFont("Segoe UI", 11, "bold")
    font_url = ctk.CTkFont("Consolas", 9)

    def selection_status():
        status.configure(text=(f"Selected {len(selected)}; matching {len(matching)}" if is_en else
                               f"Dipilih {len(selected)}; cocok {len(matching)}"))

    def card(parent, index):
        proj = projects[index]
        row = ctk.CTkFrame(parent, fg_color=p["surface2"])
        var = ctk.BooleanVar(value=index in selected)
        def toggle():
            if var.get():
                selected.add(index)
            else:
                selected.discard(index)
            selection_status()
        ctk.CTkCheckBox(row, text="", width=24, variable=var, command=toggle).pack(side="left", padx=8, pady=8)
        meta = ctk.CTkFrame(row, fg_color="transparent")
        meta.pack(side="left", fill="x", expand=True, pady=6)
        name = proj.get("name") or proj.get("id") or "-"
        url = proj.get("source_url") or proj.get("file_path") or "No URL or local file"
        ctk.CTkLabel(meta, text=name[:80], anchor="w", font=font_name, text_color=p["fg"]).pack(anchor="w")
        ctk.CTkLabel(meta, text=url[:110], anchor="w", font=font_url, text_color=p["muted"]).pack(anchor="w")
        return row

    pager = win._panel_pager = PagedCards(win, p, card, is_en=is_en)
    pager.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    bar = ctk.CTkFrame(win, fg_color="transparent")
    bar.pack(fill="x", padx=16, pady=(0, 12))
    debounce = [None]

    def rebuild():
        debounce[0] = None
        ft = search.get().strip().casefold()
        matching[:] = [i for i, proj in enumerate(projects) if not ft or any(
            ft in str(proj.get(key) or "").casefold() for key in ("name", "id", "source_url", "file_path"))]
        pager.set_items(matching)
        selection_status()

    def schedule(*_):
        if debounce[0] is not None:
            win.after_cancel(debounce[0])
        pager.ready = False
        debounce[0] = win.after(120, rebuild)

    def close():
        win.destroy()

    def destroyed(event):
        if event.widget is win:
            if debounce[0] is not None:
                win.after_cancel(debounce[0])
                debounce[0] = None
            search.trace_remove("write", search_trace)
            # This variable belongs only to the panel. CTkEntry also installs
            # a trace and does not remove it in destroy(); release that closure.
            for modes, callback in search.trace_info():
                search.trace_remove(modes, callback)

    win.bind("<Destroy>", destroyed, add="+")
    # The singleton belongs to root; closing its Manager parent must close it too.
    parent_binding = parent.bind("<Destroy>", lambda e: close() if e.widget is parent else None, add="+")
    def detach(event):
        if event.widget is win:
            parent.unbind("<Destroy>", parent_binding)
    win.bind("<Destroy>", detach, add="+")
    search_trace = search.trace_add("write", schedule)

    def select_all():
        selected.update(matching)
        pager.show(pager.page)
        selection_status()

    def clear():
        selected.clear()
        pager.show(pager.page)
        selection_status()

    def queue():
        records = [{"url": projects[i]["source_url"], "filename": projects[i].get("name", "")}
                   for i in sorted(selected) if projects[i].get("source_url")]
        if queue_records(gui, records):
            if set_status:
                set_status(f"{len(records)} URL queued; {len(selected)-len(records)} local-only skipped.", True)
            close()
        else:
            status.configure(text="Select projects with URLs; wait for any queue import." if is_en else
                             "Pilih project ber-URL; tunggu impor antrean yang aktif.")

    def serve():
        if not serve_enabled():
            status.configure(text="Enable Serve in settings first." if is_en else "Aktifkan Serve di pengaturan dahulu.")
            return
        if len(selected) != 1:
            status.configure(text="Select exactly one project for Serve." if is_en else "Pilih tepat satu project untuk Serve.")
            return
        proj = projects[next(iter(selected))]
        path = proj.get("file_path")
        if not path:
            status.configure(text="This project has no local file." if is_en else "Project ini tidak punya file lokal.")
            return
        def done(folder, error):
            controls("normal")
            if error:
                status.configure(text=str(error))
                return
            close()
            if gui._server_running:
                gui._stop_server()
            gui.root.after(300, lambda: gui._start_server(folder=folder))
        if not task.busy:
            status.configure(text="Preparing local viewer…" if is_en else "Menyiapkan viewer lokal…")
            controls("disabled")
            task.start(lambda cancel, report: prepare(path), done)

    action_buttons = []
    for text, command in (("Select All Matches" if is_en else "Pilih Semua Hasil", select_all),
                          ("Clear" if is_en else "Kosongkan", clear),
                          ("Queue URLs" if is_en else "Antrekan URL", queue),
                          ("Serve Local" if is_en else "Serve Lokal", serve)):
        btn = ctk.CTkButton(bar, text=text, width=115, command=command)
        btn.pack(side="left", padx=(0, 6))
        action_buttons.append(btn)
    ctk.CTkButton(bar, text="Close" if is_en else "Tutup", width=65, command=close).pack(side="right")

    def controls(state):
        for btn in action_buttons:
            btn.configure(state=state)

    def loaded(value, error):
        controls("normal")
        if error:
            status.configure(text=str(error))
            return
        projects[:] = value or []
        title.configure(text=f"CYOA Manager Library — {len(projects)}")
        pager.empty_text = "Library not found or empty." if is_en else "Library tidak ditemukan atau kosong."
        rebuild()

    controls("disabled")
    task.start(lambda cancel, report: load(), loaded)
