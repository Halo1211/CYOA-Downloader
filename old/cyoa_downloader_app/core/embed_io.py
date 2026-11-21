"""Render project media replacements without building a base64 string map."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

from .cancellation import _raise_if_cancelled
from .media_io import MEDIA_CHUNK_BYTES


@dataclass(frozen=True)
class EmbeddedMedia:
    path: str
    mime: str


def value_chunks(value):
    if isinstance(value, EmbeddedMedia):
        yield f"data:{value.mime};base64,"
        # Multiples of three avoid padding between base64 blocks.
        with open(value.path, "rb") as source:
            while block := source.read(MEDIA_CHUNK_BYTES // 4 * 3):
                _raise_if_cancelled()
                yield base64.b64encode(block).decode("ascii")
    else:
        for start in range(0, len(value), MEDIA_CHUNK_BYTES):
            _raise_if_cancelled()
            yield value[start:start + MEDIA_CHUNK_BYTES]


def project_chunks(text, mapping, field_group, image_fields, field_aliases):
    """Apply source substitutions and field cleanup in one pass over the input.

    Replacements are never scanned again. A mapped data URI preserves the
    source field's spacing, as the historical two-pass renderer does. Escaped
    slash fields and credited image fields retain their cleanup semantics.
    """
    expanded = dict(mapping)
    for source, replacement in mapping.items():
        if source.startswith(("http://", "https://")):
            expanded.setdefault(source.replace("://", ":/", 1), replacement)
    alt = "|".join(re.escape(key) for key in sorted(expanded, key=len, reverse=True))
    asset_re = re.compile(alt) if alt else None
    pattern = rf'(?i:"(?P<field>{field_group})"\s*:\s*"(?P<value>[^"]+)")'
    if alt:
        pattern += rf'|(?P<asset>{alt})'
    combined = re.compile(pattern)
    data_uri = re.compile(r"^data:(?:image|audio|application)/[a-zA-Z0-9.+-]+;base64,")

    def span(start, end):
        for offset in range(start, end, MEDIA_CHUNK_BYTES):
            _raise_if_cancelled()
            yield text[offset:min(end, offset + MEDIA_CHUNK_BYTES)]

    def pieces(value):
        if asset_re is None:
            return [value]
        result, position = [], 0
        for match in asset_re.finditer(value):
            if match.start() > position:
                result.append(value[position:match.start()])
            result.append(expanded[match.group(0)])
            position = match.end()
        if position < len(value):
            result.append(value[position:])
        return result

    position = 0
    for match in combined.finditer(text):
        _raise_if_cancelled()
        yield from span(position, match.start())
        position = match.end()
        field = match.group("field")
        if field is None:
            yield from value_chunks(expanded[match.group("asset")])
            continue
        start, end = match.span("value")
        if data_uri.match(text[start:min(end, start + 128)]):
            yield from span(match.start(), match.end())
            continue
        raw = match.group("value")
        key = raw.replace("\\/", "/")
        reference = field_aliases.get(key) if field.lower() in image_fields else None
        values = pieces(raw)
        preserve = bool(values) and (isinstance(values[0], EmbeddedMedia) or
                                    (isinstance(values[0], str) and data_uri.match(values[0])))
        if reference in mapping and (not preserve or len(values) > 1):
            values = [mapping[reference]]
        elif not preserve and all(isinstance(value, str) for value in values):
            localized = "".join(values)
            values = [mapping.get(localized.replace("\\/", "/"), localized)]
        if preserve:
            yield text[match.start():start]
        else:
            yield f'"{field}":"'
        for value in values:
            yield from value_chunks(value)
        yield '"'
    yield from span(position, len(text))
