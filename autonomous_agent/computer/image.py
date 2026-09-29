"""Small, dependency-free PNG helpers for bounded computer screenshots."""

from __future__ import annotations

import binascii
import struct
import zlib


def _chunk(kind: bytes, payload: bytes) -> bytes:
    length = struct.pack(">I", len(payload))
    crc = struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    return length + kind + payload + crc


def encode_rgba_png(width: int, height: int, rgba: bytes) -> bytes:
    """Encode raw RGBA pixels as a PNG using only the Python standard library."""
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(f"RGBA payload has {len(rgba)} bytes; expected {expected}")

    rows = bytearray()
    stride = width * 4
    for offset in range(0, len(rgba), stride):
        rows.append(0)  # filter type: None
        rows.extend(rgba[offset : offset + stride])

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        signature
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), level=6))
        + _chunk(b"IEND", b"")
    )


def bgra_to_rgba(bgra: bytes) -> bytes:
    """Convert packed BGRA pixels returned by Windows GDI to RGBA."""
    if len(bgra) % 4:
        raise ValueError("BGRA payload length must be a multiple of 4")
    output = bytearray(len(bgra))
    for offset in range(0, len(bgra), 4):
        b, g, r, a = bgra[offset : offset + 4]
        output[offset : offset + 4] = bytes((r, g, b, 255 if a == 0 else a))
    return bytes(output)
