"""Run-owned queue copies: freeze on Tk, derive output identities on the worker."""

import time

from ..core.progress import DownloadCancelledError
from ..storage import resume


class RunSnapshot(list):
    def __init__(self, items):
        super().__init__(dict(item) for item in items)
        self.prepared = False

    def prepare(self, default_mode, build_output_name, cancel_event):
        if self.prepared:
            return
        for index, item in enumerate(self, 1):
            if cancel_event.is_set():
                raise DownloadCancelledError("Cancelled while preparing queue identities")
            requested_name = str(item.get("filename") or "").strip()
            item["_run_file_name"] = requested_name or build_output_name(str(item.get("url") or ""))
            item["_run_requested_mode"] = str(item.get("mode") or default_mode or "auto")
            item["mode"] = item["_run_requested_mode"]
            item["_resume_key"] = resume.resume_job_key(
                str(item.get("url") or ""), item["_run_file_name"], item["_run_requested_mode"],
            )
            if index % 256 == 0:
                # Tk releases the GIL around native calls. Without cooperative
                # handoff, a CPU-only loop can delay an entire Tk callback even
                # after the Start button itself has returned.
                time.sleep(.001)
        if cancel_event.is_set():
            raise DownloadCancelledError("Cancelled while preparing queue identities")
        self.prepared = True
