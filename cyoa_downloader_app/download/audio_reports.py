"""Audio/image failure report helpers moved out of legacy.py.

These helpers are intentionally small and side-effect compatible: same output
filenames, append semantics, and log messages as the monolithic implementation.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional

from ..logging_setup import logger


def _write_failed_images_log(
    failed: List[Dict[str, str]],
    output_dir: str,
    source_url: str = "",
) -> None:
    """
    Append failed image entries to failed_images.txt.
    Uses APPEND mode so batch downloads accumulate instead of overwriting.
    Failed images keep their original URL in the project JSON.
    """
    if not failed:
        return
    target   = output_dir if output_dir and os.path.isdir(output_dir) else os.getcwd()
    log_path = os.path.join(target, "failed_images.txt")
    is_new   = not os.path.exists(log_path)

    with open(log_path, "a", encoding="utf-8") as f:
        if is_new:
            f.write("# Failed image downloads\n")
            f.write("# Note: Failed images keep their original external URL in the project JSON.\n")
            f.write("#       They will load normally when the original site is online.\n\n")
        f.write(f"# --- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        if source_url:
            f.write(f"# Source CYOA : {source_url}\n")
        f.write(f"# Count       : {len(failed)}\n")
        for item in failed:
            f.write(f"{item['url']}\t{item.get('error', '')}\n")
        f.write("\n")
    logger.warning(f"Failed images log: {log_path}")


def _write_youtube_skip_log(
    items: List[str],
    output_dir: str,
    source_url: str = "",
) -> None:
    """
    Append YouTube URLs (with source CYOA) to skipped_youtube_audio.txt.

    Uses APPEND mode — batch downloads accumulate entries instead of
    overwriting each other. The header/instructions block is written only
    once when the file does not yet exist.
    """
    if not items:
        return
    target   = output_dir if output_dir and os.path.isdir(output_dir) else os.getcwd()
    log_path = os.path.join(target, "skipped_youtube_audio.txt")
    is_new   = not os.path.exists(log_path)

    with open(log_path, "a", encoding="utf-8") as f:
        # Write explanatory header only on first creation
        if is_new:
            f.write("# Skipped YouTube audio URLs\n")
            f.write("# ============================================================\n")
            f.write("# WHY these cannot be made offline:\n")
            f.write("#   YouTube ToS prohibits downloading streams.\n")
            f.write("#   Streams use signed time-limited URLs (DASH/HLS) — no static file.\n")
            f.write("#   Old ICC viewer (Vue) creates YT.Player via JavaScript — no static\n")
            f.write("#   <iframe> in HTML, so our offline placeholder cannot replace it.\n")
            f.write("#\n")
            f.write("# WORKAROUND (manual, personal archival only):\n")
            f.write("#   1. pip install yt-dlp\n")
            f.write("#   2. yt-dlp -x --audio-format mp3 <youtube_url>\n")
            f.write("#   3. Place .mp3 in: <output_folder>/audio/\n")
            f.write("#   4. Edit project.json: bgmId -> 'audio/filename.mp3', useAudioURL -> true\n")
            f.write("#   Respect copyright — for personal use only.\n")
            f.write("# ============================================================\n\n")

        # Per-CYOA section — appended each time
        f.write(f"# --- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        if source_url:
            f.write(f"# Source CYOA : {source_url}\n")
        f.write(f"# Count       : {len(items)}\n")
        for url in items:
            f.write(url + "\n")
        f.write("\n")

    logger.warning(
        f"{len(items)} YouTube audio URL(s) kept as external links (cannot go offline). "
        f"See: {log_path}"
    )


def _find_ffmpeg() -> Optional[str]:
    """
    Find ffmpeg executable directory.
    Returns directory containing ffmpeg (for yt-dlp ffmpeg_location param),
    or None — in which case yt-dlp will try its own PATH search.
    """
    import shutil as _sh

    # 1. PATH check via shutil.which (works in most cases)
    exe = _sh.which("ffmpeg")
    if exe:
        return str(pathlib.Path(exe).parent)

    # 2. Try running ffmpeg directly — covers cases where PATH in os.environ
    #    is stale (Python launched before winget updated PATH in registry)
    try:
        import subprocess as _sp
        r = _sp.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5,
        )
        if r.returncode == 0:
            # ffmpeg works! find its actual path via 'where ffmpeg' (Windows)
            w = _sp.run(["where", "ffmpeg"], capture_output=True, timeout=5, text=True)
            if w.returncode == 0:
                found_path = w.stdout.strip().splitlines()[0].strip()
                if found_path:
                    return str(pathlib.Path(found_path).parent)
            return ""   # Works but can't determine path — let yt-dlp handle it
    except Exception as _ignored_exc:
        logger.debug("Ignored recoverable exception in _find_ffmpeg (line 13386): %s", _ignored_exc)

    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        home  = os.environ.get("USERPROFILE", str(pathlib.Path.home()))

        # 3. Read user PATH from Windows registry (updated by winget/installers
        #    even if current process's os.environ is stale)
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Environment", 0, winreg.KEY_READ
            ) as key:
                reg_path, _ = winreg.QueryValueEx(key, "Path")
            for reg_dir in reg_path.split(";"):
                reg_dir = reg_dir.strip().strip('"')
                if reg_dir and (pathlib.Path(reg_dir) / "ffmpeg.exe").exists():
                    logger.debug(f"ffmpeg found (registry PATH): {reg_dir}")
                    return reg_dir
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _find_ffmpeg (line 13407): %s", _ignored_exc)

        # Also check SYSTEM PATH from registry
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                0, winreg.KEY_READ
            ) as key:
                sys_path, _ = winreg.QueryValueEx(key, "Path")
            for reg_dir in sys_path.split(";"):
                reg_dir = reg_dir.strip().strip('"').replace("%SystemRoot%",
                    os.environ.get("SystemRoot", r"C:\Windows"))
                if reg_dir and (pathlib.Path(reg_dir) / "ffmpeg.exe").exists():
                    logger.debug(f"ffmpeg found (SYSTEM registry PATH): {reg_dir}")
                    return reg_dir
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in _find_ffmpeg (line 13425): %s", _ignored_exc)

        # 4. winget packages — RECURSIVE scan
        #    Gyan.FFmpeg nests: Packages/Gyan.FFmpeg_.../ffmpeg-8.1-full_build/bin/ffmpeg.exe
        if local:
            winget_base = pathlib.Path(local) / "Microsoft" / "WinGet" / "Packages"
            if winget_base.exists():
                for ffexe in sorted(winget_base.rglob("ffmpeg.exe")):
                    if ffexe.is_file():
                        logger.debug(f"ffmpeg found (winget): {ffexe.parent}")
                        return str(ffexe.parent)

        # 5. Fixed / common locations
        for path in [
            r"C:\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            r"C:\Program Files (x86)\ffmpeg\bin",
            r"C:\tools\ffmpeg\bin",
            r"D:\ffmpeg\bin",
            r"C:\ProgramData\chocolatey\bin",
            os.path.join(home, "scoop", "shims"),
            os.path.join(home, "scoop", "apps", "ffmpeg", "current", "bin"),
            os.path.join(local, "Programs", "yt-dlp"),
            os.path.join(local, "yt-dlp"),
            os.path.join(os.path.dirname(sys.executable), "Scripts"),
            os.path.join(home, "Downloads", "ffmpeg", "bin"),
            os.path.join(home, "Downloads", "ffmpeg-release-essentials", "bin"),
            os.path.join(home, "Downloads", "ffmpeg-master-latest-win64-gpl", "bin"),
            os.path.join(home, "Desktop", "ffmpeg", "bin"),
        ]:
            if (pathlib.Path(path) / "ffmpeg.exe").exists():
                logger.debug(f"ffmpeg found: {path}")
                return path

    else:
        for path in [
            "/usr/local/bin", "/opt/homebrew/bin",
            "/usr/bin", "/usr/local/sbin",
            str(pathlib.Path.home() / ".local" / "bin"),
        ]:
            if (pathlib.Path(path) / "ffmpeg").exists():
                return path

    return None   # yt-dlp will still try its own PATH lookup


def _patch_youtube_refs_in_json(
    project_str: str,
    yt_map: Dict[str, str],
) -> str:
    """
    Patch project JSON for offline audio.

    CRITICAL FIX: Eo(e,t,o) in ICC Plus app_B6d7tc9y.js reads e.useAudioURL
    where 'e' is the ROW OBJECT with setBgmIsOn=true, NOT the app root.
    Confirmed from source: `if(e.useAudioURL?...Eo(e,e.bgmId,0)...)`

    So we must add useAudioURL:true to EACH OBJECT that has bgmId, not just root.
    We do BOTH:
      1. Per-object: add useAudioURL:true to each dict that has bgmId (critical)
      2. Root level: add useAudioURL:true at root (belt-and-suspenders)
    """
    if not yt_map:
        return project_str

    import re as _re

    local_paths = set(yt_map.values())  # "audio/ID.mp3"
    local_ytids: Dict[str, str] = {}    # "dQw4w9W" → "audio/dQw4w9W.mp3"
    for yt_url, local_path in yt_map.items():
        vm = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', yt_url)
        if vm:
            local_ytids[vm.group(1)] = local_path

    try:
        obj = json.loads(project_str)
        patched_count = 0

        def _walk(node) -> None:
            nonlocal patched_count
            if isinstance(node, list):
                for item in node:
                    _walk(item)
            elif isinstance(node, dict):
                bgm = node.get("bgmId", "")
                if bgm:
                    new_path = None
                    if bgm in local_paths:
                        # Already patched path — just ensure useAudioURL is set
                        new_path = bgm
                    elif bgm in local_ytids:
                        # Raw YouTube video ID
                        new_path = local_ytids[bgm]
                    else:
                        # Full YouTube URL
                        vm = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', bgm)
                        if vm and vm.group(1) in local_ytids:
                            new_path = local_ytids[vm.group(1)]

                    if new_path:
                        node["bgmId"]        = new_path
                        node["useAudioURL"]  = True   # ← ON THE OBJECT (critical!)
                        patched_count += 1

                for v in node.values():
                    if isinstance(v, (dict, list)):
                        _walk(v)

        _walk(obj)

        # Also root-level for extra safety
        if "app" in obj and isinstance(obj["app"], dict):
            obj["app"]["useAudioURL"] = True
        else:
            obj["useAudioURL"] = True

        result = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        logger.info(
            f"YouTube patch: {patched_count} bgmId(s) → local MP3, "
            f"useAudioURL:true set per-object + root"
        )
        return result

    except Exception as _je:
        # JSON parse failed — string regex fallback
        logger.debug(f"Audio patch: JSON parse failed ({_je}), using regex fallback")
        patched = project_str
        any_patched = False
        for yt_url, local_path in yt_map.items():
            vm = _re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', yt_url)
            vid_id = vm.group(1) if vm else None
            before = patched
            # Replace bgmId AND inject useAudioURL right after it
            if vid_id:
                patched = _re.sub(
                    rf'"bgmId"\s*:\s*"{_re.escape(vid_id)}"',
                    f'"bgmId":"{local_path}","useAudioURL":true',
                    patched,
                )
            patched = _re.sub(
                rf'"bgmId"\s*:\s*"{_re.escape(yt_url)}"',
                f'"bgmId":"{local_path}","useAudioURL":true',
                patched,
            )
            # Also handle already-patched paths missing useAudioURL
            patched = _re.sub(
                rf'"bgmId"\s*:\s*"{_re.escape(local_path)}"(?!,\s*"useAudioURL")',
                f'"bgmId":"{local_path}","useAudioURL":true',
                patched,
            )
            if patched != before:
                any_patched = True

        # Root-level injection
        if any_patched and '"useAudioURL":true' not in patched[:200]:
            patched = patched.replace('{"version":', '{"useAudioURL":true,"version":', 1)

        return patched
