"""Progress state, formatting, and ETA helpers.

Extracted mechanically from the legacy v46 stabilization section. These helpers
are deliberately pure: they do not import GUI, network, or downloader modules.
"""

from __future__ import annotations

import math
import time
from collections import deque
from enum import Enum
from typing import Any, Dict, Optional, Set, Tuple


class DownloadCancelledError(RuntimeError):
    """Raised when the active GUI download is cancelled by the user."""


class DownloadState(str, Enum):
    IDLE = "IDLE"
    RESOLVING = "RESOLVING"
    FETCHING_ENTRY = "FETCHING_ENTRY"
    DISCOVERING_ASSETS = "DISCOVERING_ASSETS"
    DOWNLOADING = "DOWNLOADING"
    RETRYING = "RETRYING"
    REWRITING = "REWRITING"
    VERIFYING = "VERIFYING"
    PACKAGING = "PACKAGING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


_V46_STAGE_BANDS: Dict[str, Tuple[float, float]] = {
    DownloadState.IDLE.value: (0.0, 0.0),
    DownloadState.RESOLVING.value: (0.0, 0.05),
    DownloadState.FETCHING_ENTRY.value: (0.05, 0.10),
    DownloadState.DISCOVERING_ASSETS.value: (0.10, 0.25),
    DownloadState.DOWNLOADING.value: (0.25, 0.85),
    DownloadState.RETRYING.value: (0.25, 0.85),
    DownloadState.REWRITING.value: (0.85, 0.92),
    DownloadState.VERIFYING.value: (0.92, 0.97),
    DownloadState.PACKAGING.value: (0.97, 0.995),
    DownloadState.COMPLETED.value: (1.0, 1.0),
    DownloadState.COMPLETED_WITH_WARNINGS.value: (1.0, 1.0),
    DownloadState.FAILED.value: (0.0, 0.995),
    DownloadState.CANCELLING.value: (0.0, 0.995),
    DownloadState.CANCELLED.value: (0.0, 0.995),
}


def format_bytes(value: Optional[float]) -> str:
    """Format a byte count without inventing an unavailable total."""
    if value is None:
        return "Unknown"
    try:
        n = max(0.0, float(value))
    except (TypeError, ValueError):
        return "Unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    idx = 0
    while n >= 1024.0 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(n)} {units[idx]}"
    if n >= 100:
        return f"{n:.0f} {units[idx]}"
    if n >= 10:
        return f"{n:.1f} {units[idx]}"
    return f"{n:.2f} {units[idx]}"


def format_speed(bytes_per_second: Optional[float]) -> str:
    """Format transfer speed using binary byte units."""
    if bytes_per_second is None:
        return "0 B/s"
    try:
        speed = float(bytes_per_second)
    except (TypeError, ValueError):
        return "0 B/s"
    if not math.isfinite(speed) or speed <= 0:
        return "0 B/s"
    return f"{format_bytes(speed)}/s"


def format_duration(seconds: Optional[float]) -> str:
    """Format a duration as HH:MM:SS; invalid values become Unknown."""
    if seconds is None:
        return "Unknown"
    try:
        sec = float(seconds)
    except (TypeError, ValueError):
        return "Unknown"
    if not math.isfinite(sec) or sec < 0:
        return "Unknown"
    total = int(round(sec))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def calculate_smoothed_speed(
    previous_speed: Optional[float],
    instant_speed: Optional[float],
    alpha: float = 0.25,
) -> float:
    """Return an EWMA speed sample with finite/non-negative safeguards."""
    try:
        instant = float(instant_speed or 0.0)
    except (TypeError, ValueError):
        instant = 0.0
    if not math.isfinite(instant) or instant < 0:
        instant = 0.0
    try:
        prev = float(previous_speed or 0.0)
    except (TypeError, ValueError):
        prev = 0.0
    if not math.isfinite(prev) or prev < 0:
        prev = 0.0
    a = min(1.0, max(0.01, float(alpha)))
    if prev <= 0:
        return instant
    return a * instant + (1.0 - a) * prev


def calculate_eta(
    remaining_bytes: Optional[float],
    smoothed_speed: Optional[float],
    sample_count: int = 0,
    *,
    minimum_samples: int = 3,
    max_seconds: float = 365 * 24 * 3600,
) -> Optional[float]:
    """Calculate a bounded ETA or return None when the estimate is unsafe."""
    if sample_count < minimum_samples:
        return None
    try:
        remaining = float(remaining_bytes) if remaining_bytes is not None else -1.0
        speed = float(smoothed_speed) if smoothed_speed is not None else 0.0
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(remaining) and math.isfinite(speed)):
        return None
    if remaining < 0 or speed <= 0:
        return None
    eta = remaining / speed
    if not math.isfinite(eta) or eta < 0 or eta > max_seconds:
        return None
    return eta


def calculate_stage_progress(
    stage: Any,
    finished_assets: int = 0,
    total_assets: Optional[int] = None,
    previous: float = 0.0,
) -> float:
    """Map an explicit stage to a monotonic 0..1 representational progress."""
    key = stage.value if isinstance(stage, DownloadState) else str(stage or DownloadState.IDLE.value).upper()
    start, end = _V46_STAGE_BANDS.get(key, (0.0, 0.995))
    value = start
    if key in {DownloadState.DOWNLOADING.value, DownloadState.RETRYING.value}:
        if total_assets and total_assets > 0:
            ratio = min(1.0, max(0.0, float(finished_assets) / float(total_assets)))
            value = start + (end - start) * ratio
        else:
            value = start
    elif start != end:
        value = (start + end) / 2.0
    else:
        value = end
    return min(1.0, max(float(previous or 0.0), value))


class DownloadTelemetry:
    """Thread-agnostic progress state; events should be applied on the GUI thread."""

    def __init__(self) -> None:
        self.speed_history = deque(maxlen=120)
        self._known_urls: Set[str] = set()
        self.reset(0)

    def reset(self, total_jobs: int = 0) -> None:
        self.state = DownloadState.IDLE
        self.total_jobs = max(0, int(total_jobs or 0))
        self.current_job = 0
        self.completed_jobs = 0
        self.failed_jobs = 0
        self.cancelled_jobs = 0
        self.mode = ""
        self.source_url = ""
        self.resolved_url = ""
        self.current_file = ""
        self.assets_total: Optional[int] = None
        self.assets_finished = 0
        self.assets_success = 0
        self.assets_failed = 0
        self.assets_skipped = 0
        self.assets_retried = 0
        self.file_downloaded = 0
        self.file_total: Optional[int] = None
        self.total_downloaded = 0
        self.total_known = 0
        self.total_size_complete = True
        self.started_at: Optional[float] = None
        self.job_started_at: Optional[float] = None
        self.last_sample_at: Optional[float] = None
        self.sample_bytes = 0
        self.smoothed_speed = 0.0
        self.average_speed = 0.0
        self.peak_speed = 0.0
        self.sample_count = 0
        self.job_progress = 0.0
        self.last_error = ""
        self.speed_history.clear()
        self._known_urls.clear()

    def set_state(self, state: Any) -> None:
        try:
            new_state = state if isinstance(state, DownloadState) else DownloadState(str(state).upper())
        except Exception:
            return
        self.state = new_state
        self.job_progress = calculate_stage_progress(
            new_state,
            self.assets_finished,
            self.assets_total,
            self.job_progress,
        )
        if new_state in {DownloadState.COMPLETED, DownloadState.COMPLETED_WITH_WARNINGS}:
            self.job_progress = 1.0
            self.smoothed_speed = 0.0
        elif new_state in {DownloadState.FAILED, DownloadState.CANCELLED}:
            self.smoothed_speed = 0.0

    def apply(self, event: Dict[str, Any]) -> None:
        typ = str(event.get("type", ""))
        now = float(event.get("time") or time.monotonic())
        if typ == "queue_started":
            self.reset(int(event.get("total_jobs") or 0))
            self.started_at = now
            self.last_sample_at = now
            self.set_state(DownloadState.RESOLVING)
        elif typ == "job_started":
            self.current_job = max(1, int(event.get("job_index") or 1))
            self.total_jobs = max(self.total_jobs, int(event.get("total_jobs") or self.total_jobs or 1))
            self.mode = str(event.get("mode") or "")
            self.source_url = str(event.get("source_url") or "")
            self.resolved_url = str(event.get("resolved_url") or "")
            self.current_file = ""
            self.assets_total = None
            self.assets_finished = self.assets_success = self.assets_failed = self.assets_skipped = self.assets_retried = 0
            self.file_downloaded = 0
            self.file_total = None
            self.total_downloaded = 0
            self.total_known = 0
            self.total_size_complete = True
            self.job_started_at = now
            self.last_sample_at = now
            self.sample_bytes = 0
            self.smoothed_speed = self.average_speed = self.peak_speed = 0.0
            self.sample_count = 0
            self.job_progress = 0.0
            self.speed_history.clear()
            self._known_urls.clear()
            self.last_error = ""
            self.set_state(DownloadState.RESOLVING)
        elif typ == "stage_changed":
            self.set_state(event.get("state") or event.get("stage"))
            if event.get("resolved_url"):
                self.resolved_url = str(event["resolved_url"])
        elif typ == "asset_discovered":
            total = event.get("total")
            if total is not None:
                self.assets_total = max(self.assets_total or 0, int(total))
            else:
                self.assets_total = max(self.assets_total or 0, self.assets_finished + 1)
            self.set_state(DownloadState.DISCOVERING_ASSETS)
        elif typ == "file_started":
            self.current_file = str(event.get("name") or event.get("url") or "")
            self.file_downloaded = 0
            total = event.get("total_bytes")
            self.file_total = int(total) if total not in (None, "") and int(total) >= 0 else None
            self.set_state(DownloadState.DOWNLOADING)
        elif typ == "response_meta":
            url = str(event.get("url") or "")
            total = event.get("content_length")
            if url and url not in self._known_urls:
                self._known_urls.add(url)
                if total is None:
                    self.total_size_complete = False
                else:
                    try:
                        self.total_known += max(0, int(total))
                    except Exception:
                        self.total_size_complete = False
            if url:
                self.current_file = url
            if total is not None:
                try:
                    self.file_total = max(0, int(total))
                except Exception:
                    self.file_total = None
        elif typ in {"bytes_transferred", "speed_bytes"}:
            n = max(0, int(event.get("bytes") or 0))
            self.sample_bytes += n
            self.total_downloaded += n
            self.file_downloaded += n
        elif typ == "file_progress":
            current = max(0, int(event.get("downloaded") or 0))
            previous = self.file_downloaded
            self.file_downloaded = max(previous, current)
            delta = max(0, self.file_downloaded - previous)
            self.sample_bytes += delta
            self.total_downloaded += delta
            total = event.get("total")
            if total is not None:
                self.file_total = max(0, int(total))
            self.set_state(DownloadState.DOWNLOADING)
        elif typ == "file_completed":
            self.assets_finished += 1
            self.assets_success += 1
            if event.get("name") or event.get("url"):
                self.current_file = str(event.get("name") or event.get("url"))
            self.sample_speed(now, force=True)
            self.set_state(DownloadState.DOWNLOADING)
        elif typ == "file_failed":
            self.assets_finished += 1
            self.assets_failed += 1
            self.last_error = str(event.get("error") or self.last_error)
            self.set_state(DownloadState.DOWNLOADING)
        elif typ == "file_skipped":
            self.assets_finished += 1
            self.assets_skipped += 1
            self.set_state(DownloadState.DOWNLOADING)
        elif typ == "file_retry":
            self.assets_retried += 1
            self.set_state(DownloadState.RETRYING)
        elif typ == "job_completed":
            self.completed_jobs += 1
            warnings = int(event.get("failed_assets") or self.assets_failed or 0)
            self.set_state(DownloadState.COMPLETED_WITH_WARNINGS if warnings else DownloadState.COMPLETED)
        elif typ == "job_failed":
            self.failed_jobs += 1
            self.last_error = str(event.get("error") or "Download failed")
            self.set_state(DownloadState.FAILED)
        elif typ == "job_cancelled":
            self.cancelled_jobs += 1
            self.set_state(DownloadState.CANCELLED)
        elif typ == "cancelling":
            self.set_state(DownloadState.CANCELLING)
        self.sample_speed(now)

    def sample_speed(self, now: Optional[float] = None, force: bool = False) -> None:
        current = float(now or time.monotonic())
        if self.last_sample_at is None:
            self.last_sample_at = current
            return
        elapsed = current - self.last_sample_at
        if not force and elapsed < 0.5:
            return
        if elapsed <= 0:
            return
        instant = float(self.sample_bytes) / elapsed
        self.sample_bytes = 0
        self.last_sample_at = current
        self.smoothed_speed = calculate_smoothed_speed(self.smoothed_speed, instant, 0.28)
        self.peak_speed = max(self.peak_speed, instant)
        self.sample_count += 1
        self.speed_history.append(self.smoothed_speed)
        start = self.job_started_at or self.started_at
        if start is not None and current > start:
            self.average_speed = float(self.total_downloaded) / (current - start)

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        current = float(now or time.monotonic())
        self.sample_speed(current)
        start = self.job_started_at or self.started_at
        elapsed = max(0.0, current - start) if start is not None else 0.0
        total_for_eta = self.total_known if self.total_size_complete and self.total_known > 0 else None
        remaining = max(0, total_for_eta - self.total_downloaded) if total_for_eta is not None else None
        eta = calculate_eta(remaining, self.smoothed_speed, self.sample_count)
        queue_ratio = (
            min(1.0, float(self.completed_jobs + self.failed_jobs + self.cancelled_jobs) / float(self.total_jobs))
            if self.total_jobs > 0 else 0.0
        )
        return {
            "state": self.state.value,
            "total_jobs": self.total_jobs,
            "current_job": self.current_job,
            "completed_jobs": self.completed_jobs,
            "failed_jobs": self.failed_jobs,
            "remaining_jobs": max(0, self.total_jobs - self.completed_jobs - self.failed_jobs - self.cancelled_jobs),
            "queue_ratio": queue_ratio,
            "job_progress": self.job_progress,
            "mode": self.mode,
            "source_url": self.source_url,
            "resolved_url": self.resolved_url,
            "current_file": self.current_file,
            "assets_total": self.assets_total,
            "assets_finished": self.assets_finished,
            "assets_success": self.assets_success,
            "assets_failed": self.assets_failed,
            "assets_skipped": self.assets_skipped,
            "assets_retried": self.assets_retried,
            "file_downloaded": self.file_downloaded,
            "file_total": self.file_total,
            "total_downloaded": self.total_downloaded,
            "total_known": total_for_eta,
            "speed": self.smoothed_speed,
            "average_speed": self.average_speed,
            "peak_speed": self.peak_speed,
            "elapsed": elapsed,
            "eta": eta,
            "last_error": self.last_error,
            "speed_history": list(self.speed_history),
        }


__all__ = [
    "DownloadCancelledError",
    "DownloadState",
    "_V46_STAGE_BANDS",
    "format_bytes",
    "format_speed",
    "format_duration",
    "calculate_smoothed_speed",
    "calculate_eta",
    "calculate_stage_progress",
    "DownloadTelemetry",
]
