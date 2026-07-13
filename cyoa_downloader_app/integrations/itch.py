"""itch.io / itch-dl integration.

Phase 26 moves the itch-dl wrapper out of ``legacy.py``. The boolean gate is
mirrored back to legacy when toggled because the legacy CLI/orchestrator still
reads ``_ITCH_ENABLED`` directly.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ..config.secrets import _keyring_module
from ..config.settings import _load_settings
from ..logging_setup import logger
from ..network.sessions import create_retry_session, _get_shared_session

_ITCH_ENABLED: bool = False


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
    except Exception:
        pass
    logger.info(f"itch.io downloader {'enabled' if _ITCH_ENABLED else 'disabled'}.")


_ITCH_KEYRING_SERVICE = "cyoa_downloader_itch"
_ITCH_KEYRING_USER = "itch_api_key"


def _is_itch_url(url: str) -> bool:
    """True if the URL points at an itch.io page/host."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return host == "itch.io" or host.endswith(".itch.io") or "itch.zone" in host


def _resolve_itch_api_key(explicit_key: str = "") -> "Tuple[Optional[str], str]":
    """
    Resolve an itch.io API key without forcing it into settings.json.
    Order: explicit (this run) → env (ITCH_API_KEY) → keyring → plain settings.
    Returns (key_or_None, source_label). Empty key is fine — caller uses public mode.
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
            except Exception as e:
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
    except Exception:
        return _get_shared_session()


# ── itch-dl backend detection ────────────────────────────────────────────────
# We do NOT reimplement an itch.io scraper. We wrap the proven community tool
# DragoonAethis/itch-dl. Resolution order (no silent auto-install):
#   1) uvx itch-dl        (uv ephemeral run — preferred, no global install)
#   2) pipx run itch-dl   (pipx ephemeral run)
#   3) itch-dl            (already installed on PATH)
# Each candidate is probed with `--version` (or `--help` fallback) so we only
# report a backend that can actually execute.

def _which(name: str) -> "Optional[str]":
    try:
        import shutil as _sh
        return _sh.which(name)
    except Exception:
        return None


def _itch_probe(cmd: "List[str]", timeout: int = 25) -> bool:
    """Return True if `cmd --version`/`--help` runs without a launch error.

    Never raises; a missing launcher returns False quietly.
    """
    import subprocess as _sp
    for probe in (["--version"], ["--help"]):
        try:
            r = _sp.run(cmd + probe, stdout=_sp.PIPE, stderr=_sp.PIPE,
                        timeout=timeout)
            # itch-dl prints usage/version on these; rc may be 0 or small.
            if r.returncode in (0, 1, 2):
                out = (r.stdout or b"") + (r.stderr or b"")
                if b"itch" in out.lower() or probe == ["--version"]:
                    return True
        except FileNotFoundError:
            return False
        except Exception:
            continue
    return False


def detect_itch_backend() -> "Tuple[Optional[List[str]], str]":
    """Resolve an itch-dl launcher command.

    Returns (cmd_prefix or None, label). cmd_prefix is the argv list that, with
    itch-dl arguments appended, runs the tool. Never raises.
    """
    # 1) uvx
    uvx = _which("uvx")
    if uvx and _itch_probe([uvx, "itch-dl"]):
        return [uvx, "itch-dl"], "uvx itch-dl"
    # 2) pipx run
    pipx = _which("pipx")
    if pipx and _itch_probe([pipx, "run", "itch-dl"]):
        return [pipx, "run", "itch-dl"], "pipx run itch-dl"
    # 3) direct executable
    direct = _which("itch-dl")
    if direct and _itch_probe([direct]):
        return [direct], "itch-dl (PATH)"
    return None, "not found"


def itch_backend_status() -> str:
    """Human-readable backend availability line for GUI/CLI diagnostics."""
    cmd, label = detect_itch_backend()
    if cmd:
        return f"itch-dl backend: AVAILABLE via {label}"
    return ("itch-dl backend: NOT FOUND. Install one of: "
            "`uv` (uvx), `pipx`, or `pip install itch-dl`. "
            "See https://github.com/DragoonAethis/itch-dl")


def build_itch_command(cmd_prefix: "List[str]", page_url: str, dest: str,
                       api_key: "Optional[str]" = None,
                       mirror_web: bool = False) -> "List[str]":
    """Construct the full itch-dl argv.

    SECURITY: the API key is passed as a CLI argument to the child process only.
    Callers MUST NOT log the returned list verbatim when a key is present — use
    `redact_itch_command` for any logging/printing.
    """
    cmd = list(cmd_prefix) + [page_url, "--download-to", dest]
    if mirror_web:
        cmd += ["--mirror-web"]
    if api_key:
        cmd += ["--api-key", api_key]
    return cmd


def redact_itch_command(cmd: "List[str]") -> str:
    """Return a log-safe string of an itch-dl command with the key masked."""
    out: List[str] = []
    skip_next = False
    for tok in cmd:
        if skip_next:
            out.append("***")
            skip_next = False
            continue
        if tok in ("--api-key", "--api_key"):
            out.append(tok)
            skip_next = True
            continue
        out.append(tok)
    return " ".join(out)


def itch_test_connection(explicit_key: str = "") -> "Tuple[bool, str]":
    """
    Test that the itch-dl backend is available, plus a light reachability check.

    With a key configured, also verifies the key against the itch API /me
    endpoint. Without one, checks itch.io reachability (public mode). The API key
    is never printed. Returns (ok, message). Never raises.
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
            ok = cmd is not None
            note = "" if ok else " — install itch-dl to download"
            return ok, f"itch.io reachable (public mode, no API key); {backend_line}{note}."
        return False, f"itch.io not reachable (HTTP {r.status_code}); {backend_line}."
    except Exception as e:
        # Backend presence is still useful info even if the network probe fails.
        return (cmd is not None), f"itch.io probe error: {e}; {backend_line}."
    finally:
        try:
            sess.close()
        except Exception as _close_exc:
            logger.debug("itch diagnostic session close failed: %s", _close_exc)


def download_itch_assets(page_url: str, output_dir: str,
                         explicit_key: str = "",
                         mirror_web: bool = False) -> "Dict[str, Any]":
    """
    Download an itch.io project via the `itch-dl` backend into
    <output_dir>/itch_assets/.

    - Fully independent of the CYOA pipeline; never affects CYOA success/failure.
    - Never raises; failures are reported in the returned dict and logged.
    - The API key is passed only to the child process and never logged.
    - Respects the user's account access only (itch-dl downloads what the key /
      public visibility permits; this wrapper adds no bypass).
    Returns a summary dict.
    """
    import subprocess as _sp

    result: Dict[str, Any] = {"ok": False, "saved": 0, "failed": 0,
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
    except Exception as e:
        result["message"] = f"Cannot create itch_assets folder: {e}"
        return result

    cmd = build_itch_command(cmd_prefix, page_url, dest,
                             api_key=key, mirror_web=mirror_web)
    # Log a redacted form only — never the raw key.
    logger.info(f"[itch] running: {redact_itch_command(cmd)} (key source: {source})")

    try:
        proc = _sp.run(cmd, stdout=_sp.PIPE, stderr=_sp.STDOUT, timeout=3600)
        result["returncode"] = proc.returncode
        # Count files actually written under dest as the "saved" signal.
        saved = 0
        for root, _dirs, files in os.walk(dest):
            saved += len(files)
        result["saved"] = saved
        if proc.returncode == 0:
            result.update(ok=saved > 0,
                          message=(f"itch.io: completed via {label}; "
                                   f"{saved} file(s) in itch_assets/ "
                                   f"(key source: {source})."))
        else:
            # Surface a trimmed tail of itch-dl output for diagnosis (no key in it).
            tail = ""
            try:
                tail = (proc.stdout or b"").decode("utf-8", "replace")[-600:]
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in download_itch_assets (line 19781): %s", _ignored_exc)
            if not key:
                result["skipped_auth"] = True
            result.update(ok=saved > 0,
                          message=(f"itch-dl exited with code {proc.returncode} "
                                   f"via {label}; {saved} file(s) saved. "
                                   f"{'(no API key — some projects need auth) ' if not key else ''}"
                                   f"Details logged."))
            if tail.strip():
                logger.warning(f"[itch] itch-dl output tail:\n{tail}")
    except FileNotFoundError:
        result["message"] = f"itch-dl launcher disappeared ({label})."
        logger.warning("[itch] " + result["message"])
    except _sp.TimeoutExpired:
        result["message"] = "itch-dl timed out (1h limit)."
        logger.warning("[itch] " + result["message"])
    except Exception as e:
        result["message"] = f"itch-dl run error: {e}"
        logger.warning("[itch] " + result["message"])

    return result





__all__ = [
    "_ITCH_ENABLED", "_ITCH_KEYRING_SERVICE", "_ITCH_KEYRING_USER",
    "_set_itch_enabled", "_is_itch_url", "_resolve_itch_api_key",
    "_itch_session", "_which", "_itch_probe", "detect_itch_backend",
    "itch_backend_status", "build_itch_command", "redact_itch_command",
    "itch_test_connection", "download_itch_assets",
]
