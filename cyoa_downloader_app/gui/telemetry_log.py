"""GUI telemetry log bridge extracted from the v46 patch block."""

from __future__ import annotations

import logging
import re
import time
import weakref
from typing import Any, Dict, Optional

from ..core.progress import DownloadState
from ..logging_setup import logger


class _V46TelemetryLogHandler(logging.Handler):
    """Translate stable legacy log messages into progress events without touching Tk."""

    _COUNT_RE = re.compile(
        r"\[(\d+)\s*/\s*(\d+)\]\s*"
        r"(?:(✓|✗|FAILED\b|OK\b)\s*)?(.*)",
        re.IGNORECASE,
    )

    def __init__(self, gui: Any) -> None:
        super().__init__(level=logging.INFO)
        self._gui_ref = weakref.ref(gui)

    def emit(self, record: logging.LogRecord) -> None:
        gui = self._gui_ref()
        if gui is None:
            return
        try:
            msg = record.getMessage()
            low = msg.lower()
            event: Optional[Dict[str, Any]] = None
            if "resolving project source" in low or "project search start" in low or "cyoa.cafe detected" in low:
                event = {"type": "stage_changed", "state": DownloadState.RESOLVING.value}
            elif "fetching page html" in low or "website download started" in low:
                event = {"type": "stage_changed", "state": DownloadState.FETCHING_ENTRY.value}
            elif "deep scan" in low or "candidate(s)" in low or "found " in low and "asset" in low:
                event = {"type": "stage_changed", "state": DownloadState.DISCOVERING_ASSETS.value}
            elif "re-analysed:" in low or "rewrit" in low or "localiz" in low:
                event = {"type": "stage_changed", "state": DownloadState.REWRITING.value}
            elif "integrity check" in low or "verification" in low:
                event = {"type": "stage_changed", "state": DownloadState.VERIFYING.value}
            elif "zip created" in low or "saving:" in low or "finaliz" in low or "packag" in low:
                event = {"type": "stage_changed", "state": DownloadState.PACKAGING.value}
            elif "retry" in low and ("attempt" in low or "backoff" in low):
                event = {"type": "file_retry"}
            elif "asset:" in low:
                name = msg.split("Asset:", 1)[-1].strip()
                event = {"type": "file_completed", "name": name}
            match = self._COUNT_RE.search(msg)
            if match:
                done = int(match.group(1))
                total = int(match.group(2))
                marker = str(match.group(3) or "").strip().upper()
                name = match.group(4).strip()
                # When the optional marker consumed only the leading cross,
                # the capture still began with "FAILED".  Strip any remaining
                # status token so Reports shows the actual asset name.
                name = re.sub(r"^(?:✓|✗|FAILED\b|OK\b)\s*", "", name, flags=re.IGNORECASE).strip()
                # Determine failure from the explicit status marker, never
                # from the asset name.  A successfully downloaded file such as
                # ``failed_banner.png`` must remain a success.
                failed = marker in {"✗", "FAILED"}
                gui._v46_enqueue_progress({"type": "asset_discovered", "total": total, "time": time.monotonic()})
                event = {
                    "type": "file_failed" if failed else "file_completed",
                    "name": name,
                    # A cross-only legacy line used to create a failed counter
                    # with an empty explanation.  Preserve the full log line as
                    # the minimum useful diagnostic for every failure.
                    "error": msg if failed else "",
                }
                # Correct cumulative count if log numbering jumped.
                event["absolute_finished"] = done
            if event:
                event.setdefault("time", time.monotonic())
                gui._v46_enqueue_progress(event)
        except Exception as exc:
            logger.debug(f"Telemetry log bridge failed: {exc}")

__all__ = ["_V46TelemetryLogHandler"]
