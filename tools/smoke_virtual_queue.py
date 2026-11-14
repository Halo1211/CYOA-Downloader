"""Exercise pooled queue widgets in real Tk with an isolated profile."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    checks = []
    with tempfile.TemporaryDirectory(prefix="cyoa-virtual-queue-smoke-") as profile:
        os.environ["USERPROFILE"] = profile
        import customtkinter as ctk

        from cyoa_downloader_app.gui.queue_import import QueueImport
        from cyoa_downloader_app.runtime.surface import CYOADownloaderGUI

        root = ctk.CTk()
        root.withdraw()
        root.attributes("-alpha", 0.0)
        gui = CYOADownloaderGUI(root)
        root.geometry("1280x900+20+20")
        root.deiconify()
        root.lower()
        view = gui._queue_view

        def pump(seconds=.12):
            deadline = time.perf_counter() + seconds
            while time.perf_counter() < deadline:
                root.update()
                time.sleep(.001)

        def row_for(identity):
            return next(row for row in view.rows if row.identity == identity)

        try:
            pump(.3)
            QueueImport(gui, "fixture").accept([
                {"url": f"https://example.invalid/{i}", "filename": str(i), "mode": "auto"}
                for i in range(10000)
            ])
            while gui._queue_import_busy:
                pump(.02)
            pump()
            assert len(gui._queue_data) == 10000 and len(view.rows) <= 10
            checks.append("10000 items use a bounded row pool")
            first = gui._queue_data[0]
            original = row_for(first["_queue_id"])
            original.entry._entry.focus_force()
            original.filename.set("Edited α title")
            assert first["filename"] == "Edited α title"
            view.canvas.yview_moveto(.7)
            pump()
            assert first["filename"] == "Edited α title"
            assert original.identity == first["_queue_id"]
            checks.append("focused editor remains tied to its original item")
            view.canvas.focus_set()
            pump()
            view.canvas.yview_moveto(0)
            pump()
            recycled = row_for(first["_queue_id"])
            old_badge = recycled.badge
            view.canvas.yview_moveto(.5)
            pump()
            gui._set_queue_item_mode(first, "website_folder", old_badge)
            assert first["mode"] == "website_folder"
            view.canvas.yview_moveto(0)
            pump()
            assert "folder" in row_for(first["_queue_id"]).badge.cget("text").lower()
            checks.append("mode menu target survives widget reuse")
            source = row_for(first["_queue_id"])
            target_row = row_for(gui._queue_data[1]["_queue_id"])
            source._drag_start(SimpleNamespace())
            source._drag(SimpleNamespace(y_root=target_row.frame.winfo_rooty() + target_row.frame.winfo_height() // 2))
            source._drag_end(SimpleNamespace())
            pump()
            assert gui._queue_data[1] is first
            gui._swap_rows(0, 1)
            pump()
            checks.append("drag handle reorders the model rather than pool slots")
            before = list(gui._queue_data)
            gui._swap_rows(0, 9999)
            assert gui._queue_data[-1] is first and gui._queue_data[0] is before[-1]
            gui._active_run_order = tuple(item["_queue_id"] for item in before)
            gui._set_dot(0, "running")
            assert view.model.states[first["_queue_id"]] == "running"
            checks.append("reorder and run status follow stable identities")
            target = gui._queue_data[7000]
            gui._reveal_queue_ids({target["_queue_id"]})
            pump(.2)
            assert view.model.states[target["_queue_id"]] == "error"
            assert row_for(target["_queue_id"]).frame.cget("border_color") == "#ef4444"
            gui._apply_theme()
            pump()
            assert row_for(target["_queue_id"]).frame.cget("border_color") == "#ef4444"
            checks.append("offscreen errors can be revealed and highlighted")
            view.canvas.yview_moveto(0)
            pump()
            removed = gui._remove_queue_ids_from_queue({first["_queue_id"], target["_queue_id"]})
            assert removed == 2 and len(gui._queue_data) == 9998
            added = {"url": "https://example.invalid/new", "filename": "new", "mode": "auto"}
            gui._queue_data.append(added)
            gui._make_queue_row(added["url"], added["mode"], added["filename"])
            assert added["_queue_id"] in view.model.positions
            gui._set_dot(0, "done")  # Deleted run item must not affect the new row.
            assert view.model.states.get(added["_queue_id"], "idle") == "idle"
            checks.append("completion removes exact IDs and preserves newer items")
            for scale in (.75, 1.25, 1.0):
                ctk.set_widget_scaling(scale)
                pump(.25)
                active_row = next(row for row in view.rows if row.identity is not None
                                  and row.frame.winfo_ismapped())
                assert active_row.entry.winfo_y() + active_row.entry.winfo_height() <= active_row.frame.winfo_height()
            checks.append("filename editor fits at 75%, 100%, and 125% scaling")
            renders = []
            render = view.render

            def counted():
                renders.append(True)
                return render()

            with patch.object(view, "render", counted):
                pump(.2)
                renders.clear()
                pump(.2)
                assert len(renders) <= 1, "Populated queue keeps rendering while idle"
            checks.append("populated queue has no continuous render loop")
            gui._clear_queue()
            pump()
            assert not gui._queue_data and not view.model.states
            assert all(row.identity is None for row in view.rows)
            checks.append("Clear resets model and hides pooled rows")
            QueueImport(gui, "second fixture").accept([
                {"url": f"https://example.invalid/new/{i}", "mode": "auto"} for i in range(1000)
            ])
            gui._clear_queue()
            pump()
            assert not gui._queue_data and not gui._queue_import_busy
            checks.append("Clear cancels a partial import without late rows")
            with patch.object(view, "render", counted):
                pump(.2)
                renders.clear()
                pump(.2)
                assert len(renders) <= 1, "Queue keeps rendering while idle"
            checks.append("idle queue has no continuous render loop")
        finally:
            gui._v46_finish_close()
    report = {"checks_passed": len(checks), "checks": checks}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
