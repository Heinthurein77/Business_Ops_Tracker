"""Magic-byte verification for uploaded files.

The extension allowlist in the routers only checks the *filename*, which the
client controls. `content_matches_extension` additionally checks that the
first bytes of the upload really are what the extension claims, so an HTML or
script file renamed `.png` / `.pdf` is rejected before it reaches storage.
"""
import codecs
from typing import BinaryIO

_SNIFF_BYTES = 64 * 1024

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"

_TEXT_EXTENSIONS = {"csv", "txt"}


def _is_utf8_text(head: bytes, truncated: bool) -> bool:
    if b"\x00" in head:
        return False
    if head.startswith(codecs.BOM_UTF8):
        head = head[len(codecs.BOM_UTF8):]
    # Incremental decoder: when the sample was cut at the sniff boundary a
    # multibyte character split across it is left pending (final=False)
    # instead of raising, so it is not a false reject. A short file that is
    # complete is decoded with final=True so a genuinely truncated sequence
    # at real end-of-file is still caught.
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(head, final=not truncated)
    except UnicodeDecodeError:
        return False
    return True


def content_matches_extension(ext: str, fileobj: BinaryIO) -> bool:
    """True if the leading bytes of `fileobj` are consistent with `ext`
    (lowercase, no dot). Reads at most 64 KiB and always rewinds the file to
    the start. Unknown extensions return False."""
    ext = (ext or "").lower().lstrip(".")
    try:
        fileobj.seek(0)
        head = fileobj.read(_SNIFF_BYTES + 1)
    finally:
        try:
            fileobj.seek(0)
        except (OSError, ValueError):
            pass
    if not isinstance(head, (bytes, bytearray)):
        return False
    truncated = len(head) > _SNIFF_BYTES
    head = bytes(head[:_SNIFF_BYTES])

    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {"jpg", "jpeg"}:
        return head.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == "pdf":
        return head.startswith(b"%PDF-")
    if ext in {"docx", "xlsx"}:
        return head.startswith(_ZIP_MAGIC)
    if ext in {"doc", "xls"}:
        return head.startswith(_OLE_MAGIC)
    if ext in _TEXT_EXTENSIONS:
        return _is_utf8_text(head, truncated)
    return False
