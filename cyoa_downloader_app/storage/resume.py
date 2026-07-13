"""Per-output-folder resume-state helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime as _dt

from ..logging_setup import logger

_RESUME_FILE = "download_state.json"


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
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(
                {"completed": completed, "failed": failed, "updated_at": _dt.now().isoformat()},
                f,
                indent=2,
                ensure_ascii=False,
            )
        os.replace(tmp_path, path)
    except Exception as e:
        logger.warning(f"Could not save resume state: {e}")
        try:
            os.remove(tmp_path)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in save_resume_state: %s", _ignored_exc)


def clear_resume_state(output_dir: str) -> None:
    path = os.path.join(output_dir, _RESUME_FILE)
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in clear_resume_state: %s", _ignored_exc)
