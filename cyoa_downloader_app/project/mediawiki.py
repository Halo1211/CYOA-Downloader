"""Resolve image-description pages to their explicitly linked image file."""

from __future__ import annotations

import os
import re
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from ..constants.assets import IMAGE_EXTENSIONS


def mediawiki_image_url(html: str, page_url: str) -> str | None:
    """Use the original-file link of a MediaWiki File page, never a screenshot."""
    if not re.search(r"(?:^|/)File:", unquote(urlparse(page_url).path), re.IGNORECASE):
        return None
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one(".fullImageLink a[href], .fullMedia a[href]")
    if link is None:
        return None
    candidate = urljoin(page_url, str(link.get("href") or ""))
    parsed = urlparse(candidate)
    if parsed.scheme not in {"https", "http"} or os.path.splitext(parsed.path)[1].lower() not in IMAGE_EXTENSIONS:
        return None
    return candidate
