"""Build Agent Talkback's icon assets from the same brand chevron the tray draws.

    python tools/build_assets.py            write assets/
    python tools/build_assets.py --check    exit 1 if any asset is out of date

Assets are generated, never hand-edited: the tray and the window have to be the same mark, so there is one artwork
(`talkback.BRAND_PATH`, the chevron of alts-agency's mark-white.svg) and one palette. Everything is drawn at 4x and
scaled down, which keeps the 16 px version crisp.
"""
import io
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
sys.path.insert(0, str(ROOT))
from talkback import (BRAND_PATH, BRAND_SCALE, BRAND_SHIFT, ICON_BLACK, ICON_WHITE,  # noqa: E402
                      chevron_points, icon_image)

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def svg():
    """The master artwork: the brand's own path, transformed exactly as the PNGs draw it."""
    points = chevron_points()
    left, right = min(x for x, _ in points), max(x for x, _ in points)
    top, bottom = min(y for _, y in points), max(y for _, y in points)
    scale = 256.0 * 0.60 / max(right - left, bottom - top)
    middle = ((left + right) / 2, (top + bottom) / 2)
    return (f"<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 256 256\" width=\"256\" height=\"256\" "
            f"role=\"img\" aria-label=\"Agent Talkback\">\n"
            f"  <title>Agent Talkback</title>\n"
            f"  <rect width=\"256\" height=\"256\" rx=\"56\" fill=\"{ICON_BLACK}\"/>\n"
            f"  <g transform=\"translate(128 128) scale({scale:.6f}) "
            f"translate({-middle[0]:.4f} {-middle[1]:.4f})\">\n"
            f"    <path d=\"{BRAND_PATH}\" transform=\"translate({BRAND_SHIFT[0]} {BRAND_SHIFT[1]}) "
            f"scale({BRAND_SCALE} {-BRAND_SCALE})\" fill=\"{ICON_WHITE}\"/>\n"
            f"  </g>\n</svg>\n").encode("utf-8")


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _ico(images):
    """ICO container with PNG payloads, one per size, so every size gets its own art."""
    blobs = [_png(img) for img in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    for img, blob in zip(images, blobs):
        side = 0 if img.width >= 256 else img.width
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    return header + entries + b"".join(blobs)


def render():
    """name -> bytes. Everything in assets/ comes from here."""
    return {
        "icon.svg": svg(),
        "icon-16.png": _png(icon_image(16)),
        "icon-32.png": _png(icon_image(32)),
        "icon-256.png": _png(icon_image(256)),
        "talkback.ico": _ico([icon_image(size) for size in ICO_SIZES]),
    }


def main(argv):
    check = "--check" in argv
    stale = []
    for name, data in render().items():
        path = ASSETS / name
        if check:
            if not path.exists() or path.read_bytes() != data:
                stale.append(name)
        else:
            ASSETS.mkdir(exist_ok=True)
            path.write_bytes(data)
    if check:
        if stale:
            print("stale (run python tools/build_assets.py): " + ", ".join(stale))
            return 1
        print(f"{len(render())} assets are up to date")
        return 0
    for name in render():
        print(f"wrote assets/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
