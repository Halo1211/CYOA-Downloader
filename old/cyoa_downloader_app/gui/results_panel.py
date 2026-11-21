"""Snapshot reports, bounded card pages and cancellable file work off Tk."""

from __future__ import annotations

import csv
import os
import tempfile
from tkinter import TclError, filedialog, messagebox
from types import SimpleNamespace

import customtkinter as ctk

from ..core.progress import DownloadCancelledError
from ..logging_setup import logger
from .paged_cards import PagedCards
from .panel_jobs import PanelTask

FIELDS = ("result_type", "status", "url", "mode", "filename", "error")


def write_results_csv(path, rows, cancel):
    """Commit a complete UTF-8 snapshot; keep an old export on cancel/failure."""
    target = os.path.abspath(path)
    fd, temporary = tempfile.mkstemp(prefix=".results-", suffix=".part", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                if cancel.is_set():
                    raise DownloadCancelledError("Report export cancelled")
                writer.writerow({key: row.get(key, "") for key in FIELDS})
            if cancel.is_set():
                raise DownloadCancelledError("Report export cancelled")
            handle.flush()
            os.fsync(handle.fileno())
        if cancel.is_set():
            raise DownloadCancelledError("Report export cancelled")
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def failure_text(groups, cancel):
    sections = []
    for key, heading in (("cyoa_failed", "FAILED CYOA"), ("asset_failed", "FAILED ASSETS / IMAGES")):
        lines = []
        for row in groups[key]:
            if cancel.is_set():
                raise DownloadCancelledError("Report copy cancelled")
            lines.append(f"{row.get('url', '')}\t{row.get('error', '')}")
        if lines:
            sections.append(heading + "\n" + "\n".join(lines))
    return "\n\n".join(sections)


def open_results_panel(gui):
    # Imported at invocation after the compatibility composition is complete.
    from .final_behaviors import (
        _v24_badge,
        _v24_dialog_geometry,
        _v24_partition_result_rows,
        _v24_result_is_failed,
        _v24_result_rows,
        _v46_failure_details_snapshot,
    )

    if gui._focus_singleton_window("reports_center") is not None:
        return
    jobs = tuple(getattr(gui, "_last_results", None) or ())
    details = tuple(dict(row) for row in (getattr(getattr(gui, "_v46_telemetry", None), "failure_details", ()) or ()))
    failures = tuple(_v46_failure_details_snapshot(gui))
    is_en = getattr(gui, "_language", "id") == "en"
    if not jobs and not details and not failures:
        messagebox.showinfo("Reports" if is_en else "Laporan",
                            "No results yet. Run a download first." if is_en else "Belum ada hasil. Jalankan download terlebih dahulu.")
        return
    # Published job records are append-only; copying their references freezes
    # membership while the worker copies/normalizes each record off Tk.
    frozen = SimpleNamespace(_last_results=jobs, _v46_telemetry=SimpleNamespace(failure_details=details),
                             _v46_failure_events=failures)
    win = gui._make_singleton_window("reports_center")
    if win is None:
        return
    palette = gui._p()
    win.title("Reports Center" if is_en else "Pusat Laporan")
    width, height, min_width, min_height = _v24_dialog_geometry(win.winfo_screenwidth(), win.winfo_screenheight())
    win.geometry(f"{width}x{height}")
    win.minsize(min_width, min_height)
    win.configure(fg_color=palette["bg"])
    win.transient(gui.root)
    win.grab_set()
    task = win._panel_task = PanelTask(win)
    rows_all, groups, filter_buttons = [], {}, {}
    active_filter = ["all"]
    header = ctk.CTkFrame(win, fg_color=palette["panel"], height=68, corner_radius=0)
    header.pack(fill="x")
    header.pack_propagate(False)
    ctk.CTkLabel(header, text="📋 Reports Center" if is_en else "📋 Pusat Laporan",
                 font=ctk.CTkFont("Segoe UI", 16, "bold"), text_color=palette["fg"]).pack(side="left", padx=18)
    stats = ctk.CTkFrame(header, fg_color="transparent")
    stats.pack(side="right", padx=18)
    badges = [_v24_badge(stats, text, color, width) for text, color, width in (
        ("CYOA 0", "#334155", 96), ("OK 0", "#047857", 82),
        ("CYOA FAIL 0", "#b91c1c", 116), ("ASSET FAIL 0", "#b45309", 120))]
    body = ctk.CTkFrame(win, fg_color=palette["bg"], corner_radius=0)
    body.pack(fill="both", expand=True, padx=14, pady=12)
    top = ctk.CTkFrame(body, fg_color="transparent")
    top.pack(fill="x", pady=(0, 8))
    status = ctk.CTkLabel(body, text="Loading reports…" if is_en else "Memuat laporan…", anchor="w")
    status.pack(fill="x")
    fonts = (ctk.CTkFont("Segoe UI", 14, "bold"), ctk.CTkFont("Segoe UI", 12, "bold"), ctk.CTkFont("Consolas", 9))

    def card(parent, row):
        ok = not _v24_result_is_failed(row)
        asset = str(row.get("result_type") or "job").lower() == "asset"
        accent = "#22c55e" if ok else "#f59e0b" if asset else "#ef4444"
        title = ("ASSET" if asset else "CYOA") + "  ·  " + (row.get("filename") or "[auto]") + "  ·  " + str(row.get("mode", "")).replace("_", " ")
        detail, error = str(row.get("url", "")), str(row.get("error", ""))
        frame = ctk.CTkFrame(parent, fg_color=palette["surface"], corner_radius=10, border_width=1, border_color=palette["border"])
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text="✓" if ok else "!", width=34, height=34, fg_color=accent, text_color="#ffffff",
                     corner_radius=17, font=fonts[0]).grid(row=0, column=0, rowspan=2, padx=12, pady=10)
        ctk.CTkLabel(frame, text=title, anchor="w", font=fonts[1], text_color=palette["fg"]).grid(
            row=0, column=1, sticky="ew", padx=(0, 12), pady=(10, 2))
        ctk.CTkLabel(frame, text=detail if ok or not error else f"{detail}\n{error[:180]}", anchor="w",
                     justify="left", font=fonts[2], text_color=palette["muted" if ok else "fg"], wraplength=780).grid(
                         row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 10))
        return frame

    pager = win._panel_pager = PagedCards(body, palette, card, is_en=is_en)
    pager.empty_text = "Loading reports…" if is_en else "Memuat laporan…"
    pager.pack(fill="both", expand=True)
    pager.set_items([])

    def set_filter(value):
        active_filter[0] = value
        for key, button in filter_buttons.items():
            button.configure(fg_color="#3b82f6" if key == value else palette["surface2"],
                             text_color="#ffffff" if key == value else palette["muted"])
        pager.set_items(rows_all if value == "all" else groups.get(value, ()))

    for key, label in (
        ("all", "All" if is_en else "Semua"), ("cyoa_success", "CYOA Success" if is_en else "CYOA Berhasil"),
        ("cyoa_failed", "CYOA Failed" if is_en else "CYOA Gagal"), ("asset_failed", "Asset Failed" if is_en else "Asset Gagal"),
    ):
        button = ctk.CTkButton(top, text=label, width=90, height=30, state="disabled",
                              command=lambda key=key: set_filter(key))
        button.pack(side="left", padx=(0, 8))
        filter_buttons[key] = button

    def busy(value):
        for button in (export_button, copy_button):
            button.configure(state="disabled" if value else "normal")

    def export():
        if task.busy:
            return
        path = filedialog.asksaveasfilename(parent=win, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                           initialfile="download_results.csv")
        if not path:
            return
        busy(True)
        status.configure(text="Exporting…" if is_en else "Mengekspor…")

        def done(_value, error):
            busy(False)
            if error:
                status.configure(text="Export failed" if is_en else "Ekspor gagal")
                messagebox.showerror("Reports" if is_en else "Laporan", str(error), parent=win)
            else:
                status.configure(text="Exported" if is_en else "Diekspor")
                logger.info("Results exported: %s", path)
        task.start(lambda cancel, _report: write_results_csv(path, rows_all, cancel), done)

    def copy_failed():
        if task.busy:
            return
        busy(True)

        def done(text, error):
            busy(False)
            if error:
                status.configure(text=str(error))
                return
            try:
                win.clipboard_clear()
                win.clipboard_append(text)
            except TclError as exc:
                status.configure(text=str(exc))
        task.start(lambda cancel, _report: failure_text(groups, cancel), done)

    export_button = ctk.CTkButton(top, text="Export CSV" if is_en else "Ekspor CSV", width=104, height=30,
                                  state="disabled", command=export, fg_color="#0f766e", hover_color="#115e59")
    export_button.pack(side="right", padx=(8, 0))
    copy_button = ctk.CTkButton(top, text="Copy Failed" if is_en else "Salin Gagal", width=108, height=30,
                                state="disabled", command=copy_failed)
    copy_button.pack(side="right", padx=(8, 0))

    def work(cancel, _report):
        normalized = _v24_result_rows(frozen)
        if cancel.is_set():
            raise DownloadCancelledError("Report closed")
        return normalized, _v24_partition_result_rows(normalized)

    def loaded(value, error):
        if error:
            status.configure(text=str(error))
            return
        normalized, partitioned = value
        rows_all.extend(normalized)
        groups.update(partitioned)
        success, failed, assets = (len(groups[key]) for key in ("cyoa_success", "cyoa_failed", "asset_failed"))
        for badge, text in zip(badges, (f"CYOA {success + failed}", f"OK {success}", f"CYOA FAIL {failed}", f"ASSET FAIL {assets}")):
            badge.configure(text=text)
        for button in filter_buttons.values():
            button.configure(state="normal")
        busy(False)
        status.configure(text="")
        pager.empty_text = "No rows in this filter." if is_en else "Tidak ada data untuk filter ini."
        set_filter(active_filter[0])
    task.start(work, loaded)
