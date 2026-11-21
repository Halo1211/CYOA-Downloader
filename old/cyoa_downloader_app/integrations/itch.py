"""itch.io / itch-dl integration.

Phase 26 moves the itch-dl wrapper out of ``legacy.py``. The boolean gate is
mirrored back to legacy when toggled because the legacy CLI/orchestrator still
reads ``_ITCH_ENABLED`` directly.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlparse

import requests

from ..config.secrets import _keyring_module
from ..config.settings import _load_settings
from ..logging_setup import logger
from ..network.sessions import _get_shared_session, create_retry_session
from .itch_offline import (
    HTML5_MARKER_FILENAME,
    EncryptedHtml5ArchiveError,
    materialize_itch_html5_archive,
)

_ITCH_ENABLED: bool = False

# OS keyring implementations are optional and backend-defined.
_KEYRING_BACKEND_ERRORS = (Exception,)
_SESSION_CLEANUP_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    requests.RequestException,
)


def _set_itch_enabled(enabled: bool) -> None:
    """Enable/disable the optional itch.io asset downloader. Default OFF."""
    global _ITCH_ENABLED
    _ITCH_ENABLED = bool(enabled)
    # Keep legacy's historical global in sync until the orchestrator state is moved.
    try:
        import sys as _sys
        legacy_mod = _sys.modules.get("cyoa_downloader_app.runtime.surface")
        if legacy_mod is not None:
            legacy_mod._ITCH_ENABLED = _ITCH_ENABLED
    except (AttributeError, RuntimeError, TypeError) as exc:
        logger.debug("Could not mirror itch state to compatibility surface: %s", exc)
    logger.info(f"itch.io downloader {'enabled' if _ITCH_ENABLED else 'disabled'}.")


_ITCH_KEYRING_SERVICE = "cyoa_downloader_itch"
_ITCH_KEYRING_USER = "itch_api_key"


def _is_itch_url(url: str) -> bool:
    """True if the URL points at an itch.io page/host."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except (AttributeError, TypeError, ValueError):
        return False
    return parsed.scheme.lower() in {"http", "https"} and (
        host in {"itch.io", "itch.zone"}
        or host.endswith((".itch.io", ".itch.zone"))
    )


def _resolve_itch_api_key(explicit_key: str = "") -> tuple[str | None, str]:
    """
    Resolve an itch.io API key without forcing it into settings.json.
    Order: explicit (this run) → env (ITCH_API_KEY) → keyring → plain settings.
    Returns (key_or_None, source_label). A missing key permits reachability
    checks, but itch-dl currently requires a key for downloads.
    """
    if explicit_key:
        return explicit_key, "session"
    env_key = os.environ.get("ITCH_API_KEY", "").strip()
    if env_key:
        return env_key, "env"
    s = _load_settings()
    storage = s.get("itch_key_storage", "session")
    if storage == "keyring":
        kr = _keyring_module()
        if kr is not None:
            try:
                k = kr.get_password(_ITCH_KEYRING_SERVICE, _ITCH_KEYRING_USER)
                if k:
                    return k, "keyring"
            except _KEYRING_BACKEND_ERRORS as e:
                logger.debug(f"itch keyring read failed: {e}")
    plain = (s.get("itch_api_key") or "").strip()
    if plain:
        if storage != "plain":
            logger.warning("itch.io key found in plaintext settings.json. "
                           "Install 'keyring' and set itch_key_storage=keyring for safer storage.")
        return plain, "plain"
    return None, "none"


def _itch_session():
    """Build a retry-capable session for itch requests, reusing the app helper.

    Retained for the lightweight connectivity test in public mode. Actual asset
    downloading is delegated to the `itch-dl` backend (see below).
    """
    try:
        return create_retry_session()
    except (OSError, RuntimeError, TypeError, ValueError, requests.RequestException):
        return _get_shared_session()


# ── itch-dl backend detection ────────────────────────────────────────────────
# We do NOT reimplement an itch.io scraper. We wrap the proven community tool
# DragoonAethis/itch-dl. Resolution order (no silent auto-install):
#   1) itch-dl            (already installed on PATH; no repeat setup)
#   2) uvx itch-dl        (uv ephemeral run)
#   3) pipx run itch-dl   (pipx ephemeral run)
# Each candidate is probed with `--help` (or `--version` fallback) so we only
# report a backend that can actually execute.

def _which(name: str) -> str | None:
    try:
        import shutil as _sh
        return _sh.which(name)
    except (OSError, TypeError):
        return None


def _itch_probe(cmd: list[str], timeout: int = 25) -> bool:
    """Return True only when the command identifies itself as itch-dl.

    Never raises; a missing launcher returns False quietly.
    """
    import subprocess as _sp
    for probe in (["--help"], ["--version"]):
        try:
            r = _sp.run(cmd + probe, capture_output=True, timeout=timeout, check=False)
            # Current itch-dl does not implement --version and exits 1 with
            # usage text. Check identity in actual output, not exit code alone.
            out = ((r.stdout or b"") + (r.stderr or b"")).lower()
            if r.returncode in (0, 1, 2) and b"itch-dl" in out and (
                b"--download-to" in out or b"version" in out
            ):
                return True
        except FileNotFoundError:
            return False
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("itch backend probe failed for %s: %s", probe, exc)
            continue
    return False


def detect_itch_backend() -> tuple[list[str] | None, str]:
    """Resolve an itch-dl launcher command.

    Returns (cmd_prefix or None, label). cmd_prefix is the argv list that, with
    itch-dl arguments appended, runs the tool. Never raises.
    """
    # Prefer a local installation to avoid uvx/pipx resolving or downloading
    # the same package on each diagnostic and download.
    direct = _which("itch-dl")
    if direct and _itch_probe([direct]):
        return [direct], "itch-dl (PATH)"
    # 2) uvx
    uvx = _which("uvx")
    if uvx and _itch_probe([uvx, "itch-dl"]):
        return [uvx, "itch-dl"], "uvx itch-dl"
    # 3) pipx run
    pipx = _which("pipx")
    if pipx and _itch_probe([pipx, "run", "itch-dl"]):
        return [pipx, "run", "itch-dl"], "pipx run itch-dl"
    return None, "not found"


def itch_backend_status() -> str:
    """Human-readable backend availability line for GUI/CLI diagnostics."""
    cmd, label = detect_itch_backend()
    if cmd:
        return f"itch-dl backend: AVAILABLE via {label}"
    return ("itch-dl backend: NOT FOUND. Install one of: "
            "`uv` (uvx), `pipx`, or `pip install itch-dl`. "
            "See https://github.com/DragoonAethis/itch-dl")


def build_itch_command(cmd_prefix: list[str], page_url: str, dest: str,
                       api_key: str | None = None,
                       mirror_web: bool = False,
                       parallel: int = 1) -> list[str]:
    """Construct the full itch-dl argv.

    SECURITY: the API key is passed as a CLI argument to the child process only.
    Callers MUST NOT log the returned list verbatim when a key is present — use
    `redact_itch_command` for any logging/printing.
    """
    cmd = list(cmd_prefix) + [page_url, "--download-to", dest]
    if mirror_web:
        cmd += ["--mirror-web"]
    if not 1 <= parallel <= 16:
        raise ValueError("itch-dl parallel must be between 1 and 16")
    if parallel > 1:
        cmd += ["--parallel", str(parallel)]
    if api_key:
        cmd += ["--api-key", api_key]
    return cmd


def redact_itch_command(cmd: list[str]) -> str:
    """Return a log-safe string of an itch-dl command with the key masked."""
    out: list[str] = []
    skip_next = False
    secrets: list[str] = []
    for tok in cmd:
        if skip_next:
            secrets.append(tok)
            out.append("***")
            skip_next = False
            continue
        if tok in ("--api-key", "--api_key"):
            out.append(tok)
            skip_next = True
            continue
        out.append(tok)
    safe = " ".join(out)
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "***")
    return safe


def _redact_itch_output(message: str, api_key: str | None) -> str:
    """Remove the session key from child output and exception details."""
    return message.replace(api_key, "***") if api_key else message


def _run_itch_process(cmd: list[str], cancel_event: Event | None = None) -> tuple[int, str]:
    """Run itch-dl with bounded output memory and responsive cancellation."""
    from ..core.progress import DownloadCancelledError

    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(
            cmd, stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 3600
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelledError("itch-dl cancelled")
                try:
                    returncode = proc.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(cmd, 3600)
        except (DownloadCancelledError, subprocess.TimeoutExpired):
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            raise
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - 4096))
        return returncode, output.read().decode("utf-8", "replace")


def itch_test_connection(explicit_key: str = "") -> tuple[bool, str]:
    """
    Test that the itch-dl backend is available, plus a light reachability check.

    With a key configured, also verifies it against the itch API /me endpoint.
    Without one, checks reachability but does not claim download readiness.
    The API key is never printed. Returns (ok, message). Never raises.
    """
    cmd, label = detect_itch_backend()
    backend_line = (f"backend: {label}" if cmd
                    else "backend: NOT FOUND (install uv/pipx or `pip install itch-dl`)")
    key, source = _resolve_itch_api_key(explicit_key)
    # _itch_session() builds a fresh retry session per call;
    # it was never closed. One-shot diagnostic → close on every return path.
    sess = _itch_session()
    try:
        if key:
            r = sess.get("https://itch.io/api/1/key/me",
                         params={"api_key": key}, timeout=20)
            if r.status_code == 200 and isinstance(r.json(), dict) and r.json().get("user"):
                user = r.json()["user"].get("username", "?")
                ok = cmd is not None
                return ok, (f"itch.io auth OK as '{user}' (key source: {source}); {backend_line}.")
            return False, (f"itch.io auth failed (HTTP {r.status_code}). "
                           f"Check the API key (source: {source}); {backend_line}.")
        r = sess.get("https://itch.io/", timeout=20)
        if r.status_code < 400:
            return False, (
                f"itch.io reachable; {backend_line}. "
                "An itch.io API key is required by itch-dl to download."
            )
        return False, f"itch.io not reachable (HTTP {r.status_code}); {backend_line}."
    except (OSError, RuntimeError, TypeError, ValueError, requests.RequestException) as e:
        return False, f"itch.io probe error: {_redact_itch_output(str(e), key)}; {backend_line}."
    finally:
        try:
            sess.close()
        except _SESSION_CLEANUP_ERRORS as _close_exc:
            logger.debug("itch diagnostic session close failed: %s", _close_exc)


def download_itch_assets(page_url: str, output_dir: str,
                         explicit_key: str = "",
                         mirror_web: bool = False,
                         parallel: int = 1,
                         cancel_event: Event | None = None,
                         *, prepare_html5: bool = True) -> dict[str, Any]:
    """
    Download an itch.io project via the `itch-dl` backend into
    <output_dir>/itch_assets/.

    - Fully independent of the CYOA pipeline; never affects CYOA success/failure.
    - Failures are reported in the returned dict; cancellation propagates.
    - The API key is passed only to the child process and never logged.
    - HTML5 ZIPs are expanded to reusable offline folders by default.
    - Respects the user's account access only (itch-dl downloads what the key /
      public visibility permits; this wrapper adds no bypass).
    Returns a summary dict.
    """
    from ..core.progress import DownloadCancelledError

    result: dict[str, Any] = {"ok": False, "saved": 0, "failed": 0,
                              "existing": 0,
                              "offline_ready": 0, "offline_cached": 0,
                              "offline_failed": 0, "offline_encrypted": 0,
                              "offline_entries": [],
                              "skipped_auth": False, "backend": "",
                              "returncode": None, "message": ""}
    if not _is_itch_url(page_url):
        result["message"] = "Not an itch.io URL."
        return result

    cmd_prefix, label = detect_itch_backend()
    result["backend"] = label
    if not cmd_prefix:
        result["message"] = (
            "itch-dl backend not found. Install one of: `uv` (provides uvx), "
            "`pipx`, or `pip install itch-dl`. "
            "See https://github.com/DragoonAethis/itch-dl")
        logger.warning("[itch] " + result["message"])
        return result

    key, source = _resolve_itch_api_key(explicit_key)
    dest = os.path.join(output_dir, "itch_assets")
    try:
        os.makedirs(dest, exist_ok=True)
    except OSError as e:
        result["message"] = f"Cannot create itch_assets folder: {e}"
        return result

    try:
        cmd = build_itch_command(cmd_prefix, page_url, dest,
                                 api_key=key, mirror_web=mirror_web,
                                 parallel=parallel)
    except ValueError as exc:
        result["message"] = str(exc)
        return result
    # Log a redacted form only — never the raw key.
    logger.info(f"[itch] running: {redact_itch_command(cmd)} (key source: {source})")

    def _snapshot() -> dict[str, tuple[int, int]]:
        files = {}
        for root, dirs, names in os.walk(dest):
            dirs[:] = [directory for directory in dirs if not os.path.isfile(
                os.path.join(root, directory, HTML5_MARKER_FILENAME)
            )]
            for name in names:
                path = os.path.join(root, name)
                try:
                    stat = os.stat(path)
                    files[os.path.relpath(path, dest)] = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    continue
        return files

    try:
        before = _snapshot()
        returncode, output_tail = _run_itch_process(cmd, cancel_event=cancel_event)
        result["returncode"] = returncode
        after = _snapshot()
        saved = sum(1 for path, meta in after.items() if before.get(path) != meta)
        result["saved"] = saved
        result["existing"] = len(after) - saved
        if returncode == 0:
            if not after:
                result.update(failed=1, message=(
                    f"itch-dl exited successfully via {label}, but no files were saved "
                    "in itch_assets/. Check whether this page offers downloadable files."
                ))
            else:
                result.update(ok=True,
                              message=(f"itch.io: completed via {label}; "
                                       f"{saved} new/updated, {result['existing']} existing file(s) in itch_assets/ "
                                       f"(key source: {source})."))
                if prepare_html5:
                    for relative in sorted(after):
                        if not relative.lower().endswith(".zip"):
                            continue
                        archive = Path(dest) / relative
                        try:
                            entry, reused, complete = materialize_itch_html5_archive(
                                archive, page_url,
                            )
                            if entry is None:
                                continue
                            result["offline_entries"].append(entry)
                            if not complete:
                                result["offline_failed"] += 1
                                logger.warning("[itch] HTML5 offline assets incomplete: %s", entry)
                            else:
                                result["offline_cached" if reused else "offline_ready"] += 1
                                logger.info("[itch] HTML5 offline entry: %s", entry)
                        except EncryptedHtml5ArchiveError:
                            result["offline_encrypted"] += 1
                            logger.warning("[itch] ZIP needs a password; original retained: %s", relative)
                        except (OSError, RuntimeError, UnicodeError, ValueError,
                                zipfile.BadZipFile) as exc:
                            result["offline_failed"] += 1
                            logger.warning("[itch] HTML5 offline preparation failed for %s: %s", relative, exc)
                    if result["offline_failed"]:
                        result["ok"] = False
                        result["failed"] = result["offline_failed"]
                    if (result["offline_entries"] or result["offline_failed"]
                            or result["offline_encrypted"]):
                        result["message"] += (
                            f" HTML5 offline: {result['offline_ready']} prepared, "
                            f"{result['offline_cached']} reused, "
                            f"{result['offline_failed']} failed, "
                            f"{result['offline_encrypted']} password-protected ZIP(s) left intact."
                        )
        else:
            if not key:
                result["skipped_auth"] = True
            result.update(ok=False, failed=1,
                          message=(f"itch-dl exited with code {returncode} "
                                   f"via {label}; {saved} file(s) saved. "
                                   f"{'(no API key — some projects need auth) ' if not key else ''}"
                                   f"Details logged."))
            if output_tail.strip():
                logger.warning("[itch] itch-dl output tail:\n%s", _redact_itch_output(output_tail[-600:], key))
    except DownloadCancelledError:
        raise
    except FileNotFoundError:
        result["message"] = f"itch-dl launcher disappeared ({label})."
        logger.warning("[itch] " + result["message"])
    except subprocess.TimeoutExpired:
        result["message"] = "itch-dl timed out (1h limit)."
        logger.warning("[itch] " + result["message"])
    except (OSError, subprocess.SubprocessError) as e:
        result["message"] = f"itch-dl run error: {_redact_itch_output(str(e), key)}"
        logger.warning("[itch] " + result["message"])

    return result





__all__ = [
    "_ITCH_ENABLED",
    "_ITCH_KEYRING_SERVICE",
    "_ITCH_KEYRING_USER",
    "_is_itch_url",
    "_itch_probe",
    "_itch_session",
    "_resolve_itch_api_key",
    "_set_itch_enabled",
    "_which",
    "build_itch_command",
    "detect_itch_backend",
    "download_itch_assets",
    "itch_backend_status",
    "itch_test_connection",
    "redact_itch_command",
]
