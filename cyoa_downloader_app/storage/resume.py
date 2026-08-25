"""Per-output-folder resume-state helpers."""

from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime as _dt

from ..core.atomic_io import atomic_write_text
from ..logging_setup import logger

_RESUME_FILE = "download_state.json"
_RESUME_JOB_PREFIX = "job-v2:"


def resume_job_key(url: str, file_name: str, mode: str) -> str:
    """Return a stable identity for one queued output, not merely its URL."""
    payload = json.dumps(
        [str(url or "").strip(), str(file_name or "").strip(), str(mode or "auto").strip().lower()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _RESUME_JOB_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_resume_state(output_dir: str) -> dict:
    path = os.path.join(output_dir, _RESUME_FILE)
    if not os.path.exists(path):
        return {"completed": [], "failed": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        completed = data.get("completed", [])
        failed = data.get("failed", [])
        if not isinstance(completed, list):
            completed = []
        if not isinstance(failed, list):
            failed = []
        completed = [u for u in completed if isinstance(u, str)]
        failed = [u for u in failed if isinstance(u, str)]
        return {"completed": completed, "failed": failed}
    except Exception:
        return {"completed": [], "failed": []}


def save_resume_state(output_dir: str, completed: list, failed: list) -> None:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, _RESUME_FILE)
    try:
        atomic_write_text(
            path,
            json.dumps(
                {
                    "format_version": 2,
                    "completed": completed,
                    "failed": failed,
                    "updated_at": _dt.now().isoformat(),
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
    except Exception as e:
        logger.warning(f"Could not save resume state: {e}")


def clear_resume_state(output_dir: str) -> None:
    path = os.path.join(output_dir, _RESUME_FILE)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in clear_resume_state: %s", _ignored_exc)
