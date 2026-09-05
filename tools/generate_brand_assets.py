#!/usr/bin/env python3
"""Generate SunoJump icons and repository artwork from the source mark."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MARK = ROOT / "assets" / "sunojump-mark-source.png"
APP_MARK = ROOT / "assets" / "sunojump-mark.png"
APP_ICON = ROOT / "assets" / "sunojump.ico"
VERSION_INFO = ROOT / "assets" / "version_info.txt"
BANNER = ROOT / "banner.png"
SOCIAL_PREVIEW = ROOT / ".github" / "social-preview.png"

INK = "#f4f5fb"
MUTED = "#a5abc0"
CYAN = "#25d9ed"
VIOLET = "#8b5cf6"
AMBER = "#f5ad42"


def _version() -> str:
    source = (ROOT / "sunojump.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION = "([0-9]+(?:\.[0-9]+){2})"$', source, re.MULTILINE)
    if match is None:
        raise RuntimeError("sunojump.py does not contain a semantic VERSION")
    return match.group(1)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates = (
        windows / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
        Path(
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf"
        ),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def _normalized_mark(size: int) -> Image.Image:
    mark = Image.open(SOURCE_MARK).convert("RGBA")
    alpha = mark.getchannel("A")
    minimum_alpha, maximum_alpha = alpha.getextrema()
    if minimum_alpha != 0 or maximum_alpha != 255:
        raise RuntimeError(
            "source mark must contain true transparent and opaque pixels"
        )
    bounds = alpha.getbbox()
    if bounds is None:
        raise RuntimeError("source mark has no visible pixels")
    mark = mark.crop(bounds)
    side = math.ceil(max(mark.size) * 1.14)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(
        mark,
        ((side - mark.width) // 2, (side - mark.height) // 2),
    )
    return square.resize((size, size), Image.Resampling.LANCZOS)


def _background(width: int, height: int) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), "#080a12")
    draw = ImageDraw.Draw(canvas, "RGBA")
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = (
            round(8 + ratio * 8),
            round(10 + ratio * 7),
            round(18 + ratio * 17),
            255,
        )
        draw.line((0, y, width, y), fill=color)
    for x in range(0, width, 64):
        draw.line((x, 0, x, height), fill=(95, 111, 160, 13), width=1)
    for y in range(0, height, 64):
        draw.line((0, y, width, y), fill=(95, 111, 160, 13), width=1)

    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    glow_draw.ellipse(
        (width * 0.64, -height * 0.55, width * 1.18, height * 0.92),
        fill=(38, 217, 237, 54),
    )
    glow_draw.ellipse(
        (width * 0.70, height * 0.18, width * 1.16, height * 1.30),
        fill=(139, 92, 246, 52),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(max(30, width // 22)))
    canvas.alpha_composite(glow)

    trace = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    trace_draw = ImageDraw.Draw(trace, "RGBA")
    for index, color in enumerate(((37, 217, 237, 50), (139, 92, 246, 38))):
        points = []
        baseline = height * (0.72 + index * 0.08)
        for x in range(-10, width + 11, 10):
            envelope = 0.25 + 0.75 * math.exp(
                -(((x - width * 0.72) / (width * 0.28)) ** 2)
            )
            y = baseline + math.sin(x / (35 + index * 11)) * height * 0.045 * envelope
            points.append((x, y))
        trace_draw.line(points, fill=color, width=max(2, width // 500))
    canvas.alpha_composite(trace)
    return canvas


def _rounded_label(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
) -> int:
    x, y = xy
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0] + 34
    height = bounds[3] - bounds[1] + 22
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=height // 2,
        fill=(26, 31, 48, 218),
        outline=(103, 119, 166, 80),
        width=1,
    )
    draw.text((x + 17, y + 8), text, font=font, fill=MUTED)
    return width


def _draw_social(version: str) -> Image.Image:
    canvas = _background(1280, 640)
    draw = ImageDraw.Draw(canvas, "RGBA")
    eyebrow = _font(25, bold=True)
    headline = _font(66, bold=True)
    copy = _font(28)
    label = _font(17, bold=True)

    draw.text((70, 64), "SUNOJUMP", font=eyebrow, fill=CYAN)
    draw.text(
        (70, 130),
        "Hear the variation.\nKeep the evidence.",
        font=headline,
        fill=INK,
        spacing=5,
    )
    draw.text(
        (73, 315),
        "A private audio workshop for before-and-after listening,\nbatch recovery, and reproducible local reports.",
        font=copy,
        fill=MUTED,
        spacing=8,
    )

    x = 72
    for text in ("PREVIEW", "A/B COMPARE", "BATCH RECOVERY", "LOCAL REPORTS"):
        x += _rounded_label(draw, (x, 468), text, label) + 12
    draw.text(
        (74, 564),
        f"WINDOWS APP + CLI    V{version}",
        font=label,
        fill=(165, 171, 192, 210),
    )

    mark = _normalized_mark(430)
    canvas.alpha_composite(mark, (820, 98))
    return canvas.convert("RGB")


def _draw_banner(version: str) -> Image.Image:
    canvas = _background(1536, 448)
    draw = ImageDraw.Draw(canvas, "RGBA")
    title = _font(88, bold=True)
    tagline = _font(40, bold=True)
    copy = _font(24)
    label = _font(16, bold=True)

    mark = _normalized_mark(340)
    canvas.alpha_composite(mark, (42, 54))
    draw.text((390, 72), "SunoJump", font=title, fill=INK)
    draw.text(
        (394, 184), "Hear the variation. Keep the evidence.", font=tagline, fill=CYAN
    )
    draw.text(
        (396, 250),
        "Local audio preview, careful batch processing, and reports you can reproduce.",
        font=copy,
        fill=MUTED,
    )
    x = 396
    for text in (
        "PRIVATE BY DEFAULT",
        "A/B LISTENING",
        "WINDOWS + SOURCE",
        f"V{version}",
    ):
        x += _rounded_label(draw, (x, 326), text, label) + 12
    return canvas.convert("RGB")


def _write_version_info(version: str) -> None:
    numbers = [int(part) for part in version.split(".")]
    while len(numbers) < 4:
        numbers.append(0)
    numeric = ", ".join(str(number) for number in numbers[:4])
    VERSION_INFO.write_text(
        "# UTF-8\n"
        "VSVersionInfo(\n"
        f"  ffi=FixedFileInfo(filevers=({numeric}), prodvers=({numeric}), "
        "mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
        "  kids=[\n"
        "    StringFileInfo([StringTable('040904B0', [\n"
        "      StringStruct('CompanyName', 'SysAdminDoc'),\n"
        "      StringStruct('FileDescription', 'SunoJump audio variation and evidence workstation'),\n"
        f"      StringStruct('FileVersion', '{version}'),\n"
        "      StringStruct('InternalName', 'SunoJump'),\n"
        "      StringStruct('LegalCopyright', 'Copyright (c) 2026 SysAdminDoc'),\n"
        "      StringStruct('OriginalFilename', 'SunoJump.exe'),\n"
        "      StringStruct('ProductName', 'SunoJump'),\n"
        f"      StringStruct('ProductVersion', '{version}')\n"
        "    ])]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    version = _version()
    APP_MARK.parent.mkdir(parents=True, exist_ok=True)
    SOCIAL_PREVIEW.parent.mkdir(parents=True, exist_ok=True)

    mark = _normalized_mark(1024)
    mark.save(APP_MARK, optimize=True)
    mark.save(
        APP_ICON,
        format="ICO",
        sizes=[
            (16, 16),
            (24, 24),
            (32, 32),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        ],
    )
    _draw_banner(version).save(BANNER, optimize=True, quality=95)
    _draw_social(version).save(SOCIAL_PREVIEW, optimize=True, quality=95)
    _write_version_info(version)

    for path in (APP_MARK, APP_ICON, VERSION_INFO, BANNER, SOCIAL_PREVIEW):
        print(f"Generated {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
