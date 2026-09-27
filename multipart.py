"""Minimal multipart/form-data parser.

Replaces the stdlib `cgi` module, which was removed in Python 3.13. It
reproduces only the small slice of `cgi.FieldStorage` this project actually
used -- `"name" in form`, `form["name"]`, and parts exposing `.filename`,
`.type` and `.file` -- so the upload call sites did not have to change.

The body is read into memory under a hard cap. That is the right trade for a
localhost single-operator tool: streaming to temp files would be more code and
more failure modes for no benefit at this scale. The cap is what stops a
malformed or hostile request from exhausting memory.
"""

from __future__ import annotations

import io
import os
import re
from email.parser import BytesParser
from email.policy import HTTP
from typing import Any, Iterator

CRLF = b"\r\n"
LF = b"\n"
DASH = b"--"

# 8 attachments is the documented per-request limit; give the envelope slack
# for headers and boundaries on top of the per-file cap.
MAX_MULTIPART_BYTES = int(os.environ.get("MISSION_CONTROL_MAX_MULTIPART_BYTES", str(64 * 1024 * 1024)))


class MultipartError(ValueError):
    """Raised for malformed or oversized multipart bodies."""


class Part:
    """One form field. Mirrors the cgi.FieldStorage attributes we relied on."""

    __slots__ = ("name", "filename", "type", "file", "_value")

    def __init__(self, name: str, filename: str | None, content_type: str, data: bytes) -> None:
        self.name = name
        self.filename = filename
        self.type = content_type
        self.file = io.BytesIO(data)
        self._value = data

    @property
    def value(self) -> str:
        """Text value of a non-file field."""
        return self._value.decode("utf-8", errors="replace")

    @property
    def raw(self) -> bytes:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"<Part name={self.name!r} filename={self.filename!r} "
                f"type={self.type!r} bytes={len(self._value)}>")


class MultipartForm:
    """Field-name -> parts mapping with the cgi.FieldStorage access pattern.

    `form["x"]` returns a single Part when one part carries that name and a
    list when several do, because that is what the existing call sites expect.
    """

    def __init__(self, parts: list[Part]) -> None:
        self._parts = parts
        self._by_name: dict[str, list[Part]] = {}
        for part in parts:
            self._by_name.setdefault(part.name, []).append(part)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __getitem__(self, name: str) -> Part | list[Part]:
        found = self._by_name[name]
        return found[0] if len(found) == 1 else found

    def __iter__(self) -> Iterator[str]:
        return iter(self._by_name)

    def __len__(self) -> int:
        return len(self._parts)

    def get(self, name: str, default: Any = None) -> Any:
        return self[name] if name in self._by_name else default

    def getlist(self, name: str) -> list[Part]:
        return list(self._by_name.get(name, []))

    @property
    def parts(self) -> list[Part]:
        return list(self._parts)

    def keys(self) -> list[str]:
        return list(self._by_name)


_BOUNDARY_RE = re.compile(r'boundary=(?:"([^"]+)"|([^\s;]+))', re.I)


def parse_boundary(content_type: str) -> bytes:
    if "multipart/form-data" not in (content_type or "").lower():
        raise MultipartError("multipart/form-data is required")
    match = _BOUNDARY_RE.search(content_type or "")
    if not match:
        raise MultipartError("multipart boundary is missing from Content-Type")
    boundary = match.group(1) or match.group(2)
    if not boundary:
        raise MultipartError("multipart boundary is empty")
    return boundary.encode("latin-1", errors="replace")


def _delimiter_positions(data: bytes, delimiter: bytes) -> list[int]:
    """Offsets of real delimiters, ignoring the sequence appearing in content.

    RFC 2046: a delimiter is a line break, then "--", then the boundary, and it
    must be followed by a line break (another part) or "--" (the terminator).
    Without that trailing check, a file whose bytes happen to contain the
    boundary string is silently truncated there -- a corrupted upload that
    still returns 201.
    """
    positions: list[int] = []
    width = len(delimiter)
    idx = data.find(delimiter)
    while idx != -1:
        after = data[idx + width: idx + width + 2]
        if after[:2] == DASH:
            positions.append(idx)
            break  # terminator: nothing beyond it belongs to the body
        if after[:2] == CRLF or after[:1] == LF:
            positions.append(idx)
        idx = data.find(delimiter, idx + width)
    return positions


def parse_body(body: bytes, boundary: bytes) -> MultipartForm:
    """Split a complete multipart body into parts."""
    # The opening delimiter has no preceding line break; prepending one lets a
    # single rule find every delimiter including the first.
    data = CRLF + body
    delimiter = CRLF + DASH + boundary
    positions = _delimiter_positions(data, delimiter)
    if len(positions) < 2:
        # Tolerate senders that use bare LF line endings.
        lf_data = LF + body
        lf_delimiter = LF + DASH + boundary
        lf_positions = _delimiter_positions(lf_data, lf_delimiter)
        if len(lf_positions) > len(positions):
            data, delimiter, positions = lf_data, lf_delimiter, lf_positions
    if not positions:
        raise MultipartError("multipart body contained no boundary delimiter")

    width = len(delimiter)
    parts: list[Part] = []
    for start, end in zip(positions, positions[1:]):
        chunk = data[start + width: end]
        # Strip the line break that follows the delimiter. The one *before* the
        # next delimiter is already excluded, because the delimiter itself
        # begins with it -- so a payload ending in CRLF keeps that CRLF.
        if chunk[:2] == CRLF:
            chunk = chunk[2:]
        elif chunk[:1] == LF:
            chunk = chunk[1:]

        head, sep, payload = chunk.partition(CRLF + CRLF)
        if not sep:
            head, sep, payload = chunk.partition(LF + LF)
            if not sep:
                continue  # malformed part with no header terminator

        headers = BytesParser(policy=HTTP).parsebytes(head)
        disposition = headers.get("Content-Disposition", "")
        name = _param(disposition, "name")
        if name is None:
            continue  # a part without a field name is unusable
        filename = _param(disposition, "filename")
        content_type = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not content_type:
            content_type = "application/octet-stream" if filename else "text/plain"
        parts.append(Part(name=name, filename=filename, content_type=content_type, data=payload))

    return MultipartForm(parts)


_PARAM_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _param(header_value: str, key: str) -> str | None:
    """Read one parameter out of a header, quoted or bare."""
    pattern = _PARAM_RE_CACHE.get(key)
    if pattern is None:
        pattern = re.compile(rf'{re.escape(key)}\s*=\s*(?:"([^"]*)"|([^;\s]+))', re.I)
        _PARAM_RE_CACHE[key] = pattern
    match = pattern.search(header_value or "")
    if not match:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def parse(headers: Any, stream: Any) -> MultipartForm:
    """Read Content-Length bytes from `stream` and parse them.

    Exactly Content-Length bytes are read: reading past it would block on a
    keep-alive connection waiting for a body that never comes.
    """
    content_type = headers.get("Content-Type", "") or ""
    boundary = parse_boundary(content_type)

    raw_length = headers.get("Content-Length", "") or "0"
    try:
        length = int(raw_length)
    except (TypeError, ValueError):
        raise MultipartError("Content-Length header is missing or not a number") from None
    if length <= 0:
        raise MultipartError("multipart request had an empty body")
    if length > MAX_MULTIPART_BYTES:
        raise MultipartError(
            f"upload is {length} bytes, over the {MAX_MULTIPART_BYTES} byte request limit")

    body = stream.read(length)
    if len(body) != length:
        raise MultipartError(
            f"request body ended early: expected {length} bytes, received {len(body)}")
    return parse_body(body, boundary)
