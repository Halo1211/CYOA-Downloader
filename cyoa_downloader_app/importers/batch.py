"""Batch import and batch-mode normalization helpers."""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import datetime
from urllib.parse import urlparse

from ..constants.modes import (
    _BATCH_VALID_MODES,
    _CYOAP_MODES,
    _FOLDER_MODES,
    _PURE_MODES,
    _WEBSITE_MODES,
)
from ..core.atomic_io import atomic_write_text, interprocess_file_lock
from ..core.cancellation import _raise_if_cancelled
from ..core.progress import DownloadCancelledError
from ..core.url_utils import is_probable_url
from ..logging_setup import logger
from ..project.parse import try_decode_bytes

_REMOTE_BATCH_MAX_BYTES = 32 * 1024 * 1024
_REMOTE_BATCH_CHUNK_BYTES = 1024 * 1024


def fetch_response(*args, **kwargs):
    # Late import keeps batch import lightweight during compatibility bootstrap.
    from ..network.fetch import fetch_response as _fetch_response
    return _fetch_response(*args, **kwargs)


def _safe_response_text(response):
    # Shared response decoder now lives with asset-scan/download helpers.
    from ..download.asset_scan import _safe_response_text as _decode_response_text
    return _decode_response_text(response)


def _read_remote_batch_text(response) -> str:
    """Read an untrusted remote list without materializing an oversized body."""
    _raise_if_cancelled()
    raw_length = response.headers.get("Content-Length")
    try:
        declared_length = int(raw_length) if raw_length not in (None, "") else None
    except (TypeError, ValueError):
        declared_length = None
    if declared_length is not None and declared_length > _REMOTE_BATCH_MAX_BYTES:
        raise ValueError("Remote batch list exceeds the 32 MiB import limit")

    payload = bytearray()
    for chunk in response.iter_content(chunk_size=_REMOTE_BATCH_CHUNK_BYTES):
        _raise_if_cancelled()
        if not chunk:
            continue
        if len(payload) + len(chunk) > _REMOTE_BATCH_MAX_BYTES:
            raise ValueError("Remote batch list exceeds the 32 MiB import limit")
        payload.extend(chunk)

    _raise_if_cancelled()

    ignored_encodings = {"iso-8859-1", "iso8859-1", "latin-1", "latin1", ""}
    encoding = str(getattr(response, "encoding", "") or "")
    preferred = encoding if encoding.lower() not in ignored_encodings else ""
    return try_decode_bytes(bytes(payload), preferred_encoding=preferred)

def _derive_mode_flags(mode: str) -> dict[str, object]:
    """Map a canonical batch mode key to run_download() keyword flags.

    Returns a dict with keys: zip, both, pure, website, website_zip, engine.
    Mirrors the CLI batch semantics (the reference behavior) so the GUI and
    CLI dispatch loops cannot drift. ``mode`` should already be canonical
    (post ``_normalize_batch_mode``); unknown values fall back to embed.
    """
    # Normalize separators/case defensively. Callers normally pass canonical
    # underscore keys, but accepting the dash form too (e.g. "icc-folder")
    # prevents a silent mis-dispatch if a future caller forgets to normalize.
    mode = (mode or "").strip().lower().replace("-", "_").replace(" ", "_")
    is_pure = mode in _PURE_MODES
    is_cyoap = mode in _CYOAP_MODES
    # website_output is True for any website-style mirror: ICC/website,
    # cyoap_vue, and pure_website. (run_download early-returns for pure_website
    # before website_output is consulted, but parity keeps it True to match CLI.)
    website_output = mode in _WEBSITE_MODES or is_cyoap or is_pure
    # ZIP unless an explicit *_folder mode was requested.
    website_zip = mode not in _FOLDER_MODES
    return {
        "zip": mode == "zip",
        "both": mode == "both",
        "pure": is_pure,
        "website": website_output,
        "website_zip": website_zip,
        "engine": "cyoap_vue" if is_cyoap else "standard",
    }

def _normalize_batch_mode(raw_mode: str, url: str = "") -> str:
    """Validate/normalize a batch-row mode string for both TXT and CSV/XLSX paths.

    Previously the TXT import path passed parts[2] through
    raw, while the CSV/XLSX path validated against the mode set and mapped the
    icc/icc_folder aliases. A TXT row like ``url | name | icc_folder`` therefore
    reached the queue with an unknown mode key, which the downstream
    ``mode in {...}`` dispatch silently coerced into a website ZIP instead of the
    requested folder. Both paths now share this normalizer.

    Returns the canonical internal mode key, or "" to mean "use GUI/CLI default".
    """
    if not raw_mode:
        return ""
    canon = raw_mode.strip().lower().replace("-", "_").replace(" ", "_")
    if not canon:
        return ""
    if canon in {"icc", "icc_zip"}:
        return "website_zip"
    if canon == "icc_folder":
        return "website_folder"
    if canon in _BATCH_VALID_MODES:
        return canon
    logger.warning(f"Unknown mode '{canon}' for {url or '(row)'} — using GUI/CLI default")
    return ""


def _import_csv_without_pandas(file_path: str) -> list[dict[str, str]]:
    """Read the portable CSV subset without requiring the optional pandas package."""
    items: list[dict[str, str]] = []
    with open(file_path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        columns = {str(column).strip().lower(): column for column in fieldnames}
        url_col = next((columns[name] for name in ("url", "link", "urls", "links") if name in columns), None)
        name_col = next((columns[name] for name in ("filename", "name", "output", "title", "file") if name in columns), None)
        mode_col = next((columns[name] for name in ("mode", "output_mode", "type") if name in columns), None)
        if url_col is None:
            logger.warning("Batch import: no URL/Link column found.")
            return items

        for row in reader:
            url = str(row.get(url_col) or "").strip()
            if not url or not is_probable_url(url):
                continue
            filename = str(row.get(name_col) or "").strip() if name_col else ""
            mode = _normalize_batch_mode(str(row.get(mode_col) or ""), url) if mode_col else ""
            items.append({"url": url, "filename": filename, "mode": mode})
    return items

def import_queue_items_from_file(file_path: str) -> list[dict[str, str]]:
    """
    Import batch URLs from txt/csv/xlsx/xls.

    Supported columns (case-insensitive):
      url / link / URL / Link          ← required
      filename / name / output / title ← optional, output filename
      mode                             ← optional, one of: embed zip both
                                         website_zip website_folder
                                         cyoap_vue_zip cyoap_vue_folder

    TXT format:
      https://example.com/cyoa/
      https://example.com/cyoa2/ | MyFilename
    """
    items: list[dict[str, str]] = []
    if not file_path or not os.path.isfile(file_path):
        return items

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".txt":
        # ``utf-8-sig`` also accepts plain UTF-8 and strips the BOM written by
        # Excel/Notepad, which otherwise invalidates the first URL.
        with open(file_path, "r", encoding="utf-8-sig", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    url      = parts[0] if len(parts) > 0 else ""
                    filename = parts[1] if len(parts) > 1 else ""
                    mode     = parts[2] if len(parts) > 2 else ""
                else:
                    url, filename, mode = line, "", ""
                if url and is_probable_url(url):
                    mode = _normalize_batch_mode(mode, url)
                    items.append({"url": url, "filename": filename, "mode": mode})
        return items

    try:
        import pandas as pd  # type: ignore
    except (ImportError, OSError) as e:
        if ext == ".csv":
            logger.info("pandas unavailable for CSV; using the standard-library CSV reader")
            try:
                return _import_csv_without_pandas(file_path)
            except (csv.Error, OSError, UnicodeError, TypeError, ValueError) as csv_error:
                logger.error(f"Failed reading CSV batch file {file_path}: {csv_error}")
        else:
            logger.warning(f"Batch import needs pandas for {ext}: {e}")
        return items

    try:
        if ext in {".xlsx", ".xls"}:
            df = pd.read_excel(file_path, dtype=str, keep_default_na=False)
        elif ext == ".csv":
            # Skip malformed rows instead of failing the whole
            # import. Previously a single row with the wrong column count made
            # pandas raise and the entire batch returned empty — even though the
            # plain-text path already tolerates bad lines. on_bad_lines='skip' is
            # pandas>=1.3; fall back to the legacy kwarg, then to a plain read.
            try:
                df = pd.read_csv(file_path, on_bad_lines="skip", dtype=str, keep_default_na=False)
            except TypeError:
                try:
                    df = pd.read_csv(file_path, error_bad_lines=False, dtype=str, keep_default_na=False)
                except TypeError:
                    df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
        else:
            logger.warning(f"Unsupported import file: {file_path}")
            return items
    except (ImportError, OSError, UnicodeError, TypeError, ValueError) as e:
        logger.error(f"Failed reading batch file {file_path}: {e}")
        return items

    url_col  = None
    name_col = None
    mode_col = None
    for col in df.columns:
        lowered = str(col).strip().lower()
        if lowered in {"url", "link", "urls", "links"} and url_col is None:
            url_col = col
        if lowered in {"filename", "name", "output", "title", "file"} and name_col is None:
            name_col = col
        if lowered in {"mode", "output_mode", "type"} and mode_col is None:
            mode_col = col

    if url_col is None:
        logger.warning("Batch import: no URL/Link column found.")
        return items

    for _, row in df.iterrows():
        url = "" if pd.isna(row[url_col]) else str(row[url_col]).strip()
        if not url or not is_probable_url(url):
            continue
        filename = ""
        if name_col is not None and not pd.isna(row[name_col]):
            filename = str(row[name_col]).strip()
        mode = ""
        if mode_col is not None and not pd.isna(row[mode_col]):
            mode = _normalize_batch_mode(str(row[mode_col]), url)
        items.append({"url": url, "filename": filename, "mode": mode})

    return items

def _google_sheet_csv_export_url(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if not m:
        return url
    # Share links commonly carry the sheet tab in the
    # fragment ("/edit#gid=456"). The old [?&] pattern missed it, silently
    # exporting gid=0 (first tab) instead of the tab the user selected.
    gid_match = re.search(r"[?&#]gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"

def import_queue_items_from_source(source: str) -> list[dict[str, str]]:
    source = (source or "").strip()
    if not source:
        return []
    if os.path.isfile(source):
        return import_queue_items_from_file(source)
    if not is_probable_url(source):
        return []
    url = _google_sheet_csv_export_url(source) if "docs.google.com/spreadsheets" in source else source
    r = None
    try:
        r = fetch_response(
            url,
            timeout=30,
            extra_headers={"User-Agent": "Mozilla/5.0"},
            as_bytes=False,
            stream=True,
        )
        if r is None:
            raise RuntimeError("request failed")
        text = _read_remote_batch_text(r)
    except DownloadCancelledError:
        raise
    except (AttributeError, OSError, RuntimeError, UnicodeError, TypeError, ValueError) as e:
        logger.error(f"Failed to import remote list: {e}")
        return []
    finally:
        if r is not None:
            try:
                r.close()
            except DownloadCancelledError:
                raise
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.debug("Could not close remote batch response: %s", exc)

    # A remote source is untrusted input. Keep an accidental HTML dump or a
    # giant generated sheet from freezing the GUI while materializing every
    # row, and contain csv.Error instead of crashing the import callback.
    try:
        # Remote TXT lists use the same pipe-separated format as local TXT.
        # Keep CSV parsing for URL-only lists and real comma-separated rows.
        if os.path.splitext(urlparse(url).path)[1].lower() == ".txt":
            rows = []
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    rows.append([field.strip() for field in line.split("|", 2)])
                else:
                    literal_url = is_probable_url(line)
                    parsed_line = urlparse(line) if literal_url else None
                    csv_row = next(csv.reader([line]), [])
                    csv_header = bool(
                        rows and any(field.strip().lower() in {"url", "link", "urls", "links"} for field in rows[0])
                    )
                    csv_mode = (
                        csv_row[2].strip().lower().replace("-", "_").replace(" ", "_")
                        if len(csv_row) > 2 else ""
                    )
                    # Keep the historical CSV-in-TXT format when a header or
                    # explicit mode identifies it. Otherwise commas in a URL's
                    # query/fragment belong to the URL, as in local TXT lists.
                    if (
                        not csv_header and csv_mode not in _BATCH_VALID_MODES
                        and literal_url
                        and ("," in parsed_line.query or "," in parsed_line.fragment)
                    ):
                        rows.append([line])
                    else:
                        rows.append(csv_row)
        else:
            rows = list(csv.reader(io.StringIO(text)))
    except (csv.Error, UnicodeError) as exc:
        logger.error(f"Failed parsing remote batch list: {exc}")
        return []
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    items: list[dict[str, str]] = []

    def add_item(url_value: str, filename_value: str = "", mode_value: str = "") -> None:
        url_value      = (url_value      or "").strip()
        filename_value = (filename_value or "").strip()
        if url_value.startswith("#"):
            return
        if not is_probable_url(url_value):
            return
        # Use the shared normalizer (introduced rev10) so the
        # remote CSV / Google Sheets path validates and maps modes identically to
        # the local TXT/CSV path. Previously this carried its own literal
        # `valid_modes` set + inline alias logic — a third copy that would drift
        # silently if the mode set ever grew (a new mode valid in files but
        # rejected in remote lists). Single source of truth: _BATCH_VALID_MODES.
        mode_norm = _normalize_batch_mode(mode_value, url_value)
        items.append({"url": url_value, "filename": filename_value, "mode": mode_norm})

    if any(h in {"url", "link", "urls", "links"} for h in header):
        url_idx  = next((i for i, h in enumerate(header) if h in {"url", "link", "urls", "links"}), 0)
        fn_idx   = next((i for i, h in enumerate(header) if h in {"filename", "name", "output", "title", "file"}), -1)
        mode_idx = next((i for i, h in enumerate(header) if h in {"mode", "output_mode", "type"}), -1)
        for row in rows[1:]:
            if not row:
                continue
            url_v  = row[url_idx]  if url_idx  < len(row) else ""
            fn_v   = row[fn_idx]   if 0 <= fn_idx  < len(row) else ""
            mode_v = row[mode_idx] if 0 <= mode_idx < len(row) else ""
            add_item(url_v, fn_v, mode_v)
    else:
        for row in rows:
            if not row:
                continue
            add_item(
                row[0] if len(row) > 0 else "",
                row[1] if len(row) > 1 else "",
                row[2] if len(row) > 2 else "",
            )
    return items


def export_queue_items_to_file(items: list[dict[str, str]], file_path: str) -> int:
    """Export queue rows in a format that :func:`import_queue_items_from_file` can read.

    CSV is the default-friendly format because it keeps URL, filename, and mode
    in separate columns.  TXT uses the existing ``url | filename | mode``
    syntax, so exported lists can also be edited in a plain text editor.
    Internal queue fields (for example ``_queue_id``) are deliberately omitted.

    Returns the number of rows written.
    """
    if not file_path:
        raise ValueError("An export file path is required")

    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        rows.append({
            "url": url,
            "filename": str(item.get("filename") or "").strip(),
            "mode": str(item.get("mode") or "auto").strip() or "auto",
        })

    ext = os.path.splitext(str(file_path))[1].lower()
    if ext == ".txt":
        lines = []
        for row in rows:
            fields = [row["url"]]
            if row["filename"] or row["mode"]:
                fields.append(row["filename"])
            if row["mode"]:
                fields.append(row["mode"])
            lines.append(" | ".join(fields))
        atomic_write_text(file_path, "".join(line + "\n" for line in lines))
    elif ext == ".csv":
        # utf-8-sig makes the exported file open cleanly in Excel while remaining
        # compatible with pandas and the existing CSV importer.
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=["url", "filename", "mode"])
        writer.writeheader()
        writer.writerows(rows)
        atomic_write_text(file_path, buffer.getvalue(), encoding="utf-8-sig")
    else:
        raise ValueError("Queue export supports .csv and .txt files only")

    return len(rows)

def write_failed_url_log(
    failed_items: list[dict[str, str]],
    output_dir: str,
    filename: str = "failed_urls.txt",
) -> str | None:
    """
    Append failed batch URLs to failed_urls.txt.
    Uses APPEND mode so multiple batch runs accumulate instead of overwriting.
    """
    if not failed_items:
        return None
    target_dir = output_dir if output_dir and os.path.isdir(output_dir) else os.getcwd()
    log_path   = os.path.join(target_dir, filename)
    with interprocess_file_lock(log_path):
        is_new = not os.path.exists(log_path) or os.path.getsize(log_path) == 0
        with open(log_path, "a", encoding="utf-8") as f:
            if is_new:
                f.write("# Failed batch URL downloads\n")
                f.write("# Format: url<TAB>error_message\n\n")
            f.write(f"# --- {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')} ({len(failed_items)} failed) ---\n")
            for item in failed_items:
                # Keep the tab-separated log structurally valid even when a
                # remote URL or exception text contains control characters.
                url = re.sub(r"[\r\n\t]+", " ", str(item.get("url", ""))).strip()
                err = re.sub(r"[\r\n\t]+", " ", str(item.get("error", ""))).strip()
                f.write(f"{url}\t{err}\n")
            f.write("\n")

    logger.info(f"Failed URL log saved: {log_path}")
    return log_path

__all__ = [
    '_derive_mode_flags',
    '_google_sheet_csv_export_url',
    '_normalize_batch_mode',
    'export_queue_items_to_file',
    'import_queue_items_from_file',
    'import_queue_items_from_source',
    'write_failed_url_log',
]
