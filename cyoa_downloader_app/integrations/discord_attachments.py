"""Discord attachment recovery used by the main CYOA download pipeline.

This module is deliberately self-contained inside the downloader package.  It
does not use a Discord SDK: the small REST surface we need is implemented with
the Python standard library so the normal application install remains the
single dependency boundary.

The normal application path is:

1. download the CDN URL directly (old, unsigned attachment URLs still work);
2. if Discord returns an expired/invalid URL response, refresh the URL in
   batches through ``POST /attachments/refresh-urls``;
3. retry the normal image download, whose existing pipeline localizes the
   project reference to ``images/...``.

The standalone JSON helpers at the bottom are retained for API compatibility,
but the GUI and CLI use this module automatically during ordinary downloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


DEFAULT_API_VERSION = "10"
DEFAULT_API_ROOT = "https://discord.com/api"
DEFAULT_USER_AGENT = "DiscordBot (CYOA Downloader, 1.0)"
DISCORD_ATTACHMENT_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
DISCORD_REFRESH_BATCH_SIZE = 50
DISCORD_REFRESH_PATH = "/attachments/refresh-urls"
REFRESHABLE_HTTP_STATUSES = {401, 403, 404, 410}
DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024


class DiscordAttachmentError(RuntimeError):
    """A recoverable or user-facing Discord attachment error."""


def discord_recovery_enabled() -> bool:
    """Return whether automatic refresh is enabled for this process."""

    disabled = os.environ.get("CYOA_DISABLE_DISCORD_REFRESH", "").strip().lower()
    if disabled in {"1", "true", "yes", "on"}:
        return False
    return True


def resolve_discord_bot_token(explicit: str = "") -> str:
    """Resolve an explicit/process token, then the plain settings.json token."""

    token = str(explicit or "").strip()
    if token:
        return token
    for variable in ("CYOA_DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN"):
        token = os.environ.get(variable, "").strip()
        if token:
            return token
    try:
        from ..config.settings import _load_settings

        settings = _load_settings()
        return str(settings.get("discord_bot_token", "") or "").strip()
    except Exception:
        return ""


@dataclass(frozen=True)
class DownloadAttempt:
    ok: bool
    status: int | None = None
    error: str = ""


@dataclass
class DiscordRunSummary:
    discovered: int = 0
    downloaded: int = 0
    skipped: int = 0
    refreshed: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)
    output_json: str = ""
    image_dir: str = ""


def is_discord_attachment_url(value: str) -> bool:
    """Return true only for HTTPS Discord attachment CDN URLs.

    This strict allowlist is intentional: a URL found in a project file must
    not turn the downloader into a general-purpose SSRF fetcher.
    """

    try:
        parsed = urlsplit(str(value).strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return (
        parsed.scheme.lower() == "https"
        and host in DISCORD_ATTACHMENT_HOSTS
        and parsed.path.lower().startswith("/attachments/")
    )


def collect_discord_attachment_urls(value: Any) -> list[str]:
    """Collect unique Discord attachment URLs from any JSON-compatible value."""

    found: list[str] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, str):
            candidate = node.strip()
            if candidate and candidate not in seen and is_discord_attachment_url(candidate):
                seen.add(candidate)
                found.append(candidate)
        elif isinstance(node, Mapping):
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return found


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _auth_header(token: str) -> str:
    clean = str(token or "").strip()
    if not clean:
        return ""
    if re.match(r"^(Bot|Bearer)\s+", clean, flags=re.IGNORECASE):
        return clean
    return f"Bot {clean}"


def _retry_after(headers: Mapping[str, str], body: bytes = b"") -> float:
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw:
        try:
            return max(0.0, min(60.0, float(raw)))
        except ValueError:
            pass
    try:
        payload = json.loads(body.decode("utf-8"))
        return max(0.0, min(60.0, float(payload.get("retry_after", 1))))
    except (ValueError, TypeError, AttributeError):
        return 1.0


def _safe_filename(value: str) -> str:
    """Make a URL-derived filename safe on Windows, macOS, and Linux."""

    name = unquote(str(value or "")).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f\x7f]", "_", name).strip(". ")
    if not name:
        name = "attachment.bin"
    if name.upper().split(".", 1)[0] in {
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
        "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3",
        "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }:
        name = "_" + name
    return name[:180]


def _canonical_attachment_key(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"


def _output_filename(url: str) -> str:
    parsed = urlsplit(url)
    original = _safe_filename(Path(parsed.path).name)
    digest = hashlib.sha256(_canonical_attachment_key(url).encode("utf-8")).hexdigest()[:12]
    return f"discord_{digest}_{original}"


def _local_path_for(url: str, image_dir: Path) -> Path:
    return image_dir / _output_filename(url)


class DiscordAttachmentClient:
    """Small REST client for URL refresh and CDN downloads."""

    def __init__(
        self,
        token: str = "",
        *,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = 60.0,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.token = str(token or "").strip()
        self.api_root = f"{DEFAULT_API_ROOT.rstrip('/')}/v{api_version.strip('vV') or DEFAULT_API_VERSION}"
        self.timeout = max(1.0, float(timeout))
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.user_agent = user_agent

    def _request_json(
        self,
        url: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.token:
            raise DiscordAttachmentError(
                "Discord bot token dibutuhkan untuk me-refresh URL yang sudah kedaluwarsa."
            )
        body = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Authorization": _auth_header(self.token),
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            request = Request(url, data=body, headers=headers, method=method)
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read()
                    if response.status < 200 or response.status >= 300:
                        raise DiscordAttachmentError(f"Discord API HTTP {response.status}")
                    parsed = json.loads(raw.decode("utf-8"))
                    if not isinstance(parsed, dict):
                        raise DiscordAttachmentError("Discord API returned a non-object response")
                    return parsed
            except HTTPError as error:
                raw = error.read()
                if error.code == 429 and attempt < 2:
                    time.sleep(_retry_after(error.headers, raw))
                    continue
                detail = raw.decode("utf-8", errors="replace")[:300].strip()
                suffix = f": {detail}" if detail else ""
                raise DiscordAttachmentError(f"Discord API HTTP {error.code}{suffix}") from error
            except (URLError, TimeoutError, OSError) as error:
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise DiscordAttachmentError(f"Discord API network error: {error}") from error
            except json.JSONDecodeError as error:
                raise DiscordAttachmentError("Discord API returned invalid JSON") from error
        raise DiscordAttachmentError("Discord API request failed after retries")

    def refresh_urls(self, urls: list[str]) -> dict[str, str]:
        """Return ``original -> refreshed`` URLs, preserving unmatched inputs."""

        if not urls:
            return {}
        refreshed: dict[str, str] = {}
        endpoint = self.api_root + DISCORD_REFRESH_PATH
        for batch in _chunks(urls, DISCORD_REFRESH_BATCH_SIZE):
            payload = self._request_json(endpoint, method="POST", payload={"attachment_urls": batch})
            rows = payload.get("refreshed_urls", [])
            if not isinstance(rows, list):
                raise DiscordAttachmentError("Discord API returned an invalid refreshed_urls value")
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                original = str(row.get("original", ""))
                refreshed_url = str(row.get("refreshed", ""))
                if original in batch and is_discord_attachment_url(refreshed_url):
                    refreshed[original] = refreshed_url
        return refreshed

    def validate_token(self) -> dict[str, Any]:
        """Validate the bot token with Discord's documented ``/users/@me`` endpoint."""

        return self._request_json(self.api_root + "/users/@me", method="GET")

    def download(self, url: str, destination: Path, *, overwrite: bool = False) -> DownloadAttempt:
        """Stream one attachment to an atomic destination file."""

        if not is_discord_attachment_url(url):
            return DownloadAttempt(False, error="URL is not a supported Discord attachment URL")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and not overwrite:
            return DownloadAttempt(True)

        request = Request(
            url,
            headers={
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "User-Agent": self.user_agent,
            },
        )
        temporary_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    return DownloadAttempt(False, status=response.status, error=f"HTTP {response.status}")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > self.max_file_bytes:
                            return DownloadAttempt(False, status=response.status, error="file exceeds max size")
                    except ValueError:
                        pass
                received = 0
                with open(temporary_path, "wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > self.max_file_bytes:
                            raise DiscordAttachmentError("file exceeds max size")
                        output.write(chunk)
                os.replace(temporary_path, destination)
                return DownloadAttempt(True, status=response.status)
        except HTTPError as error:
            return DownloadAttempt(False, status=error.code, error=f"HTTP {error.code}")
        except (URLError, TimeoutError, OSError, DiscordAttachmentError) as error:
            return DownloadAttempt(False, error=str(error))
        finally:
            try:
                if temporary_path.exists():
                    temporary_path.unlink()
            except OSError:
                pass


def _replace_urls(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_urls(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_urls(item, replacements) for key, item in value.items()}
    return value


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary_name, path)
    finally:
        try:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        except OSError:
            pass


def _default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_discord.json")


def run_discord_json(
    input_json: str | os.PathLike[str],
    *,
    output_json: str | os.PathLike[str] | None = None,
    image_dir: str | os.PathLike[str] | None = None,
    token: str = "",
    refresh_only: bool = False,
    overwrite: bool = False,
    workers: int = 4,
    timeout: float = 60.0,
    api_version: str = DEFAULT_API_VERSION,
) -> DiscordRunSummary:
    """Recover Discord attachment URLs in a JSON project and write a copy."""

    input_path = Path(input_json).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input JSON not found: {input_path}")
    with input_path.open("r", encoding="utf-8") as source:
        data = json.load(source)

    output_path = Path(output_json).expanduser().resolve() if output_json else _default_output_path(input_path)
    if output_path == input_path:
        raise ValueError("Output JSON harus berbeda dari input JSON agar file asli tetap aman")
    target_dir = (
        Path(image_dir).expanduser().resolve()
        if image_dir
        else output_path.parent / "images"
    )
    urls = collect_discord_attachment_urls(data)
    summary = DiscordRunSummary(discovered=len(urls), output_json=str(output_path), image_dir=str(target_dir))
    if not urls:
        _write_json_atomic(output_path, data)
        return summary

    client = DiscordAttachmentClient(token, api_version=api_version, timeout=timeout)
    replacements: dict[str, str] = {}
    refreshed_urls: dict[str, str] = {}

    if refresh_only:
        refreshed_urls = client.refresh_urls(urls)
        replacements.update(refreshed_urls)
        summary.refreshed = len(refreshed_urls)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        attempts: dict[str, DownloadAttempt] = {}
        workers = max(1, min(32, int(workers)))

        def download_one(source_url: str) -> tuple[str, DownloadAttempt]:
            destination = _local_path_for(source_url, target_dir)
            return source_url, client.download(source_url, destination, overwrite=overwrite)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = [pool.submit(download_one, url) for url in urls]
            for future in as_completed(pending):
                source_url, attempt = future.result()
                attempts[source_url] = attempt

        needs_refresh = [
            url for url in urls
            if not attempts[url].ok and attempts[url].status in REFRESHABLE_HTTP_STATUSES
        ]
        if needs_refresh:
            if token:
                refreshed_urls = client.refresh_urls(needs_refresh)
                summary.refreshed = len(refreshed_urls)
            for source_url, refreshed_url in refreshed_urls.items():
                destination = _local_path_for(source_url, target_dir)
                retry = client.download(refreshed_url, destination, overwrite=overwrite)
                if retry.ok:
                    attempts[source_url] = retry
                    replacements[source_url] = os.path.relpath(destination, output_path.parent).replace("\\", "/")

        for source_url in urls:
            attempt = attempts[source_url]
            destination = _local_path_for(source_url, target_dir)
            if attempt.ok:
                replacements.setdefault(
                    source_url,
                    os.path.relpath(destination, output_path.parent).replace("\\", "/"),
                )
                if destination.exists() and attempt.status is None:
                    summary.skipped += 1
                else:
                    summary.downloaded += 1
            else:
                summary.failed += 1
                summary.failures.append(f"{source_url} ({attempt.error or attempt.status or 'unknown error'})")

    _write_json_atomic(output_path, _replace_urls(data, replacements))
    return summary


__all__ = [
    "DiscordAttachmentClient",
    "DiscordAttachmentError",
    "DiscordRunSummary",
    "collect_discord_attachment_urls",
    "discord_recovery_enabled",
    "is_discord_attachment_url",
    "resolve_discord_bot_token",
    "run_discord_json",
]
