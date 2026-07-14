"""Project payload parsing helpers.

Phase 14 moves the core project-payload parser out of ``legacy.py``.  These
functions are intentionally mechanical copies of the stabilized legacy bodies so
project detection, archive inspection, and JSON normalization keep the same
behavior while the compatibility facade continues to expose the old names.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from typing import Any, Optional, Tuple
from urllib.parse import unquote

from ..constants.assets import IMAGE_FIELDS
from ..core.archive import validate_zip_archive
from ..logging_setup import logger

try:
    import json5  # type: ignore
except Exception:  # pragma: no cover - optional dependency fallback
    json5 = None
# archive.org CYOA Manager catalog ZIP link matcher.
_ARCHIVE_ORG_CYOA_RE = re.compile(
    r'https://archive\.org/download/CYOAZipArchive/([^\s"\'<>]+\.zip)',
    re.IGNORECASE,
)


def try_decode_bytes(raw: bytes, preferred_encoding: str = "") -> str:
    """
    Decode bytes to str with correct encoding priority.

    UTF-8 is ALWAYS tried first — it's the correct encoding for 95%+ of web
    content. chardet/charset_normalizer are used only when UTF-8 fails.
    latin-1 / ISO-8859-1 / cp1006 variants are treated as last resort ONLY
    because they 'succeed' on any byte sequence (including UTF-8 Korean),
    producing mojibake that then gets double-encoded as %C3%AC%C2%8B... in URLs.
    """
    # ── 1. UTF-8 always first ────────────────────────────────────────────────
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as _ignored_exc:
        logger.debug("Ignored recoverable exception in try_decode_bytes (line 17397): %s", _ignored_exc)
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as _ignored_exc:
        logger.debug("Ignored recoverable exception in try_decode_bytes (line 17401): %s", _ignored_exc)

    # ── 2. Explicit preferred (only if it's NOT a latin-1 variant) ──────────
    _LATIN_VARIANTS = {"latin-1", "iso-8859-1", "iso8859-1", "windows-1252",
                       "cp1252", "cp1006", "ascii"}
    if preferred_encoding and preferred_encoding.lower() not in _LATIN_VARIANTS:
        try:
            return raw.decode(preferred_encoding)
        except (UnicodeDecodeError, LookupError) as _ignored_exc:
            logger.debug("Ignored recoverable exception in try_decode_bytes (line 17410): %s", _ignored_exc)

    # ── 3. chardet / charset_normalizer for genuinely non-UTF-8 content ─────
    if any(b > 0x7f for b in raw[:512]):
        detected = None
        try:
            import chardet
            result = chardet.detect(raw[:4096])
            if result and result.get("confidence", 0) > 0.75:
                enc = result.get("encoding", "")
                if enc and enc.lower() not in _LATIN_VARIANTS:
                    detected = enc
        except ImportError as _ignored_exc:
            logger.debug("Ignored recoverable exception in try_decode_bytes (line 17423): %s", _ignored_exc)
        if not detected:
            try:
                from charset_normalizer import from_bytes
                best = from_bytes(raw[:4096]).best()
                if best and str(best.encoding).lower() not in _LATIN_VARIANTS:
                    detected = best.encoding
            except ImportError as _ignored_exc:
                logger.debug("Ignored recoverable exception in try_decode_bytes (line 17431): %s", _ignored_exc)
        if detected:
            try:
                return raw.decode(detected)
            except (UnicodeDecodeError, LookupError) as _ignored_exc:
                logger.debug("Ignored recoverable exception in try_decode_bytes (line 17436): %s", _ignored_exc)

    # ── 4. East-Asian encodings (legacy sites) ───────────────────────────────
    for enc in ["shift-jis", "euc-kr", "gb2312", "big5"]:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError) as _ignored_exc:
            logger.debug("Ignored recoverable exception in try_decode_bytes (line 17443): %s", _ignored_exc)

    # ── 5. latin-1 as absolute last resort (always succeeds, may be wrong) ───
    return raw.decode("latin-1")

def is_zip_bytes(raw: bytes) -> bool:
    return len(raw) >= 4 and raw[:4] == b"PK\x03\x04"

def looks_like_project_object(obj: dict) -> bool:
    if not isinstance(obj, dict):
        return False

    # Standard ICC payloads are often wrapped as {"app": {...}}. Score both
    # the envelope and its app object while preserving the full wrapper.
    candidates = [obj]
    if isinstance(obj.get("app"), dict):
        candidates.append(obj["app"])
    return any(_project_object_score(candidate) >= 4 for candidate in candidates)


def _project_object_score(obj: dict) -> int:
    score = 0
    for key in IMAGE_FIELDS:
        if key in obj:
            score += 3
    for key in [
        "rows", "backpack", "cards", "sections", "scenes", "pages", "tabs", "choices",
        "name", "title", "author", "theme", "meta", "character", "points",
        "imageSets", "templates", "objects", "groups", "words", "variables",
        "defaultRowTitle", "defaultChoiceTitle", "pointTypes", "chapters", "version",
    ]:
        if key in obj:
            score += 1

    if isinstance(obj.get("rows"), list):
        score += 3
    if isinstance(obj.get("backpack"), list):
        score += 2
    if isinstance(obj.get("groups"), list):
        score += 1

    return score

def looks_like_project_payload(text: str) -> bool:
    if not text:
        return False

    parsed = parse_jsonish_text(text)
    standalone = text.strip()
    # Do not classify a standalone JavaScript object with expressions such as
    # ``!0``, function calls, or spread references as a project payload. Such
    # fragments are often viewer/editor state, not serializable project data;
    # accepting them later would write an invalid project.json.
    if standalone.startswith("{") and standalone.endswith("}") and parsed is None:
        return False
    if isinstance(parsed, dict):
        return looks_like_project_object(parsed)

    sample = text[:300000]
    lowered = sample.lower()

    score = 0
    for key in IMAGE_FIELDS:
        if re.search(
            rf'(?<![A-Za-z0-9_$])["\\\']?{re.escape(key)}["\\\']?\s*:',
            sample,
            flags=re.IGNORECASE,
        ):
            score += 3
    for key in [
        "rows", "backpack", "cards", "sections", "scenes", "pages", "tabs", "choices",
        "name", "title", "author", "theme", "meta", "character", "points",
        "imageSets", "templates", "objects", "groups", "words", "variables",
    ]:
        if re.search(
            rf'(?<![A-Za-z0-9_$])["\\\']?{re.escape(key)}["\\\']?\s*:',
            sample,
            flags=re.IGNORECASE,
        ):
            score += 1

    if score >= 4:
        return True

    if sample.strip().startswith("{") and sample.strip().endswith("}") and ('"image"' in lowered or '"rows"' in lowered or '"backpack"' in lowered):
        return True

    return False

def extract_balanced_brace_block(text: str, start_idx: int) -> str:
    if start_idx < 0 or start_idx >= len(text) or text[start_idx] != "{":
        return ""
    depth = 0
    in_string = False
    string_char = ""
    escaped = False

    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == string_char:
                in_string = False
            continue

        if ch in {'"', "'"}:
            in_string = True
            string_char = ch
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx:idx + 1]
    return ""

def extract_embedded_project_from_js(js_text: str) -> Optional[str]:
    # ── Fast-path: Vuex },getters split (original downloader technique) ──
    # ICC Plus old viewer embeds project as: Store({state:{app:{...}},getters:...})
    # This is faster than balanced-brace for the exact Vuex pattern.
    for start_marker, end_marker in [
        ("Store({state:{app:", "},getters"),
        ("state:{app:",        "},getters"),
    ]:
        if start_marker in js_text and end_marker in js_text:
            try:
                candidate = js_text.split(start_marker)[-1].split(end_marker)[0]
                # Find the opening brace and try to extract a full object
                brace_idx = candidate.find("{")
                if brace_idx != -1:
                    block = extract_balanced_brace_block(candidate, brace_idx)
                    if block and looks_like_project_payload(block):
                        logger.info(f"Found embedded project payload via Vuex split: {start_marker[:30]}")
                        return block
            except (IndexError, Exception) as _ignored_exc:
                logger.debug("Ignored recoverable exception in extract_embedded_project_from_js (line 17566): %s", _ignored_exc)

    markers = [
        "Store({state:{app:",
        "state:{app:",
        "__INITIAL_STATE__=",
        "__INITIAL_STATE__ =",
        "window.__INITIAL_STATE__=",
        "window.__INITIAL_STATE__ =",
        "window.__APP__=",
        "window.__APP__ =",
        "window.__NUXT__=",
        "window.__NUXT__ =",
        "app:{",
        '"app":{',
        "project:{",
        '"project":{',
    ]

    for marker in markers:
        start = 0
        while True:
            idx = js_text.find(marker, start)
            if idx == -1:
                break
            brace_idx = js_text.find("{", idx)
            if brace_idx == -1:
                break
            block = extract_balanced_brace_block(js_text, brace_idx)
            if block and looks_like_project_payload(block):
                logger.info(f"Found embedded project payload via marker: {marker[:40]}")
                return block
            start = idx + len(marker)

    # Modern CYOA Plus/Vite builds can keep the complete project in a
    # reactive wrapper rather than exposing a plain ``app:{...}`` property.
    # For example, the Valentine's build contains ``app=i({...})`` where
    # ``i`` is the minified reactive-state factory.  The object itself is
    # valid JSON, so extract it before the broad fallback patterns below.
    # Restrict this pass to variables named app/project: generic ``x=i({...})``
    # assignments are common in bundles and can produce false positives.
    reactive_assignment = re.compile(
        r'\b(?:app|project)\s*=\s*[A-Za-z_$][\w$]*\s*\(\s*(\{)',
        re.IGNORECASE,
    )
    for match in reactive_assignment.finditer(js_text):
        block = extract_balanced_brace_block(js_text, match.start(1))
        if block and looks_like_project_payload(block):
            logger.info("Found embedded project payload via reactive app/project wrapper")
            return block

    fallback_patterns = [
        r'(?:app|project)\s*:\s*\{',
        r'"(?:app|project)"\s*:\s*\{',
        r'(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*\{',
        r'return\s*\{',
    ]
    for pattern in fallback_patterns:
        for m in re.finditer(pattern, js_text):
            brace_idx = js_text.find("{", m.start())
            if brace_idx == -1:
                continue
            block = extract_balanced_brace_block(js_text, brace_idx)
            if block and looks_like_project_payload(block):
                logger.info(f"Found embedded project payload via regex: {pattern}")
                return block

    keyword_patterns = [
        r'["\\\']rows["\\\']\s*:',
        r'\brows\s*:',
        r'["\\\']backpack["\\\']\s*:',
        r'\bbackpack\s*:',
        r'["\\\']groups["\\\']\s*:',
        r'["\\\']words["\\\']\s*:',
    ]

    for pattern in keyword_patterns:
        for m in re.finditer(pattern, js_text):
            scan_start = max(0, m.start() - 250000)
            brace_idx = js_text.rfind("{", scan_start, m.start())
            while brace_idx != -1:
                block = extract_balanced_brace_block(js_text, brace_idx)
                if block and len(block) > 100 and looks_like_project_payload(block):
                    logger.info(f"Found embedded project payload near keyword: {pattern}")
                    return block
                brace_idx = js_text.rfind("{", scan_start, brace_idx)

    return None

def extract_project_from_archive_bytes(raw: bytes, source_url: str, depth: int = 0) -> Optional[str]:
    if depth > 2 or not is_zip_bytes(raw):
        return None

    # v46: reject traversal, excessive member counts, total expansion, and
    # suspicious compression ratios before reading any archive member.
    try:
        validate_zip_archive(
            raw,
            max_members=10000,
            max_total_size=1024 * 1024 * 1024,
            max_member_size=512 * 1024 * 1024,
            max_ratio=250.0,
        )
    except (ValueError, zipfile.BadZipFile) as exc:
        logger.warning(f"Unsafe or invalid archive rejected from {source_url}: {exc}")
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                return None

            def sort_key(name: str) -> Tuple[int, int, str]:
                lname = name.lower()
                ext = os.path.splitext(lname)[1]
                priority = 99
                if lname.endswith("project.json"):
                    priority = 0
                elif "project" in lname and ext == ".json":
                    priority = 1
                elif ext == ".json":
                    priority = 2
                elif "project" in lname and ext == ".txt":
                    priority = 3
                elif ext == ".txt":
                    priority = 4
                elif ext == ".zip":
                    priority = 5
                return (priority, len(lname), lname)

            for member in sorted(names, key=sort_key):
                try:
                    # v7.5.6 hardening: cap decompressed member size for
                    # remotely downloaded archives (zip-bomb guard). Read via
                    # zf.open with a hard byte ceiling instead of trusting the
                    # declared file_size, which can lie.
                    _MAX_MEMBER = 512 * 1024 * 1024  # 512 MB
                    try:
                        if zf.getinfo(member).file_size > _MAX_MEMBER:
                            logger.warning(f"Archive member too large, skipped: {member}")
                            continue
                    except Exception as _ignored_exc:
                        logger.debug("Ignored recoverable exception in extract_project_from_archive_bytes (line 17693): %s", _ignored_exc)
                    with zf.open(member) as _fh:
                        member_raw = _fh.read(_MAX_MEMBER + 1)
                    if len(member_raw) > _MAX_MEMBER:
                        logger.warning(f"Archive member exceeded decompression cap, skipped: {member}")
                        continue
                except Exception as e:
                    logger.warning(f"Failed to read archive member {member}: {e}")
                    continue

                logger.info(f"Checking archive member: {member}")

                if is_zip_bytes(member_raw):
                    extracted = extract_project_from_archive_bytes(member_raw, f"{source_url}!/{member}", depth + 1)
                    if extracted:
                        logger.info(f"Found project payload inside nested archive: {member}")
                        return extracted

                text = try_decode_bytes(member_raw)
                project_text = extract_project_text_from_payload(text)
                if project_text:
                    logger.info(f"Found project payload inside archive member: {member}")
                    return project_text
    except zipfile.BadZipFile:
        return None
    except Exception as e:
        logger.warning(f"Failed to inspect archive from {source_url}: {e}")
        return None

    return None

def extract_json_like_block(text: str) -> str:
    start = text.find("{")
    end   = text.rfind("}") + 1
    return text[start:end] if start != -1 and end > start else ""

def parse_jsonish_text(text: str) -> Optional[dict]:
    if not text:
        return None

    candidates = [text.strip()]
    trimmed = extract_json_like_block(text)
    if trimmed and trimmed not in candidates:
        candidates.append(trimmed)

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as _ignored_exc:
            logger.debug("Ignored recoverable exception in parse_jsonish_text (line 17739): %s", _ignored_exc)
        if json5 is not None:
            try:
                return json5.loads(candidate)
            except Exception as _ignored_exc:
                logger.debug("Ignored recoverable exception in parse_jsonish_text (line 17744): %s", _ignored_exc)
    return None

def normalize_project_payload_text(text: str) -> Optional[str]:
    if not text:
        return None

    obj = parse_jsonish_text(text)
    if isinstance(obj, dict) and looks_like_project_object(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    cleaned = extract_json_like_block(text)
    if cleaned:
        obj = parse_jsonish_text(cleaned)
        if isinstance(obj, dict) and looks_like_project_object(obj):
            return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    # Returning an unparseable JavaScript fragment here creates a project.json
    # that the viewer and package verifier cannot read. JSON/JSON5 payloads are
    # normalized by the branches above; everything else must be rejected.
    return None

def extract_project_text_from_payload(text: str) -> Optional[str]:
    if not text:
        return None

    normalized = normalize_project_payload_text(text)
    if normalized:
        return normalized

    embedded = extract_embedded_project_from_js(text)
    if embedded:
        normalized = normalize_project_payload_text(embedded)
        return normalized or embedded

    return None

def _extract_website_from_archive_zip_name(zip_filename: str) -> Optional[str]:
    """
    Convert archive.org CYOA zip filename back to the original website URL.
    Format: Name.[YYYY-MM-DD].https~~~site.com~path~subpath.zip
    → https://site.com/path/subpath
    """
    from urllib.parse import unquote
    fname = unquote(zip_filename)
    # Accept http~~~ too: "~~~" is the archive's encoding
    # of "://" (documented below), which is scheme-agnostic by construction —
    # http-only sites previously returned None and lost URL recovery.
    m = re.search(r'\.(https?~~~[^.]+(?:\.[^.]+)*?)\.zip$', fname, re.IGNORECASE)
    if not m:
        return None
    url_part = m.group(1)
    # https~~~site.com~path → https://site.com/path
    url = url_part.replace("~~~", "://").replace("~", "/")
    if not url.lower().startswith("http"):  # regex is IGNORECASE; case-sensitive check double-prefixed HTTPS~~~ names
        url = "https://" + url
    return url.rstrip("/") + "/"

__all__ = [
    "try_decode_bytes",
    "is_zip_bytes",
    "looks_like_project_object",
    "looks_like_project_payload",
    "extract_balanced_brace_block",
    "extract_embedded_project_from_js",
    "extract_project_from_archive_bytes",
    "parse_jsonish_text",
    "normalize_project_payload_text",
    "extract_project_text_from_payload",
    "extract_json_like_block",
    "_extract_website_from_archive_zip_name",
]
