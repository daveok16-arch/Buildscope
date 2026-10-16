#!/usr/bin/env python3
"""Generate the static brand assets the web app ships.

These are build artifacts, not hand-drawn files: the Open Graph share card, the favicon and
the Apple touch icon are drawn here from the same tokens as the site CSS, so the brand stays
consistent and the images can be regenerated instead of re-exported from a design tool.

Run:  python ops/make_brand_assets.py
Out:  src/oppintel/app/static/{og-image.png,favicon.svg,favicon.ico,apple-touch-icon.png,logo.png}

Requires Pillow, which is a dev tool for regenerating these files only — it is deliberately not a
runtime dependency (the built assets are committed). Install with `pip install Pillow` before
running this. No network access.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# The card's size comes from the app, not a literal here: the same constants are what the
# <head> declares, so the drawn file and the advertised dimensions cannot drift apart.
from oppintel.app.seo import OG_IMAGE_HEIGHT, OG_IMAGE_WIDTH  # noqa: E402

# Brand tokens, kept in step with src/oppintel/app/static/css/app.css.
NAVY_950 = (6, 11, 23)
NAVY_900 = (11, 19, 40)
NAVY_700 = (30, 49, 96)
SLATE_300 = (203, 213, 225)
SLATE_400 = (148, 163, 184)
GOLD_400 = (245, 158, 11)
GOLD_300 = (252, 211, 77)
WHITE = (255, 255, 255)

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
STATIC_DIR = REPO_ROOT / "src" / "oppintel" / "app" / "static"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        # Fall back to Pillow's bundled font so the script still runs on a machine without
        # the DejaVu package; the layout is unchanged, only the typeface differs.
        return ImageFont.load_default(size)


def _draw_layers_mark(draw: ImageDraw.ImageDraw, cx: float, cy: float, unit: float) -> None:
    """The stacked-layers logo mark, drawn from the same geometry as the site's inline SVG."""
    top = [
        (cx, cy - unit * 1.15),
        (cx + unit * 1.15, cy),
        (cx, cy + unit * 1.15),
        (cx - unit * 1.15, cy),
    ]
    draw.polygon(top, fill=GOLD_400)
    for dy in (unit * 1.35, unit * 2.7):
        draw.line(
            [(cx - unit * 1.15, cy + dy - unit), (cx, cy + dy), (cx + unit * 1.15, cy + dy - unit)],
            fill=SLATE_300,
            width=max(2, int(unit * 0.42)),
            joint="curve",
        )


def _grid(draw: ImageDraw.ImageDraw, w: int, h: int, step: int, color: tuple[int, int, int]) -> None:
    for x in range(0, w, step):
        draw.line([(x, 0), (x, h)], fill=color, width=1)
    for y in range(0, h, step):
        draw.line([(0, y), (w, y)], fill=color, width=1)


def make_og_image() -> Path:
    w, h = OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT
    img = Image.new("RGB", (w, h), NAVY_950)
    draw = ImageDraw.Draw(img)
    # A faint blueprint grid and a diagonal navy field keep the card from reading as flat black.
    _grid(draw, w, h, 40, (16, 28, 56))
    draw.polygon([(w * 0.62, 0), (w, 0), (w, h), (w * 0.86, h)], fill=NAVY_900)
    draw.rectangle([(0, 0), (w, 6)], fill=GOLD_400)

    _draw_layers_mark(draw, 96, 108, 26)
    draw.text((150, 78), "BUILD SCOPE", font=_font("DejaVuSans-Bold.ttf", 44), fill=WHITE)
    dot_w = draw.textlength("BUILD SCOPE", font=_font("DejaVuSans-Bold.ttf", 44))
    draw.text((150 + dot_w, 78), ".", font=_font("DejaVuSans-Bold.ttf", 44), fill=GOLD_400)
    draw.text(
        (150, 132),
        "CONSTRUCTION OPPORTUNITY INTELLIGENCE",
        font=_font("DejaVuSans.ttf", 20),
        fill=SLATE_400,
    )

    draw.text(
        (96, 272),
        "See the work",
        font=_font("DejaVuSans-Bold.ttf", 74),
        fill=WHITE,
    )
    draw.text((96, 362), "before the work begins.", font=_font("DejaVuSans-Bold.ttf", 74), fill=GOLD_400)

    draw.line([(96, 486), (96 + 88, 486)], fill=GOLD_400, width=6)
    draw.text(
        (96, 512),
        "Public DFW permit records \u00b7 every fact linked to its source",
        font=_font("DejaVuSans.ttf", 26),
        fill=SLATE_300,
    )
    out = STATIC_DIR / "og-image.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_apple_touch_icon() -> Path:
    size = 180
    img = Image.new("RGB", (size, size), NAVY_950)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (size, 4)], fill=GOLD_400)
    _draw_layers_mark(draw, size / 2, size / 2 - 8, size * 0.16)
    out = STATIC_DIR / "apple-touch-icon.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_logo() -> Path:
    # Square mark for schema.org Organization.logo, which search engines expect to be square.
    size = 512
    img = Image.new("RGB", (size, size), NAVY_950)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (size, 6)], fill=GOLD_400)
    _draw_layers_mark(draw, size / 2, size / 2 - 24, size * 0.17)
    out = STATIC_DIR / "logo.png"
    img.save(out, "PNG", optimize=True)
    return out


def make_favicon_ico() -> Path:
    size = 32
    img = Image.new("RGB", (size, size), NAVY_950)
    draw = ImageDraw.Draw(img)
    _draw_layers_mark(draw, size / 2, size / 2 - 2, size * 0.19)
    out = STATIC_DIR / "favicon.ico"
    img.save(out, "ICO", sizes=[(32, 32), (16, 16)])
    return out


def make_favicon_svg() -> Path:
    # Hand-authored vector so the tab icon stays crisp at any size; geometry mirrors the mark
    # above and the inline SVG in base.html.
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" '
        'aria-label="BuildScope">\n'
        '  <rect width="32" height="32" rx="6" fill="#060b17"/>\n'
        '  <path d="M16 5l8 4-8 4-8-4 8-4z" fill="#f59e0b"/>\n'
        '  <path d="M8 15l8 4 8-4" fill="none" stroke="#cbd5e1" stroke-width="2.4" '
        'stroke-linecap="round" stroke-linejoin="round"/>\n'
        '  <path d="M8 21l8 4 8-4" fill="none" stroke="#94a3b8" stroke-width="2.4" '
        'stroke-linecap="round" stroke-linejoin="round"/>\n'
        '</svg>\n'
    )
    out = STATIC_DIR / "favicon.svg"
    out.write_text(svg, encoding="utf-8")
    return out


def main() -> int:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    for maker in (make_og_image, make_favicon_svg, make_favicon_ico, make_apple_touch_icon, make_logo):
        path = maker()
        print(f"wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
