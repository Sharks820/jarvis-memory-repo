"""Generate a Jarvis .ico file using raw binary ICO format (no Pillow needed).

Creates a 32x32 and 16x16 icon with a teal-themed "J" orb matching the
Jarvis Desktop Widget color scheme.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def _make_bmp_data(size: int) -> bytes:
    """Create a 32-bit BGRA BMP pixel data for a Jarvis icon at given size."""
    pixels = bytearray(size * size * 4)

    cx, cy = size / 2.0, size / 2.0
    outer_r = size / 2.0 - 1.0
    inner_r = outer_r * 0.65
    ring_width = 2.0 if size >= 32 else 1.5

    # Colors (BGRA format)
    bg = (0, 0, 0, 0)  # transparent
    glow = (191, 212, 45, 255)  # #2dd4bf -> teal glow (BGR)
    ring = (233, 165, 14, 200)  # #0ea5e9 -> blue ring (BGR)
    core = (110, 118, 15, 255)  # #0f766e -> dark teal core (BGR)
    letter = (255, 248, 236, 255)  # #ecfeff -> near-white (BGR)

    # "J" glyph as a simple bitmap mask for each size
    j_mask = _make_j_mask(size)

    for row in range(size):
        for col in range(size):
            dx = col - cx + 0.5
            dy = row - cy + 0.5
            dist = (dx * dx + dy * dy) ** 0.5

            if dist > outer_r + 0.5:
                color = bg
            elif dist > outer_r - ring_width:
                color = glow
            elif dist > inner_r + ring_width:
                color = ring
            elif dist > inner_r:
                # Blend ring to core
                t = (inner_r + ring_width - dist) / ring_width
                color = _lerp(ring, core, t)
            else:
                # Inside core - check for J letter
                if j_mask[row][col]:
                    color = letter
                else:
                    color = core

            # BMP rows are bottom-up
            bmp_row = size - 1 - row
            offset = (bmp_row * size + col) * 4
            pixels[offset] = color[0]      # B
            pixels[offset + 1] = color[1]  # G
            pixels[offset + 2] = color[2]  # R
            pixels[offset + 3] = color[3]  # A

    return bytes(pixels)


def _lerp(c1: tuple, c2: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
        int(c1[3] + (c2[3] - c1[3]) * t),
    )


def _make_j_mask(size: int) -> list[list[bool]]:
    """Create a boolean mask for the letter 'J' centered in the icon."""
    mask = [[False] * size for _ in range(size)]
    cx = size / 2.0
    cy = size / 2.0

    if size >= 32:
        # 32x32 "J" glyph
        # Top bar: row 8-10, col 10-22
        for r in range(8, 11):
            for c in range(10, 23):
                mask[r][c] = True
        # Vertical stroke: row 10-21, col 15-19
        for r in range(10, 22):
            for c in range(15, 20):
                mask[r][c] = True
        # Bottom curve: rows 21-24
        for r in range(21, 25):
            for c in range(9, 20):
                dx = c - 13.5
                dy = r - 21.0
                dist = (dx * dx + dy * dy) ** 0.5
                if 1.0 < dist < 6.5 and c < 16:
                    mask[r][c] = True
                elif r < 23 and 15 <= c < 20:
                    mask[r][c] = True
    else:
        # 16x16 "J" glyph
        for r in range(4, 6):
            for c in range(5, 12):
                mask[r][c] = True
        for r in range(5, 11):
            for c in range(7, 10):
                mask[r][c] = True
        for r in range(10, 13):
            for c in range(4, 10):
                dx = c - 6.5
                dy = r - 10.0
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < 4.0 and c < 8:
                    mask[r][c] = True
                elif r < 12 and 7 <= c < 10:
                    mask[r][c] = True

    return mask


def generate_ico(output_path: Path) -> None:
    """Generate a multi-size .ico file at the given path."""
    sizes = [32, 16]
    images = []

    for s in sizes:
        pixel_data = _make_bmp_data(s)

        # BITMAPINFOHEADER (40 bytes) - height is doubled for ICO (includes AND mask)
        bih = struct.pack(
            "<IiiHHIIiiII",
            40,       # biSize
            s,        # biWidth
            s * 2,    # biHeight (doubled for XOR + AND mask)
            1,        # biPlanes
            32,       # biBitCount
            0,        # biCompression (BI_RGB)
            len(pixel_data),  # biSizeImage
            0,        # biXPelsPerMeter
            0,        # biYPelsPerMeter
            0,        # biClrUsed
            0,        # biClrImportant
        )

        # AND mask: 1-bit per pixel, rows padded to 4-byte boundary
        and_row_bytes = ((s + 31) // 32) * 4
        and_mask = b"\x00" * (and_row_bytes * s)

        entry_data = bih + pixel_data + and_mask
        images.append((s, entry_data))

    # ICO file header
    header = struct.pack("<HHH", 0, 1, len(images))

    # Build directory entries and concatenate image data
    offset = 6 + len(images) * 16  # after header + directory
    directory = b""
    all_data = b""

    for s, data in images:
        w = 0 if s == 256 else s
        h = 0 if s == 256 else s
        directory += struct.pack(
            "<BBBBHHII",
            w,          # bWidth
            h,          # bHeight
            0,          # bColorCount
            0,          # bReserved
            1,          # wPlanes
            32,         # wBitCount
            len(data),  # dwBytesInRes
            offset,     # dwImageOffset
        )
        offset += len(data)
        all_data += data

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(header + directory + all_data)
    print(f"Generated {output_path} ({output_path.stat().st_size} bytes)")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "jarvis.ico"
    generate_ico(out)
