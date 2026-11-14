"""Validate large JSON while retaining prefixes of string values, not bodies.

The standard JSON decoder still validates structure, numbers and duplicate-key
semantics. The streaming lexer validates every string escape/control character
before shortening value strings. Keys and requested metadata values stay exact.
"""

from __future__ import annotations

import json
import re

from .cancellation import _raise_if_cancelled

_STRING_SPECIAL = re.compile(r'["\\\x00-\x1f]')
_OUTSIDE_SPECIAL = re.compile(r'["{}\[\],:]')
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class _Reader:
    def __init__(self, source, chunk_size):
        self.source, self.chunk_size = source, chunk_size
        self.buffer, self.position = "", 0

    def fill(self):
        if self.position == len(self.buffer):
            _raise_if_cancelled()
            self.buffer = self.source.read(self.chunk_size)
            self.position = 0
        return bool(self.buffer)

    def char(self):
        if not self.fill():
            raise ValueError("Unterminated JSON string")
        result = self.buffer[self.position]
        self.position += 1
        return result

    def string(self, *, retain):
        raw, prefix = ['"'] if retain else [], ""
        def capture(value):
            nonlocal prefix
            if not prefix:
                value = value.lstrip()
            if len(prefix) < 64:
                prefix += value[:64 - len(prefix)]

        while self.fill():
            match = _STRING_SPECIAL.search(self.buffer, self.position)
            stop = match.start() if match else len(self.buffer)
            part = self.buffer[self.position:stop]
            if retain:
                raw.append(part)
            else:
                capture(part)
            self.position = stop
            if match is None:
                continue
            special = self.char()
            if special == '"':
                if retain:
                    raw.append('"')
                    return "".join(raw)
                return json.dumps(prefix)
            if special != "\\":
                raise ValueError("Unescaped control character in JSON string")
            escape = self.char()
            if escape == "u":
                digits = "".join(self.char() for _ in range(4))
                if any(char not in "0123456789abcdefABCDEF" for char in digits):
                    raise ValueError("Invalid JSON Unicode escape")
                decoded = chr(int(digits, 16))
                encoded = "\\u" + digits
            elif escape in _ESCAPES:
                decoded, encoded = _ESCAPES[escape], "\\" + escape
            else:
                raise ValueError("Invalid JSON string escape")
            if retain:
                raw.append(encoded)
            else:
                capture(decoded)
        raise ValueError("Unterminated JSON string")


def load_json_summary(source, *, preserve_keys=frozenset(), chunk_size=65536):
    """Return parsed JSON with value strings shortened to 64 non-leading chars.

    Memory scales with document structure/keys, not large image/audio strings.
    This is a read-only summary; never write it back as a replacement project.
    """
    reader = _Reader(source, max(1, chunk_size))
    parts, stack = [], []
    while reader.fill():
        match = _OUTSIDE_SPECIAL.search(reader.buffer, reader.position)
        stop = match.start() if match else len(reader.buffer)
        parts.append(reader.buffer[reader.position:stop])
        reader.position = stop
        if match is None:
            continue
        symbol = reader.char()
        if symbol == '"':
            is_key = bool(stack and stack[-1][0] == "{" and stack[-1][1])
            retained = is_key or bool(stack and stack[-1][2] in preserve_keys)
            token = reader.string(retain=retained)
            if is_key:
                stack[-1][2] = json.loads(token)
            parts.append(token)
        else:
            parts.append(symbol)
            if symbol in "{[":
                stack.append([symbol, symbol == "{", None])
            elif symbol in "}]":
                if stack:
                    stack.pop()
            elif symbol == "," and stack and stack[-1][0] == "{":
                stack[-1][1], stack[-1][2] = True, None
            elif symbol == ":" and stack and stack[-1][0] == "{":
                stack[-1][1] = False
    return json.loads("".join(parts))
