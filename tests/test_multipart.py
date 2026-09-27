"""Tests for the multipart parser that replaced the removed `cgi` module.

Covers the shapes real browsers send plus the malformed cases that must fail
loudly rather than silently dropping an attachment.
"""

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import multipart

B = "----WebKitFormBoundary7MA4YWxkTrZu0gW"


def build(parts, boundary=B):
    """Assemble a body exactly as a browser would, with CRLF line endings."""
    out = b""
    for part in parts:
        out += ("--" + boundary + "\r\n").encode() + part + b"\r\n"
    out += ("--" + boundary + "--\r\n").encode()
    return out


def file_part(name, filename, ctype, data):
    head = (
        'Content-Disposition: form-data; name="' + name + '"; filename="' + filename + '"\r\n'
        "Content-Type: " + ctype + "\r\n\r\n"
    )
    return head.encode("utf-8") + data


def field_part(name, value):
    return ('Content-Disposition: form-data; name="' + name + '"\r\n\r\n' + value).encode("utf-8")


class Headers(dict):
    """Stand-in for http.client.HTTPMessage (case-insensitive .get)."""

    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == key.lower():
                return v
        return default


def parse_bytes(body, boundary=B, content_type=None):
    headers = Headers({
        "Content-Type": content_type or ("multipart/form-data; boundary=" + boundary),
        "Content-Length": str(len(body)),
    })
    return multipart.parse(headers, io.BytesIO(body))


class BoundaryTests(unittest.TestCase):
    def test_plain_and_quoted(self):
        self.assertEqual(multipart.parse_boundary("multipart/form-data; boundary=" + B), B.encode())
        self.assertEqual(multipart.parse_boundary('multipart/form-data; boundary="' + B + '"'), B.encode())

    def test_case_insensitive_and_charset_suffix(self):
        self.assertEqual(
            multipart.parse_boundary("Multipart/Form-Data; BOUNDARY=" + B + "; charset=utf-8"),
            B.encode())

    def test_rejects_wrong_or_missing(self):
        for bad in ("application/json", "multipart/form-data", ""):
            with self.assertRaises(multipart.MultipartError):
                multipart.parse_boundary(bad)


class ParseTests(unittest.TestCase):
    def test_single_file_roundtrip(self):
        form = parse_bytes(build([file_part("files", "a.txt", "text/plain", b"hello world")]))
        self.assertIn("files", form)
        part = form["files"]
        self.assertEqual(part.filename, "a.txt")
        self.assertEqual(part.type, "text/plain")
        self.assertEqual(part.file.read(), b"hello world")

    def test_multiple_same_name_returns_list(self):
        form = parse_bytes(build([
            file_part("files", "a.txt", "text/plain", b"one"),
            file_part("files", "b.txt", "text/plain", b"two"),
        ]))
        items = form["files"]
        self.assertIsInstance(items, list)
        self.assertEqual([i.filename for i in items], ["a.txt", "b.txt"])
        self.assertEqual([i.file.read() for i in items], [b"one", b"two"])

    def test_binary_payload_is_byte_exact(self):
        blob = bytes(range(256)) * 40
        form = parse_bytes(build([file_part("files", "b.bin", "application/octet-stream", blob)]))
        self.assertEqual(form["files"].file.read(), blob)

    def test_payload_containing_boundary_like_text(self):
        """Data mentioning the boundary must not split the part."""
        tricky = ("line\r\n--" + B + " not really a boundary\r\nmore").encode()
        form = parse_bytes(build([file_part("files", "t.txt", "text/plain", tricky)]))
        self.assertEqual(form["files"].file.read(), tricky)

    def test_crlf_inside_payload_preserved(self):
        data = b"first\r\nsecond\r\nthird\r\n\r\n"
        form = parse_bytes(build([file_part("files", "c.txt", "text/plain", data)]))
        self.assertEqual(form["files"].file.read(), data)

    def test_empty_file_is_kept(self):
        form = parse_bytes(build([file_part("files", "empty.txt", "text/plain", b"")]))
        self.assertEqual(form["files"].file.read(), b"")
        self.assertEqual(form["files"].filename, "empty.txt")

    def test_plain_field_and_file_together(self):
        form = parse_bytes(build([
            field_part("note", "hello"),
            file_part("files", "a.txt", "text/plain", b"data"),
        ]))
        self.assertEqual(form["note"].value, "hello")
        self.assertIsNone(form["note"].filename)
        self.assertEqual(form["files"].filename, "a.txt")

    def test_missing_content_type_defaults(self):
        body = build([b'Content-Disposition: form-data; name="files"; filename="x.dat"\r\n\r\nZ'])
        self.assertEqual(parse_bytes(body)["files"].type, "application/octet-stream")

    def test_unicode_filename(self):
        form = parse_bytes(build([file_part("files", "réport ✓.txt", "text/plain", b"x")]))
        self.assertEqual(form["files"].filename, "réport ✓.txt")

    def test_helpers(self):
        form = parse_bytes(build([
            file_part("files", "a.txt", "text/plain", b"one"),
            field_part("note", "n"),
        ]))
        self.assertEqual(len(form), 2)
        self.assertEqual(sorted(form.keys()), ["files", "note"])
        self.assertEqual(len(form.getlist("files")), 1)
        self.assertEqual(form.getlist("absent"), [])
        self.assertIsNone(form.get("absent"))
        self.assertNotIn("absent", form)


class FailureTests(unittest.TestCase):
    def test_errors_are_valueerror(self):
        """Handlers map ValueError to 400; MultipartError must inherit it."""
        self.assertTrue(issubclass(multipart.MultipartError, ValueError))

    def test_empty_body_rejected(self):
        with self.assertRaises(multipart.MultipartError):
            parse_bytes(b"")

    def test_truncated_body_rejected(self):
        body = build([file_part("files", "a.txt", "text/plain", b"12345")])
        headers = Headers({
            "Content-Type": "multipart/form-data; boundary=" + B,
            "Content-Length": str(len(body) + 500),  # claims more than is sent
        })
        with self.assertRaises(multipart.MultipartError) as ctx:
            multipart.parse(headers, io.BytesIO(body))
        self.assertIn("ended early", str(ctx.exception))

    def test_oversize_rejected_before_reading(self):
        headers = Headers({
            "Content-Type": "multipart/form-data; boundary=" + B,
            "Content-Length": str(multipart.MAX_MULTIPART_BYTES + 1),
        })

        class Exploding(io.RawIOBase):
            def read(self, n=-1):  # pragma: no cover - must never run
                raise AssertionError("body was read despite exceeding the cap")

        with self.assertRaises(multipart.MultipartError):
            multipart.parse(headers, Exploding())

    def test_body_without_delimiter_rejected(self):
        with self.assertRaises(multipart.MultipartError):
            parse_bytes(b"nothing that resembles a multipart body at all")

    def test_bad_content_length_rejected(self):
        headers = Headers({"Content-Type": "multipart/form-data; boundary=" + B,
                           "Content-Length": "not-a-number"})
        with self.assertRaises(multipart.MultipartError):
            multipart.parse(headers, io.BytesIO(b"x"))

    def test_part_without_name_skipped(self):
        body = build([b"Content-Disposition: form-data\r\n\r\norphan",
                      file_part("files", "a.txt", "text/plain", b"kept")])
        form = parse_bytes(body)
        self.assertEqual(len(form), 1)
        self.assertEqual(form["files"].file.read(), b"kept")


if __name__ == "__main__":
    unittest.main()
