"""Faded and tinted copies of a PNG icon (disabled buttons, and the blue ink under the mouse),
with nothing but the standard library.

The icons are 8-bit RGBA PNGs. Fading scales each pixel's transparency rather than painting
pixels a paler colour: Tk's PhotoImage.put would make every pixel fully opaque and lose the
soft edges of the ink lines, and a faded icon then also suits any background.
"""

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def read_rgba(data):
    """(width, height, [row bytes]) of an 8-bit RGBA, non-interlaced PNG. Raises ValueError
    for anything else."""
    if not data.startswith(_SIGNATURE):
        raise ValueError("not a PNG")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        n, = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    if header is None or header[2:5] != (8, 6, 0) or header[6] != 0:
        raise ValueError("only 8-bit RGBA PNGs")
    w, h = header[:2]
    raw, stride, rows, prev, p = zlib.decompress(idat), w * 4, [], bytearray(w * 4), 0
    for _ in range(h):
        kind, line = raw[p], bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):          # undo the row's filter
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if kind == 1:
                line[i] = (line[i] + a) & 255
            elif kind == 2:
                line[i] = (line[i] + b) & 255
            elif kind == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif kind == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return w, h, rows


def faded_png(data, opacity):
    """The PNG with every pixel's opacity multiplied by opacity (0 to 1)."""
    w, h, rows = read_rgba(data)
    out = bytearray()
    for line in rows:
        line = bytearray(line)
        for i in range(3, len(line), 4):
            line[i] = round(line[i] * opacity)
        out += b"\x00" + line
    return _encode(w, h, out)


def _encode(w, h, filtered):
    """An RGBA PNG from rows that each start with filter byte 0."""
    header = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (_SIGNATURE + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(filtered))) + _chunk(b"IEND", b""))


def tinted_png(data, color):
    """The PNG with every pixel turned color ("#rrggbb"), keeping its opacity: the icon's
    shape, soft edges and all, in another ink."""
    w, h, rows = read_rgba(data)
    rgb = bytes(int(color[i:i + 2], 16) for i in (1, 3, 5))
    out = bytearray()
    for line in rows:
        line = bytearray(line)
        for i in range(0, len(line), 4):
            line[i:i + 3] = rgb
        out += b"\x00" + line
    return _encode(w, h, out)
