"""Download national flag PNGs and generate England / West Indies cricket flags."""

from __future__ import annotations

import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
FLAGS = ROOT / "assets" / "flags"

FLAGCDN_CODES = {
    "india": "in",
    "australia": "au",
    "new-zealand": "nz",
    "south-africa": "za",
    "pakistan": "pk",
    "bangladesh": "bd",
    "sri-lanka": "lk",
    "afghanistan": "af",
    "ireland": "ie",
    "zimbabwe": "zw",
}


def _draw_england_flag(width: int = 640, height: int = 427) -> Image.Image:
    img = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    cross_w = width // 5
    cross_h = height // 5
    draw.rectangle([width // 2 - cross_w // 2, 0, width // 2 + cross_w // 2, height], fill="#CE1124")
    draw.rectangle([0, height // 2 - cross_h // 2, width, height // 2 + cross_h // 2], fill="#CE1124")
    return img


def _draw_west_indies_flag(width: int = 640, height: int = 427) -> Image.Image:
    img = Image.new("RGB", (width, height), "#750000")
    draw = ImageDraw.Draw(img)
    cx, cy = width // 2, height // 2
    draw.ellipse([cx - 110, cy - 110, cx + 110, cy + 110], fill="#D4AF37", outline="#FFD700", width=3)
    draw.ellipse([cx - 88, cy - 88, cx + 88, cy + 88], fill="#750000")
    draw.rectangle([cx - 10, cy - 10, cx + 10, cy + 55], fill="#8B4513")
    draw.ellipse([cx - 45, cy - 70, cx + 45, cy - 10], fill="#228B22")
    draw.ellipse([cx - 30, cy - 60, cx + 30, cy - 20], fill="#32CD32")
    for sx in (cx - 30, cx, cx + 30):
        draw.rectangle([sx - 4, cy + 30, sx + 4, cy + 70], fill="#F5DEB3")
    return img


def main() -> None:
    FLAGS.mkdir(parents=True, exist_ok=True)

    for slug, code in FLAGCDN_CODES.items():
        url = f"https://flagcdn.com/w640/{code}.png"
        dest = FLAGS / f"{slug}.png"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
        print(f"Downloaded {slug} ({dest.stat().st_size} bytes)")

    _draw_england_flag().save(FLAGS / "england.png")
    print("Generated england (St George's cross)")

    _draw_west_indies_flag().save(FLAGS / "west-indies.png")
    print("Generated west-indies (cricket maroon crest)")


if __name__ == "__main__":
    main()
