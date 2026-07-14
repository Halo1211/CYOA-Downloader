"""Final GUI behavior bodies consolidated from historical versioned modules."""
from __future__ import annotations
from collections import Counter
from typing import Any, Dict, Optional, Tuple
from ..app_info import DEFAULT_MAX_WORKERS

# This module intentionally keeps the old _v* compatibility names while
# removing the need for separate versioned behavior files.


def _sync_legacy_globals(namespace: dict) -> None:
    globals().update({
        key: value
        for key, value in namespace.items()
        if not (key.startswith("__") and key.endswith("__"))
    })


# ---- historical GUI behavior block ----

"""Historical v24 GUI patch helpers.

Phase 52 moves the v24 patch bodies out of ``legacy.py``. The functions still
use legacy global names and are synchronized mechanically to preserve behavior.
"""






def _v24_card(parent: Any, p: Dict[str, str], *, title: str, body: str = "", icon: str = "", accent: str = "#3b82f6", command: Any = None, button_text: str = "Open") -> Any:
    """Small modern action card used by v24 panels."""
    import customtkinter as ctk
    frame = ctk.CTkFrame(parent, fg_color=p["surface"], corner_radius=12, border_width=1, border_color=p["border"])
    frame.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(frame, text=icon, width=36, font=ctk.CTkFont("Segoe UI", 20), text_color=accent).grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=12, sticky="n")
    ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 2))
    if body:
        ctk.CTkLabel(frame, text=body, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=520).grid(row=1, column=1, sticky="ew", pady=(0, 12))
    if command is not None:
        ctk.CTkButton(frame, text=button_text, width=94, height=30, fg_color=accent, hover_color=accent, text_color="#ffffff", font=ctk.CTkFont("Segoe UI", 10, "bold"), command=command).grid(row=0, column=2, rowspan=2, padx=12, pady=12)
    return frame

def _v24_badge(parent: Any, text: str, color: str, width: int = 96) -> Any:
    import customtkinter as ctk
    lbl = ctk.CTkLabel(parent, text=text, width=width, height=32, fg_color=color, text_color="#ffffff", corner_radius=10, font=ctk.CTkFont("Segoe UI", 11, "bold"))
    lbl.pack(side="left", padx=(0, 8))
    return lbl

def _v24_show_results(self: Any) -> None:
    """Modernized results/report panel with safer row handling."""
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
    import csv as csv_mod

    is_en = getattr(self, "_language", "id") == "en"
    if not getattr(self, "_last_results", None):
        messagebox.showinfo("Reports" if is_en else "Laporan", "No results yet. Run a download first." if is_en else "Belum ada hasil. Jalankan download terlebih dahulu.")
        return

    p = self._p()
    rows_all = list(self._last_results or [])
    total = len(rows_all)
    ok_cnt = sum(1 for r in rows_all if str(r.get("status", "")).upper() == "OK")
    fail_cnt = total - ok_cnt

    win = self._make_singleton_window("reports_center")
    if win is None:
        return
    win.title("Reports Center" if is_en else "Pusat Laporan")
    win.geometry("980x640")
    win.minsize(820, 520)
    win.configure(fg_color=p["bg"])
    win.transient(self.root)
    win.grab_set()

    hdr = ctk.CTkFrame(win, fg_color=p["panel"], corner_radius=0, height=68)
    hdr.pack(fill="x"); hdr.pack_propagate(False)
    ctk.CTkLabel(hdr, text=("📋 Reports Center" if is_en else "📋 Pusat Laporan"), font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=p["fg"]).pack(side="left", padx=18)
    stats = ctk.CTkFrame(hdr, fg_color="transparent")
    stats.pack(side="right", padx=18)
    _v24_badge(stats, f"TOTAL {total}", "#334155", 104)
    _v24_badge(stats, f"OK {ok_cnt}", "#047857", 92)
    _v24_badge(stats, f"FAIL {fail_cnt}", "#b91c1c", 92)

    body = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    top = ctk.CTkFrame(body, fg_color="transparent")
    top.pack(fill="x", pady=(0, 10))
    filter_var = ctk.StringVar(value="all")
    filter_buttons: Dict[str, Any] = {}

    list_frame = ctk.CTkScrollableFrame(body, fg_color=p["bg"], corner_radius=0, scrollbar_button_color=p["surface2"])
    list_frame.pack(fill="both", expand=True)
    list_frame.grid_columnconfigure(0, weight=1)

    def _set_filter(value: str) -> None:
        filter_var.set(value)
        for key, btn in filter_buttons.items():
            active = key == value
            btn.configure(fg_color="#3b82f6" if active else p["surface2"], text_color="#ffffff" if active else p["muted"])
        _render()

    for key, label in [("all", "All" if is_en else "Semua"), ("ok", "Success" if is_en else "Berhasil"), ("fail", "Failed" if is_en else "Gagal")]:
        b = ctk.CTkButton(top, text=label, width=90, height=30, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=lambda k=key: _set_filter(k))
        b.pack(side="left", padx=(0, 8))
        filter_buttons[key] = b

    def _export_csv() -> None:
        path = filedialog.asksaveasfilename(parent=win, defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="download_results.csv")
        if not path:
            return
        fields = ["status", "url", "mode", "filename", "error"]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv_mod.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for r in rows_all:
                    writer.writerow({k: r.get(k, "") for k in fields})
            logger.info(f"Results exported: {path}")
        except Exception as e:
            messagebox.showerror("Reports" if is_en else "Laporan", str(e), parent=win)

    def _copy_failed() -> None:
        failed = [r for r in rows_all if str(r.get("status", "")).upper() != "OK"]
        text = "\n".join(f"{r.get('url','')}\t{r.get('error','')}" for r in failed)
        try:
            win.clipboard_clear(); win.clipboard_append(text)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _copy_failed (line 19924): %s", _ignored_exc)

    ctk.CTkButton(top, text="Export CSV" if is_en else "Ekspor CSV", width=104, height=30, fg_color="#0f766e", hover_color="#115e59", text_color="#ffffff", command=_export_csv).pack(side="right", padx=(8, 0))
    ctk.CTkButton(top, text="Copy Failed" if is_en else "Salin Gagal", width=108, height=30, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=_copy_failed).pack(side="right", padx=(8, 0))

    def _render() -> None:
        for child in list_frame.winfo_children():
            child.destroy()
        flt = filter_var.get()
        rows = [r for r in rows_all if flt == "all" or (flt == "ok" and str(r.get("status", "")).upper() == "OK") or (flt == "fail" and str(r.get("status", "")).upper() != "OK")]
        if not rows:
            ctk.CTkLabel(list_frame, text="No rows in this filter." if is_en else "Tidak ada data untuk filter ini.", font=ctk.CTkFont("Segoe UI", 12), text_color=p["muted"]).grid(row=0, column=0, padx=12, pady=28, sticky="w")
            return
        for idx, r in enumerate(rows):
            ok = str(r.get("status", "")).upper() == "OK"
            accent = "#22c55e" if ok else "#ef4444"
            title = (r.get("filename") or "[auto]") + "  ·  " + str(r.get("mode", "")).replace("_", " ")
            detail = str(r.get("url", ""))
            err = str(r.get("error", ""))
            card = ctk.CTkFrame(list_frame, fg_color=p["surface"], corner_radius=10, border_width=1, border_color=p["border"])
            card.grid(row=idx, column=0, sticky="ew", padx=2, pady=4)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text="✓" if ok else "!", width=34, height=34, fg_color=accent, text_color="#ffffff", corner_radius=17, font=ctk.CTkFont("Segoe UI", 14, "bold")).grid(row=0, column=0, rowspan=2, padx=12, pady=10)
            ctk.CTkLabel(card, text=title, anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"]).grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 2))
            ctk.CTkLabel(card, text=detail if ok or not err else f"{detail}\n{err[:180]}", anchor="w", justify="left", font=ctk.CTkFont("Consolas", 9), text_color=p["muted" if ok else "fg"], wraplength=780).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 10))

    _set_filter("all")

def _v24_batch_update_panel(self: Any) -> None:
    """Modernized Batch Check with exception containment and non-closing requeue."""
    import customtkinter as ctk
    from tkinter import messagebox

    is_en = getattr(self, "_language", "id") == "en"
    p = self._p()
    history = _load_history()
    items = {k: v for k, v in (history or {}).items() if isinstance(v, dict) and v.get("success")}
    if not items:
        messagebox.showinfo("Batch Check", "No download history yet." if is_en else "Belum ada riwayat download.")
        return

    win = self._make_singleton_window("batch_check_center")
    if win is None:
        return
    win.title("Batch Check Center" if is_en else "Pusat Cek Batch")
    win.geometry("900x620")
    win.minsize(760, 500)
    win.configure(fg_color=p["bg"])
    win.transient(self.root)
    win.grab_set()

    header = ctk.CTkFrame(win, fg_color=p["panel"], height=70, corner_radius=0)
    header.pack(fill="x"); header.pack_propagate(False)
    title = ctk.CTkLabel(header, text="📥 Batch Check Center" if is_en else "📥 Pusat Cek Batch", font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=p["fg"])
    title.pack(side="left", padx=18)
    status = ctk.CTkLabel(header, text=("Ready" if is_en else "Siap"), font=ctk.CTkFont("Segoe UI", 11), text_color=p["muted"])
    status.pack(side="right", padx=18)

    body = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    summary = ctk.CTkFrame(body, fg_color="transparent")
    summary.pack(fill="x", pady=(0, 10))
    total_badge = _v24_badge(summary, f"TOTAL {len(items)}", "#334155", 108)
    cur_badge = _v24_badge(summary, "CURRENT 0", "#047857", 116)
    upd_badge = _v24_badge(summary, "UPDATED 0", "#1d4ed8", 116)
    err_badge = _v24_badge(summary, "ERROR 0", "#b91c1c", 104)

    pb = ctk.CTkProgressBar(body, height=8, progress_color="#3b82f6")
    pb.pack(fill="x", pady=(0, 10)); pb.set(0)

    toolbar = ctk.CTkFrame(body, fg_color="transparent")
    toolbar.pack(fill="x", pady=(0, 8))
    filter_var = ctk.StringVar(value="all")
    result_holder = {"rows": []}
    buttons: Dict[str, Any] = {}

    list_frame = ctk.CTkScrollableFrame(body, fg_color=p["bg"], corner_radius=0, scrollbar_button_color=p["surface2"])
    list_frame.pack(fill="both", expand=True)
    list_frame.grid_columnconfigure(0, weight=1)

    def _render() -> None:
        for child in list_frame.winfo_children():
            child.destroy()
        rows = list(result_holder.get("rows") or [])
        flt = filter_var.get()
        if flt != "all":
            rows = [r for r in rows if r.get("status") == flt]
        if not rows:
            ctk.CTkLabel(list_frame, text="Run the check to see results." if is_en else "Jalankan cek untuk melihat hasil.", text_color=p["muted"], font=ctk.CTkFont("Segoe UI", 12)).grid(row=0, column=0, sticky="w", padx=12, pady=24)
            return
        for idx, r in enumerate(rows):
            st = r.get("status", "")
            color = {"current": "#047857", "updated": "#2563eb", "error": "#dc2626", "unreachable": "#dc2626"}.get(st, "#64748b")
            icon = {"current": "✓", "updated": "↻", "error": "!", "unreachable": "!"}.get(st, "?")
            name = r.get("name") or r.get("url", "")[:70]
            reason = r.get("reason") or st
            card = ctk.CTkFrame(list_frame, fg_color=p["surface"], corner_radius=10, border_width=1, border_color=p["border"])
            card.grid(row=idx, column=0, sticky="ew", padx=2, pady=4)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=icon, width=34, height=34, fg_color=color, text_color="#ffffff", corner_radius=17, font=ctk.CTkFont("Segoe UI", 14, "bold")).grid(row=0, column=0, rowspan=2, padx=12, pady=10)
            ctk.CTkLabel(card, text=name, anchor="w", font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], wraplength=620).grid(row=0, column=1, sticky="ew", pady=(10, 2))
            ctk.CTkLabel(card, text=f"{r.get('url','')}\n{reason}", anchor="w", justify="left", font=ctk.CTkFont("Consolas", 9), text_color=p["muted"], wraplength=660).grid(row=1, column=1, sticky="ew", pady=(0, 10))
            if st in ("updated", "error", "unreachable"):
                def _queue(url=r.get("url", "")):
                    if url:
                        self._add_url_to_queue(url)
                        status.configure(text=("Queued for download" if is_en else "Masuk antrean download"))
                ctk.CTkButton(card, text="Queue" if is_en else "Antrekan", width=90, height=30, fg_color=color, hover_color=color, text_color="#ffffff", command=_queue).grid(row=0, column=2, rowspan=2, padx=12, pady=10)

    def _set_filter(value: str) -> None:
        filter_var.set(value)
        for key, btn in buttons.items():
            active = key == value
            btn.configure(fg_color="#3b82f6" if active else p["surface2"], text_color="#ffffff" if active else p["muted"])
        _render()

    for key, label in [("all", "All" if is_en else "Semua"), ("updated", "Updated" if is_en else "Update"), ("error", "Errors" if is_en else "Error"), ("current", "Current" if is_en else "Terkini")]:
        b = ctk.CTkButton(toolbar, text=label, width=90, height=30, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=lambda k=key: _set_filter(k))
        b.pack(side="left", padx=(0, 8))
        buttons[key] = b

    def _queue_all_updated() -> None:
        count = 0
        for r in result_holder.get("rows") or []:
            if r.get("status") == "updated" and r.get("url"):
                self._add_url_to_queue(r["url"])
                count += 1
        status.configure(text=(f"Queued {count} updated URLs" if is_en else f"{count} URL update masuk antrean"))

    queue_btn = ctk.CTkButton(toolbar, text="Queue Updated" if is_en else "Antrekan Update", width=126, height=30, fg_color="#2563eb", hover_color="#1d4ed8", text_color="#ffffff", command=_queue_all_updated, state="disabled")
    queue_btn.pack(side="right", padx=(8, 0))

    def _run() -> None:
        run_btn.configure(state="disabled")
        queue_btn.configure(state="disabled")
        status.configure(text="Checking…" if is_en else "Mengecek…")
        pb.set(0)
        result_holder["rows"] = []
        _render()
        def _progress(done: int, total: int) -> None:
            if total and _gui_exists(win):
                _v25_safe_after_widget(self.root, pb,
                                       lambda d=done, t=total: pb.set(d / t))
        def _worker() -> None:
            try:
                results = _batch_check_updates(items, progress_cb=_progress)
                err = ""
            except Exception as exc:
                results, err = [], str(exc)
            def _done() -> None:
                if not _gui_exists(win):
                    return
                if err:
                    status.configure(text=("Batch check failed" if is_en else "Cek batch gagal") + f": {err}")
                result_holder["rows"] = list(results or [])
                cur = sum(1 for r in result_holder["rows"] if r.get("status") == "current")
                upd = sum(1 for r in result_holder["rows"] if r.get("status") == "updated")
                erc = sum(1 for r in result_holder["rows"] if r.get("status") in ("error", "unreachable"))
                cur_badge.configure(text=f"CURRENT {cur}")
                upd_badge.configure(text=f"UPDATED {upd}")
                err_badge.configure(text=f"ERROR {erc}")
                pb.set(1)
                status.configure(text=("Done" if is_en else "Selesai") + f" — current {cur}, updated {upd}, error {erc}")
                queue_btn.configure(state="normal" if upd else "disabled")
                run_btn.configure(state="normal")
                _set_filter("all")
            self.root.after(0, _done)
        threading.Thread(target=_worker, daemon=True).start()

    run_btn = ctk.CTkButton(toolbar, text="Run Check" if is_en else "Jalankan Cek", width=112, height=30, fg_color="#3b82f6", hover_color="#2563eb", text_color="#ffffff", command=_run)
    run_btn.pack(side="right", padx=(8, 0))
    _set_filter("all")
    _run()

def _v24_diagnostics_panel(self: Any) -> None:
    """Modern Diagnostics Center with summary sidebar, colored text, filters, and robust worker error handling."""
    import customtkinter as ctk
    from tkinter import filedialog

    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    labels = {
        "title": "Diagnostics Center" if is_en else "Pusat Diagnostik",
        "run": "Run Again" if is_en else "Jalankan Lagi",
        "copy": "Copy" if is_en else "Salin",
        "save": "Save As…" if is_en else "Simpan Sebagai…",
        "save_output": "Save to Output" if is_en else "Simpan ke Output",
        "close": "Close" if is_en else "Tutup",
        "all": "All" if is_en else "Semua",
        "warn": "Warnings + Fails" if is_en else "Peringatan + Gagal",
        "fail": "Fails Only" if is_en else "Gagal Saja",
        "running": "Running diagnostics…" if is_en else "Menjalankan diagnostik…",
        "done": "Done" if is_en else "Selesai",
    }
    win = self._make_singleton_window("diagnostics_center")
    if win is None:
        return
    win.title(labels["title"])
    win.geometry("1020x680")
    win.minsize(860, 560)
    win.configure(fg_color=p["bg"])
    win.transient(self.root)
    win.grab_set()

    header = ctk.CTkFrame(win, fg_color=p["panel"], height=72, corner_radius=0)
    header.pack(fill="x"); header.pack_propagate(False)
    ctk.CTkLabel(header, text="🩺 " + labels["title"], font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=p["fg"]).pack(side="left", padx=18)
    status = ctk.CTkLabel(header, text=labels["running"], font=ctk.CTkFont("Segoe UI", 11), text_color=p["muted"])
    status.pack(side="right", padx=18)

    body = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    body.grid_columnconfigure(1, weight=1)
    body.grid_rowconfigure(1, weight=1)

    summary = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    summary.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, 12), pady=0)
    ctk.CTkLabel(summary, text="Summary" if is_en else "Ringkasan", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"]).pack(anchor="w", padx=14, pady=(14, 8))
    pass_box = ctk.CTkLabel(summary, text="PASS\n0", width=140, height=58, fg_color="#065f46", text_color="#d1fae5", corner_radius=12, font=ctk.CTkFont("Segoe UI", 13, "bold"))
    warn_box = ctk.CTkLabel(summary, text="WARN\n0", width=140, height=58, fg_color="#92400e", text_color="#fef3c7", corner_radius=12, font=ctk.CTkFont("Segoe UI", 13, "bold"))
    fail_box = ctk.CTkLabel(summary, text="FAIL\n0", width=140, height=58, fg_color="#991b1b", text_color="#fee2e2", corner_radius=12, font=ctk.CTkFont("Segoe UI", 13, "bold"))
    for b in (pass_box, warn_box, fail_box):
        b.pack(padx=14, pady=(0, 10))
    ctk.CTkLabel(summary, text=("Secret-safe: API keys, tokens, cookies, and passwords are not printed." if is_en else "Aman-secret: API key, token, cookie, dan password tidak dicetak."), wraplength=150, justify="left", font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"]).pack(anchor="w", padx=14, pady=(4, 12))

    filters = ctk.CTkFrame(body, fg_color="transparent")
    filters.grid(row=0, column=1, sticky="ew", pady=(0, 8))
    filter_var = ctk.StringVar(value="all")
    filter_buttons: Dict[str, Any] = {}

    text_box = ctk.CTkTextbox(body, font=ctk.CTkFont("Consolas", 11), fg_color=p["bg"], text_color=p["fg"], wrap="none", border_width=1, border_color=p["border"])
    text_box.grid(row=1, column=1, sticky="nsew")
    try:
        text_box._textbox.tag_config("pass", foreground="#86efac")
        text_box._textbox.tag_config("warn", foreground="#fcd34d")
        text_box._textbox.tag_config("fail", foreground="#fca5a5")
        text_box._textbox.tag_config("head", foreground="#93c5fd")
        text_box._textbox.tag_config("muted", foreground=p["muted"])
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v24_diagnostics_panel (line 20164): %s", _ignored_exc)
    report = {"text": "", "counts": {"PASS": 0, "WARN": 0, "FAIL": 0}}

    def _filtered_lines() -> List[str]:
        lines = (report.get("text") or "").splitlines()
        flt = filter_var.get()
        if flt == "fail":
            return [ln for ln in lines if ln.startswith("FAIL ") or ln.startswith("CYOA Downloader") or set(ln) in ({"="}, {"-"})]
        if flt == "warn":
            return [ln for ln in lines if ln.startswith(("WARN ", "FAIL ")) or ln.startswith("CYOA Downloader") or set(ln) in ({"="}, {"-"})]
        return lines

    def _draw_text() -> None:
        text_box.configure(state="normal")
        text_box.delete("1.0", "end")
        for line in _filtered_lines():
            tag = "muted"
            if line.startswith("PASS "):
                tag = "pass"
            elif line.startswith("WARN "):
                tag = "warn"
            elif line.startswith("FAIL "):
                tag = "fail"
            elif line.startswith("CYOA Downloader") or set(line) in ({"="}, {"-"}):
                tag = "head"
            text_box.insert("end", line + "\n", tag)
        text_box.configure(state="disabled")

    def _set_filter(value: str) -> None:
        filter_var.set(value)
        for key, btn in filter_buttons.items():
            active = key == value
            btn.configure(fg_color="#3b82f6" if active else p["surface2"], text_color="#ffffff" if active else p["muted"])
        _draw_text()

    for key, label in [("all", labels["all"]), ("warn", labels["warn"]), ("fail", labels["fail"])]:
        b = ctk.CTkButton(filters, text=label, width=130, height=30, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=lambda k=key: _set_filter(k))
        b.pack(side="left", padx=(0, 8))
        filter_buttons[key] = b

    btns = ctk.CTkFrame(win, fg_color="transparent")
    btns.pack(fill="x", padx=14, pady=(0, 12))

    def _copy() -> None:
        try:
            win.clipboard_clear(); win.clipboard_append(report.get("text") or "")
            status.configure(text=("Copied" if is_en else "Disalin"))
        except Exception as e:
            status.configure(text=str(e))

    def _save(path: str) -> None:
        pathlib.Path(path).write_text(report.get("text") or "", encoding="utf-8")
        status.configure(text=("Saved: " if is_en else "Tersimpan: ") + path)

    def _save_as() -> None:
        path = filedialog.asksaveasfilename(parent=win, defaultextension=".txt", initialfile="cyoa_diagnostics.txt", filetypes=[("Text", "*.txt"), ("All files", "*.*")])
        if path:
            _save(path)

    def _save_to_output() -> None:
        folder = self._outdir_var.get() or os.path.dirname(_SETTINGS_FILE)
        os.makedirs(folder, exist_ok=True)
        _save(os.path.join(folder, "cyoa_diagnostics.txt"))

    def _run() -> None:
        run_btn.configure(state="disabled")
        status.configure(text=labels["running"])
        report["text"] = labels["running"]
        _draw_text()
        def _worker() -> None:
            try:
                text, counts = build_diagnostic_report(output_dir=self._outdir_var.get() or "", check_network=True, check_ai=bool(getattr(self, "_ai_enabled", False)), language=getattr(self, "_language", "id"))
            except Exception as exc:
                text, counts = f"FAIL diagnostics {exc}", {"PASS": 0, "WARN": 0, "FAIL": 1}
            def _done() -> None:
                if not _gui_exists(win):
                    return
                report["text"] = text
                report["counts"] = counts
                pcount = int(counts.get("PASS", 0)); wcount = int(counts.get("WARN", 0)); fcount = int(counts.get("FAIL", 0))
                pass_box.configure(text=f"PASS\n{pcount}")
                warn_box.configure(text=f"WARN\n{wcount}")
                fail_box.configure(text=f"FAIL\n{fcount}")
                status.configure(text=f"{labels['done']} — PASS {pcount}, WARN {wcount}, FAIL {fcount}")
                run_btn.configure(state="normal")
                for b in (copy_btn, save_btn, save_out_btn):
                    b.configure(state="normal")
                _set_filter(filter_var.get())
            self.root.after(0, _done)
        threading.Thread(target=_worker, daemon=True).start()

    run_btn = ctk.CTkButton(btns, text=labels["run"], width=112, height=32, fg_color="#3b82f6", hover_color="#2563eb", text_color="#ffffff", command=_run)
    run_btn.pack(side="left", padx=(0, 8))
    copy_btn = ctk.CTkButton(btns, text=labels["copy"], width=90, height=32, state="disabled", fg_color=p["surface2"], text_color=p["fg"], command=_copy)
    copy_btn.pack(side="left", padx=(0, 8))
    save_btn = ctk.CTkButton(btns, text=labels["save"], width=110, height=32, state="disabled", fg_color=p["surface2"], text_color=p["fg"], command=_save_as)
    save_btn.pack(side="left", padx=(0, 8))
    save_out_btn = ctk.CTkButton(btns, text=labels["save_output"], width=132, height=32, state="disabled", fg_color=p["surface2"], text_color=p["fg"], command=_save_to_output)
    save_out_btn.pack(side="left", padx=(0, 8))
    ctk.CTkButton(btns, text=labels["close"], width=90, height=32, fg_color=p["surface2"], text_color=p["fg"], command=win.destroy).pack(side="right")
    _set_filter("all")
    _run()

def _v24_add_url_to_queue(self: Any, url: str, filename: str = "") -> None:
    """Safer queue injection: report failures to logs instead of silently swallowing."""
    try:
        self._url_var.set(url or "")
        if filename and hasattr(self, "_fn_var"):
            self._fn_var.set(filename)
        self._add_to_queue()
    except Exception as exc:
        logger.debug(f"Could not add URL to queue from helper: {exc}")



# ---- historical GUI behavior block ----

"""Historical v25 GUI patch bodies moved out of legacy.py.

Phase 54 keeps the original function bodies intact and supplies their global
compatibility namespace with ``_sync_legacy_globals``.
"""



def _v25_ai_settings_panel(self: Any) -> None:
    import customtkinter as ctk
    import threading
    from tkinter import messagebox

    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    win = self._make_singleton_window("ai_assist_center")
    if win is None:
        return
    win.title("🤖 AI Assist Center" if is_en else "🤖 Pusat AI Assist")
    win.configure(fg_color=p["bg"])
    _v25_center_window(win, self.root, 880, 700, min_w=760, min_h=600)
    try:
        win.transient(self.root)
        win.grab_set()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v25_ai_settings_panel (line 20344): %s", _ignored_exc)

    root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    root.pack(fill="both", expand=True)
    root.grid_rowconfigure(1, weight=1)
    root.grid_columnconfigure(0, weight=1)

    header = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=84)
    header.grid(row=0, column=0, sticky="ew")
    header.grid_propagate(False)
    header.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(header, text="🤖", width=54, height=54,
                 fg_color="#111827" if self._is_dark else "#dbeafe",
                 corner_radius=14, font=ctk.CTkFont("Segoe UI Emoji", 24),
                 text_color="#7dd3fc").grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=15)
    ctk.CTkLabel(header,
                 text=("AI Assist — Diagnostics & Recovery" if is_en else "AI Assist — Diagnostik & Pemulihan"),
                 font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 0))
    ctk.CTkLabel(header,
                 text=("Optional recovery helper. Keys stay secret-safe; session storage is the safest default."
                       if is_en else
                       "Helper recovery opsional. Key tetap secret-safe; penyimpanan sesi adalah default paling aman."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 14))

    body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
    body.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=1)

    footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
    footer.grid(row=2, column=0, sticky="ew")
    footer.grid_columnconfigure(0, weight=1)

    st = _load_settings()
    toggle_var = ctk.BooleanVar(value=bool(getattr(self, "_ai_enabled", False)))
    provider_var = ctk.StringVar(value=_normalize_ai_provider(st.get("ai_provider", "anthropic")))
    model_var = ctk.StringVar(value=_get_ai_model(provider_var.get()))
    mode_var = ctk.StringVar(value=_normalize_ai_mode(st.get("ai_mode", "auto_fallback")))
    storage_var = ctk.StringVar(value=_normalize_ai_key_storage(st.get("ai_key_storage", getattr(self, "_ai_key_storage", "session"))))
    session_key_var = ctk.StringVar(value=getattr(self, "_ai_api_key", "") if storage_var.get() in {"session", "plain"} else "")
    ollama_url_var = ctk.StringVar(value=st.get("ollama_url", OLLAMA_DEFAULT_URL))
    status_var = ctk.StringVar(value=_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get()))
    warn_var = ctk.StringVar(value="")

    def section(row: int, title: str) -> int:
        ctk.CTkLabel(body, text=title.upper(), font=ctk.CTkFont("Segoe UI", 10, "bold"),
                     text_color=p["accent"], anchor="w").grid(row=row, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 4))
        return row + 1

    def card(row: int, col: int, title: str, desc: str, icon: str = ""):
        frame = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14,
                             border_width=1, border_color=p["border"])
        frame.grid(row=row, column=col, sticky="nsew", padx=7, pady=7)
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=icon, width=34, font=ctk.CTkFont("Segoe UI Emoji", 18),
                     text_color="#a78bfa").grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=14, sticky="n")
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 13, "bold"),
                     text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(14, 2))
        ctk.CTkLabel(frame, text=desc, font=ctk.CTkFont("Segoe UI", 10),
                     text_color=p["muted"], anchor="w", justify="left", wraplength=330).grid(
                         row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 12))
        return frame

    r = 0
    overview = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    overview.grid(row=r, column=0, columnspan=2, sticky="ew", padx=7, pady=(0, 8))
    overview.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(overview, text="⚡", width=34, font=ctk.CTkFont("Segoe UI Emoji", 20), text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(16, 8), pady=16)
    ctk.CTkLabel(overview, text=("Enable AI Assist" if is_en else "Aktifkan AI Assist"),
                 font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 2))
    ctk.CTkLabel(overview, text=("Use AI only as a fallback when normal project detection cannot resolve the CYOA."
                                 if is_en else
                                 "Gunakan AI hanya sebagai fallback saat deteksi normal tidak dapat menemukan data CYOA."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 16))
    ctk.CTkSwitch(overview, text="", variable=toggle_var, progress_color="#8b5cf6", width=54).grid(row=0, column=2, rowspan=2, padx=18, pady=16)
    r += 1

    r = section(r, "Provider" if is_en else "Provider")
    prov_card = card(r, 0, "Provider & model" if is_en else "Provider & model",
                     "Pick the provider and model used by optional recovery." if is_en else "Pilih provider dan model untuk recovery opsional.", "🧠")
    prov_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(prov_card, text="Provider", text_color=p["muted"], anchor="w").grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(4, 2))
    provider_menu = ctk.CTkOptionMenu(prov_card, variable=provider_var, values=["anthropic", "openai", "gemini", "ollama"],
                                      fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"],
                                      text_color=p["fg"], dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"])
    provider_menu.grid(row=3, column=1, sticky="ew", padx=(0, 12), pady=(0, 8))
    ctk.CTkLabel(prov_card, text="Model", text_color=p["muted"], anchor="w").grid(row=4, column=1, sticky="ew", padx=(0, 12), pady=(0, 2))
    model_menu = ctk.CTkComboBox(prov_card, variable=model_var, values=_ai_model_options(provider_var.get()),
                                 fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"],
                                 border_color=p["border"], text_color=p["fg"], dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"])
    model_menu.grid(row=5, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))

    mode_card = card(r, 1, "Mode & storage" if is_en else "Mode & penyimpanan",
                     "Control when AI runs and where the key is stored." if is_en else "Atur kapan AI berjalan dan lokasi penyimpanan key.", "🔐")
    mode_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(mode_card, text=("AI Mode" if is_en else "Mode AI"), text_color=p["muted"], anchor="w").grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(4, 2))
    ctk.CTkOptionMenu(mode_card, variable=mode_var, values=["off", "diagnostics", "auto_fallback", "aggressive_recovery"],
                      fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"],
                      dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"]).grid(row=3, column=1, sticky="ew", padx=(0, 12), pady=(0, 8))
    ctk.CTkLabel(mode_card, text=("Key Storage" if is_en else "Penyimpanan Key"), text_color=p["muted"], anchor="w").grid(row=4, column=1, sticky="ew", padx=(0, 12), pady=(0, 2))
    ctk.CTkOptionMenu(mode_card, variable=storage_var, values=["session", "env", "keyring", "plain"],
                      fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"],
                      dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"]).grid(row=5, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))
    r += 1

    r = section(r, "Credentials" if is_en else "Kredensial")
    key_card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    key_card.grid(row=r, column=0, columnspan=2, sticky="ew", padx=7, pady=7)
    key_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(key_card, text="🔑", width=34, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#fbbf24").grid(row=0, column=0, rowspan=5, padx=(16, 8), pady=16, sticky="n")
    ctk.CTkLabel(key_card, text="API Key", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(14, 2))
    ctk.CTkLabel(key_card, text=("Session/keyring/env are recommended. Plain-text storage is intentionally explicit."
                                 if is_en else "Session/keyring/env direkomendasikan. Penyimpanan plain-text harus dipilih secara sengaja."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left").grid(row=1, column=1, sticky="ew", pady=(0, 8))
    key_entry = ctk.CTkEntry(key_card, textvariable=session_key_var, show="•", fg_color=p["input_bg"],
                             border_color=p["border"], text_color=p["input_fg"], height=34)
    key_entry.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=(0, 8))
    ctk.CTkLabel(key_card, text=("Ollama URL" if is_en else "URL Ollama"), text_color=p["muted"], anchor="w").grid(row=3, column=1, sticky="ew", pady=(0, 2))
    ollama_url_entry = ctk.CTkEntry(key_card, textvariable=ollama_url_var, fg_color=p["input_bg"],
                                    border_color=p["border"], text_color=p["input_fg"], height=34)
    ollama_url_entry.grid(row=4, column=1, sticky="ew", padx=(0, 14), pady=(0, 14))
    r += 1

    status_card = ctk.CTkFrame(body, fg_color=p["panel"], corner_radius=12, border_width=1, border_color=p["border"])
    status_card.grid(row=r, column=0, columnspan=2, sticky="ew", padx=7, pady=(4, 10))
    status_card.grid_columnconfigure(0, weight=1)
    status_lbl = ctk.CTkLabel(status_card, textvariable=status_var, font=ctk.CTkFont("Segoe UI", 10),
                              text_color=p["muted"], anchor="w", justify="left", wraplength=700)
    status_lbl.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))
    warn_lbl = ctk.CTkLabel(status_card, textvariable=warn_var, font=ctk.CTkFont("Segoe UI", 10, "bold"),
                            text_color="#f59e0b", anchor="w", justify="left", wraplength=700)
    warn_lbl.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))

    def _refresh_key_ui(*_):
        mode = _normalize_ai_key_storage(storage_var.get())
        provider = _normalize_ai_provider(provider_var.get())
        if provider == "ollama":
            try: key_entry.configure(state="disabled", placeholder_text="Ollama uses local API")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20483): %s", _ignored_exc)
            try: ollama_url_entry.configure(state="normal")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20485): %s", _ignored_exc)
            warn_var.set("Ollama uses a local endpoint; no cloud API key is needed." if is_en else "Ollama memakai endpoint lokal; API key cloud tidak diperlukan.")
        elif mode == "env":
            try: ollama_url_entry.configure(state="disabled")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20489): %s", _ignored_exc)
            try: key_entry.configure(state="disabled", placeholder_text=_ai_primary_env_var(provider) or "Environment variable")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20491): %s", _ignored_exc)
            warn_var.set(("Set " + " or ".join(_ai_env_vars(provider)) + " in the OS environment. The app will not store the key.") if is_en else ("Atur " + " atau ".join(_ai_env_vars(provider)) + " di environment OS. Aplikasi tidak menyimpan key."))
        elif mode == "keyring":
            try: ollama_url_entry.configure(state="disabled")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20495): %s", _ignored_exc)
            try: key_entry.configure(state="normal", placeholder_text="Enter key to save to OS Credential Manager")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20497): %s", _ignored_exc)
            warn_var.set(("Requires optional package: pip install keyring" if not _keyring_module() else "Key will be stored in the OS credential store.") if is_en else ("Membutuhkan paket opsional: pip install keyring" if not _keyring_module() else "Key akan disimpan di credential store sistem operasi."))
        elif mode == "plain":
            try: ollama_url_entry.configure(state="disabled")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20501): %s", _ignored_exc)
            try: key_entry.configure(state="normal", placeholder_text="API key...")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20503): %s", _ignored_exc)
            warn_var.set("Warning: plain-text storage writes the API key into settings.json." if is_en else "Peringatan: penyimpanan plain-text menulis API key ke settings.json.")
        else:
            try: ollama_url_entry.configure(state="disabled")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20507): %s", _ignored_exc)
            try: key_entry.configure(state="normal", placeholder_text="Session only; cleared when app exits")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 20509): %s", _ignored_exc)
            warn_var.set("Safest default. The key stays in memory only." if is_en else "Default paling aman. Key hanya berada di memori.")
        status_var.set(_ai_key_status_text(mode, session_key_var.get(), provider))

    def _provider_changed(*_):
        prov = _normalize_ai_provider(provider_var.get())
        opts = _ai_model_options(prov)
        try: model_menu.configure(values=opts)
        except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _provider_changed (line 20517): %s", _ignored_exc)
        if model_var.get() not in opts:
            model_var.set(_default_ai_model(prov))
        if prov == "ollama":
            session_key_var.set("")
        elif _normalize_ai_key_storage(storage_var.get()) == "plain":
            session_key_var.set(_resolve_ai_api_key(storage="plain", provider=prov))
        else:
            session_key_var.set("")
        _refresh_key_ui()

    storage_var.trace_add("write", _refresh_key_ui)
    provider_var.trace_add("write", _provider_changed)
    session_key_var.trace_add("write", lambda *_: status_var.set(_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get())))

    def _save() -> None:
        prov = _normalize_ai_provider(provider_var.get())
        storage = _normalize_ai_key_storage(storage_var.get())
        mode = _normalize_ai_mode(mode_var.get())
        key_value = session_key_var.get().strip()
        settings = {
            "ai_enabled": bool(toggle_var.get()),
            "ai_provider": prov,
            "ai_model": model_var.get().strip() or _default_ai_model(prov),
            "ai_mode": mode,
            "ai_key_storage": storage,
            "ollama_url": ollama_url_var.get().strip() or OLLAMA_DEFAULT_URL,
        }
        try:
            if storage == "plain":
                settings = _clear_ai_plain_keys(settings, prov)
                if key_value and prov != "ollama":
                    settings[_plain_ai_key_setting(prov)] = key_value
            else:
                settings = _clear_ai_plain_keys(settings, prov)
            _update_settings(settings)
            if storage == "session" and prov != "ollama":
                self._ai_api_key = key_value
            elif storage == "keyring" and key_value and prov != "ollama":
                if not _write_ai_key_to_keyring(key_value, prov):
                    raise RuntimeError("keyring write failed")
                self._ai_api_key = ""
            elif storage in {"env", "plain"}:
                self._ai_api_key = ""
            self._ai_enabled = bool(toggle_var.get())
            self._ai_key_storage = storage
            try: self._ai_var.set(self._ai_enabled)
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _save (line 20564): %s", _ignored_exc)
            _refresh_key_ui()
            status_var.set("Saved. AI Assist settings updated." if is_en else "Tersimpan. Pengaturan AI Assist diperbarui.")
        except Exception as exc:
            messagebox.showerror("AI Assist", (f"Failed to save: {exc}" if is_en else f"Gagal menyimpan: {exc}"), parent=win)

    def _clear_key() -> None:
        try:
            _clear_ai_api_key_storage(storage_var.get(), provider_var.get(), clear_all=False)
            session_key_var.set("")
            self._ai_api_key = ""
            _refresh_key_ui()
            status_var.set("Key cleared for the selected storage/provider." if is_en else "Key dibersihkan untuk storage/provider terpilih.")
        except Exception as exc:
            messagebox.showerror("AI Assist", str(exc), parent=win)

    def _test() -> None:
        _save()
        prov = _normalize_ai_provider(provider_var.get())
        key = _resolve_ai_api_key(session_key=session_key_var.get(), storage=storage_var.get(), provider=prov)
        if prov != "ollama" and not key:
            messagebox.showwarning("AI Assist", "No API key available." if is_en else "API key belum tersedia.", parent=win)
            return
        status_var.set("Testing provider connection…" if is_en else "Menguji koneksi provider…")
        def worker():
            try:
                ok = _ai_is_available(key, prov)
                msg = "API key works." if ok and is_en else "API key berhasil digunakan." if ok else "API key test failed. Check key, model, and network." if is_en else "Tes API key gagal. Cek key, model, dan jaringan."
                _v25_safe_after(win, lambda: status_var.set(msg))
            except Exception as exc:
                _v25_safe_after(win, lambda e=str(exc): status_var.set(("Test failed: " if is_en else "Tes gagal: ") + e))
        threading.Thread(target=worker, daemon=True).start()

    ctk.CTkButton(footer, text=("Test API Key" if is_en else "Tes API Key"), width=130,
                  fg_color="#3b82f6", hover_color="#2563eb", command=_test).grid(row=0, column=0, sticky="w", padx=(18, 8), pady=12)
    ctk.CTkButton(footer, text=("Clear Key" if is_en else "Bersihkan Key"), width=110,
                  fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=_clear_key).grid(row=0, column=1, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Save" if is_en else "Simpan"), width=110,
                  fg_color="#065f46", hover_color="#047857", text_color="#d1fae5", command=_save).grid(row=0, column=2, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=110,
                  fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=win.destroy).grid(row=0, column=3, padx=(0, 18), pady=12)
    _refresh_key_ui()

def _v25_manage_offline_viewers(self: Any) -> None:
    import customtkinter as ctk
    import threading
    from tkinter import filedialog, messagebox

    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    win = self._make_singleton_window("offline_viewer_center")
    if win is None:
        return
    win.title("📺 Offline Viewer Center" if is_en else "📺 Pusat Viewer Offline")
    win.configure(fg_color=p["bg"])
    _v25_center_window(win, self.root, 960, 700, min_w=820, min_h=600)
    try:
        win.transient(self.root)
        win.grab_set()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v25_manage_offline_viewers (line 20624): %s", _ignored_exc)

    root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    root.pack(fill="both", expand=True)
    root.grid_rowconfigure(2, weight=1)
    root.grid_columnconfigure(0, weight=1)

    hdr = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=88)
    hdr.grid(row=0, column=0, sticky="ew")
    hdr.grid_propagate(False)
    hdr.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(hdr, text="📺", width=54, height=54,
                 fg_color="#0f172a" if self._is_dark else "#dbeafe", corner_radius=14,
                 font=ctk.CTkFont("Segoe UI Emoji", 24), text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=16)
    ctk.CTkLabel(hdr, text=("Offline Viewer Center" if is_en else "Pusat Viewer Offline"),
                 font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 0))
    ctk.CTkLabel(hdr, text=("Register local viewer archives and keep ICC Plus viewers ready for offline preview."
                            if is_en else "Daftarkan arsip viewer lokal dan siapkan viewer ICC Plus untuk preview offline."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 14))

    actions = ctk.CTkFrame(root, fg_color=p["bg"], corner_radius=0)
    actions.grid(row=1, column=0, sticky="ew", padx=18, pady=(12, 6))
    actions.grid_columnconfigure(3, weight=1)
    status_var = ctk.StringVar(value="")
    filter_var = ctk.StringVar(value="All")

    list_frame = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
    list_frame.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 10))
    list_frame.grid_columnconfigure(0, weight=1)

    footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
    footer.grid(row=3, column=0, sticky="ew")
    footer.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(footer, textvariable=status_var, font=ctk.CTkFont("Segoe UI", 10),
                 text_color=p["accent"], anchor="w").grid(row=0, column=0, sticky="ew", padx=18, pady=12)
    ctk.CTkButton(footer, text=("Open viewer folder" if is_en else "Buka folder viewer"), width=150,
                  fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
                  command=lambda: self._open_path_in_os(_VIEWERS_DIR)).grid(row=0, column=1, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=100,
                  fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=win.destroy).grid(row=0, column=2, padx=(0, 18), pady=12)

    def _counts(manifest):
        total = len(manifest)
        icc = sum(1 for m in manifest.values() if m.get("viewer_type") == "icc_plus")
        custom = sum(1 for m in manifest.values() if m.get("viewer_type") not in {"icc_plus", "icc", "cyoap_vue"})
        return total, icc, custom

    total_badge = ctk.CTkLabel(actions, text="Total 0", width=95, height=34, fg_color=p["surface"],
                               corner_radius=10, font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=p["fg"])
    total_badge.grid(row=0, column=0, padx=(0, 8), pady=4)
    icc_badge = ctk.CTkLabel(actions, text="ICC 0", width=95, height=34, fg_color="#052e16", corner_radius=10,
                             font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#86efac")
    icc_badge.grid(row=0, column=1, padx=(0, 8), pady=4)
    custom_badge = ctk.CTkLabel(actions, text="Custom 0", width=105, height=34, fg_color="#2e1065", corner_radius=10,
                                font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color="#d8b4fe")
    custom_badge.grid(row=0, column=2, padx=(0, 12), pady=4)

    ctk.CTkSegmentedButton(actions, values=["All", "ICC", "CYOAP", "Custom"], variable=filter_var,
                           command=lambda *_: _refresh_list(), width=260, height=34,
                           fg_color=p["surface2"], selected_color="#3b82f6", selected_hover_color="#2563eb",
                           unselected_color=p["surface2"], unselected_hover_color=p["surface"],
                           text_color="#ffffff").grid(row=0, column=3, sticky="w", pady=4)

    def _add_viewer():
        zip_path = filedialog.askopenfilename(
            parent=win,
            title="Select offline viewer archive" if is_en else "Pilih arsip viewer offline",
            filetypes=[("Viewer archives", "*.zip *.rar"), ("ZIP files", "*.zip"), ("RAR files", "*.rar"), ("All files", "*.*")]
        )
        if not zip_path:
            return
        name_win = ctk.CTkToplevel(win)
        self._apply_window_icon_to(name_win)
        name_win.title("Viewer Info" if is_en else "Info Viewer")
        name_win.configure(fg_color=p["bg"])
        _v25_center_window(name_win, win, 460, 360, min_w=420, min_h=320)
        try: name_win.grab_set()
        except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _add_viewer (line 20701): %s", _ignored_exc)
        form = ctk.CTkFrame(name_win, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
        form.pack(fill="both", expand=True, padx=18, pady=18)
        form.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(form, text=("Register viewer archive" if is_en else "Daftarkan arsip viewer"), font=ctk.CTkFont("Segoe UI", 15, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        name_var = ctk.StringVar(value=os.path.splitext(os.path.basename(zip_path))[0])
        type_var = ctk.StringVar(value="icc_plus")
        desc_var = ctk.StringVar()
        ctk.CTkLabel(form, text=("Name" if is_en else "Nama"), text_color=p["muted"], anchor="w").grid(row=1, column=0, sticky="ew", padx=16)
        ctk.CTkEntry(form, textvariable=name_var, fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"]).grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 8))
        ctk.CTkLabel(form, text=("Viewer type" if is_en else "Tipe viewer"), text_color=p["muted"], anchor="w").grid(row=3, column=0, sticky="ew", padx=16)
        ctk.CTkSegmentedButton(form, values=["icc_plus", "icc", "cyoap_vue", "custom"], variable=type_var,
                               fg_color=p["surface2"], selected_color="#3b82f6", selected_hover_color="#2563eb",
                               unselected_color=p["surface2"], unselected_hover_color=p["surface"], text_color="#ffffff").grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 8))
        ctk.CTkLabel(form, text=("Description (optional)" if is_en else "Deskripsi (opsional)"), text_color=p["muted"], anchor="w").grid(row=5, column=0, sticky="ew", padx=16)
        ctk.CTkEntry(form, textvariable=desc_var, fg_color=p["input_bg"], text_color=p["input_fg"], border_color=p["border"]).grid(row=6, column=0, sticky="ew", padx=16, pady=(2, 14))
        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.grid(row=7, column=0, sticky="e", padx=16, pady=(0, 16))
        def _do_register():
            try:
                vid = register_offline_viewer(zip_path, name=name_var.get().strip() or os.path.basename(zip_path), viewer_type=type_var.get(), description=desc_var.get().strip())
                name_win.destroy()
                if vid:
                    status_var.set((f"Viewer '{vid}' registered." if is_en else f"Viewer '{vid}' berhasil didaftarkan."))
                    _refresh_list()
                else:
                    messagebox.showerror("Viewer", "Failed to register viewer. Check log for details." if is_en else "Gagal mendaftarkan viewer. Cek log untuk detail.", parent=win)
            except Exception as exc:
                messagebox.showerror("Viewer", str(exc), parent=win)
        ctk.CTkButton(btns, text=("Cancel" if is_en else "Batal"), width=90, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=name_win.destroy).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text=("Register" if is_en else "Daftarkan"), width=110, fg_color="#3b82f6", hover_color="#2563eb", command=_do_register).pack(side="left")

    def _remove(vid: str):
        if messagebox.askyesno("Remove Viewer" if is_en else "Hapus Viewer", (f"Remove '{vid}' from registry?\nThe archive file is kept on disk." if is_en else f"Hapus '{vid}' dari registry?\nFile arsip tetap disimpan di disk."), parent=win):
            unregister_offline_viewer(vid, delete_zip=False)
            status_var.set((f"Viewer '{vid}' removed." if is_en else f"Viewer '{vid}' dihapus."))
            _refresh_list()

    def _check_icc_update():
        status_var.set("Checking GitHub for latest ICCPlus release…" if is_en else "Mengecek rilis ICCPlus terbaru di GitHub…")
        def _do_check():
            r = None
            try:
                api = "https://api.github.com/repos/wahawa303/ICCPlus/releases/latest"
                r = fetch_response(api, timeout=8, extra_headers={"User-Agent": "CYOA-Downloader"})
                if r is None or r.status_code != 200:
                    code = getattr(r, "status_code", None)
                    msg = f"GitHub API: {code}" if code is not None else "GitHub API: no response"
                    _v25_safe_after(win, lambda m=msg: status_var.set(m))
                    return
                data = r.json()
                tag = data.get("tag_name", "")
                assets = data.get("assets", [])
                offline_asset = next((a for a in assets if any(kw in a.get("name", "").lower() for kw in ["local", "offline", "standalone"])), assets[0] if assets else None)
                if not offline_asset:
                    _v25_safe_after(win, lambda: status_var.set("No downloadable asset found." if is_en else "Aset unduhan tidak ditemukan."))
                    return
                asset_name = offline_asset.get("name", "ICCPlus.zip")
                asset_url = offline_asset.get("browser_download_url", "")
                manifest = _load_viewers_manifest()
                already = any(tag and (tag in m.get("name", "") or tag in vid) for vid, m in manifest.items())
                def _offer():
                    if already:
                        status_var.set((f"Already have {tag} registered." if is_en else f"{tag} sudah terdaftar."))
                        return
                    if messagebox.askyesno("New ICCPlus Release" if is_en else "Rilis ICCPlus Baru", (f"Latest release: {tag}\nFile: {asset_name}\n\nDownload and register?" if is_en else f"Rilis terbaru: {tag}\nFile: {asset_name}\n\nUnduh dan daftarkan?"), parent=win):
                        _do_download(tag, asset_name, asset_url)
                _v25_safe_after(win, _offer)
            except Exception as exc:
                _v25_safe_after(win, lambda e=str(exc): status_var.set(("Update check failed: " if is_en else "Cek update gagal: ") + e))
            finally:
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass
        def _do_download(tag: str, asset_name: str, asset_url: str):
            status_var.set((f"Downloading {asset_name}…" if is_en else f"Mengunduh {asset_name}…"))
            def _dl():
                try:
                    os.makedirs(_VIEWERS_DIR, exist_ok=True)
                    dest = os.path.join(_VIEWERS_DIR, asset_name)
                    sess = _get_shared_session(use_cf=False)
                    with sess.get(asset_url, stream=True, timeout=30, headers={"User-Agent": "CYOA-Downloader"}) as rr:
                        rr.raise_for_status()
                        total = int(rr.headers.get("content-length", 0))
                        done = 0
                        with open(dest, "wb") as f:
                            for chunk in rr.iter_content(65536):
                                if not chunk:
                                    continue
                                f.write(chunk)
                                done += len(chunk)
                                if total:
                                    pct = done * 100 // total
                                    _v25_safe_after(win, lambda pct=pct: status_var.set(f"{asset_name}: {pct}%"))
                    vid = register_offline_viewer(dest, name=f"ICCPlus {tag} (auto)", viewer_type="icc_plus")
                    _v25_safe_after(win, lambda: (status_var.set((f"{asset_name} registered as '{vid}'." if is_en else f"{asset_name} terdaftar sebagai '{vid}'.")), _refresh_list()))
                except Exception as exc:
                    _v25_safe_after(win, lambda e=str(exc): status_var.set(("Download failed: " if is_en else "Unduhan gagal: ") + e))
            threading.Thread(target=_dl, daemon=True).start()
        threading.Thread(target=_do_check, daemon=True).start()

    ctk.CTkButton(actions, text=("+ Add ZIP" if is_en else "+ Tambah ZIP"), width=120, height=34,
                  fg_color="#3b82f6", hover_color="#2563eb", command=_add_viewer).grid(row=0, column=4, padx=(8, 8), pady=4)

    def _refresh_list():
        for w in list_frame.winfo_children():
            w.destroy()
        manifest = _load_viewers_manifest()
        total, icc, custom = _counts(manifest)
        total_badge.configure(text=f"Total {total}")
        icc_badge.configure(text=f"ICC {icc}")
        custom_badge.configure(text=f"Custom {custom}")
        if not manifest:
            empty = ctk.CTkFrame(list_frame, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
            empty.grid(row=0, column=0, sticky="ew", padx=6, pady=18)
            ctk.CTkLabel(empty, text="📦", font=ctk.CTkFont("Segoe UI Emoji", 28), text_color=p["muted"]).pack(pady=(24, 6))
            ctk.CTkLabel(empty, text=("No offline viewers registered yet." if is_en else "Belum ada viewer offline terdaftar."), font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=p["fg"]).pack()
            ctk.CTkLabel(empty, text=("Click + Add ZIP to register an ICCPlus/ICC/CYOAP viewer archive." if is_en else "Klik + Tambah ZIP untuk mendaftarkan arsip viewer ICCPlus/ICC/CYOAP."), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"]).pack(pady=(2, 24))
            return
        mode = filter_var.get().lower()
        row = 0
        for vid, meta in manifest.items():
            vtype = str(meta.get("viewer_type", "custom") or "custom")
            if mode == "icc" and vtype not in {"icc_plus", "icc"}:
                continue
            if mode == "cyoap" and vtype != "cyoap_vue":
                continue
            if mode == "custom" and vtype in {"icc_plus", "icc", "cyoap_vue"}:
                continue
            icon = {"icc_plus": "⚡", "icc": "📄", "cyoap_vue": "🌿", "custom": "📦"}.get(vtype, "📦")
            card = ctk.CTkFrame(list_frame, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
            card.grid(row=row, column=0, sticky="ew", padx=6, pady=6)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=icon, width=34, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#60a5fa").grid(row=0, column=0, rowspan=3, padx=(14, 8), pady=14, sticky="n")
            ctk.CTkLabel(card, text=str(meta.get("name", vid)), font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(12, 1))
            ctk.CTkLabel(card, text=f"type: {vtype}  ·  entry: {meta.get('entry_point', 'index.html')}  ·  {meta.get('zip_filename', '')}", font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew")
            desc = meta.get("description") or vid
            ctk.CTkLabel(card, text=str(desc), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted2"], anchor="w", wraplength=660).grid(row=2, column=1, sticky="ew", pady=(1, 12))
            ctk.CTkButton(card, text=("💉 Inject" if is_en else "💉 Inject"), width=96, height=30,
                          fg_color="#1d4ed8", hover_color="#2563eb", text_color="#dbeafe",
                          command=lambda m=dict(meta, id=vid): _v25_inject_into_viewer(self, m, parent_win=win)
                          ).grid(row=0, column=2, rowspan=3, padx=(14, 0), pady=14)
            ctk.CTkButton(card, text=("Remove" if is_en else "Hapus"), width=92, height=30,
                          fg_color="#7f1d1d", hover_color="#991b1b", text_color="#fecaca",
                          command=lambda v=vid: _remove(v)).grid(row=0, column=3, rowspan=3, padx=14, pady=14)
            row += 1
        if row == 0:
            ctk.CTkLabel(list_frame, text=("No viewers match this filter." if is_en else "Tidak ada viewer yang cocok dengan filter ini."), text_color=p["muted"]).grid(row=0, column=0, pady=24)

    _refresh_list()

def _v25_inject_into_viewer(self: Any, viewer_meta: Dict, parent_win: Any = None) -> None:
    """Manually inject project data into a registered offline viewer.

    Bridges an already-registered offline viewer (viewer_meta) with a project
    source supplied by the user, then runs the existing _apply_offline_viewer
    pipeline. The project source can be:

      • File — project.json / project.txt / app.xxx.js / .zip / .rar.
        Bytes are run through the same resolution chain used during a normal
        download: extract_project_from_archive_bytes() for ZIP/RAR payloads,
        otherwise extract_project_text_from_payload() (which handles embedded
        project data inside app.xxx.js via extract_embedded_project_from_js).
      • Folder — a previous download folder. Auto-scans for project.json /
        project_original.json first, then app*.js as fallback, and detects
        sibling images/ + audio/ folders to pass as asset_source_dirs.
      • URL — uses the full get_project_source() resolver (cyoa.cafe, archive
        wrappers, candidate probing, embedded-JS extraction, optional AI).

    Output is a self-contained <name>_offline/ folder. Purely additive: does not
    change CLI flags, output formats, or the existing auto-inject download path.
    """
    import customtkinter as ctk
    import threading
    from tkinter import filedialog, messagebox

    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    owner = parent_win if parent_win is not None else self.root

    win = ctk.CTkToplevel(owner)
    self._apply_window_icon_to(win)
    win.title(("Inject into Viewer" if is_en else "Inject ke Viewer"))
    win.configure(fg_color=p["bg"])
    _v25_center_window(win, owner, 560, 520, min_w=520, min_h=460)
    try:
        win.transient(owner)
        win.grab_set()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v25_inject_into_viewer (grab): %s", _ignored_exc)

    viewer_name = str(viewer_meta.get("name", viewer_meta.get("id", "viewer")))

    root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    root.pack(fill="both", expand=True)
    root.grid_columnconfigure(0, weight=1)
    root.grid_rowconfigure(2, weight=1)

    hdr = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=74)
    hdr.grid(row=0, column=0, sticky="ew")
    hdr.grid_propagate(False)
    hdr.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(hdr, text="💉", width=46, height=46,
                 fg_color="#0f172a" if self._is_dark else "#dbeafe", corner_radius=12,
                 font=ctk.CTkFont("Segoe UI Emoji", 20), text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(16, 10), pady=14)
    ctk.CTkLabel(hdr, text=(f"Inject into: {viewer_name}" if is_en else f"Inject ke: {viewer_name}"),
                 font=ctk.CTkFont("Segoe UI", 15, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(14, 0))
    ctk.CTkLabel(hdr, text=("Pick a project source. File can be project.json, app.js, or a zip — content is auto-detected."
                            if is_en else "Pilih sumber project. File bisa project.json, app.js, atau zip — isinya dideteksi otomatis."),
                 font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 12))

    form = ctk.CTkFrame(root, fg_color=p["bg"], corner_radius=0)
    form.grid(row=1, column=0, sticky="ew", padx=16, pady=(12, 4))
    form.grid_columnconfigure(1, weight=1)

    src_kind = ctk.StringVar(value="file")
    src_path = ctk.StringVar(value="")
    out_dir = ctk.StringVar(value="")

    ctk.CTkLabel(form, text=("Source type" if is_en else "Tipe sumber"), text_color=p["muted"], anchor="w").grid(row=0, column=0, columnspan=2, sticky="w")
    ctk.CTkSegmentedButton(form, values=["file", "folder", "url"], variable=src_kind,
                           command=lambda *_: _on_kind(),
                           fg_color=p["surface2"], selected_color="#3b82f6", selected_hover_color="#2563eb",
                           unselected_color=p["surface2"], unselected_hover_color=p["surface"],
                           text_color="#ffffff").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 12))

    src_label = ctk.CTkLabel(form, text=("Project file" if is_en else "File project"), text_color=p["muted"], anchor="w")
    src_label.grid(row=2, column=0, columnspan=2, sticky="w")
    src_entry = ctk.CTkEntry(form, textvariable=src_path, fg_color=p["input_bg"], text_color=p["input_fg"],
                             border_color=p["border"], font=ctk.CTkFont("Consolas", 10))
    src_entry.grid(row=3, column=0, sticky="ew", pady=(2, 10))
    src_browse = ctk.CTkButton(form, text=("Browse" if is_en else "Pilih"), width=86,
                               fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
                               command=lambda: _browse_src())
    src_browse.grid(row=3, column=1, padx=(8, 0), pady=(2, 10), sticky="e")

    ctk.CTkLabel(form, text=("Output folder" if is_en else "Folder output"), text_color=p["muted"], anchor="w").grid(row=4, column=0, columnspan=2, sticky="w")
    ctk.CTkEntry(form, textvariable=out_dir, fg_color=p["input_bg"], text_color=p["input_fg"],
                 border_color=p["border"], font=ctk.CTkFont("Consolas", 10)).grid(row=5, column=0, sticky="ew", pady=(2, 4))
    ctk.CTkButton(form, text=("Browse" if is_en else "Pilih"), width=86,
                  fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"],
                  command=lambda: _browse_out()).grid(row=5, column=1, padx=(8, 0), pady=(2, 4), sticky="e")

    status_box = ctk.CTkTextbox(root, fg_color=p["surface"], text_color=p["muted"],
                                font=ctk.CTkFont("Consolas", 10), border_width=1, border_color=p["border"], wrap="word")
    status_box.grid(row=2, column=0, sticky="nsew", padx=16, pady=(8, 6))
    status_box.configure(state="disabled")

    footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
    footer.grid(row=3, column=0, sticky="ew")
    footer.grid_columnconfigure(0, weight=1)

    def _log(msg: str) -> None:
        def _do():
            status_box.configure(state="normal")
            status_box.insert("end", msg + "\n")
            status_box.see("end")
            status_box.configure(state="disabled")
        _v25_safe_after(win, _do)

    def _on_kind() -> None:
        k = src_kind.get()
        if k == "file":
            src_label.configure(text=("Project file (json / app.js / zip)" if is_en else "File project (json / app.js / zip)"))
            src_browse.configure(state="normal")
        elif k == "folder":
            src_label.configure(text=("Download folder (auto-scan)" if is_en else "Folder hasil download (auto-scan)"))
            src_browse.configure(state="normal")
        else:
            src_label.configure(text=("Source URL" if is_en else "URL sumber"))
            src_browse.configure(state="disabled")

    def _browse_src() -> None:
        k = src_kind.get()
        if k == "file":
            path = filedialog.askopenfilename(
                parent=win,
                title=("Select project source file" if is_en else "Pilih file sumber project"),
                filetypes=[("Project sources", "*.json *.txt *.js *.zip *.rar"),
                           ("All files", "*.*")])
        elif k == "folder":
            path = filedialog.askdirectory(parent=win,
                                           title=("Select download folder" if is_en else "Pilih folder download"))
        else:
            return
        if path:
            src_path.set(path)

    def _browse_out() -> None:
        path = filedialog.askdirectory(parent=win, title=("Select output folder" if is_en else "Pilih folder output"))
        if path:
            out_dir.set(path)

    # ── Project-source resolution (reuses existing extraction pipeline) ──
    def _resolve_from_file(path: str):
        """Return (project_str, asset_source_dirs) or (None, {})."""
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except Exception as exc:
            _log((f"Could not read file: {exc}" if is_en else f"Gagal membaca file: {exc}"))
            return None, {}
        assets: Dict[str, str] = {}
        # ZIP/RAR (or any archive-like payload) first.
        if is_zip_bytes(raw) or path.lower().endswith((".zip", ".rar")):
            proj = extract_project_from_archive_bytes(raw, path)
            if proj:
                _log(("Resolved project from archive." if is_en else "Project ditemukan dari arsip."))
                return proj, assets
        text = try_decode_bytes(raw)
        proj = extract_project_text_from_payload(text)
        if proj:
            _log(("Resolved project from file payload (json/app.js)."
                  if is_en else "Project ditemukan dari isi file (json/app.js)."))
            # Sibling images/ + audio/ next to the chosen file.
            base = os.path.dirname(os.path.abspath(path))
            for sub in ("images", "audio"):
                d = os.path.join(base, sub)
                if os.path.isdir(d):
                    assets[sub] = d
            return proj, assets
        _log(("No project data found in file." if is_en else "Tidak ada data project di file."))
        return None, {}

    def _resolve_from_folder(folder: str):
        assets: Dict[str, str] = {}
        for sub in ("images", "audio"):
            d = os.path.join(folder, sub)
            if os.path.isdir(d):
                assets[sub] = d
        # Prefer explicit project json files.
        for cand in ("project.json", "project_original.json"):
            cpath = os.path.join(folder, cand)
            if os.path.isfile(cpath):
                proj, _a = _resolve_from_file(cpath)
                if proj:
                    _log((f"Using {cand} from folder." if is_en else f"Memakai {cand} dari folder."))
                    return proj, (assets or _a)
        # Fallback: scan app*.js / *.js for embedded project data.
        try:
            js_files = sorted(
                [f for f in os.listdir(folder) if f.lower().endswith(".js")],
                key=lambda n: (0 if n.lower().startswith("app") else 1, n.lower()))
        except Exception:
            js_files = []
        for js in js_files:
            jpath = os.path.join(folder, js)
            try:
                with open(jpath, "rb") as fh:
                    raw = fh.read()
            except Exception:
                continue
            proj = extract_project_text_from_payload(try_decode_bytes(raw))
            if proj:
                _log((f"Resolved embedded project from {js}." if is_en else f"Project tertanam ditemukan di {js}."))
                return proj, assets
        _log(("No project.json or embedded JS project found in folder."
              if is_en else "project.json atau project tertanam di JS tidak ditemukan di folder."))
        return None, assets

    def _resolve_from_url(url: str):
        try:
            ai_provider = _get_ai_provider()
        except Exception:
            ai_provider = ""
        ai_key = ""
        try:
            ai_key = _resolve_ai_api_key()
        except Exception:
            ai_key = ""
        _log(("Resolving project from URL…" if is_en else "Mengambil project dari URL…"))
        proj, resolved = get_project_source(
            url, ai_api_key=ai_key or "", ai_provider=ai_provider or "",
            ai_mode=("auto_fallback" if ai_key else "off"))
        if proj:
            _log((f"Resolved from {resolved or url}." if is_en else f"Berhasil dari {resolved or url}."))
            return proj, {}
        _log(("Could not resolve project from URL." if is_en else "Gagal mengambil project dari URL."))
        return None, {}

    def _do_inject() -> None:
        kind = src_kind.get()
        src = src_path.get().strip()
        out = out_dir.get().strip()
        if not src:
            messagebox.showwarning("Inject", ("Please choose a project source." if is_en else "Pilih sumber project dulu."), parent=win)
            return
        if not out:
            messagebox.showwarning("Inject", ("Please choose an output folder." if is_en else "Pilih folder output dulu."), parent=win)
            return
        inject_btn.configure(state="disabled")
        close_btn.configure(state="disabled")

        def _worker():
            try:
                if kind == "file":
                    proj, assets = _resolve_from_file(src)
                elif kind == "folder":
                    proj, assets = _resolve_from_folder(src)
                else:
                    proj, assets = _resolve_from_url(src)
                if not proj:
                    _v25_safe_after(win, lambda: (inject_btn.configure(state="normal"), close_btn.configure(state="normal")))
                    return
                # Derive an output file_name stem.
                if kind == "url":
                    try:
                        stem = _build_output_name(src)
                    except Exception:
                        stem = "project"
                else:
                    base = os.path.basename(src.rstrip("/\\"))
                    stem = os.path.splitext(base)[0] or "project"
                _log(("Injecting into viewer…" if is_en else "Meng-inject ke viewer…"))
                out_path = _apply_offline_viewer(
                    output_dir=out,
                    project_json_str=proj,
                    viewer_meta=viewer_meta,
                    file_name=stem,
                    asset_source_dirs=assets or None,
                )
                if out_path:
                    rel = os.path.dirname(out_path)
                    _log(("✓ Offline viewer ready." if is_en else "✓ Viewer offline siap."))
                    _log(rel)

                    def _done_ok():
                        inject_btn.configure(state="normal")
                        close_btn.configure(state="normal")
                        if messagebox.askyesno("Inject",
                                               ("Offline viewer created.\nOpen the folder?"
                                                if is_en else "Viewer offline dibuat.\nBuka foldernya?"), parent=win):
                            try:
                                self._open_path_in_os(rel)
                            except Exception as _e:
                                logger.debug("open folder failed: %s", _e)
                    _v25_safe_after(win, _done_ok)
                else:
                    _log(("✗ Injection failed — viewer index.html not found or unsupported."
                          if is_en else "✗ Inject gagal — index.html viewer tidak ada atau tidak didukung."))
                    _v25_safe_after(win, lambda: (inject_btn.configure(state="normal"), close_btn.configure(state="normal")))
            except Exception as exc:
                _log((f"✗ Error: {exc}" if is_en else f"✗ Error: {exc}"))
                _v25_safe_after(win, lambda: (inject_btn.configure(state="normal"), close_btn.configure(state="normal")))

        threading.Thread(target=_worker, daemon=True).start()

    inject_btn = ctk.CTkButton(footer, text=("Inject" if is_en else "Inject"), width=130,
                               fg_color="#3b82f6", hover_color="#2563eb", command=_do_inject)
    inject_btn.grid(row=0, column=1, padx=(0, 8), pady=12)
    close_btn = ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=100,
                              fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=win.destroy)
    close_btn.grid(row=0, column=2, padx=(0, 16), pady=12)

    _on_kind()

def _v25_cloudflare_panel(self: Any) -> None:
    import customtkinter as ctk
    import threading
    from tkinter import messagebox

    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    st = _load_settings()
    _load_cloudflare_settings()

    win = self._make_singleton_window("cloudflare_center")
    if win is None:
        return
    win.title("☁ Cloudflare / FlareSolverr Center" if is_en else "☁ Pusat Cloudflare / FlareSolverr")
    win.configure(fg_color=p["bg"])
    _v25_center_window(win, self.root, 920, 720, min_w=780, min_h=620)
    try:
        win.transient(self.root)
        win.grab_set()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v25_cloudflare_panel (line 20863): %s", _ignored_exc)

    root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    root.pack(fill="both", expand=True)
    root.grid_rowconfigure(1, weight=1)
    root.grid_columnconfigure(0, weight=1)

    header = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=90)
    header.grid(row=0, column=0, sticky="ew")
    header.grid_propagate(False)
    header.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(header, text="☁", width=56, height=56,
                 fg_color="#172554" if self._is_dark else "#dbeafe", corner_radius=16,
                 font=ctk.CTkFont("Segoe UI Emoji", 24), text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=17)
    ctk.CTkLabel(header, text=("Cloudflare Access" if is_en else "Akses Cloudflare"),
                 font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(18, 0))
    ctk.CTkLabel(header, text=("Normal request → cloudscraper → FlareSolverr fallback. Use Auto unless you know the site needs a specific backend."
                                if is_en else "Request normal → cloudscraper → fallback FlareSolverr. Gunakan Auto kecuali situs membutuhkan backend tertentu."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 16))

    body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
    body.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=1)

    footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
    footer.grid(row=2, column=0, sticky="ew")
    footer.grid_columnconfigure(0, weight=1)

    mode_var = ctk.StringVar(value=_display_cloudflare_mode(st.get("cloudflare_mode", _CLOUDFLARE_MODE)))
    priority_var = ctk.StringVar(value=_display_cloudflare_priority(st.get("cloudflare_priority", "flaresolverr_first")))
    url_var = ctk.StringVar(value=st.get("flaresolverr_url", _FLARESOLVERR_URL))
    sess_var = ctk.StringVar(value=st.get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY))
    timeout_var = ctk.StringVar(value=str(st.get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT)))
    wait_var = ctk.StringVar(value=str(st.get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER)))
    proxy_var = ctk.StringVar(value=st.get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE))
    status_var = ctk.StringVar(value="Ready" if is_en else "Siap")

    def card(row: int, col: int, title: str, desc: str, icon: str):
        frame = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
        frame.grid(row=row, column=col, sticky="nsew", padx=7, pady=7)
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=icon, width=34, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=14, sticky="n")
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(14, 2))
        ctk.CTkLabel(frame, text=desc, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=340).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 12))
        return frame

    mode_card = card(0, 0, "Mode", ("Auto is recommended for most downloads." if is_en else "Auto direkomendasikan untuk sebagian besar download."), "🛡")
    ctk.CTkOptionMenu(mode_card, variable=mode_var, values=["Off", "Auto", "cloudscraper", "FlareSolverr"],
                      fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"],
                      dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"]).grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))
    ctk.CTkLabel(mode_card, text=("Auto fallback priority" if is_en else "Prioritas fallback Auto"),
                 text_color=p["muted"], anchor="w").grid(row=3, column=1, sticky="w", padx=(0, 12), pady=(0, 2))
    ctk.CTkOptionMenu(mode_card, variable=priority_var,
                      values=["FlareSolverr first", "cloudscraper first"],
                      fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"],
                      text_color=p["fg"], dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"]).grid(
        row=4, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))

    session_card = card(0, 1, "Session", ("Reuse-domain avoids recreating solver sessions too often." if is_en else "Reuse-domain mengurangi pembuatan sesi solver berulang."), "🧭")
    ctk.CTkOptionMenu(session_card, variable=sess_var, values=["temporary", "reuse-domain", "manual"],
                      fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"],
                      dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"]).grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))

    fs_card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    fs_card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=7, pady=7)
    fs_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(fs_card, text="⚙", width=34, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#38bdf8").grid(row=0, column=0, rowspan=6, padx=(16, 8), pady=16, sticky="n")
    ctk.CTkLabel(fs_card, text="FlareSolverr", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(14, 2))
    ctk.CTkLabel(fs_card, text=("Run flaresolverr.exe or Docker first. Default endpoint: http://localhost:8191/v1" if is_en else "Jalankan flaresolverr.exe atau Docker lebih dulu. Endpoint default: http://localhost:8191/v1"), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, columnspan=3, sticky="ew", pady=(0, 8))
    ctk.CTkLabel(fs_card, text="API URL", text_color=p["muted"], anchor="w").grid(row=2, column=1, sticky="w", pady=(2, 2))
    ctk.CTkEntry(fs_card, textvariable=url_var, fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"], font=ctk.CTkFont("Consolas", 10)).grid(row=3, column=1, columnspan=3, sticky="ew", padx=(0, 16), pady=(0, 8))
    ctk.CTkLabel(fs_card, text="Timeout", text_color=p["muted"]).grid(row=4, column=1, sticky="w", pady=(0, 2))
    ctk.CTkEntry(fs_card, textvariable=timeout_var, width=90, fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"], justify="center").grid(row=5, column=1, sticky="w", pady=(0, 14))
    ctk.CTkLabel(fs_card, text=("Wait after solve" if is_en else "Tunggu setelah solve"), text_color=p["muted"]).grid(row=4, column=2, sticky="w", padx=(20, 0), pady=(0, 2))
    ctk.CTkEntry(fs_card, textvariable=wait_var, width=90, fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"], justify="center").grid(row=5, column=2, sticky="w", padx=(20, 0), pady=(0, 14))
    ctk.CTkLabel(fs_card, text="Proxy", text_color=p["muted"]).grid(row=4, column=3, sticky="w", padx=(20, 16), pady=(0, 2))
    ctk.CTkOptionMenu(fs_card, variable=proxy_var, values=["inherit", "none"], width=130,
                      fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"]).grid(row=5, column=3, sticky="w", padx=(20, 16), pady=(0, 14))

    rec_card = card(2, 0, "Recommended defaults" if is_en else "Default yang disarankan",
                    "Auto · reuse-domain · timeout 60s · proxy inherit" if is_en else "Auto · reuse-domain · timeout 60 detik · proxy inherit", "✅")
    ctk.CTkButton(rec_card, text=("Apply recommended" if is_en else "Pakai rekomendasi"),
                  fg_color="#065f46", hover_color="#047857", text_color="#d1fae5",
                  command=lambda: (mode_var.set("Auto"), priority_var.set("FlareSolverr first"), sess_var.set("reuse-domain"), timeout_var.set("60"), wait_var.set("3"), proxy_var.set("inherit"), status_var.set("Recommended values applied." if is_en else "Nilai rekomendasi diterapkan."))).grid(row=2, column=1, sticky="w", padx=(0, 12), pady=(0, 14))

    status_card = card(2, 1, "Status", "Connection test and session cleanup results appear here." if is_en else "Hasil tes koneksi dan pembersihan sesi tampil di sini.", "📡")
    ctk.CTkLabel(status_card, textvariable=status_var, text_color=p["accent"], anchor="w", justify="left", wraplength=340).grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))

    def apply_settings(persist: bool = True) -> None:
        try: timeout_s = int(timeout_var.get() or 60)
        except Exception: timeout_s = 60
        try: wait_s = int(wait_var.get() or 3)
        except Exception: wait_s = 3
        _set_cloudflare_config(mode_var.get(), priority=_normalize_cloudflare_priority(priority_var.get()), flaresolverr_url=url_var.get(), session_policy=sess_var.get(), timeout=timeout_s, wait_after=wait_s, proxy_mode=proxy_var.get(), persist=persist)
        try: self._cf_mode_var.set(_display_cloudflare_mode(_CLOUDFLARE_MODE))
        except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in apply_settings (line 20952): %s", _ignored_exc)

    def do_test():
        apply_settings(True)
        status_var.set("Testing FlareSolverr…" if is_en else "Menguji FlareSolverr…")
        def worker():
            ok, msg = flaresolverr_test_connection()
            _v25_safe_after(win, lambda: status_var.set(("✓ " if ok else "✗ ") + msg))
        threading.Thread(target=worker, daemon=True).start()

    def do_clear():
        apply_settings(True)
        def worker():
            n = flaresolverr_destroy_sessions()
            _v25_safe_after(win, lambda: status_var.set((f"Cleared {n} session(s)" if is_en else f"{n} sesi dibersihkan")))
        threading.Thread(target=worker, daemon=True).start()

    def save_settings():
        apply_settings(True)
        status_var.set((f"Saved: {_display_cloudflare_mode(_CLOUDFLARE_MODE)}" if is_en else f"Tersimpan: {_display_cloudflare_mode(_CLOUDFLARE_MODE)}"))

    ctk.CTkButton(footer, text=("Test Connection" if is_en else "Tes Koneksi"), width=140,
                  fg_color="#3b82f6", hover_color="#2563eb", command=do_test).grid(row=0, column=0, sticky="w", padx=(18, 8), pady=12)
    ctk.CTkButton(footer, text=("Clear Sessions" if is_en else "Bersihkan Sesi"), width=130,
                  fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=do_clear).grid(row=0, column=1, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Save" if is_en else "Simpan"), width=110,
                  fg_color="#065f46", hover_color="#047857", text_color="#d1fae5", command=save_settings).grid(row=0, column=2, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=100,
                  fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=win.destroy).grid(row=0, column=3, padx=(0, 18), pady=12)



# ---- historical GUI behavior block ----

"""Historical v27 GUI patch panel bodies.

Phase 53 moves the v27 panel bodies out of ``legacy.py``. The code is copied
mechanically and synchronized with the legacy namespace to preserve callbacks,
text, and patch order.
"""






def _v27_cache_manager_panel(self: Any) -> None:
    """Modern cache center with location, refresh, open-folder, and safe clear."""
    import customtkinter as ctk
    from tkinter import messagebox
    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    win = self._make_singleton_window("cache_manager")
    if win is None:
        return
    win.title("💾 Image Cache Center" if is_en else "💾 Pusat Cache Gambar")
    win.configure(fg_color=p["bg"])
    _v25_center_window(win, self.root, 720, 520, min_w=640, min_h=440)
    try:
        win.transient(self.root); win.grab_set()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v27_cache_manager_panel (line 21047): %s", _ignored_exc)

    root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    root.pack(fill="both", expand=True)
    root.grid_rowconfigure(1, weight=1)
    root.grid_columnconfigure(0, weight=1)

    header = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=82)
    header.grid(row=0, column=0, sticky="ew"); header.grid_propagate(False)
    header.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(header, text="💾", width=52, height=52, fg_color=p["surface"], corner_radius=14,
                 font=ctk.CTkFont("Segoe UI Emoji", 22), text_color="#38bdf8").grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=15)
    ctk.CTkLabel(header, text=("Image Cache Center" if is_en else "Pusat Cache Gambar"),
                 font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 0))
    ctk.CTkLabel(header, text=("Shows where cached images are stored and lets you clear only the image cache."
                               if is_en else "Menampilkan lokasi cache gambar dan membersihkan cache gambar saja."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 14))

    body = ctk.CTkFrame(root, fg_color=p["bg"], corner_radius=0)
    body.grid(row=1, column=0, sticky="nsew", padx=18, pady=16)
    body.grid_columnconfigure(0, weight=1)
    body.grid_columnconfigure(1, weight=1)

    stats_frame = ctk.CTkFrame(body, fg_color="transparent")
    stats_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
    stats_var = ctk.StringVar(value="")
    path_var = ctk.StringVar(value="")

    badge_entries = ctk.CTkLabel(stats_frame, text="ENTRIES 0", width=130, height=38,
                                 fg_color="#0f766e", text_color="#ecfeff", corner_radius=12,
                                 font=ctk.CTkFont("Segoe UI", 12, "bold"))
    badge_entries.pack(side="left", padx=(0, 10))
    badge_size = ctk.CTkLabel(stats_frame, text="SIZE 0 MB", width=130, height=38,
                              fg_color="#1d4ed8", text_color="#eff6ff", corner_radius=12,
                              font=ctk.CTkFont("Segoe UI", 12, "bold"))
    badge_size.pack(side="left", padx=(0, 10))

    def _refresh() -> None:
        try:
            stats = _cache_stats()
            badge_entries.configure(text=(f"ENTRIES {stats['entries']}" if is_en else f"ENTRI {stats['entries']}"))
            badge_size.configure(text=f"SIZE {stats['size_mb']} MB" if is_en else f"UKURAN {stats['size_mb']} MB")
            cache_dir = os.path.join(os.path.dirname(_SETTINGS_FILE), "image_cache")
            path_var.set(cache_dir)
            stats_var.set((f"Cache is writable and used to avoid re-downloading duplicate images.\nLocation: {cache_dir}"
                           if is_en else f"Cache dapat ditulis dan dipakai agar gambar yang sama tidak diunduh ulang.\nLokasi: {cache_dir}"))
        except Exception as exc:
            stats_var.set((f"Failed to read cache: {exc}" if is_en else f"Gagal membaca cache: {exc}"))

    card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    card.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 12))
    card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(card, text="📁", width=38, font=ctk.CTkFont("Segoe UI Emoji", 20), text_color="#93c5fd").grid(row=0, column=0, rowspan=3, padx=(16, 10), pady=16, sticky="n")
    ctk.CTkLabel(card, text=("Cache location" if is_en else "Lokasi cache"), font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 2))
    ctk.CTkLabel(card, textvariable=stats_var, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=560).grid(row=1, column=1, sticky="ew", pady=(0, 8))
    path_entry = ctk.CTkEntry(card, textvariable=path_var, fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"], font=ctk.CTkFont("Consolas", 10))
    path_entry.grid(row=2, column=1, sticky="ew", padx=(0, 16), pady=(0, 16))

    note = ctk.CTkFrame(body, fg_color=p["panel"], corner_radius=12, border_width=1, border_color=p["border"])
    note.grid(row=2, column=0, columnspan=2, sticky="ew")
    ctk.CTkLabel(note, text=("Safe operation" if is_en else "Operasi aman"), font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], anchor="w").pack(fill="x", padx=14, pady=(12, 2))
    ctk.CTkLabel(note, text=("Clearing this cache removes only cached downloaded images. It does not delete completed CYOA outputs or reports."
                             if is_en else "Membersihkan cache ini hanya menghapus cache gambar. Output CYOA selesai dan laporan tidak dihapus."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=620).pack(fill="x", padx=14, pady=(0, 12))

    footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
    footer.grid(row=2, column=0, sticky="ew"); footer.grid_columnconfigure(0, weight=1)

    def _clear() -> None:
        if not messagebox.askyesno(("Clear image cache" if is_en else "Bersihkan cache gambar"),
                                   ("Remove cached image files? Completed downloads are not touched."
                                    if is_en else "Hapus file cache gambar? Download yang sudah selesai tidak disentuh."), parent=win):
            return
        try:
            n = _clear_image_cache()
            logger.info(f"Image cache cleared: {n} file(s)")
            _refresh()
        except Exception as exc:
            messagebox.showerror("Image Cache" if is_en else "Cache Gambar", str(exc), parent=win)

    ctk.CTkButton(footer, text=("Refresh" if is_en else "Muat Ulang"), width=110, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=_refresh).grid(row=0, column=0, sticky="w", padx=(18, 8), pady=12)
    ctk.CTkButton(footer, text=("Open Folder" if is_en else "Buka Folder"), width=120, fg_color="#0f766e", hover_color="#0d9488", text_color="#ecfeff", command=lambda: _v27_open_path(os.path.dirname(path_var.get()) if not os.path.isdir(path_var.get()) else path_var.get())).grid(row=0, column=1, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Clear Cache" if is_en else "Bersihkan Cache"), width=120, fg_color=p["danger_bg"], hover_color=p["danger_hv"], text_color=p["danger_fg"], command=_clear).grid(row=0, column=2, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=100, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=win.destroy).grid(row=0, column=3, padx=(0, 18), pady=12)
    _refresh()

def _v27_check_updates_panel(self: Any) -> None:
    """Modern update center that explains where it comes from and why it may be disabled."""
    import customtkinter as ctk
    import webbrowser
    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    win = self._make_singleton_window("check_updates")
    if win is None:
        return
    win.title("🔄 Update Center" if is_en else "🔄 Pusat Update")
    win.configure(fg_color=p["bg"])
    _v25_center_window(win, self.root, 720, 500, min_w=640, min_h=420)
    try:
        win.transient(self.root); win.grab_set()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v27_check_updates_panel (line 21149): %s", _ignored_exc)

    root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    root.pack(fill="both", expand=True)
    root.grid_rowconfigure(1, weight=1); root.grid_columnconfigure(0, weight=1)
    header = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=82)
    header.grid(row=0, column=0, sticky="ew"); header.grid_propagate(False); header.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(header, text="🔄", width=52, height=52, fg_color=p["surface"], corner_radius=14,
                 font=ctk.CTkFont("Segoe UI Emoji", 22), text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=15)
    ctk.CTkLabel(header, text=("Update Center" if is_en else "Pusat Update"), font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 0))
    ctk.CTkLabel(header, text=("Checks a configured GitHub release API endpoint. This is intentionally disabled in local/private builds unless configured."
                               if is_en else "Mengecek endpoint GitHub Release API yang dikonfigurasi. Build lokal/private sengaja nonaktif kecuali dikonfigurasi."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 14))

    body = ctk.CTkFrame(root, fg_color=p["bg"], corner_radius=0)
    body.grid(row=1, column=0, sticky="nsew", padx=18, pady=16)
    body.grid_columnconfigure(0, weight=1)
    status_var = ctk.StringVar(value="Checking…" if is_en else "Mengecek…")
    detail_var = ctk.StringVar(value="")
    release_url = {"url": ""}

    status_card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    status_card.grid(row=0, column=0, sticky="ew", pady=(0, 12)); status_card.grid_columnconfigure(1, weight=1)
    icon_lbl = ctk.CTkLabel(status_card, text="⏳", width=44, font=ctk.CTkFont("Segoe UI Emoji", 24), text_color="#fbbf24")
    icon_lbl.grid(row=0, column=0, rowspan=2, padx=(16, 10), pady=18, sticky="n")
    ctk.CTkLabel(status_card, textvariable=status_var, font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(18, 4))
    ctk.CTkLabel(status_card, textvariable=detail_var, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=600).grid(row=1, column=1, sticky="ew", pady=(0, 18))

    where_card = ctk.CTkFrame(body, fg_color=p["panel"], corner_radius=12, border_width=1, border_color=p["border"])
    where_card.grid(row=1, column=0, sticky="ew"); where_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(where_card, text="📍", width=36, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#93c5fd").grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=14, sticky="n")
    ctk.CTkLabel(where_card, text=("Where this setting lives" if is_en else "Lokasi pengaturan ini"), font=ctk.CTkFont("Segoe UI", 12, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(14, 2))
    ctk.CTkLabel(where_card, text=("_GITHUB_RELEASE_API is a script-level constant near the top of the Python file. Leave it blank for private/local builds, or set it to your repository releases/latest API URL."
                                   if is_en else "_GITHUB_RELEASE_API adalah konstanta di bagian atas file Python. Biarkan kosong untuk build lokal/private, atau isi dengan URL API releases/latest repository Anda."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=620).grid(row=1, column=1, sticky="ew", pady=(0, 14))

    footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
    footer.grid(row=2, column=0, sticky="ew"); footer.grid_columnconfigure(0, weight=1)

    open_btn = ctk.CTkButton(footer, text=("Open Release" if is_en else "Buka Release"), width=120, state="disabled",
                             fg_color="#3b82f6", hover_color="#2563eb", command=lambda: webbrowser.open(release_url.get("url") or ""))
    open_btn.grid(row=0, column=1, padx=(0, 8), pady=12)

    def _apply(info: Any = None, error: str = "") -> None:
        if error:
            icon_lbl.configure(text="⚠", text_color="#f59e0b")
            status_var.set("Update check failed" if is_en else "Cek update gagal")
            detail_var.set(error)
            return
        if info == "__not_configured__":
            icon_lbl.configure(text="ℹ", text_color="#60a5fa")
            status_var.set((f"CYOA Downloader v{_APP_VERSION}" if is_en else f"CYOA Downloader v{_APP_VERSION}"))
            detail_var.set(("Auto-update is not configured. This is normal for standalone/local builds. Set _GITHUB_RELEASE_API in the script to enable release checks."
                            if is_en else "Auto-update belum dikonfigurasi. Ini normal untuk build standalone/lokal. Isi _GITHUB_RELEASE_API di script untuk mengaktifkan cek release."))
            return
        if info:
            icon_lbl.configure(text="⬆", text_color="#22c55e")
            status_var.set((f"Update available: v{info.get('version','?')}" if is_en else f"Update tersedia: v{info.get('version','?')}"))
            detail_var.set((f"Current: v{_APP_VERSION}\n" + str(info.get("notes", ""))[:500]))
            release_url["url"] = info.get("url", "") or ""
            if release_url["url"]:
                open_btn.configure(state="normal")
            return
        icon_lbl.configure(text="✅", text_color="#22c55e")
        status_var.set("Already up to date" if is_en else "Sudah versi terbaru")
        detail_var.set(f"Current version: v{_APP_VERSION}" if is_en else f"Versi saat ini: v{_APP_VERSION}")

    def _run_check() -> None:
        status_var.set("Checking…" if is_en else "Mengecek…"); detail_var.set(""); icon_lbl.configure(text="⏳", text_color="#fbbf24"); open_btn.configure(state="disabled"); release_url["url"] = ""
        def worker() -> None:
            try:
                if not _GITHUB_RELEASE_API:
                    _v27_safe_after(win, lambda: _apply("__not_configured__"))
                    return
                info = _check_for_app_updates()
                _v27_safe_after(win, lambda i=info: _apply(i))
            except Exception as exc:
                _v27_safe_after(win, lambda e=str(exc): _apply(None, e))
        threading.Thread(target=worker, daemon=True).start()

    ctk.CTkButton(footer, text=("Check Again" if is_en else "Cek Lagi"), width=120, fg_color="#0f766e", hover_color="#0d9488", text_color="#ecfeff", command=_run_check).grid(row=0, column=0, sticky="w", padx=(18, 8), pady=12)
    ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=100, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=win.destroy).grid(row=0, column=2, padx=(0, 18), pady=12)
    _run_check()

def _v27_ai_settings_panel(self: Any) -> None:
    """AI Assist Center with DeepSeek/Qwen/custom endpoint support surfaced in GUI."""
    import customtkinter as ctk
    import threading
    from tkinter import messagebox
    p = self._p()
    is_en = getattr(self, "_language", "id") == "en"
    win = self._make_singleton_window("ai_assist_center")
    if win is None:
        return
    win.title("🤖 AI Assist Center" if is_en else "🤖 Pusat AI Assist")
    win.configure(fg_color=p["bg"])
    _v25_center_window(win, self.root, 920, 760, min_w=780, min_h=620)
    try:
        win.transient(self.root); win.grab_set()
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _v27_ai_settings_panel (line 21250): %s", _ignored_exc)

    root = ctk.CTkFrame(win, fg_color=p["bg"], corner_radius=0)
    root.pack(fill="both", expand=True)
    root.grid_rowconfigure(1, weight=1); root.grid_columnconfigure(0, weight=1)
    header = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0, height=84)
    header.grid(row=0, column=0, sticky="ew"); header.grid_propagate(False); header.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(header, text="🤖", width=54, height=54, fg_color=p["surface"], corner_radius=14,
                 font=ctk.CTkFont("Segoe UI Emoji", 24), text_color="#a78bfa").grid(row=0, column=0, rowspan=2, padx=(18, 12), pady=15)
    ctk.CTkLabel(header, text=("AI Assist — Multi-provider Recovery" if is_en else "AI Assist — Recovery Multi-provider"),
                 font=ctk.CTkFont("Segoe UI", 18, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 0))
    ctk.CTkLabel(header, text=("Supports Anthropic, OpenAI, Gemini, Ollama, DeepSeek, Qwen/DashScope, Groq, OpenRouter, and custom OpenAI-compatible endpoints."
                               if is_en else "Mendukung Anthropic, OpenAI, Gemini, Ollama, DeepSeek, Qwen/DashScope, Groq, OpenRouter, dan endpoint custom kompatibel OpenAI."),
                 font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 14))

    body = ctk.CTkScrollableFrame(root, fg_color=p["bg"], scrollbar_button_color=p["surface2"])
    body.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
    body.grid_columnconfigure(0, weight=1); body.grid_columnconfigure(1, weight=1)
    footer = ctk.CTkFrame(root, fg_color=p["panel"], corner_radius=0)
    footer.grid(row=2, column=0, sticky="ew"); footer.grid_columnconfigure(0, weight=1)

    st = _load_settings()
    toggle_var = ctk.BooleanVar(value=bool(getattr(self, "_ai_enabled", False)))
    provider_var = ctk.StringVar(value=_normalize_ai_provider(st.get("ai_provider", "anthropic")))
    model_var = ctk.StringVar(value=_get_ai_model(provider_var.get()))
    mode_var = ctk.StringVar(value=_normalize_ai_mode(st.get("ai_mode", "auto_fallback")))
    storage_var = ctk.StringVar(value=_normalize_ai_key_storage(st.get("ai_key_storage", getattr(self, "_ai_key_storage", "session"))))
    session_key_var = ctk.StringVar(value=getattr(self, "_ai_api_key", "") if storage_var.get() in {"session", "plain"} else "")
    ollama_url_var = ctk.StringVar(value=st.get("ollama_url", OLLAMA_DEFAULT_URL))
    custom_base_var = ctk.StringVar(value=st.get("ai_custom_base_url", ""))
    status_var = ctk.StringVar(value=_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get()))
    warn_var = ctk.StringVar(value="")

    def card(row: int, col: int, title: str, desc: str, icon: str = ""):
        frame = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
        frame.grid(row=row, column=col, sticky="nsew", padx=7, pady=7)
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=icon, width=34, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#a78bfa").grid(row=0, column=0, rowspan=2, padx=(14, 8), pady=14, sticky="n")
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(14, 2))
        ctk.CTkLabel(frame, text=desc, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=350).grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 12))
        return frame

    overview = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    overview.grid(row=0, column=0, columnspan=2, sticky="ew", padx=7, pady=(0, 8)); overview.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(overview, text="⚡", width=34, font=ctk.CTkFont("Segoe UI Emoji", 20), text_color="#60a5fa").grid(row=0, column=0, rowspan=2, padx=(16, 8), pady=16)
    ctk.CTkLabel(overview, text=("Enable AI Assist" if is_en else "Aktifkan AI Assist"), font=ctk.CTkFont("Segoe UI", 14, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(16, 2))
    ctk.CTkLabel(overview, text=("Use AI only as fallback diagnostics/recovery when normal detection fails." if is_en else "Gunakan AI hanya sebagai fallback diagnostik/recovery saat deteksi normal gagal."), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w").grid(row=1, column=1, sticky="ew", pady=(0, 16))
    ctk.CTkSwitch(overview, text="", variable=toggle_var, progress_color="#8b5cf6", width=54).grid(row=0, column=2, rowspan=2, padx=18, pady=16)

    prov_card = card(1, 0, "Provider & model" if is_en else "Provider & model", "Choose preset or custom OpenAI-compatible provider." if is_en else "Pilih preset atau provider custom kompatibel OpenAI.", "🧠")
    ctk.CTkLabel(prov_card, text="Provider", text_color=p["muted"], anchor="w").grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(4, 2))
    provider_menu = ctk.CTkOptionMenu(prov_card, variable=provider_var, values=_v27_ai_provider_values(), fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"], dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"])
    provider_menu.grid(row=3, column=1, sticky="ew", padx=(0, 12), pady=(0, 8))
    ctk.CTkLabel(prov_card, text="Model", text_color=p["muted"], anchor="w").grid(row=4, column=1, sticky="ew", padx=(0, 12), pady=(0, 2))
    model_menu = ctk.CTkComboBox(prov_card, variable=model_var, values=_ai_model_options(provider_var.get()), fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], border_color=p["border"], text_color=p["fg"], dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"])
    model_menu.grid(row=5, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))

    mode_card = card(1, 1, "Mode & storage" if is_en else "Mode & penyimpanan", "Control when AI runs and where the key is stored." if is_en else "Atur kapan AI berjalan dan lokasi penyimpanan key.", "🔐")
    ctk.CTkLabel(mode_card, text=("AI Mode" if is_en else "Mode AI"), text_color=p["muted"], anchor="w").grid(row=2, column=1, sticky="ew", padx=(0, 12), pady=(4, 2))
    ctk.CTkOptionMenu(mode_card, variable=mode_var, values=["off", "diagnostics", "auto_fallback", "aggressive_recovery"], fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"], dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"]).grid(row=3, column=1, sticky="ew", padx=(0, 12), pady=(0, 8))
    ctk.CTkLabel(mode_card, text=("Key Storage" if is_en else "Penyimpanan Key"), text_color=p["muted"], anchor="w").grid(row=4, column=1, sticky="ew", padx=(0, 12), pady=(0, 2))
    ctk.CTkOptionMenu(mode_card, variable=storage_var, values=["session", "env", "keyring", "plain"], fg_color=p["surface2"], button_color=p["surface"], button_hover_color=p["surface2"], text_color=p["fg"], dropdown_fg_color=p["surface"], dropdown_text_color=p["fg"]).grid(row=5, column=1, sticky="ew", padx=(0, 12), pady=(0, 14))

    key_card = ctk.CTkFrame(body, fg_color=p["surface"], corner_radius=14, border_width=1, border_color=p["border"])
    key_card.grid(row=2, column=0, columnspan=2, sticky="ew", padx=7, pady=7); key_card.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(key_card, text="🔑", width=34, font=ctk.CTkFont("Segoe UI Emoji", 18), text_color="#fbbf24").grid(row=0, column=0, rowspan=7, padx=(16, 8), pady=16, sticky="n")
    ctk.CTkLabel(key_card, text="Credentials & endpoints" if is_en else "Kredensial & endpoint", font=ctk.CTkFont("Segoe UI", 13, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=1, sticky="ew", pady=(14, 2))
    ctk.CTkLabel(key_card, text=("Session/keyring/env are recommended. Custom endpoint is used only when provider = custom." if is_en else "Session/keyring/env direkomendasikan. Endpoint custom hanya dipakai saat provider = custom."), font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left").grid(row=1, column=1, sticky="ew", pady=(0, 8))
    key_entry = ctk.CTkEntry(key_card, textvariable=session_key_var, show="•", fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"], height=34)
    key_entry.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=(0, 8))
    ctk.CTkLabel(key_card, text=("Ollama URL" if is_en else "URL Ollama"), text_color=p["muted"], anchor="w").grid(row=3, column=1, sticky="ew", pady=(0, 2))
    ollama_url_entry = ctk.CTkEntry(key_card, textvariable=ollama_url_var, fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"], height=34)
    ollama_url_entry.grid(row=4, column=1, sticky="ew", padx=(0, 14), pady=(0, 8))
    ctk.CTkLabel(key_card, text=("Custom base URL" if is_en else "Base URL custom"), text_color=p["muted"], anchor="w").grid(row=5, column=1, sticky="ew", pady=(0, 2))
    custom_base_entry = ctk.CTkEntry(key_card, textvariable=custom_base_var, fg_color=p["input_bg"], border_color=p["border"], text_color=p["input_fg"], height=34, placeholder_text="https://your-openai-compatible-endpoint/v1")
    custom_base_entry.grid(row=6, column=1, sticky="ew", padx=(0, 14), pady=(0, 14))

    status_card = ctk.CTkFrame(body, fg_color=p["panel"], corner_radius=12, border_width=1, border_color=p["border"])
    status_card.grid(row=3, column=0, columnspan=2, sticky="ew", padx=7, pady=(4, 10)); status_card.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(status_card, textvariable=status_var, font=ctk.CTkFont("Segoe UI", 10), text_color=p["muted"], anchor="w", justify="left", wraplength=740).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))
    ctk.CTkLabel(status_card, textvariable=warn_var, font=ctk.CTkFont("Segoe UI", 10, "bold"), text_color="#f59e0b", anchor="w", justify="left", wraplength=740).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))

    def _refresh_key_ui(*_):
        mode = _normalize_ai_key_storage(storage_var.get()); provider = _normalize_ai_provider(provider_var.get())
        for entry in (ollama_url_entry, custom_base_entry):
            try: entry.configure(state="disabled")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21337): %s", _ignored_exc)
        if provider == "ollama":
            try: key_entry.configure(state="disabled", placeholder_text="Ollama uses local API")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21340): %s", _ignored_exc)
            try: ollama_url_entry.configure(state="normal")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21342): %s", _ignored_exc)
            warn_var.set("Ollama uses a local endpoint; no cloud API key is needed." if is_en else "Ollama memakai endpoint lokal; API key cloud tidak diperlukan.")
        elif provider == "custom":
            try: custom_base_entry.configure(state="normal")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21346): %s", _ignored_exc)
            try: key_entry.configure(state="normal" if mode != "env" else "disabled", placeholder_text="API key..." if mode != "env" else (_ai_primary_env_var(provider) or "CUSTOM_AI_API_KEY"))
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21348): %s", _ignored_exc)
            warn_var.set("Custom provider uses /chat/completions on the custom base URL. Store keys with session/keyring/env when possible." if is_en else "Provider custom memakai /chat/completions pada base URL custom. Simpan key dengan session/keyring/env jika memungkinkan.")
        elif mode == "env":
            try: key_entry.configure(state="disabled", placeholder_text=_ai_primary_env_var(provider) or "Environment variable")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21352): %s", _ignored_exc)
            warn_var.set(("Set " + " or ".join(_ai_env_vars(provider)) + " in the OS environment. The app will not store the key.") if is_en else ("Atur " + " atau ".join(_ai_env_vars(provider)) + " di environment OS. Aplikasi tidak menyimpan key."))
        elif mode == "keyring":
            try: key_entry.configure(state="normal", placeholder_text="Enter key to save to OS Credential Manager")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21356): %s", _ignored_exc)
            warn_var.set(("Requires optional package: pip install keyring" if not _keyring_module() else "Key will be stored in the OS credential store.") if is_en else ("Membutuhkan paket opsional: pip install keyring" if not _keyring_module() else "Key akan disimpan di credential store sistem operasi."))
        elif mode == "plain":
            try: key_entry.configure(state="normal", placeholder_text="API key...")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21360): %s", _ignored_exc)
            warn_var.set("Warning: plain-text storage writes the API key into settings.json." if is_en else "Peringatan: penyimpanan plain-text menulis API key ke settings.json.")
        else:
            try: key_entry.configure(state="normal", placeholder_text="Session only; cleared when app exits")
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _refresh_key_ui (line 21364): %s", _ignored_exc)
            warn_var.set("Safest default. The key stays in memory only." if is_en else "Default paling aman. Key hanya berada di memori.")
        status_var.set(_ai_key_status_text(mode, session_key_var.get(), provider))

    def _provider_changed(*_):
        prov = _normalize_ai_provider(provider_var.get()); opts = _ai_model_options(prov)
        try: model_menu.configure(values=opts)
        except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _provider_changed (line 21371): %s", _ignored_exc)
        if model_var.get() not in opts:
            model_var.set(_default_ai_model(prov))
        if prov == "ollama":
            session_key_var.set("")
        elif _normalize_ai_key_storage(storage_var.get()) == "plain":
            session_key_var.set(_resolve_ai_api_key(storage="plain", provider=prov))
        else:
            session_key_var.set("")
        _refresh_key_ui()

    storage_var.trace_add("write", _refresh_key_ui); provider_var.trace_add("write", _provider_changed)
    session_key_var.trace_add("write", lambda *_: status_var.set(_ai_key_status_text(storage_var.get(), session_key_var.get(), provider_var.get())))

    def _save(show_status: bool = True) -> bool:
        prov = _normalize_ai_provider(provider_var.get()); storage = _normalize_ai_key_storage(storage_var.get()); mode = _normalize_ai_mode(mode_var.get())
        key_value = session_key_var.get().strip(); custom_base = custom_base_var.get().strip().rstrip("/")
        settings = {"ai_enabled": bool(toggle_var.get()), "ai_provider": prov, "ai_model": model_var.get().strip() or _default_ai_model(prov), "ai_mode": mode, "ai_key_storage": storage, "ollama_url": ollama_url_var.get().strip() or OLLAMA_DEFAULT_URL, "ai_custom_base_url": custom_base}
        try:
            if prov == "custom" and not custom_base:
                raise ValueError("Custom provider requires a base URL." if is_en else "Provider custom membutuhkan base URL.")
            settings = _clear_ai_plain_keys(settings, None)
            if storage == "plain" and key_value and prov != "ollama":
                settings[_plain_ai_key_setting(prov)] = key_value
            _update_settings(settings)
            if storage == "session" and prov != "ollama": self._ai_api_key = key_value
            elif storage == "keyring" and key_value and prov != "ollama":
                if not _write_ai_key_to_keyring(key_value, prov): raise RuntimeError("keyring write failed")
                self._ai_api_key = ""
            elif storage in {"env", "plain"}: self._ai_api_key = ""
            self._ai_enabled = bool(toggle_var.get()); self._ai_key_storage = storage; self._ai_provider = prov; self._ai_model = settings["ai_model"]; self._ai_mode = mode
            try: self._ai_var.set(self._ai_enabled)
            except Exception as _ignored_exc: logger.debug("Ignored recoverable exception in _save (line 21403): %s", _ignored_exc)
            _refresh_key_ui()
            if show_status: status_var.set("Saved. AI Assist settings updated." if is_en else "Tersimpan. Pengaturan AI Assist diperbarui.")
            return True
        except Exception as exc:
            messagebox.showerror("AI Assist", (f"Failed to save: {exc}" if is_en else f"Gagal menyimpan: {exc}"), parent=win)
            return False

    def _clear_key() -> None:
        try:
            _clear_ai_api_key_storage(storage_var.get(), provider_var.get(), clear_all=False); session_key_var.set(""); self._ai_api_key = ""; _refresh_key_ui()
            status_var.set("Key cleared for the selected storage/provider." if is_en else "Key dibersihkan untuk storage/provider terpilih.")
        except Exception as exc: messagebox.showerror("AI Assist", str(exc), parent=win)

    def _test() -> None:
        if not _save(show_status=False): return
        prov = _normalize_ai_provider(provider_var.get())
        key = _resolve_ai_api_key(session_key=session_key_var.get(), storage=storage_var.get(), provider=prov)
        if prov != "ollama" and not key:
            messagebox.showwarning("AI Assist", "No API key available." if is_en else "API key belum tersedia.", parent=win); return
        status_var.set("Testing provider connection…" if is_en else "Menguji koneksi provider…")
        def worker():
            try:
                res = _ai_call(key, "Reply exactly: OK", max_tokens=16, label="AI provider test", model=model_var.get(), provider=prov)
                ok = bool(res)
                _v27_safe_after(win, lambda: status_var.set(("Provider test succeeded." if ok and is_en else "Tes provider berhasil." if ok else "Provider test failed. Check key, model, endpoint, and network." if is_en else "Tes provider gagal. Cek key, model, endpoint, dan jaringan.")))
            except Exception as exc:
                _v27_safe_after(win, lambda e=str(exc): status_var.set(("Test failed: " if is_en else "Tes gagal: ") + e))
        threading.Thread(target=worker, daemon=True).start()

    ctk.CTkButton(footer, text=("Test Provider" if is_en else "Tes Provider"), width=130, fg_color="#3b82f6", hover_color="#2563eb", command=_test).grid(row=0, column=0, sticky="w", padx=(18, 8), pady=12)
    ctk.CTkButton(footer, text=("Clear Key" if is_en else "Bersihkan Key"), width=110, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], command=_clear_key).grid(row=0, column=1, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Save" if is_en else "Simpan"), width=110, fg_color="#065f46", hover_color="#047857", text_color="#d1fae5", command=lambda: _save(True)).grid(row=0, column=2, padx=(0, 8), pady=12)
    ctk.CTkButton(footer, text=("Close" if is_en else "Tutup"), width=100, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["fg"], command=win.destroy).grid(row=0, column=3, padx=(0, 18), pady=12)
    _refresh_key_ui()



# ---- historical GUI behavior block ----

"""Historical v46 GUI/progress patch bodies moved out of legacy.py.

The functions are copied mechanically. Legacy still owns the patch ordering and
method assignment in this transition phase; this module receives the historical
global namespace through ``_sync_legacy_globals``.
"""



def _v46_gui_init(self, root) -> None:
    self._cancel_event = threading.Event()
    self._worker_thread = None
    self._run_started_wall = 0.0
    self._v46_progress_queue = log_queue_module.Queue(maxsize=1200)
    self._v46_telemetry = DownloadTelemetry()
    self._v46_progress_after_id = None
    self._v46_close_pending = False
    self._v46_source_full = ""
    self._v46_resolved_full = ""
    _v46_gui_init_legacy(self, root)
    self._v46_progress_handler = _V46TelemetryLogHandler(self)
    logger.addHandler(self._v46_progress_handler)
    self._v46_progress_after_id = self.root.after(125, self._v46_poll_progress)
    try:
        self.root.protocol("WM_DELETE_WINDOW", self._v46_on_close)
    except Exception as exc:
        logger.debug(f"Could not install close handler: {exc}")

def _v46_default_progress_expanded(screen_height: int) -> bool:
    """Keep detailed telemetry collapsed by default on short displays."""
    try:
        return int(screen_height) >= 900
    except (TypeError, ValueError):
        return False

def _v46_apply_progress_visibility(self, expanded: Optional[bool] = None) -> None:
    """Show or hide detailed cards while retaining a compact progress summary."""
    if expanded is not None:
        self._v46_progress_expanded = bool(expanded)
    panel = getattr(self, "_v46_progress_panel", None)
    button = getattr(self, "_v46_progress_toggle_btn", None)
    if panel is None:
        return
    if bool(getattr(self, "_v46_progress_expanded", False)):
        panel.grid()
        if button is not None:
            button.configure(text="Hide Progress")
    else:
        panel.grid_remove()
        if button is not None:
            button.configure(text="Show Progress")

def _v46_toggle_progress_panel(self) -> None:
    self._v46_apply_progress_visibility(not bool(getattr(self, "_v46_progress_expanded", False)))

def _v46_gui_setup_ui(self) -> None:
    _v46_gui_setup_ui_legacy(self)
    import customtkinter as ctk
    import tkinter as tk
    p = self._p()
    action_bar = self._pb.master.master.master
    main = action_bar.master
    try:
        main.grid_rowconfigure(3, weight=1, minsize=150)
    except Exception as exc:
        logger.debug(f"Could not reserve minimum log height: {exc}")

    progress_host = ctk.CTkFrame(action_bar, fg_color=p["panel"], corner_radius=0)
    progress_host.grid(row=3, column=0, sticky="ew", padx=0, pady=0)
    progress_host.grid_columnconfigure(0, weight=1)
    self._v46_progress_host = progress_host

    progress_header = ctk.CTkFrame(progress_host, fg_color=p["panel"], corner_radius=0)
    progress_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(3, 3))
    progress_header.grid_columnconfigure(0, weight=1)
    self._v46_progress_summary_var = ctk.StringVar(value=f"{_v469_ps(self, 'progress')}: {_v469_state_label(self, 'IDLE')} | {_v469_ps(self, 'job')} 0 {_v469_ps(self, 'of')} 0 | {_v469_ps(self, 'speed')} 0 B/s")
    ctk.CTkLabel(
        progress_header,
        textvariable=self._v46_progress_summary_var,
        font=ctk.CTkFont("Segoe UI", 9, "bold"),
        text_color=p["muted"],
        anchor="w",
    ).grid(row=0, column=0, sticky="ew", padx=(2, 8))
    self._v46_progress_toggle_btn = ctk.CTkButton(
        progress_header,
        text="Show Progress",
        width=104,
        height=24,
        fg_color=p["surface2"],
        hover_color=p["surface"],
        text_color=p["muted"],
        command=self._v46_toggle_progress_panel,
    )
    self._v46_progress_toggle_btn.grid(row=0, column=1, sticky="e")

    panel = ctk.CTkFrame(progress_host, fg_color=p["panel"], corner_radius=0)
    panel.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 0))
    panel.grid_columnconfigure(0, weight=1, uniform="v46progress")
    panel.grid_columnconfigure(1, weight=2, uniform="v46progress")
    panel.grid_columnconfigure(2, weight=2, uniform="v46progress")
    self._v46_progress_panel = panel
    self._v46_progress_expanded = _v46_default_progress_expanded(self.root.winfo_screenheight())
    self._v46_apply_progress_visibility(self._v46_progress_expanded)

    def card(column: int, title: str):
        frame = ctk.CTkFrame(panel, fg_color=p["surface"], corner_radius=10, border_width=1, border_color=p["border"])
        frame.grid(row=0, column=column, sticky="nsew", padx=(10 if column == 0 else 4, 10 if column == 2 else 4), pady=8)
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont("Segoe UI", 11, "bold"), text_color=p["fg"], anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 3))
        return frame

    overall = card(0, "Overall Queue")
    self._v46_overall_var = ctk.StringVar(value=f"{_v469_ps(self, 'job')} 0 {_v469_ps(self, 'of')} 0\n{_v469_ps(self, 'completed')}: 0 | {_v469_ps(self, 'failed')}: 0 | {_v469_ps(self, 'remaining')}: 0")
    ctk.CTkLabel(overall, textvariable=self._v46_overall_var, font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"], anchor="w", justify="left").grid(row=1, column=0, sticky="ew", padx=10)
    self._v46_overall_pb = ctk.CTkProgressBar(overall, height=7, fg_color=p["surface2"], progress_color="#22c55e")
    self._v46_overall_pb.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 8)); self._v46_overall_pb.set(0)

    current = card(1, "Current Job")
    self._v46_job_var = ctk.StringVar(value=f"{_v469_ps(self, 'mode')}: — | {_v469_ps(self, 'stage')}: {_v469_state_label(self, 'IDLE')}\n{_v469_ps(self, 'assets')}: — | {_v469_ps(self, 'success')}: 0 | {_v469_ps(self, 'failed')}: 0 | {_v469_ps(self, 'skipped')}: 0")
    ctk.CTkLabel(current, textvariable=self._v46_job_var, font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"], anchor="w", justify="left").grid(row=1, column=0, sticky="ew", padx=10)
    self._v46_source_var = ctk.StringVar(value=_v469_ps(self, "source") + ": —")
    self._v46_resolved_var = ctk.StringVar(value=_v469_ps(self, "resolved") + ": —")
    self._v46_source_label = ctk.CTkLabel(current, textvariable=self._v46_source_var, font=ctk.CTkFont("Segoe UI", 9), text_color="#60a5fa", anchor="w", cursor="hand2")
    self._v46_source_label.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 0))
    self._v46_resolved_label = ctk.CTkLabel(current, textvariable=self._v46_resolved_var, font=ctk.CTkFont("Segoe UI", 9), text_color="#a78bfa", anchor="w", cursor="hand2")
    self._v46_resolved_label.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 3))
    self._v46_job_pb = ctk.CTkProgressBar(current, height=7, fg_color=p["surface2"], progress_color="#3b82f6", mode="indeterminate")
    self._v46_job_pb.grid(row=4, column=0, sticky="ew", padx=10, pady=(2, 8)); self._v46_job_pb.start()

    file_card = card(2, "Current File & Transfer")
    self._v46_file_var = ctk.StringVar(value=f"{_v469_ps(self, 'current_file')}: —\n{_v469_ps(self, 'data')}: 0 B | {_v469_ps(self, 'speed')}: 0 B/s | {_v469_ps(self, 'average')}: 0 B/s\n{_v469_ps(self, 'elapsed')}: 00:00:00 | {_v469_ps(self, 'eta')}: {_v469_ps(self, 'unknown')}")
    ctk.CTkLabel(file_card, textvariable=self._v46_file_var, font=ctk.CTkFont("Segoe UI", 9), text_color=p["muted"], anchor="w", justify="left", wraplength=410).grid(row=1, column=0, sticky="ew", padx=10)
    self._v46_file_pb = ctk.CTkProgressBar(file_card, height=7, fg_color=p["surface2"], progress_color="#14b8a6", mode="indeterminate")
    self._v46_file_pb.grid(row=2, column=0, sticky="ew", padx=10, pady=(5, 3)); self._v46_file_pb.start()
    self._v46_speed_canvas = tk.Canvas(file_card, height=38, bg=p["surface2"], highlightthickness=0, bd=0)
    self._v46_speed_canvas.grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 8))
    self._v46_speed_canvas.bind("<Configure>", lambda _e: self._v46_draw_speed_graph())

    controls = ctk.CTkFrame(panel, fg_color="transparent")
    controls.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 8))
    controls.grid_columnconfigure(2, weight=1)
    self._v46_cancel_btn = ctk.CTkButton(controls, text=_v469_ps(self, "cancel"), width=90, height=28, fg_color="#7f1d1d", hover_color="#991b1b", state="disabled", command=self._v46_cancel)
    self._v46_cancel_btn.grid(row=0, column=0, padx=(0, 6))
    self._v46_copy_error_btn = ctk.CTkButton(controls, text=_v469_ps(self, "copy_error"), width=100, height=28, fg_color=p["surface2"], hover_color=p["surface"], text_color=p["muted"], state="disabled", command=self._v46_copy_error)
    self._v46_copy_error_btn.grid(row=0, column=1, padx=(0, 6))
    self._v46_state_var = ctk.StringVar(value=_v469_state_label(self, "IDLE"))
    ctk.CTkLabel(controls, textvariable=self._v46_state_var, text_color=p["muted"], anchor="e", font=ctk.CTkFont("Segoe UI", 10, "bold")).grid(row=0, column=2, sticky="e")

    self._v46_install_url_menu(self._v46_source_label, lambda: self._v46_source_full, "source")
    self._v46_install_url_menu(self._v46_resolved_label, lambda: self._v46_resolved_full, "resolved")

    # Disable the ambiguous legacy mini graph; v46 panel owns transfer telemetry.
    try:
        if hasattr(self, "_speed_canvas"):
            self._speed_canvas.grid_forget()
        if hasattr(self, "_speed_label"):
            self._speed_label.grid_forget()
    except Exception as exc:
        logger.debug(f"Legacy speed widgets could not be hidden: {exc}")

def _v46_install_url_menu(self, label: Any, getter: Any, kind: str) -> None:
    import tkinter as tk
    menu = tk.Menu(self.root, tearoff=0)

    def copy_url() -> None:
        value = getter() or ""
        if value:
            self.root.clipboard_clear(); self.root.clipboard_append(value)

    def open_url() -> None:
        value = getter() or ""
        if value:
            import webbrowser
            webbrowser.open(value)

    menu.add_command(label=f"Copy {kind} URL", command=copy_url)
    menu.add_command(label=f"Open {kind} URL", command=open_url)
    label.bind("<Button-1>", lambda _e: copy_url())
    label.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

    tooltip = {"window": None}
    def show_tip(_event=None) -> None:
        value = getter() or ""
        if not value or tooltip["window"] is not None:
            return
        top = tk.Toplevel(self.root); tooltip["window"] = top
        top.wm_overrideredirect(True)
        top.attributes("-topmost", True)
        x = label.winfo_rootx(); y = label.winfo_rooty() + label.winfo_height() + 4
        top.geometry(f"+{x}+{y}")
        tk.Label(top, text=value, justify="left", wraplength=680, bg="#111827", fg="#f8fafc", relief="solid", borderwidth=1, padx=7, pady=5).pack()
    def hide_tip(_event=None) -> None:
        top = tooltip.get("window")
        if top is not None:
            try: top.destroy()
            except Exception as exc: logger.debug(f"Tooltip destroy failed: {exc}")
            tooltip["window"] = None
    label.bind("<Enter>", show_tip, add="+")
    label.bind("<Leave>", hide_tip, add="+")

def _v46_enqueue_progress(self, event: Dict[str, Any]) -> None:
    important = event.get("type") in {"job_completed", "job_failed", "job_cancelled", "cancelling", "stage_changed"}
    try:
        self._v46_progress_queue.put_nowait(event)
        return
    except log_queue_module.Full:
        if not important:
            return
    # Preserve important events by evicting one stale progress event.
    try:
        self._v46_progress_queue.get_nowait()
    except log_queue_module.Empty as _ignored_exc:
        _ = _ignored_exc  # expected non-blocking queue control flow
    try:
        self._v46_progress_queue.put_nowait(event)
    except log_queue_module.Full:
        logger.warning("Progress event queue saturated; an important event was dropped")

def _v46_set_event_sink(self) -> None:
    set_progress_event_sink(self._v46_enqueue_progress, self._cancel_event)

def _v46_start(self) -> None:
    from tkinter import messagebox
    if self._is_running:
        return
    if not self._queue_data:
        messagebox.showwarning(self._tr("queue_empty_title"), self._tr("queue_empty_body"))
        return

    def safe_int(raw: Any, default: int) -> int:
        try: return int(str(raw).strip() or default)
        except (ValueError, TypeError): return int(default)
    def safe_float(raw: Any, default: float) -> float:
        try: return float(str(raw).strip() or default)
        except (ValueError, TypeError): return float(default)

    wt = max(1, safe_int(self._wait_var.get(), DEFAULT_WAIT_TIME))
    threads = max(1, min(32, safe_int(self._threads_var.get(), DEFAULT_MAX_WORKERS)))
    bw = max(0.0, safe_float(self._bw_var.get(), 0.0))
    outdir = self._outdir_var.get().strip()
    if outdir:
        try:
            os.makedirs(outdir, exist_ok=True)
            probe = os.path.join(outdir, f".cyoa_write_test_{os.getpid()}")
            atomic_write_text(probe, "ok")
            os.remove(probe)
        except Exception as exc:
            messagebox.showerror("Output folder", f"Folder output tidak bisa ditulis:\n{outdir}\n\n{exc}")
            return
    else:
        outdir = os.getcwd()

    run_items = [dict(item) for item in self._queue_data]
    # Snapshot the output identity together with the URL. Queue rows remain
    # editable/reorderable while a run is active; the worker must never derive
    # a later folder name from mutable UI state.
    for item in run_items:
        requested_name = str(item.get("filename") or "").strip()
        item["_run_file_name"] = requested_name or _build_output_name(str(item.get("url") or ""))
    self._active_run_queue_ids = {
        str(it.get("_queue_id") or "")
        for it in run_items
        if it.get("_queue_id")
    }
    # Compatibility field for older completion paths. The active v46 done
    # handler removes by row identity, not URL, so duplicate URLs are safe.
    self._active_run_urls = {str(it.get("url", "")) for it in run_items if it.get("url")}
    self._active_run_success_ids = set()
    self._cancel_event.clear()
    self._paused.set()
    self._run_started_wall = time.time()
    self._v46_telemetry.reset(len(run_items))
    self._v46_set_event_sink()
    self._v46_enqueue_progress({"type": "queue_started", "total_jobs": len(run_items), "time": time.monotonic()})
    self._is_running = True
    self._dl_btn.configure(state="disabled")
    self._pause_btn.configure(state="normal", text="⏸ Pause")
    self._v46_cancel_btn.configure(state="normal")
    self._v46_copy_error_btn.configure(state="disabled")
    self._status_var.set("Preparing download…")
    self._worker_thread = threading.Thread(
        target=self._worker,
        args=(run_items, self._mode_var, wt, threads, outdir,
              self._fonts_var.get(), self._analyse_var.get(),
              _normalize_cloudflare_mode(self._cf_mode_var.get()),
              self._http2_var.get(), self._ytdlp_var.get(), bw,
              self._cyoa_mgr_var.get()),
        name="cyoa-download-worker",
        daemon=True,
    )
    self._worker_thread.start()

def _v46_worker(self, items, default_mode, wt, threads, outdir, dl_fonts, show_analysis, cloudflare_mode, http2_enabled, ytdlp_enabled, bw_limit, cyoa_mgr) -> None:
    global wait_time, use_cloudscraper, _shared_session, _shared_session_cf, _ytdlp_enabled, _bandwidth_limit_kbps
    self._v46_set_event_sink()
    module = sys.modules.get(__name__)
    if module is not None:
        module._ytdlp_gui_progress_cb = self._on_ytdlp_progress
        module._gui_speed_cb = self._record_speed_bytes
    try:
        from cyoa_downloader_app.runtime import state as _runtime_state
        from cyoa_downloader_app.runtime.compat import mirror_to_legacy
        _runtime_state._ytdlp_gui_progress_cb = self._on_ytdlp_progress
        _runtime_state._gui_speed_cb = self._record_speed_bytes
        _runtime_state._ytdlp_enabled = ytdlp_enabled
        _runtime_state._bandwidth_limit_kbps = bw_limit
        _runtime_state.wait_time = wt
        mirror_to_legacy("_ytdlp_gui_progress_cb", _runtime_state._ytdlp_gui_progress_cb)
        mirror_to_legacy("_gui_speed_cb", _runtime_state._gui_speed_cb)
        mirror_to_legacy("_ytdlp_enabled", _runtime_state._ytdlp_enabled)
        mirror_to_legacy("_bandwidth_limit_kbps", _runtime_state._bandwidth_limit_kbps)
        mirror_to_legacy("wait_time", _runtime_state.wait_time)
    except Exception as _state_sync_exc:
        logger.debug("Ignored runtime-state sync exception in v46 worker: %s", _state_sync_exc)
    _ytdlp_enabled = ytdlp_enabled
    _bandwidth_limit_kbps = bw_limit
    wait_time = wt
    _set_cloudflare_config(
        cloudflare_mode,
        flaresolverr_url=_load_settings().get("flaresolverr_url", _FLARESOLVERR_URL),
        session_policy=_load_settings().get("flaresolverr_session_policy", _FLARESOLVERR_SESSION_POLICY),
        timeout=int(_load_settings().get("flaresolverr_timeout", _FLARESOLVERR_TIMEOUT) or _FLARESOLVERR_TIMEOUT),
        wait_after=int(_load_settings().get("flaresolverr_wait_after", _FLARESOLVERR_WAIT_AFTER) or _FLARESOLVERR_WAIT_AFTER),
        proxy_mode=_load_settings().get("flaresolverr_proxy_mode", _FLARESOLVERR_PROXY_MODE),
        persist=True,
    )
    _set_http2_enabled(bool(http2_enabled))
    setup_file_logging(outdir)
    state = load_resume_state(outdir)
    completed = set(state["completed"])
    prev_failed = set(f["url"] if isinstance(f, dict) else f for f in state["failed"])
    completed_urls: List[str] = list(completed)
    failed_items: List[Dict[str, str]] = []
    self._last_results = []
    cancelled = False
    skipped_count = 0
    # Resume state historically keyed only by URL. That is fine for a single
    # queue row, but it incorrectly skipped the second occurrence when a user
    # intentionally queued the same CYOA twice. Duplicate rows are therefore
    # treated as explicit jobs for this run; each still gets its unique output
    # name/folder from the queue snapshot.
    url_counts = Counter(str(item.get("url") or "") for item in items if item.get("url"))
    duplicate_urls = {url for url, count in url_counts.items() if count > 1}

    # Surface prior-session state on the queue dots before the
    # run starts, matching the legacy GUI worker. `prev_failed` was previously
    # computed but never used here, so URLs that failed last session showed no
    # initial status. This does NOT change skip/retry logic: a previously-failed
    # URL is still retried (it is not in `completed`); only the initial dot color
    # is restored.
    for _idx0, _item0 in enumerate(items):
        _u0 = str(_item0.get("url") or "")
        if _u0 in completed and _u0 not in duplicate_urls:
            self._set_dot(_idx0, "done")
        elif _u0 in prev_failed:
            self._set_dot(_idx0, "error")

    try:
        auto_items = [
            it for it in items
            if it.get("mode", default_mode) == "auto"
            and (it.get("url") not in completed or it.get("url") in duplicate_urls)
        ]
        if auto_items:
            self._set_status(f"Auto-detecting mode for {len(auto_items)} URL(s)…")
            self._v46_enqueue_progress({"type": "stage_changed", "state": DownloadState.RESOLVING.value, "time": time.monotonic()})
            def progress(done: int, total: int) -> None:
                _raise_if_cancelled()
                self._set_status(f"Auto-detecting… {done}/{total}")
            auto_detect_modes_batch(auto_items, max_workers=min(4, threads), progress_cb=progress)

        for idx, item in enumerate(items, 1):
            _raise_if_cancelled()
            url = str(item.get("url") or "")
            mode = str(item.get("mode") or default_mode or "auto")
            if url in completed and url not in duplicate_urls:
                skipped_count += 1
                self._last_results.append({"url": url, "mode": mode, "status": "SKIP", "filename": item.get("filename", ""), "error": "Already completed"})
                self._set_dot(idx - 1, "skip")
                if item.get("_queue_id"):
                    self._active_run_success_ids.add(str(item["_queue_id"]))
                self._v46_enqueue_progress({"type": "job_started", "job_index": idx, "total_jobs": len(items), "mode": mode, "source_url": url, "time": time.monotonic()})
                self._v46_enqueue_progress({"type": "job_completed", "failed_assets": 0, "time": time.monotonic()})
                continue
            while not self._paused.is_set():
                if self._cancel_event.wait(0.1):
                    raise DownloadCancelledError("Cancelled while paused")
            if mode == "auto":
                self._set_status(f"Job {idx} of {len(items)} — auto-detecting")
                mode = auto_detect_mode(url)
                item["mode"] = mode
            self._v46_enqueue_progress({"type": "job_started", "job_index": idx, "total_jobs": len(items), "mode": mode, "source_url": url, "time": time.monotonic()})
            self._set_dot(idx - 1, "running")
            self._set_status(f"Job {idx} of {len(items)} — {mode} — {truncate_display_url(url, 68)}")
            try:
                is_pure = mode in {"pure_website_zip", "pure_website_folder"}
                is_cyoap = mode in {"cyoap_vue_zip", "cyoap_vue_folder"}
                run_download(
                    url=url,
                    file_name=item.get("_run_file_name", item.get("filename", "")),
                    zip_output=(mode == "zip"),
                    both_output=(mode == "both"),
                    website_output=(mode in {"website", "website_zip", "website_folder", "cyoap_vue_zip", "cyoap_vue_folder"}),
                    website_zip_output=(mode not in {"website_folder", "cyoap_vue_folder", "pure_website_folder"}),
                    pure_website=is_pure,
                    download_fonts=dl_fonts,
                    show_font_analysis=show_analysis,
                    output_dir=outdir,
                    max_workers=threads,
                    engine_mode="cyoap_vue" if is_cyoap else "standard",
                    cyoa_mgr_enabled=cyoa_mgr,
                    ai_api_key=_resolve_ai_api_key(session_key=self._ai_api_key, storage=getattr(self, "_ai_key_storage", "session"), provider=getattr(self, "_ai_provider", "anthropic")) if self._ai_enabled and _normalize_ai_mode(getattr(self, "_ai_mode", "auto_fallback")) != "off" else "",
                    ai_provider=getattr(self, "_ai_provider", "anthropic"),
                    ai_mode=getattr(self, "_ai_mode", "auto_fallback"),
                )
                _raise_if_cancelled()
                completed_urls.append(url)
                if item.get("_queue_id"):
                    self._active_run_success_ids.add(str(item["_queue_id"]))
                self._last_results.append({"url": url, "mode": mode, "status": "OK", "filename": item.get("filename", ""), "error": ""})
                self._set_dot(idx - 1, "done")
                _record_history(url, item.get("filename", ""), mode, success=True)
                save_resume_state(outdir, completed_urls, [f["url"] for f in failed_items])
                self._v46_enqueue_progress({"type": "job_completed", "failed_assets": 0, "time": time.monotonic()})
            except DownloadCancelledError:
                cancelled = True
                self._last_results.append({"url": url, "mode": mode, "status": "CANCELLED", "filename": item.get("filename", ""), "error": "Cancelled by user"})
                self._set_dot(idx - 1, "skip")
                self._v46_enqueue_progress({"type": "job_cancelled", "time": time.monotonic()})
                break
            except Exception as exc:
                logger.error(f"Failed [{url}]: {exc}")
                failed_items.append({"url": url, "error": str(exc)})
                self._last_results.append({"url": url, "mode": mode, "status": "FAIL", "filename": item.get("filename", ""), "error": str(exc)})
                self._set_dot(idx - 1, "error")
                _record_history(url, item.get("filename", ""), mode, success=False)
                save_resume_state(outdir, completed_urls, [f["url"] for f in failed_items])
                self._v46_enqueue_progress({"type": "job_failed", "error": str(exc), "time": time.monotonic()})

        write_failed_url_log(failed_items, outdir)
        succeeded = sum(1 for r in self._last_results if r.get("status") in {"OK", "SKIP"})
        if cancelled or self._cancel_event.is_set():
            removed = _cleanup_recent_part_files(outdir, self._run_started_wall)
            logger.info(f"[Cancel] Cleaned {removed} partial file(s)")
            self._set_status(f"Cancelled — {succeeded}/{len(items)} completed")
        elif failed_items:
            self._set_status(f"Completed with warnings — {succeeded}/{len(items)} succeeded, {len(failed_items)} failed")
        else:
            self._set_status(f"Completed — {succeeded}/{len(items)} succeeded")
            clear_resume_state(outdir)
    except DownloadCancelledError:
        cancelled = True
        removed = _cleanup_recent_part_files(outdir, self._run_started_wall)
        logger.info(f"[Cancel] Cleaned {removed} partial file(s)")
        self._v46_enqueue_progress({"type": "job_cancelled", "time": time.monotonic()})
        self._set_status("Cancelled")
    except Exception as exc:
        logger.exception("Unhandled worker failure")
        self._v46_enqueue_progress({"type": "job_failed", "error": str(exc), "time": time.monotonic()})
        self._set_status(f"Failed — {exc}")
    finally:
        if len(items) > 1 and self._last_results:
            self.root.after(0, self._show_results)
        self.root.after(0, self._done)

def _v46_done(self) -> None:
    self._is_running = False
    self._paused.set()
    try: self._pause_btn.configure(text="⏸ Pause", state="disabled")
    except Exception as exc: logger.debug(f"Pause button reset failed: {exc}")
    try: self._dl_btn.configure(state="normal")
    except Exception as exc: logger.debug(f"Start button reset failed: {exc}")
    try: self._v46_cancel_btn.configure(state="disabled")
    except Exception as exc: logger.debug(f"Cancel button reset failed: {exc}")
    status = self._status_var.get()
    failed = [r for r in self._last_results if r.get("status") == "FAIL"]
    cancelled = any(r.get("status") == "CANCELLED" for r in self._last_results) or self._cancel_event.is_set()
    if failed:
        self._v46_copy_error_btn.configure(state="normal")
    if cancelled:
        self._v46_enqueue_progress({"type": "job_cancelled", "time": time.monotonic()})
    elif failed:
        self._v46_enqueue_progress({"type": "stage_changed", "state": DownloadState.COMPLETED_WITH_WARNINGS.value, "time": time.monotonic()})
    else:
        self._v46_enqueue_progress({"type": "stage_changed", "state": DownloadState.COMPLETED.value, "time": time.monotonic()})
    _send_desktop_notification("CYOA Downloader", status)
    successful_ids = set(getattr(self, "_active_run_success_ids", set()))
    if successful_ids:
        removed = self._remove_queue_ids_from_queue(successful_ids)
        logger.info("[Queue] Removed %s completed row(s) by queue identity.", removed)
    self._active_run_urls = set()
    self._active_run_queue_ids = set()
    self._active_run_success_ids = set()
    if sys.modules.get(__name__) is not None:
        sys.modules[__name__]._gui_speed_cb = None
        sys.modules[__name__]._ytdlp_gui_progress_cb = None
    clear_progress_event_sink()
    if self._v46_close_pending:
        self.root.after(50, self._v46_finish_close)

def _v46_cancel(self) -> None:
    if not self._is_running or self._cancel_event.is_set():
        return
    self._cancel_event.set()
    self._paused.set()
    self._v46_cancel_btn.configure(state="disabled")
    self._status_var.set("Cancelling…")
    self._v46_enqueue_progress({"type": "cancelling", "time": time.monotonic()})
    logger.warning("[Cancel] Cancellation requested; active network call may finish before shutdown")

def _v46_on_close(self) -> None:
    from tkinter import messagebox
    if self._is_running:
        if not messagebox.askyesno("Download in progress", "A download is still running. Cancel it and close the application?"):
            return
        self._v46_close_pending = True
        self._v46_cancel()
        self.root.after(100, self._v46_finish_close)
        return
    self._v46_finish_close()

def _v46_finish_close(self) -> None:
    thread = getattr(self, "_worker_thread", None)
    if thread is not None and thread.is_alive():
        self.root.after(150, self._v46_finish_close)
        return
    try:
        if self._v46_progress_after_id:
            self.root.after_cancel(self._v46_progress_after_id)
    except Exception as exc:
        logger.debug(f"Progress callback cancel failed: {exc}")
    try:
        handler = getattr(self, "_v46_progress_handler", None)
        if handler is not None:
            logger.removeHandler(handler)
    except Exception as exc:
        logger.debug(f"Telemetry handler removal failed: {exc}")
    try:
        self._stop_server()
    except Exception as exc:
        logger.debug(f"Server stop during close failed: {exc}")
    self.root.destroy()

def _v46_copy_error(self) -> None:
    errors = [f"{r.get('url','')}\n{r.get('error','')}" for r in self._last_results if r.get("status") == "FAIL"]
    text = "\n\n".join(errors) or self._v46_telemetry.last_error
    if text:
        self.root.clipboard_clear(); self.root.clipboard_append(text)

def _v46_record_speed_bytes(self, n_bytes: int) -> None:
    self._v46_enqueue_progress({"type": "speed_bytes", "bytes": max(0, int(n_bytes or 0)), "time": time.monotonic()})

def _v46_on_ytdlp_progress(self, vid_id: str, idx: int, total: int, pct_str: str, speed_str: str) -> None:
    self._v46_enqueue_progress({"type": "file_started", "name": f"YouTube audio {idx}/{total}: {vid_id}", "time": time.monotonic()})
    self._set_status(f"YouTube audio {idx}/{total} — {pct_str} — {speed_str}")

def _v46_start_speed_graph(self) -> None:
    # The v46 graph is updated by _v46_poll_progress; no duplicate timer.
    return None

def _v46_stop_speed_graph(self) -> None:
    if sys.modules.get(__name__) is not None:
        sys.modules[__name__]._gui_speed_cb = None

def _v46_poll_progress(self) -> None:
    try:
        if not self.root.winfo_exists():
            return
    except Exception:
        return
    drained = 0
    # Limit work per Tk tick. A large asset batch can emit thousands of
    # progress events; draining/rendering all of them in one callback freezes
    # the window on low-spec machines and delays the Cancel button.
    while drained < 100:
        try:
            event = self._v46_progress_queue.get_nowait()
        except log_queue_module.Empty:
            break
        # Repair absolute asset count emitted by parsed legacy logs.
        absolute = event.pop("absolute_finished", None)
        self._v46_telemetry.apply(event)
        if absolute is not None:
            self._v46_telemetry.assets_finished = max(self._v46_telemetry.assets_finished, int(absolute))
            self._v46_telemetry.assets_success = max(self._v46_telemetry.assets_success, self._v46_telemetry.assets_finished - self._v46_telemetry.assets_failed)
            self._v46_telemetry.job_progress = calculate_stage_progress(
                self._v46_telemetry.state,
                self._v46_telemetry.assets_finished,
                self._v46_telemetry.assets_total,
                self._v46_telemetry.job_progress,
            )
        drained += 1
    snapshot = self._v46_telemetry.snapshot()
    try:
        self._v46_render_progress(snapshot)
    except Exception as exc:
        logger.debug(f"Progress render failed: {exc}")
    next_delay = 75 if drained >= 100 else 150
    self._v46_progress_after_id = self.root.after(next_delay, self._v46_poll_progress)

def _v46_render_progress(self, s: Dict[str, Any]) -> None:
    # v46.9: localize displayed text only. state_disp is for the UI; every
    # `s["state"] in {...}` comparison below still uses the raw enum value.
    state_disp = _v469_state_label(self, s["state"])
    _U = lambda k: _v469_ps(self, k)  # noqa: E731 - short local alias for readability
    total = s["total_jobs"]
    current = min(total, s["current_job"]) if total else 0
    self._v46_overall_var.set(
        f"{_U('job')} {current} {_U('of')} {total} — {_U('overall')}: {s['queue_ratio']*100:.1f}%\n"
        f"{_U('completed')}: {s['completed_jobs']} | {_U('failed')}: {s['failed_jobs']} | {_U('remaining')}: {s['remaining_jobs']}"
    )
    self._v46_overall_pb.set(s["queue_ratio"])
    summary_var = getattr(self, "_v46_progress_summary_var", None)
    if summary_var is not None:
        summary_var.set(
            f"{_U('progress')}: {state_disp} | {_U('job')} {current} {_U('of')} {total} | "
            f"{_U('overall')} {s['queue_ratio']*100:.1f}% | {_U('speed')} {format_speed(s['speed'])}"
        )
    assets_total = s["assets_total"]
    assets_text = f"{s['assets_finished']} / {assets_total}" if assets_total is not None else f"{s['assets_finished']} / {_U('unknown')}"
    mode = str(s["mode"] or "—").replace("_", " ").title()
    self._v46_job_var.set(
        f"{_U('mode')}: {mode} | {_U('stage')}: {state_disp}\n"
        f"{_U('assets')}: {assets_text} | {_U('success')}: {s['assets_success']} | {_U('failed')}: {s['assets_failed']} | {_U('skipped')}: {s['assets_skipped']}"
    )
    self._v46_source_full = s["source_url"]
    self._v46_resolved_full = s["resolved_url"]
    self._v46_source_var.set(_U("source") + ": " + (truncate_display_url(s["source_url"], 34) if s["source_url"] else "—"))
    self._v46_resolved_var.set(_U("resolved") + ": " + (truncate_display_url(s["resolved_url"], 34) if s["resolved_url"] else "—"))
    determinate = assets_total is not None and assets_total > 0 and s["state"] in {DownloadState.DOWNLOADING.value, DownloadState.RETRYING.value, DownloadState.REWRITING.value, DownloadState.VERIFYING.value, DownloadState.PACKAGING.value, DownloadState.COMPLETED.value, DownloadState.COMPLETED_WITH_WARNINGS.value}
    if determinate:
        self._v46_job_pb.stop(); self._v46_job_pb.configure(mode="determinate"); self._v46_job_pb.set(s["job_progress"])
    else:
        self._v46_job_pb.configure(mode="indeterminate")
        if s["state"] not in {DownloadState.IDLE.value, DownloadState.COMPLETED.value, DownloadState.COMPLETED_WITH_WARNINGS.value, DownloadState.FAILED.value, DownloadState.CANCELLED.value}:
            self._v46_job_pb.start()
        else:
            self._v46_job_pb.stop(); self._v46_job_pb.set(s["job_progress"])
    file_total = s["file_total"]
    if file_total and file_total > 0:
        file_ratio = min(1.0, float(s["file_downloaded"]) / float(file_total))
        file_progress = f"{format_bytes(s['file_downloaded'])} / {format_bytes(file_total)} — {file_ratio*100:.1f}%"
        self._v46_file_pb.stop(); self._v46_file_pb.configure(mode="determinate"); self._v46_file_pb.set(file_ratio)
    else:
        file_progress = f"{format_bytes(s['file_downloaded'])} {_U('downloaded')} — {_U('total_unknown')}"
        self._v46_file_pb.configure(mode="indeterminate")
        if s["state"] in {DownloadState.DOWNLOADING.value, DownloadState.RETRYING.value}:
            self._v46_file_pb.start()
        else:
            self._v46_file_pb.stop(); self._v46_file_pb.set(0)
    if s["total_known"] is not None:
        total_data = f"{format_bytes(s['total_downloaded'])} / {format_bytes(s['total_known'])}"
    else:
        total_data = f"{format_bytes(s['total_downloaded'])} {_U('downloaded')} | {_U('total_size_unknown')}"
    if s["state"] in {DownloadState.COMPLETED.value, DownloadState.COMPLETED_WITH_WARNINGS.value}:
        eta_text = _U("completed_word")
    elif s["state"] == DownloadState.CANCELLED.value:
        eta_text = _U("cancelled_word")
    else:
        eta_text = format_duration(s["eta"]) if s["eta"] is not None else (_U("calculating") if s["speed"] > 0 else _U("unknown"))
    current_file = truncate_display_url(s["current_file"], 42) if s["current_file"] else "—"
    self._v46_file_var.set(
        f"{_U('current_file')}: {current_file}\n"
        f"{_U('file_progress')}: {file_progress} | {_U('total_data')}: {total_data}\n"
        f"{_U('speed')}: {format_speed(s['speed'])} | {_U('average')}: {format_speed(s['average_speed'])}\n"
        f"{_U('elapsed')}: {format_duration(s['elapsed'])} | {_U('eta')}: {eta_text}"
    )
    self._v46_state_var.set(state_disp + (f" | {_U('peak')} {format_speed(s['peak_speed'])}" if s["peak_speed"] > 0 else ""))
    if s["last_error"]:
        self._v46_copy_error_btn.configure(state="normal")
    self._v46_draw_speed_graph()

def _v46_draw_speed_graph(self) -> None:
    canvas = getattr(self, "_v46_speed_canvas", None)
    if canvas is None:
        return
    data = list(self._v46_telemetry.speed_history)[-120:]
    width = max(10, int(canvas.winfo_width() or 10))
    height = max(10, int(canvas.winfo_height() or 38))
    canvas.delete("v46graph")
    if not data or max(data) <= 0:
        return
    peak = max(data)
    step = float(width - 4) / max(1, len(data) - 1)
    points: List[float] = []
    for index, value in enumerate(data):
        points.extend([2 + index * step, height - 2 - (float(value) / peak) * (height - 6)])
    if len(points) >= 4:
        canvas.create_line(*points, fill="#60a5fa", width=2, smooth=True, tags="v46graph")
    canvas.create_text(width - 4, 3, text=format_speed(peak), anchor="ne", fill="#94a3b8", font=("Segoe UI", 7), tags="v46graph")



# ---- historical GUI behavior block ----

"""Historical v46.2 CYOA.CAFE/responsive patch bodies moved out of legacy.py."""



def _v462_default_cafe_fetch(url: str, timeout: int = 15) -> Optional[requests.Response]:
    """Fetch resolver probes quietly; expected 404 candidates belong in DEBUG logs."""
    return fetch_response(
        url,
        extra_headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json,*/*"},
        timeout=timeout,
        return_error_response=True,
        quiet=True,
    )

def _v462_resolution_key(url: str) -> str:
    try:
        return CYOACafeResolver.normalize_input(url)
    except Exception:
        return str(url or "").strip()

def _v462_record_resolution_kind(source_url: str, resolved_url: str, kind: str) -> None:
    source_key = _v462_resolution_key(source_url)
    resolved_key = _v462_resolution_key(resolved_url)
    with _CYOA_CAFE_RESOLUTION_KIND_LOCK:
        _CYOA_CAFE_RESOLUTION_KIND[source_key] = kind
        _CYOA_CAFE_RESOLUTION_KIND[resolved_key] = kind

def _v462_get_resolution_kind(url: str) -> str:
    key = _v462_resolution_key(url)
    with _CYOA_CAFE_RESOLUTION_KIND_LOCK:
        return str(_CYOA_CAFE_RESOLUTION_KIND.get(key, ""))

def _v462_pure_cache_get(key: str) -> Optional[str]:
    now = time.monotonic()
    with _CYOA_CAFE_PURE_CACHE_LOCK:
        item = _CYOA_CAFE_PURE_CACHE.get(key)
        if not item:
            return None
        expires, value = item
        if expires <= now:
            _CYOA_CAFE_PURE_CACHE.pop(key, None)
            return None
        return value

def _v462_pure_cache_put(key: str, value: str) -> None:
    with _CYOA_CAFE_PURE_CACHE_LOCK:
        if len(_CYOA_CAFE_PURE_CACHE) >= _CYOA_CAFE_CACHE_MAX:
            oldest = min(_CYOA_CAFE_PURE_CACHE, key=lambda item: _CYOA_CAFE_PURE_CACHE[item][0])
            _CYOA_CAFE_PURE_CACHE.pop(oldest, None)
        _CYOA_CAFE_PURE_CACHE[key] = (time.monotonic() + _CYOA_CAFE_CACHE_TTL, value)

def _v462_invalidate_cafe_cache(url: str) -> None:
    _V461_CAFE_INVALIDATE(url)
    key = _v462_resolution_key(url)
    with _CYOA_CAFE_PURE_CACHE_LOCK:
        _CYOA_CAFE_PURE_CACHE.pop(key, None)
        stale = [source for source, (_expires, value) in _CYOA_CAFE_PURE_CACHE.items()
                 if _v462_resolution_key(value) == key]
        for source in stale:
            _CYOA_CAFE_PURE_CACHE.pop(source, None)
    with _CYOA_CAFE_RESOLUTION_KIND_LOCK:
        _CYOA_CAFE_RESOLUTION_KIND.pop(key, None)

def _v462_authoritative_pure_method(method: str) -> bool:
    normalized = str(method or "").strip().lower()
    return (
        normalized.startswith("pocketbase api")
        or normalized == "html iframe"
        or normalized == "embedded json"
        or normalized.startswith("script field ")
    )

def _v462_validate_pure_website_candidate(
    self: CYOACafeResolver,
    candidate: str,
    *,
    rejection_prefix: str = "pure website",
) -> bool:
    """Validate an authoritative custom HTML application without project files."""
    allowed, reason = self._candidate_allowed(candidate)
    if not allowed:
        self._reject(candidate, f"{rejection_prefix}: {reason}")
        return False
    try:
        canonical = canonicalize_url(candidate)
    except Exception as exc:
        self._reject(candidate, f"{rejection_prefix}: normalization failed: {exc}")
        return False
    parsed = urlparse(canonical)
    host = parsed.netloc.lower()
    if host == "cyoa.cafe" and parsed.path.rstrip("/").startswith("/game/"):
        self._reject(canonical, f"{rejection_prefix}: catalogue metadata page")
        return False
    response = self._fetch(canonical)
    if response is None:
        self._reject(canonical, f"{rejection_prefix}: request failed")
        return False
    status = self._response_status(response)
    if status and not 200 <= status < 400:
        self._reject(canonical, f"{rejection_prefix}: HTTP {status}")
        return False
    content_type = str(getattr(response, "headers", {}).get("Content-Type", "")).lower()
    text = self._response_text(response)
    lower = text.lstrip().lower()
    looks_html = (
        "text/html" in content_type
        or "application/xhtml+xml" in content_type
        or lower.startswith("<!doctype html")
        or lower.startswith("<html")
        or "<body" in lower
    )
    if not looks_html:
        self._reject(canonical, f"{rejection_prefix}: response is not HTML")
        return False
    if len(text.strip()) < 80:
        self._reject(canonical, f"{rejection_prefix}: HTML response is implausibly small")
        return False
    try:
        soup = BeautifulSoup(text, "html.parser")
        title = " ".join((soup.title.get_text(" ", strip=True) if soup.title else "").lower().split())
        heading = soup.find(["h1", "h2"])
        heading_text = " ".join((heading.get_text(" ", strip=True) if heading else "").lower().split())
    except Exception as exc:
        self._reject(canonical, f"{rejection_prefix}: HTML parse failed: {exc}")
        return False
    soft_error_text = f"{title} {heading_text}".strip()
    if any(token in soft_error_text for token in ("404 not found", "page not found", "site not found", "error 404")):
        self._reject(canonical, f"{rejection_prefix}: soft 404/error page")
        return False
    app_markers = (
        "<script", "<link", "<style", "<img", "<canvas", "<button",
        "<form", "<input", "addeventlistener", "localstorage", "sessionstorage",
    )
    if not any(marker in lower for marker in app_markers):
        self._reject(canonical, f"{rejection_prefix}: no interactive/resource HTML markers")
        return False
    return True

def _v462_resolve_cafe(self: CYOACafeResolver, url: str) -> str:
    normalized = self.normalize_input(url)
    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    if host != "cyoa.cafe" and not host.endswith(".cyoa.cafe"):
        return normalized

    cached_pure = _v462_pure_cache_get(normalized)
    if cached_pure:
        if _v462_validate_pure_website_candidate(self, cached_pure, rejection_prefix="cached pure website"):
            _v462_record_resolution_kind(normalized, cached_pure, "pure_website")
            logger.info(f"cyoa.cafe resolved from pure-website TTL cache: {cached_pure}")
            return cached_pure
        _v462_invalidate_cafe_cache(normalized)

    try:
        resolved = _V461_CAFE_RESOLVE(self, normalized)
        _v462_record_resolution_kind(normalized, resolved, "viewer")
        return resolved
    except CYOACafeResolutionError as strict_error:
        # Strict validation intentionally ran first. Reuse its per-resolution
        # response cache, then allow only authoritative metadata/iframe targets.
        candidates: List[Tuple[str, str]] = []
        candidates.extend(self._api_candidates(normalized))
        candidates.extend(self._html_candidates(normalized))
        seen: Set[str] = set()
        for raw, method in candidates[: self.max_hops * 8]:
            if not _v462_authoritative_pure_method(method):
                continue
            try:
                candidate = canonicalize_url(urljoin(normalized, raw))
            except Exception as exc:
                self._reject(str(raw), f"pure website normalization failed: {exc}")
                continue
            if candidate in seen or candidate == normalized:
                continue
            seen.add(candidate)
            if _v462_validate_pure_website_candidate(self, candidate):
                _v462_pure_cache_put(normalized, candidate)
                _v462_record_resolution_kind(normalized, candidate, "pure_website")
                logger.info(
                    f"cyoa.cafe resolved via {method} as pure website "
                    f"(no standard project signature): {candidate}"
                )
                return candidate
        raise strict_error

def _v462_auto_detect_output_variant(kind: str, output_pref: Optional[str] = None) -> str:
    normalized_kind = str(kind or "").strip().lower().replace("-", "_")
    if normalized_kind == "pure_website":
        pref = _normalize_auto_detect_output(
            output_pref if output_pref is not None else _load_settings().get("auto_detect_output", "folder")
        )
        return "pure_website_zip" if pref == "zip" else "pure_website_folder"
    return _V461_AUTO_DETECT_OUTPUT_VARIANT(kind, output_pref)

def _v462_auto_detect_mode(url: str, timeout: int = 6) -> str:
    detected = _V461_AUTO_DETECT_MODE(url, timeout=timeout)
    source_key = _v462_resolution_key(url)
    if _v462_get_resolution_kind(source_key) == "pure_website" and detected in {"website_zip", "website_folder"}:
        pure_mode = _v462_auto_detect_output_variant("pure_website")
        logger.info(f"[Auto-detect] → authoritative custom HTML target; using {pure_mode}")
        return pure_mode
    return detected

def _v462_is_cafe_url(url: str) -> bool:
    try:
        host = urlparse(canonicalize_url(url)).netloc.lower()
    except Exception:
        return False
    return host == "cyoa.cafe" or host.endswith(".cyoa.cafe")

def _v462_resolve_pure_download_url(source_url: str) -> str:
    if not _v462_is_cafe_url(source_url):
        return source_url
    resolved = get_iframe_url_from_cyoa_cafe(source_url)
    return resolved or source_url

def _v462_run_download(
    url: str,
    file_name: str = "",
    zip_output: bool = False,
    both_output: bool = False,
    website_output: bool = False,
    website_zip_output: bool = True,
    pure_website: bool = False,
    download_fonts: bool = False,
    show_font_analysis: bool = True,
    output_dir: str = "",
    max_workers: int = DEFAULT_MAX_WORKERS,
    engine_mode: str = "standard",
    cyoa_mgr_enabled: bool = False,
    ai_api_key: str = "",
    ai_provider: str = "",
    ai_mode: str = "auto_fallback",
    analysis_only: bool = False,
    archive_strategy: str = "",
    archive_max_pages: int = 0,
    archive_max_depth: int = -1,
    archive_capture_interactions: bool = False,
) -> None:
    source_url = url
    preserved_name = file_name
    if pure_website and _v462_is_cafe_url(source_url):
        from ..project.cyoa_cafe import classify_cyoa_cafe_record, fetch_cyoa_cafe_record
        _record_kind = classify_cyoa_cafe_record(fetch_cyoa_cafe_record(source_url))
        resolved = source_url if _record_kind == "static_pages" else _v462_resolve_pure_download_url(source_url)
        if resolved != source_url:
            if not preserved_name:
                preserved_name = _build_output_name(source_url)
            logger.info(f"Pure website source resolved: {source_url} → {resolved}")
            url = resolved
    kwargs = dict(
        url=url,
        file_name=preserved_name,
        zip_output=zip_output,
        both_output=both_output,
        website_output=website_output,
        website_zip_output=website_zip_output,
        pure_website=pure_website,
        download_fonts=download_fonts,
        show_font_analysis=show_font_analysis,
        output_dir=output_dir,
        max_workers=max_workers,
        engine_mode=engine_mode,
        cyoa_mgr_enabled=cyoa_mgr_enabled,
        ai_api_key=ai_api_key,
        ai_provider=ai_provider,
        ai_mode=ai_mode,
        analysis_only=analysis_only,
        archive_strategy=archive_strategy,
        archive_max_pages=archive_max_pages,
        archive_max_depth=archive_max_depth,
        archive_capture_interactions=archive_capture_interactions,
    )
    try:
        return _V461_RUN_DOWNLOAD(**kwargs)
    except RuntimeError as exc:
        message = str(exc)
        fallback_allowed = (
            website_output
            and not pure_website
            and _v462_is_cafe_url(source_url)
            and "Could not resolve project data" in message
        )
        if not fallback_allowed:
            raise
        resolved = _v462_resolve_pure_download_url(source_url)
        if not resolved or resolved == source_url:
            raise
        fallback_name = preserved_name or _build_output_name(source_url)
        logger.warning(
            "Standard project payload was not found; retrying the authoritative "
            f"CYOA.CAFE target as Pure Website: {resolved}"
        )
        kwargs.update(
            url=resolved,
            file_name=fallback_name,
            website_output=False,
            pure_website=True,
            engine_mode="standard",
        )
        return _V461_RUN_DOWNLOAD(**kwargs)

def _v462_default_progress_expanded(_screen_height: int) -> bool:
    return False

def _v462_compact_queue_height(window_height: int, screen_height: int) -> int:
    """Return a bounded queue viewport height that leaves room for the log."""
    available = max(1, int(window_height or 1))
    screen = max(1, int(screen_height or 1))
    effective = available if available > 100 else screen
    if effective <= 800:
        return 60
    if effective <= 920:
        return 90
    return 140

def _v462_find_main_panels(self: CYOADownloaderGUI) -> Tuple[Optional[Any], Optional[Any]]:
    input_panel = getattr(self, "_v462_input_panel", None)
    queue_panel = getattr(self, "_v462_queue_panel", None)
    if input_panel is not None and queue_panel is not None:
        return input_panel, queue_panel
    try:
        main = self._v46_progress_host.master.master
        for child in main.winfo_children():
            info = child.grid_info()
            row = int(info.get("row", -1))
            if row == 0:
                input_panel = child
            elif row == 1:
                queue_panel = child
        self._v462_input_panel = input_panel
        self._v462_queue_panel = queue_panel
    except Exception as exc:
        logger.debug(f"Responsive panel discovery failed: {exc}")
    return input_panel, queue_panel

def _v462_configure_queue_viewport(self: CYOADownloaderGUI) -> None:
    try:
        height = _v462_compact_queue_height(
            int(self.root.winfo_height() or 1),
            int(self.root.winfo_screenheight() or 1),
        )
        scroll = getattr(self, "_qlist", None)
        parent_frame = getattr(scroll, "_parent_frame", None)
        parent_canvas = getattr(scroll, "_parent_canvas", None)
        if parent_frame is not None:
            parent_frame.configure(height=height)
            parent_frame.grid_propagate(False)
        if parent_canvas is not None:
            parent_canvas.configure(height=height)
    except Exception as exc:
        logger.debug(f"Could not compact queue viewport: {exc}")

def _v462_apply_progress_visibility_gui(self: CYOADownloaderGUI, expanded: Optional[bool] = None) -> None:
    _V461_APPLY_PROGRESS_VISIBILITY_FINAL(self, expanded)
    input_panel, queue_panel = _v462_find_main_panels(self)
    try:
        window_height = int(self.root.winfo_height() or 1)
        screen_height = int(self.root.winfo_screenheight() or 1)
    except (TypeError, ValueError):
        window_height, screen_height = 1, 1
    effective_height = window_height if window_height > 100 else screen_height
    compact = effective_height < 900
    details_visible = bool(getattr(self, "_v46_progress_expanded", False))
    # On short windows, detailed progress replaces the editable input/queue
    # area. The toolbar, telemetry, Cancel, and log remain visible. Collapsing
    # progress restores the normal input workflow exactly where it was.
    for panel in (input_panel, queue_panel):
        if panel is None:
            continue
        try:
            if details_visible and compact:
                panel.grid_remove()
            else:
                panel.grid()
        except Exception as exc:
            logger.debug(f"Responsive panel visibility update failed: {exc}")

def _v462_refresh_responsive_layout(self: CYOADownloaderGUI) -> None:
    _v462_configure_queue_viewport(self)
    self._v46_apply_progress_visibility(bool(getattr(self, "_v46_progress_expanded", False)))

def _v462_gui_setup_ui_final(self: CYOADownloaderGUI) -> None:
    _V461_GUI_SETUP_UI_FINAL(self)
    _v462_configure_queue_viewport(self)
    _v462_find_main_panels(self)
    self._v46_apply_progress_visibility(bool(getattr(self, "_v46_progress_expanded", False)))
    self._v462_resize_after_id = None

    def schedule_refresh(event: Any) -> None:
        if event.widget is not self.root:
            return
        prior = getattr(self, "_v462_resize_after_id", None)
        if prior is not None:
            try:
                self.root.after_cancel(prior)
            except Exception as exc:
                logger.debug(f"Responsive resize debounce cancel failed: {exc}")
        self._v462_resize_after_id = self.root.after(140, self._v462_refresh_responsive_layout)

    self.root.bind("<Configure>", schedule_refresh, add="+")



# ---- historical GUI behavior block ----

"""Historical v46.3 progress workspace patch bodies moved out of legacy.py."""

# Phase 61: progress localization constants now live with their consumers.
_V469_STATE_LABELS_ID: Dict[str, str] = {
    "IDLE": "SIAP",
    "RESOLVING": "MERESOLUSI",
    "FETCHING_ENTRY": "MENGAMBIL HALAMAN",
    "DISCOVERING_ASSETS": "MEMINDAI ASET",
    "DOWNLOADING": "MENGUNDUH",
    "RETRYING": "MENGULANG",
    "REWRITING": "MENULIS ULANG",
    "VERIFYING": "MEMVERIFIKASI",
    "PACKAGING": "MENGEMAS",
    "COMPLETED": "SELESAI",
    "COMPLETED_WITH_WARNINGS": "SELESAI DENGAN PERINGATAN",
    "FAILED": "GAGAL",
    "CANCELLING": "MEMBATALKAN",
    "CANCELLED": "DIBATALKAN",
}

# Static label / dynamic keyword strings used by the progress card.
_V469_PROGRESS_STRINGS: Dict[str, Dict[str, str]] = {
    "show_details":   {"id": "Tampilkan Detail",     "en": "Show Details"},
    "hide_details":   {"id": "Sembunyikan Detail",   "en": "Hide Details"},
    "cancel":         {"id": "Batalkan",             "en": "Cancel"},
    "copy_error":     {"id": "Salin Error",          "en": "Copy Error"},
    "progress":       {"id": "Progres",              "en": "Progress"},
    "job":            {"id": "Tugas",                "en": "Job"},
    "of":             {"id": "dari",                 "en": "of"},
    "overall":        {"id": "Keseluruhan",          "en": "Overall"},
    "speed":          {"id": "Kecepatan",            "en": "Speed"},
    "completed":      {"id": "Selesai",              "en": "Completed"},
    "failed":         {"id": "Gagal",                "en": "Failed"},
    "remaining":      {"id": "Sisa",                 "en": "Remaining"},
    "mode":           {"id": "Mode",                 "en": "Mode"},
    "stage":          {"id": "Tahap",                "en": "Stage"},
    "assets":         {"id": "Aset",                 "en": "Assets"},
    "success":        {"id": "Sukses",               "en": "Success"},
    "skipped":        {"id": "Dilewati",             "en": "Skipped"},
    "source":         {"id": "Sumber",               "en": "Source"},
    "resolved":       {"id": "Hasil resolusi",       "en": "Resolved"},
    "current_file":   {"id": "File saat ini",        "en": "Current file"},
    "file_progress":  {"id": "Progres file",         "en": "File progress"},
    "total_data":     {"id": "Total data",           "en": "Total data"},
    "average":        {"id": "Rata-rata",            "en": "Average"},
    "elapsed":        {"id": "Waktu berjalan",       "en": "Elapsed"},
    "eta":            {"id": "Estimasi",             "en": "ETA"},
    "data":           {"id": "Data",                 "en": "Data"},
    "peak":           {"id": "Puncak",               "en": "Peak"},
    "downloaded":     {"id": "terunduh",             "en": "downloaded"},
    "total_unknown":  {"id": "total tidak diketahui","en": "total unknown"},
    "total_size_unknown": {"id": "Total ukuran: Tidak diketahui", "en": "Total size: Unknown"},
    "unknown":        {"id": "Tidak diketahui",      "en": "Unknown"},
    "calculating":    {"id": "Menghitung…",          "en": "Calculating…"},
    "completed_word": {"id": "Selesai",              "en": "Completed"},
    "cancelled_word": {"id": "Dibatalkan",           "en": "Cancelled"},
}


def _v463_arrange_progress_and_log(self: CYOADownloaderGUI) -> None:
    """Keep telemetry and log side-by-side on normal windows, stacked when narrow."""
    host = getattr(self, "_v46_progress_host", None)
    log_frame = getattr(getattr(self, "_log_txt", None), "master", None)
    if host is None or log_frame is None:
        return
    main = log_frame.master
    try:
        width = int(main.winfo_width() or 1)
        if width <= 1:
            width = max(1, int(self.root.winfo_width() or 1) - 380)
    except (TypeError, ValueError):
        width = 1
    # Break on actual workspace width, not full window width (which includes
    # the sidebar). This prevents two 430 px panes overflowing a 900–1100 px
    # application window.
    wide = width >= 880
    expanded = bool(getattr(self, "_v46_progress_expanded", False))

    try:
        # Existing input, queue, and action rows must span the whole workspace.
        for child in main.winfo_children():
            if child in (host, log_frame):
                continue
            info = child.grid_info()
            if info and int(info.get("row", -1)) < 3:
                child.grid_configure(column=0, columnspan=2)

        if wide:
            main.grid_columnconfigure(0, weight=1, minsize=360)
            main.grid_columnconfigure(1, weight=1, minsize=360)
            main.grid_rowconfigure(3, weight=1, minsize=230 if expanded else 155)
            main.grid_rowconfigure(4, weight=0, minsize=0)
            host.grid(row=3, column=0, columnspan=1, sticky="nsew", padx=(0, 4), pady=0)
            log_frame.grid(row=3, column=1, columnspan=1, sticky="nsew", padx=(4, 0), pady=0)
        else:
            main.grid_columnconfigure(0, weight=1, minsize=0)
            main.grid_columnconfigure(1, weight=0, minsize=0)
            main.grid_rowconfigure(3, weight=0, minsize=225 if expanded else 145)
            main.grid_rowconfigure(4, weight=1, minsize=170)
            host.grid(row=3, column=0, columnspan=2, sticky="ew", padx=0, pady=(0, 4))
            log_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=0, pady=(4, 0))
    except Exception as exc:
        logger.debug(f"v46.3 responsive workspace arrangement failed: {exc}")


def _v463_progress_detail_height(expanded: bool, window_height: int, workspace_height: int) -> int:
    """Retained for layout callers; the redesigned detail frame is content-sized."""
    return 0 if not expanded else 148


def _v463_resize_detail_viewport(details, height: int) -> None:
    target = max(0, int(height))
    try:
        if target:
            details.configure(height=target)
    except (AttributeError, TypeError):
        pass


def _v463_set_queue_density(self: CYOADownloaderGUI, expanded: bool) -> None:
    """Keep the queue visible while reclaiming just enough room for telemetry."""
    try:
        if expanded:
            height = 64
        else:
            height = _v462_compact_queue_height(
                int(self.root.winfo_height() or 1),
                int(self.root.winfo_screenheight() or 1),
            )
        scroll = getattr(self, "_qlist", None)
        parent_frame = getattr(scroll, "_parent_frame", None)
        parent_canvas = getattr(scroll, "_parent_canvas", None)
        if parent_frame is not None:
            parent_frame.configure(height=height)
            parent_frame.grid_propagate(False)
        if parent_canvas is not None:
            parent_canvas.configure(height=height)
    except Exception as exc:
        logger.debug(f"Could not update queue density for progress details: {exc}")

def _v463_apply_progress_visibility(self: CYOADownloaderGUI, expanded: Optional[bool] = None) -> None:
    """Toggle details inside the single telemetry card; never hide the whole panel."""
    if expanded is not None:
        self._v46_progress_expanded = bool(expanded)
    details = getattr(self, "_v463_progress_details", None)
    compact = getattr(self, "_v463_progress_compact", None)
    button = getattr(self, "_v46_progress_toggle_btn", None)
    expanded_now = bool(getattr(self, "_v46_progress_expanded", False))
    if details is not None:
        if expanded_now:
            details.grid()
        else:
            details.grid_remove()
    if compact is not None:
        if expanded_now:
            compact.grid_remove()
        else:
            compact.grid()
    if button is not None:
        button.configure(text=_v469_ps(self, "hide_details") if expanded_now else _v469_ps(self, "show_details"))
    input_panel, queue_panel = _v462_find_main_panels(self)
    for panel in (input_panel, queue_panel):
        if panel is None:
            continue
        try:
            # Expanded telemetry must not erase the user's input or queue
            # context. Older responsive layers may have removed these panels,
            # so explicitly restore both before arranging the workspace.
            panel.grid()
        except Exception as exc:
            logger.debug(f"Progress panel restore failed: {exc}")
    _v463_set_queue_density(self, expanded_now)
    if details is not None:
        _v463_resize_detail_viewport(details, _v463_progress_detail_height(expanded_now, 0, 0))
    _v463_arrange_progress_and_log(self)
    try:
        self.root.after_idle(self._v463_arrange_progress_and_log)
    except Exception:
        pass

def _v469_lang(self: "CYOADownloaderGUI") -> str:
    return "en" if getattr(self, "_language", "id") == "en" else "id"

def _v469_ps(self: "CYOADownloaderGUI", key: str) -> str:
    """Return a localized progress-panel string for the given key."""
    entry = _V469_PROGRESS_STRINGS.get(key)
    if not entry:
        return key
    return entry.get(_v469_lang(self), entry.get("en", key))

def _v469_state_label(self: "CYOADownloaderGUI", state_value: str) -> str:
    """Translate a DownloadState value for display only (never mutates state)."""
    if _v469_lang(self) == "en":
        return str(state_value)
    return _V469_STATE_LABELS_ID.get(str(state_value), str(state_value))

def _v463_rebuild_progress_workspace(self: CYOADownloaderGUI) -> None:
    """Replace three separate progress cards with one compact telemetry card."""
    import customtkinter as ctk
    import tkinter as tk

    p = self._p()
    old_host = getattr(self, "_v46_progress_host", None)
    log_frame = getattr(getattr(self, "_log_txt", None), "master", None)
    if log_frame is None:
        return
    main = log_frame.master
    if old_host is not None:
        try:
            old_host.destroy()
        except Exception as exc:
            logger.debug(f"Could not remove previous progress host: {exc}")

    host = ctk.CTkFrame(main, fg_color=p["bg"], corner_radius=0)
    host.grid_columnconfigure(0, weight=1)
    self._v46_progress_host = host

    card = ctk.CTkFrame(
        host,
        fg_color=p["surface"],
        corner_radius=10,
        border_width=1,
        border_color=p["border"],
    )
    card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
    card.grid_columnconfigure(0, weight=1)
    self._v463_progress_card = card

    header = ctk.CTkFrame(card, fg_color="transparent")
    header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
    header.grid_columnconfigure(0, weight=1)
    self._v46_progress_summary_var = ctk.StringVar(value=f"{_v469_ps(self, 'progress')}: {_v469_state_label(self, 'IDLE')} | {_v469_ps(self, 'job')} 0 {_v469_ps(self, 'of')} 0 | {_v469_ps(self, 'speed')} 0 B/s")
    ctk.CTkLabel(
        header,
        textvariable=self._v46_progress_summary_var,
        font=ctk.CTkFont("Segoe UI", 10, "bold"),
        text_color=p["fg"],
        anchor="w",
    ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    self._v46_progress_toggle_btn = ctk.CTkButton(
        header,
        text=_v469_ps(self, "show_details"),
        width=92,
        height=24,
        fg_color=p["surface2"],
        hover_color=p["surface"],
        text_color=p["muted"],
        command=self._v46_toggle_progress_panel,
    )
    self._v46_progress_toggle_btn.grid(row=0, column=1, sticky="e")

    compact = ctk.CTkFrame(card, fg_color="transparent")
    compact.grid(row=1, column=0, sticky="ew", padx=10, pady=(1, 4))
    compact.grid_columnconfigure(0, weight=1)
    self._v46_overall_var = ctk.StringVar(value=f"{_v469_ps(self, 'job')} 0 {_v469_ps(self, 'of')} 0 | {_v469_ps(self, 'completed')} 0 | {_v469_ps(self, 'failed')} 0 | {_v469_ps(self, 'remaining')} 0")
    ctk.CTkLabel(
        compact,
        textvariable=self._v46_overall_var,
        font=ctk.CTkFont("Segoe UI", 9),
        text_color=p["muted"],
        anchor="w",
        justify="left",
    ).grid(row=0, column=0, sticky="ew")
    self._v46_overall_pb = ctk.CTkProgressBar(
        compact, height=7, fg_color=p["surface2"], progress_color="#22c55e"
    )
    self._v46_overall_pb.grid(row=1, column=0, sticky="ew", pady=(4, 2))
    self._v46_overall_pb.set(0)
    self._v463_progress_compact = compact

    # Expanded telemetry is content-sized and split into two balanced columns.
    # The compact summary above is hidden while this frame is visible, avoiding
    # duplicated information and the tall empty scroll viewport from v46.4.
    details = ctk.CTkFrame(
        card,
        fg_color="transparent",
        corner_radius=0,
    )
    details.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 4))
    details.grid_columnconfigure(0, weight=1)
    details.grid_columnconfigure(1, weight=1)
    self._v463_progress_details = details

    job_block = ctk.CTkFrame(details, fg_color=p["surface2"], corner_radius=7)
    job_block.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
    job_block.grid_columnconfigure(0, weight=1)
    self._v46_job_var = ctk.StringVar(value=f"{_v469_ps(self, 'mode')}: — | {_v469_ps(self, 'stage')}: {_v469_state_label(self, 'IDLE')}\n{_v469_ps(self, 'assets')}: — | {_v469_ps(self, 'success')}: 0 | {_v469_ps(self, 'failed')}: 0 | {_v469_ps(self, 'skipped')}: 0")
    ctk.CTkLabel(
        job_block,
        textvariable=self._v46_job_var,
        font=ctk.CTkFont("Segoe UI", 9),
        text_color=p["muted"],
        anchor="w",
        justify="left",
    ).grid(row=0, column=0, sticky="ew", padx=8, pady=(7, 3))

    self._v46_job_pb = ctk.CTkProgressBar(
        job_block, height=6, fg_color=p["panel"], progress_color="#3b82f6", mode="indeterminate"
    )
    self._v46_job_pb.grid(row=1, column=0, sticky="ew", padx=8, pady=(3, 8))
    self._v46_job_pb.start()

    transfer_block = ctk.CTkFrame(details, fg_color=p["surface2"], corner_radius=7)
    transfer_block.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
    transfer_block.grid_columnconfigure(0, weight=1)
    self._v46_file_var = ctk.StringVar(
        value=f"{_v469_ps(self, 'current_file')}: —\n{_v469_ps(self, 'data')}: 0 B | {_v469_ps(self, 'speed')}: 0 B/s\n{_v469_ps(self, 'elapsed')}: 00:00:00 | {_v469_ps(self, 'eta')}: {_v469_ps(self, 'unknown')}"
    )
    ctk.CTkLabel(
        transfer_block,
        textvariable=self._v46_file_var,
        font=ctk.CTkFont("Segoe UI", 9),
        text_color=p["muted"],
        anchor="w",
        justify="left",
        wraplength=275,
    ).grid(row=0, column=0, sticky="ew", padx=8, pady=(7, 3))
    self._v46_file_pb = ctk.CTkProgressBar(
        transfer_block, height=6, fg_color=p["panel"], progress_color="#14b8a6", mode="indeterminate"
    )
    self._v46_file_pb.grid(row=1, column=0, sticky="ew", padx=8, pady=(3, 8))
    self._v46_file_pb.start()

    self._v46_source_var = ctk.StringVar(value=_v469_ps(self, "source") + ": —")
    self._v46_resolved_var = ctk.StringVar(value=_v469_ps(self, "resolved") + ": —")
    self._v46_source_label = ctk.CTkLabel(
        details,
        textvariable=self._v46_source_var,
        font=ctk.CTkFont("Segoe UI", 9),
        text_color="#60a5fa",
        anchor="w",
        cursor="hand2",
    )
    self._v46_source_label.grid(row=1, column=0, sticky="ew", padx=(4, 8), pady=(4, 0))
    self._v46_resolved_label = ctk.CTkLabel(
        details,
        textvariable=self._v46_resolved_var,
        font=ctk.CTkFont("Segoe UI", 9),
        text_color="#a78bfa",
        anchor="w",
        cursor="hand2",
    )
    self._v46_resolved_label.grid(row=1, column=1, sticky="ew", padx=(8, 4), pady=(4, 0))

    self._v46_speed_canvas = tk.Canvas(
        details, height=30, bg=p["surface2"], highlightthickness=0, bd=0
    )
    self._v46_speed_canvas.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
    self._v46_speed_canvas.bind("<Configure>", lambda _e: self._v46_draw_speed_graph())

    controls = ctk.CTkFrame(card, fg_color="transparent")
    controls.grid(row=3, column=0, sticky="ew", padx=10, pady=(1, 8))
    controls.grid_columnconfigure(2, weight=1)
    self._v46_cancel_btn = ctk.CTkButton(
        controls,
        text=_v469_ps(self, "cancel"),
        width=82,
        height=27,
        fg_color="#7f1d1d",
        hover_color="#991b1b",
        state="disabled",
        command=self._v46_cancel,
    )
    self._v46_cancel_btn.grid(row=0, column=0, padx=(0, 6))
    self._v46_copy_error_btn = ctk.CTkButton(
        controls,
        text=_v469_ps(self, "copy_error"),
        width=94,
        height=27,
        fg_color=p["surface2"],
        hover_color=p["surface"],
        text_color=p["muted"],
        state="disabled",
        command=self._v46_copy_error,
    )
    self._v46_copy_error_btn.grid(row=0, column=1, padx=(0, 6))
    self._v46_state_var = ctk.StringVar(value=_v469_state_label(self, "IDLE"))
    ctk.CTkLabel(
        controls,
        textvariable=self._v46_state_var,
        text_color=p["muted"],
        anchor="e",
        font=ctk.CTkFont("Segoe UI", 10, "bold"),
    ).grid(row=0, column=2, sticky="e")

    self._v46_install_url_menu(self._v46_source_label, lambda: self._v46_source_full, "source")
    self._v46_install_url_menu(self._v46_resolved_label, lambda: self._v46_resolved_full, "resolved")
    self._v46_progress_expanded = False
    self._v46_apply_progress_visibility(False)

def _v463_gui_setup_ui_final(self: CYOADownloaderGUI) -> None:
    _V462_GUI_SETUP_UI_FOR_V463(self)
    _v463_rebuild_progress_workspace(self)
    self._v463_resize_after_id = None

    def schedule_layout(event: Any) -> None:
        if event.widget is not self.root:
            return
        prior = getattr(self, "_v463_resize_after_id", None)
        if prior is not None:
            try:
                self.root.after_cancel(prior)
            except Exception as exc:
                logger.debug(f"v46.3 resize debounce cancel failed: {exc}")
        self._v463_resize_after_id = self.root.after(120, self._v463_arrange_progress_and_log)

    self.root.bind("<Configure>", schedule_layout, add="+")
    self.root.after(50, self._v463_arrange_progress_and_log)



# ---- historical GUI behavior block ----

"""Historical v46.5 theme patch body moved out of legacy.py."""



def _v465_apply_theme(self: CYOADownloaderGUI) -> None:
    _V465_PREVIOUS_APPLY_THEME(self)
    _v465_configure_log_tags(self)
    # v46.8 theme fix: the v46 progress/telemetry card is built once from the
    # palette active at init time and was never re-themed on a live theme
    # switch, so it stayed dark under Light mode. The existing rebuild helper
    # reconstructs the whole card from the *current* palette. Rebuild only when
    # idle; rebuilding mid-download would destroy live telemetry widgets, so in
    # that case the card re-themes naturally once the job returns to idle.
    if not getattr(self, "_is_running", False):
        try:
            _v463_rebuild_progress_workspace(self)
        except Exception as exc:
            logger.debug(f"Progress workspace re-theme skipped: {exc}")



# ---- historical GUI behavior block ----

"""Historical v46.6 final wrapper/body moved out of legacy.py."""



def _v466_is_cafe_metadata_game_url(value: str) -> bool:
    """Return True only for public CYOA.CAFE metadata game routes."""
    try:
        parsed = urlparse(canonicalize_url(value))
    except Exception:
        return False
    path = re.sub(r"/+", "/", parsed.path or "/")
    return parsed.netloc.lower() == "cyoa.cafe" and bool(
        re.fullmatch(r"/game/[^/]+/?", path, flags=re.IGNORECASE)
    )

def _v466_run_download(
    url: str,
    file_name: str = "",
    zip_output: bool = False,
    both_output: bool = False,
    website_output: bool = False,
    website_zip_output: bool = True,
    pure_website: bool = False,
    download_fonts: bool = False,
    show_font_analysis: bool = True,
    output_dir: str = "",
    max_workers: int = DEFAULT_MAX_WORKERS,
    engine_mode: str = "standard",
    cyoa_mgr_enabled: bool = False,
    ai_api_key: str = "",
    ai_provider: str = "",
    ai_mode: str = "auto_fallback",
    analysis_only: bool = False,
    archive_strategy: str = "",
    archive_max_pages: int = 0,
    archive_max_depth: int = -1,
    archive_capture_interactions: bool = False,
) -> None:
    """Resolve CYOA.CAFE metadata URLs before website/deep-scan execution."""
    source_url = url
    effective_name = file_name
    should_resolve = (website_output or pure_website) and _v466_is_cafe_metadata_game_url(source_url)
    if should_resolve:
        from ..project.cyoa_cafe import classify_cyoa_cafe_record, fetch_cyoa_cafe_record
        if classify_cyoa_cafe_record(fetch_cyoa_cafe_record(source_url)) == "static_pages":
            # Keep the metadata URL intact; the base orchestrator's structured
            # static adapter needs the record id and never mirrors the React shell.
            should_resolve = False
    if should_resolve:
        resolved = get_iframe_url_from_cyoa_cafe(source_url)
        if resolved and canonicalize_url(resolved) != canonicalize_url(source_url):
            if not effective_name:
                effective_name = _build_output_name(source_url)
            logger.info(f"CYOA.CAFE website target resolved before download: {source_url} → {resolved}")
            url = resolved
    kwargs = dict(
        url=url,
        file_name=effective_name,
        zip_output=zip_output,
        both_output=both_output,
        website_output=website_output,
        website_zip_output=website_zip_output,
        pure_website=pure_website,
        download_fonts=download_fonts,
        show_font_analysis=show_font_analysis,
        output_dir=output_dir,
        max_workers=max_workers,
        engine_mode=engine_mode,
        cyoa_mgr_enabled=cyoa_mgr_enabled,
        ai_api_key=ai_api_key,
        ai_provider=ai_provider,
        ai_mode=ai_mode,
        analysis_only=analysis_only,
        archive_strategy=archive_strategy,
        archive_max_pages=archive_max_pages,
        archive_max_depth=archive_max_depth,
        archive_capture_interactions=archive_capture_interactions,
    )
    try:
        return _V466_PREVIOUS_RUN_DOWNLOAD(**kwargs)
    except RuntimeError as exc:
        # After pre-resolving a metadata page, the compatibility wrapper no
        # longer sees the original cyoa.cafe URL. Preserve its controlled
        # pure-website fallback for authoritative targets without re-crawling
        # the metadata host.
        can_retry_pure = (
            should_resolve
            and website_output
            and not pure_website
            and "Could not resolve project data" in str(exc)
        )
        if not can_retry_pure:
            raise
        logger.warning(
            "Resolved target has no standard project payload; retrying the same "
            f"authoritative target as Pure Website: {url}"
        )
        kwargs.update(
            website_output=False,
            pure_website=True,
            engine_mode="standard",
        )
        return _V466_PREVIOUS_RUN_DOWNLOAD(**kwargs)

def _v466_setup_ui(self: CYOADownloaderGUI) -> None:
    _V466_PREVIOUS_SETUP_UI(self)
    for attr in ("_v46_source_label", "_v46_resolved_label", "_v46_job_pb"):
        widget = getattr(self, attr, None)
        if widget is not None:
            try:
                widget.grid_remove()
            except Exception as exc:
                logger.debug(f"Could not hide redundant progress widget {attr}: {exc}")
    legacy_status = getattr(getattr(self, "_status_lbl", None), "master", None)
    if legacy_status is not None:
        try:
            legacy_status.grid_remove()
        except Exception as exc:
            logger.debug(f"Could not hide legacy toolbar status strip: {exc}")


__all__ = [name for name in globals() if name.startswith("_v") or name in {"_sync_legacy_globals"}]
