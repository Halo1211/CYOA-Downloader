"""Path safety helpers."""

from __future__ import annotations

import os
import re
import shutil
from typing import List
from urllib.parse import unquote

from ..logging_setup import logger

def _is_windows_reserved_basename(name: str) -> bool:
    """True if *name*'s base (before first dot) is a Windows reserved device
    name (CON, PRN, AUX, NUL, COM1-9, LPT1-9), case-insensitive.

    Extracted so both the output-filename guard (rev8-16)
    and the asset-path guard below share one definition.
    """
    base = str(name or "").split(".", 1)[0]
    return bool(re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", base))

def _safe_rel_path(value: str, fallback: str = "asset") -> str:
    """Return a sanitized relative path safe for writing inside an output folder."""
    raw = unquote(str(value or "")).replace("\\", "/")
    raw = raw.split("?", 1)[0].split("#", 1)[0].replace("\x00", "")
    parts: List[str] = []
    for part in raw.split("/"):
        part = part.strip()
        if not part or part in {".", ".."}:
            continue
        # Prevent Windows drive names and illegal filename characters.
        part = re.sub(r'^[A-Za-z]:', "_", part)
        part = re.sub(r'[<>:"|?*\x00-\x1f\x7f]', "_", part)
        part = part.strip(". ")
        # Neutralize Windows reserved device names per
        # segment. rev8-16 fixed this for the output *filename* builder, but
        # asset paths flow through here instead — a remote asset URL ending in
        # e.g. /nul.png or /CON/icon.svg would write to a Windows device handle
        # rather than a file, silently failing on Windows (invisible on Linux).
        # Prefix "_" so the on-disk segment is legal everywhere yet recognizable.
        if part and _is_windows_reserved_basename(part):
            part = "_" + part
        if part:
            parts.append(part)
    return "/".join(parts) or fallback

def _safe_join(root: str, rel_path: str, fallback: str = "asset") -> str:
    """Join root + rel_path after sanitizing URL-derived asset paths."""
    root_abs = os.path.abspath(root or os.getcwd())
    safe_rel = _safe_rel_path(rel_path, fallback=fallback)
    target = os.path.abspath(os.path.join(root_abs, *safe_rel.split("/")))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise ValueError(f"Unsafe output path rejected: {rel_path!r}")
    return target

def _safe_archive_rel_path(member: str) -> str:
    """Validate archive member path strictly. Reject traversal instead of normalizing it."""
    # This validator's contract is reject-not-normalize
    # (unlike the sanitizing _safe_rel_path). A NUL byte in a zip member name
    # is malformed/hostile; the old top-level `.replace("\x00","")` silently
    # normalized e.g. "foo\x00bar" into the innocuous "foobar" and accepted it,
    # making the per-segment NUL guard below dead code. Reject on NUL instead.
    raw = unquote(str(member or "")).replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"Unsafe archive path rejected: {member!r}")
    parts: List[str] = []
    for part in raw.split("/"):
        if part in {"", ".", ".."} or part.strip() != part:
            raise ValueError(f"Unsafe archive path rejected: {member!r}")
        if re.match(r"^[A-Za-z]:", part) or any(ch in part for ch in "<>:\"|?*\x00"):
            raise ValueError(f"Unsafe archive path rejected: {member!r}")
        parts.append(part)
    if not parts:
        raise ValueError(f"Unsafe archive path rejected: {member!r}")
    return "/".join(parts)

def _safe_archive_join(root: str, member: str) -> str:
    """Join archive member to root only after strict archive-member validation."""
    root_abs = os.path.abspath(root or os.getcwd())
    rel = _safe_archive_rel_path(member)
    target = os.path.abspath(os.path.join(root_abs, *rel.split("/")))
    if target == root_abs or not target.startswith(root_abs + os.sep):
        raise ValueError(f"Unsafe archive path rejected: {member!r}")
    return target

def _copytree_merge_safe(src_dir: str, dst_dir: str, *, label: str = "assets") -> int:
    """Copy src_dir into dst_dir without deleting existing user/output folders."""
    if not src_dir or not os.path.isdir(src_dir):
        return 0
    count = 0
    for root, _dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        if rel_root == ".":
            rel_root = ""
        for name in files:
            src_file = os.path.join(root, name)
            rel = os.path.join(rel_root, name).replace("\\", "/")
            dst_file = _safe_join(dst_dir, rel, fallback=name or "asset")
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            try:
                shutil.copy2(src_file, dst_file)
                count += 1
            except Exception as e:
                logger.debug(f"Copy {label} failed: {src_file} → {dst_file}: {e}")
    return count

__all__ = ['_is_windows_reserved_basename', '_safe_rel_path', '_safe_join', '_safe_archive_rel_path', '_safe_archive_join', '_copytree_merge_safe']
