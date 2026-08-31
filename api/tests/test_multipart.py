"""Pass 13 — stdlib multipart/form-data parser (no python-multipart dep).

Hand-rolled RFC 7578 parsing is exactly the kind of thing that's easy to get
subtly wrong at the boundary edges — these tests build real multipart bodies
byte-for-byte the way a browser or the iOS Shortcut would, including binary
JPEG magic bytes, to prove the boundary/CRLF handling is correct rather than
just plausible."""
from __future__ import annotations

import pytest

from api import multipart

BOUNDARY = "----BrainCockpitTestBoundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"


def _body(*parts: tuple[str, str, bytes | None, str | None]) -> bytes:
    """parts: (name, filename_or_'', data_or_None_for_text, content_type)."""
    out = []
    for name, filename, data, ctype in parts:
        out.append(f"--{BOUNDARY}\r\n".encode())
        disp = f'Content-Disposition: form-data; name="{name}"'
        if filename:
            disp += f'; filename="{filename}"'
        out.append((disp + "\r\n").encode())
        if ctype:
            out.append(f"Content-Type: {ctype}\r\n".encode())
        out.append(b"\r\n")
        out.append(data if data is not None else b"")
        out.append(b"\r\n")
    out.append(f"--{BOUNDARY}--\r\n".encode())
    return b"".join(out)


JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF" + bytes(range(256)) * 4  # binary payload, not text-safe


def test_text_field_round_trips():
    body = _body(("text", "", b"a nice sunset", None))
    fields = multipart.parse(body, CONTENT_TYPE)
    assert fields["text"]["data"] == b"a nice sunset"
    assert fields["text"]["filename"] is None


def test_multiple_fields_all_present():
    body = _body(
        ("text", "", b"my thought", None),
        ("tag", "", b"resource", None),
        ("ocr", "", b"", None),
        ("file", "photo.jpg", JPEG_MAGIC, "image/jpeg"),
    )
    fields = multipart.parse(body, CONTENT_TYPE)
    assert set(fields) == {"text", "tag", "ocr", "file"}
    assert fields["tag"]["data"] == b"resource"
    assert fields["ocr"]["data"] == b""


def test_binary_file_survives_byte_for_byte():
    body = _body(("file", "photo.jpg", JPEG_MAGIC, "image/jpeg"))
    fields = multipart.parse(body, CONTENT_TYPE)
    assert fields["file"]["data"] == JPEG_MAGIC
    assert fields["file"]["filename"] == "photo.jpg"
    assert fields["file"]["content_type"] == "image/jpeg"


def test_binary_content_containing_crlf_is_not_truncated():
    """A real JPEG can contain the bytes \\r\\n anywhere in its data — the
    parser must split on the boundary marker only, never on bare CRLF."""
    tricky = b"\xff\xd8\xff" + b"\r\n" * 50 + b"more-bytes-after-crlf" + b"\r\n\r\n" + b"even-more"
    body = _body(("file", "photo.jpg", tricky, "image/jpeg"))
    fields = multipart.parse(body, CONTENT_TYPE)
    assert fields["file"]["data"] == tricky


def test_quoted_boundary_in_content_type():
    ct = f'multipart/form-data; boundary="{BOUNDARY}"'
    body = _body(("text", "", b"hello", None))
    fields = multipart.parse(body, ct)
    assert fields["text"]["data"] == b"hello"


def test_no_boundary_raises():
    with pytest.raises(multipart.MultipartError):
        multipart.parse(b"whatever", "multipart/form-data")


def test_wrong_boundary_raises():
    body = _body(("text", "", b"hello", None))
    with pytest.raises(multipart.MultipartError):
        multipart.parse(body, "multipart/form-data; boundary=SomethingElseEntirely")


def test_malicious_filename_is_parsed_but_never_trusted_by_this_module():
    """The parser itself is honest about what the client sent — path safety
    is enforced by capture code never using this field to build a path, which
    other tests (api/tests/test_capture_image.py) verify directly."""
    body = _body(("file", "../../../etc/passwd", b"\xff\xd8\xff", "image/jpeg"))
    fields = multipart.parse(body, CONTENT_TYPE)
    assert fields["file"]["filename"] == "../../../etc/passwd"


def test_empty_body_part_is_fine():
    body = _body(("ocr", "", b"", None))
    fields = multipart.parse(body, CONTENT_TYPE)
    assert fields["ocr"]["data"] == b""
