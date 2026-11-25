"""Rebuild the conventional macOS icon from the unchanged iPhone art.

Requires Pillow (python3 -m pip install Pillow). Run from any directory.
The 824px body leaves 100px on each side of the 1024px canvas for its shadow.
The committed assets are build inputs; packaging does not require Python.
"""

from io import BytesIO
from pathlib import Path
import struct

from PIL import Image, ImageDraw, ImageFilter

DESKTOP = Path(__file__).resolve().parents[1]
SOURCE = DESKTOP.parent / "ios/Panels/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
OUTPUT = DESKTOP / "resources"
CANVAS = 1024
BODY = 824
MARGIN = (CANVAS - BODY) // 2
CORNER = 185
SCALE = 4


def make_icon():
    mask = Image.new("L", (CANVAS * SCALE, CANVAS * SCALE))
    ImageDraw.Draw(mask).rounded_rectangle(
        (MARGIN * SCALE, MARGIN * SCALE, (MARGIN + BODY) * SCALE - 1, (MARGIN + BODY) * SCALE - 1),
        radius=CORNER * SCALE,
        fill=255,
    )
    mask = mask.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    # A soft, downward shadow stays inside the transparent canvas.
    shadow_mask = Image.new("L", (CANVAS, CANVAS))
    shadow_mask.paste(mask, (0, 12))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(12)).point(lambda value: round(value * 0.3))
    shadow = Image.new("RGBA", (CANVAS, CANVAS), "black")
    shadow.putalpha(shadow_mask)
    art = Image.new("RGBA", (CANVAS, CANVAS))
    with Image.open(SOURCE) as source:
        art.paste(source.convert("RGBA").resize((BODY, BODY), Image.Resampling.LANCZOS), (MARGIN, MARGIN))
    art.putalpha(mask)
    return Image.alpha_composite(shadow, art)


def write_icns(icon, destination):
    # PNG representations cover every Dock/Finder size, including Retina 1024px.
    chunks = []
    for kind, size in [(b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128),
                       (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024)]:
        png = BytesIO()
        icon.resize((size, size), Image.Resampling.LANCZOS).save(png, format="PNG")
        data = png.getvalue()
        chunks.append(kind + struct.pack(">I", len(data) + 8) + data)
    body = b"".join(chunks)
    destination.write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)


if __name__ == "__main__":
    OUTPUT.mkdir(exist_ok=True)
    icon = make_icon()
    icon.save(OUTPUT / "icon.png")
    write_icns(icon, OUTPUT / "icon.icns")
