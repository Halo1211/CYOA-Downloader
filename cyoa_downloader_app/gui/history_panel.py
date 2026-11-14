"""History update checks with window-owned work and bounded result cards."""

from __future__ import annotations

import customtkinter as ctk

from .library_panel import queue_records
from .paged_cards import PagedCards
from .panel_jobs import PanelTask


def open_history_panel(gui, load, check):
    win = gui._make_singleton_window("batch_check_center")
    if win is None:
        return
    is_en = getattr(gui, "_language", "id") == "en"
    p = gui._p()
    win.title("Batch Check Center" if is_en else "Pusat Cek Batch")
    win.geometry("900x620")
    win.minsize(760, 500)
    win.configure(fg_color=p["bg"])
    win.transient(gui.root)
    win.grab_set()
    task = win._panel_task = PanelTask(win)
    header = ctk.CTkFrame(win, fg_color=p["panel"], height=60)
    header.pack(fill="x")
    ctk.CTkLabel(header, text="Batch Check Center" if is_en else "Pusat Cek Batch",
                 font=ctk.CTkFont("Segoe UI", 16, "bold")).pack(side="left", padx=18, pady=14)
    status = ctk.CTkLabel(header, text="", text_color=p["muted"])
    status.pack(side="right", padx=18)
    body = ctk.CTkFrame(win, fg_color="transparent")
    body.pack(fill="both", expand=True, padx=14, pady=12)
    summary = ctk.CTkLabel(body, text="TOTAL 0 · CURRENT 0 · UPDATED 0 · ERROR 0", anchor="w")
    summary.pack(fill="x")
    pb = ctk.CTkProgressBar(body, height=8)
    pb.pack(fill="x", pady=10)
    pb.set(0)
    toolbar = ctk.CTkFrame(body, fg_color="transparent")
    toolbar.pack(fill="x", pady=(0, 8))
    rows, buttons = [], {}
    active_filter = ["all"]
    name_font = ctk.CTkFont("Segoe UI", 12, "bold")
    url_font = ctk.CTkFont("Consolas", 9)

    def card(parent, result):
        st = result.get("status", "")
        color = {"current": "#047857", "updated": "#2563eb"}.get(st, "#dc2626")
        frame = ctk.CTkFrame(parent, fg_color=p["surface2"])
        frame.grid_columnconfigure(0, weight=1)
        name = result.get("name") or result.get("url", "")
        ctk.CTkLabel(frame, text=name[:110], font=name_font, anchor="w", text_color=color).grid(
            row=0, column=0, sticky="ew", padx=12, pady=(8, 0))
        ctk.CTkLabel(frame, text=f"{result.get('url', '')[:120]}\n{(result.get('reason') or st)[:120]}",
                     font=url_font, anchor="w", justify="left", text_color=p["muted"]).grid(
                         row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        if st in ("updated", "error", "unreachable"):
            def queue():
                gui._add_url_to_queue(result.get("url", ""))
                status.configure(text="Queued for download" if is_en else "Masuk antrean download")
            ctk.CTkButton(frame, text="Queue" if is_en else "Antrekan", width=90, command=queue).grid(
                row=0, column=1, rowspan=2, padx=12, pady=10)
        return frame

    pager = win._panel_pager = PagedCards(body, p, card, is_en=is_en)
    pager.empty_text = "Run the check to see results." if is_en else "Jalankan cek untuk melihat hasil."
    pager.pack(fill="both", expand=True)

    def set_filter(value):
        active_filter[0] = value
        for key, button in buttons.items():
            button.configure(fg_color="#3b82f6" if key == value else p["surface2"])
        pager.set_items([r for r in rows if value == "all" or r.get("status") == value or
                         (value == "error" and r.get("status") == "unreachable")])

    for key, label in (("all", "All" if is_en else "Semua"), ("updated", "Updated" if is_en else "Update"),
                       ("error", "Errors"), ("current", "Current" if is_en else "Terkini")):
        btn = ctk.CTkButton(toolbar, text=label, width=86, command=lambda k=key: set_filter(k))
        btn.pack(side="left", padx=(0, 6))
        buttons[key] = btn

    def queue_updated():
        records = [{"url": r["url"], "filename": r.get("name", "")} for r in rows
                   if r.get("status") == "updated" and r.get("url")]
        if queue_records(gui, records):
            status.configure(text=f"Queued {len(records)} updated URLs" if is_en else f"{len(records)} URL masuk antrean")
        else:
            status.configure(text="Wait for the active queue import." if is_en else "Tunggu impor antrean yang aktif.")

    queue_btn = ctk.CTkButton(toolbar, text="Queue Updated" if is_en else "Antrekan Update", width=126,
                             command=queue_updated, state="disabled")
    queue_btn.pack(side="right", padx=(8, 0))

    def run():
        if task.busy:
            return
        run_btn.configure(state="disabled")
        queue_btn.configure(state="disabled")
        status.configure(text="Checking…" if is_en else "Mengecek…")
        pb.set(0)
        rows.clear()
        pager.set_items([])
        summary.configure(text="TOTAL 0 · CURRENT 0 · UPDATED 0 · ERROR 0")

        def work(cancel, report):
            history = load()
            items = {k: v for k, v in (history or {}).items() if isinstance(v, dict) and v.get("success")}
            if cancel.is_set():
                return 0, []
            report(0, len(items))
            return len(items), check(items, progress_cb=report, cancel_event=cancel) if items else []

        def done(value, error):
            run_btn.configure(state="normal")
            if error:
                status.configure(text=("Batch check failed: " if is_en else "Cek batch gagal: ") + str(error))
                return
            total, results = value
            rows[:] = results or []
            cur = sum(r.get("status") == "current" for r in rows)
            upd = sum(r.get("status") == "updated" for r in rows)
            err = sum(r.get("status") in ("error", "unreachable") for r in rows)
            summary.configure(text=f"TOTAL {total} · CURRENT {cur} · UPDATED {upd} · ERROR {err}")
            pb.set(1)
            status.configure(text=("Done" if is_en else "Selesai") if total else
                             ("No download history yet." if is_en else "Belum ada riwayat download."))
            queue_btn.configure(state="normal" if upd else "disabled")
            set_filter(active_filter[0])

        task.start(work, done, lambda done, total: pb.set(done / total if total else 0))

    run_btn = ctk.CTkButton(toolbar, text="Run Check" if is_en else "Jalankan Cek", width=112, command=run)
    run_btn.pack(side="right", padx=(8, 0))
    set_filter("all")
    run()
