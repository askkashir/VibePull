#!/usr/bin/env python3
"""Generate local component preview images for the VibePull corpus.

This renders a deterministic visual preview for each indexed component using the
metadata already produced by enrichment. The output is saved under web/previews
so the presentation UI can display real PNG assets instead of rebuilding mockups
in every card at runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None


ROOT = Path(__file__).resolve().parents[1]
METADATA_PATH = ROOT / "indexes" / "text_metadata.json"
ENRICHED_ROOT = ROOT / "data" / "enriched"
PREVIEW_ROOT = ROOT / "web" / "previews"

WIDTH = 1200
HEIGHT = 720


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None or value == "":
        return []
    return [str(value)]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "component"


def clean_name(item: dict[str, Any]) -> str:
    return str(
        item.get("display_name")
        or item.get("name")
        or item.get("clean_id")
        or item.get("id")
        or "Component"
    )


def preview_path_for(item: dict[str, Any]) -> Path:
    server = slugify(str(item.get("server") or "unknown"))
    identity = slugify(str(item.get("id") or item.get("clean_id") or clean_name(item)))
    return PREVIEW_ROOT / server / f"{identity}.png"


def preview_url_for(item: dict[str, Any]) -> str:
    path = preview_path_for(item).relative_to(ROOT / "web").as_posix()
    return f"/{path}"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


FONTS = {
    "xs": font(20),
    "sm": font(24),
    "md": font(32),
    "lg": font(48, bold=True),
    "xl": font(64, bold=True),
    "mono": font(22),
}


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: Any, outline: Any = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def fit_text(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def meta_text(item: dict[str, Any]) -> str:
    return " ".join(
        [
            clean_name(item),
            str(item.get("clean_id") or ""),
            str(item.get("id") or ""),
            str(item.get("component_type") or ""),
            str(item.get("visual_summary") or ""),
            " ".join(as_list(item.get("tags"))),
            " ".join(as_list(item.get("style_tags"))),
            " ".join(as_list(item.get("interaction_tags"))),
        ]
    ).lower()


def has_meta(item: dict[str, Any], *terms: str) -> bool:
    words = meta_text(item)
    return any(re.search(rf"(^|[^a-z0-9]){re.escape(term)}([^a-z0-9]|$)", words) for term in terms)


def rng_for(item: dict[str, Any]) -> random.Random:
    key = f"{item.get('server')}::{item.get('id')}::{clean_name(item)}"
    seed = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], 16)
    return random.Random(seed)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def accent_for(item: dict[str, Any]) -> tuple[str, str, str]:
    words = meta_text(item)
    server = str(item.get("server") or "").lower()
    if "gradient" in words or "aurora" in words:
        return "#f5f5f5", "#8b5cf6", "#22d3ee"
    if "glass" in words:
        return "#f8fafc", "#67e8f9", "#a78bfa"
    if "success" in words or "green" in words:
        return "#f0fdf4", "#22c55e", "#86efac"
    if "warning" in words or "orange" in words:
        return "#fff7ed", "#f97316", "#fed7aa"
    if "danger" in words or "error" in words or "red" in words:
        return "#fff1f2", "#ef4444", "#fda4af"
    by_server = {
        "aceternity": ("#fdf2f8", "#ec4899", "#a855f7"),
        "magicui": ("#faf5ff", "#8b5cf6", "#38bdf8"),
        "mantine": ("#ecfdf5", "#10b981", "#93c5fd"),
        "heroui": ("#fff7ed", "#f97316", "#fde68a"),
        "radixuithemes": ("#eff6ff", "#3b82f6", "#93c5fd"),
        "shadcn": ("#f8fafc", "#60a5fa", "#e5e7eb"),
        "parkui": ("#f0fdfa", "#14b8a6", "#ccfbf1"),
        "tremor": ("#eef2ff", "#6366f1", "#a5b4fc"),
        "floatui": ("#fdf4ff", "#d946ef", "#c4b5fd"),
    }
    return by_server.get(server, ("#f5f5f5", "#a3a3a3", "#737373"))


def wrap_text(text: str, width: int, draw: ImageDraw.ImageDraw, fnt: ImageFont.ImageFont, max_lines: int = 3) -> list[str]:
    words = re.sub(r"\s+", " ", str(text or "")).strip().split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if text_size(draw, candidate, fnt)[0] <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = fit_text(lines[-1], max(8, len(lines[-1]) - 1))
    return lines


def classify(item: dict[str, Any]) -> str:
    component_type = str(item.get("component_type") or "other").lower()
    has = lambda *terms: has_meta(item, *terms)

    if component_type == "background" or has("background", "aurora", "beam", "beams", "shader", "grid pattern"):
        return "background"
    if has("ascii", "pixel", "pixelated"):
        return "ascii"
    if has("rating", "stars"):
        return "rating"
    if has("progress", "stepper", "steps"):
        return "progress"
    if has("color picker", "color swatch", "color"):
        return "color"
    if has("file upload", "dropzone", "upload"):
        return "file"
    if has("accordion", "collapsible", "expandable"):
        return "accordion"
    if has("avatar", "testimonial", "user profile", "profile"):
        return "testimonial"
    if component_type == "carousel" or has("carousel", "slider", "swipe"):
        return "carousel"
    if component_type == "tooltip" or has("tooltip", "popover"):
        return "tooltip"
    if "sidebar" in component_type or has("sidebar"):
        return "sidebar"
    if "calendar" in component_type or has("calendar", "date picker"):
        return "calendar"
    if "toast" in component_type or has("toast", "notification"):
        return "toast"
    if has("globe", "map"):
        return "globe"
    if has("marquee"):
        return "marquee"
    if component_type == "button" or has("button"):
        return "button"
    if component_type == "loader" or has("loader", "spinner", "loading"):
        return "loader"
    if component_type == "chart" or has("chart", "graph"):
        return "chart"
    if component_type == "table" or has("table", "data grid"):
        return "table"
    if component_type == "modal" or has("modal", "dialog"):
        return "modal"
    if component_type == "navbar" or has("navbar", "navigation menu"):
        return "navbar"
    if component_type == "hero" or has("hero"):
        return "hero"
    if component_type == "input" or has("input", "select", "field"):
        return "input"
    if component_type == "form" or has("form"):
        return "form"
    if component_type == "badge" or has("badge", "tag", "chip"):
        return "badge"
    if component_type == "card" or has("card", "3d"):
        return "card"
    return "code"


def make_canvas(item: dict[str, Any]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    _, accent, _ = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    image = Image.new("RGB", (WIDTH, HEIGHT), "#080808")
    draw = ImageDraw.Draw(image)
    # Fine, low-contrast gallery grid. Keep the preview itself visual-only; the
    # card already renders title, tags, source, and usage metadata.
    for x in range(0, WIDTH, 44):
        draw.line((x, 0, x, HEIGHT), fill="#0f0f0f", width=1)
    for y in range(0, HEIGHT, 44):
        draw.line((0, y, WIDTH, y), fill="#0f0f0f", width=1)

    # Soft radial glow approximation.
    for radius, alpha in [(520, 22), (380, 26), (240, 30)]:
        overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        odraw.ellipse((WIDTH - radius - 100, 60, WIDTH + radius - 100, 60 + radius * 2), fill=(ar, ag, ab, alpha))
        image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"))
    draw = ImageDraw.Draw(image)
    rounded(draw, (64, 60, WIDTH - 64, HEIGHT - 60), 32, "#0d0d0d", "#232323", 2)
    # A subtle side plane gives the generated thumbnail some dimensionality
    # without adding text or duplicate metadata.
    draw.polygon([(WIDTH - 64, 90), (WIDTH, 118), (WIDTH, HEIGHT - 120), (WIDTH - 64, HEIGHT - 60)], fill="#242424")
    return image, draw


def draw_metadata_overlay(image: Image.Image, item: dict[str, Any]) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    primary, accent, secondary = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    sr, sg, sb = hex_to_rgb(secondary)
    pr, pg, pb = hex_to_rgb(primary)
    rng = rng_for(item)

    if has_meta(item, "glassmorphism", "glass"):
        for _ in range(3):
            x = rng.randint(140, 780)
            y = rng.randint(120, 470)
            w = rng.randint(135, 250)
            h = rng.randint(52, 108)
            draw.rounded_rectangle((x, y, x + w, y + h), radius=22, fill=(255, 255, 255, 16), outline=(255, 255, 255, 40), width=2)

    if has_meta(item, "animated", "animation", "motion", "hover", "draggable", "scrollable", "rotating"):
        for _ in range(5):
            y = rng.randint(125, 560)
            x = rng.randint(95, 620)
            draw.line((x, y, x + rng.randint(130, 340), y - rng.randint(8, 70)), fill=(sr, sg, sb, 68), width=rng.randint(2, 5))

    if has_meta(item, "3d", "depth", "perspective"):
        draw.polygon([(760, 140), (1060, 210), (1040, 520), (720, 450)], fill=(255, 255, 255, 16), outline=(255, 255, 255, 38))

    if has_meta(item, "gradient", "colorful", "aurora"):
        for idx, color in enumerate([(ar, ag, ab), (sr, sg, sb), (pr, pg, pb)]):
            x = 120 + idx * 240 + rng.randint(-20, 20)
            y = 90 + idx * 62 + rng.randint(-10, 10)
            draw.ellipse((x, y, x + 470, y + 320), fill=(*color, 20))


def draw_lines(draw: ImageDraw.ImageDraw, x: int, y: int, widths: list[int], height: int = 16, gap: int = 18) -> None:
    for idx, w in enumerate(widths):
        rounded(draw, (x, y + idx * gap, x + w, y + idx * gap + height), height // 2, "#d7d7d7" if idx == 0 else "#777777")


def draw_button(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    label = fit_text(clean_name(item), 16)
    box = (390, 286, 810, 400)
    rounded(draw, box, 24, "#f5f5f5", "#ffffff")
    tw, th = text_size(draw, label, FONTS["md"])
    draw.text(((box[0] + box[2] - tw) // 2, (box[1] + box[3] - th) // 2 - 3), label, fill="#080808", font=FONTS["md"])
    rounded(draw, (430, 430, 770, 452), 11, "#2a2a2a")


def draw_card(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (345, 190, 855, 480), 28, "#151515", "#3a3a3a", 2)
    rounded(draw, (390, 238, 810, 350), 22, "#242424", "#444444")
    draw_lines(draw, 390, 382, [260, 370, 210], 15, 25)
    rounded(draw, (650, 236, 810, 272), 18, "#f2f2f2")


def draw_loader(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    cx, cy = WIDTH // 2, 330
    for i in range(16):
        angle = (math.pi * 2 * i) / 16
        alpha = 70 + i * 10
        color = (alpha, alpha, alpha)
        x = cx + math.cos(angle) * 112
        y = cy + math.sin(angle) * 112
        draw.rounded_rectangle((x - 10, y - 28, x + 10, y + 28), radius=10, fill=color)
    draw.ellipse((cx - 60, cy - 60, cx + 60, cy + 60), fill="#0f0f0f", outline="#2d2d2d", width=2)


def draw_chart(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    base = 478
    left = 250
    for idx, height in enumerate([120, 220, 168, 292, 202, 250, 145]):
        x = left + idx * 95
        rounded(draw, (x, base - height, x + 56, base), 16, "#d8d8d8")
    draw.line((230, base, 935, base), fill="#555555", width=3)


def draw_table(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    x0, y0, x1 = 210, 200, 990
    rounded(draw, (x0, y0, x1, 482), 24, "#141414", "#383838", 2)
    for row in range(6):
        y = y0 + 38 + row * 38
        draw.line((x0, y, x1, y), fill="#292929", width=2)
    for col in [420, 650, 835]:
        draw.line((col, y0, col, 482), fill="#242424", width=2)
    for row in range(5):
        y = y0 + 58 + row * 38
        draw_lines(draw, x0 + 28, y, [120, 170, 110], 10, 0)


def draw_modal(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (120, 150, 1080, 530), 24, "#050505")
    rounded(draw, (340, 210, 860, 472), 28, "#151515", "#444444", 2)
    draw_lines(draw, 390, 270, [210, 330, 280], 17, 34)
    rounded(draw, (626, 400, 810, 444), 18, "#f5f5f5")


def draw_navbar(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (230, 235, 970, 318), 42, "#161616", "#444444", 2)
    rounded(draw, (270, 268, 355, 286), 9, "#f5f5f5")
    x = 575
    for w in [70, 82, 64]:
        rounded(draw, (x, 266, x + w, 288), 11, "#6f6f6f")
        x += w + 28
    rounded(draw, (790, 255, 930, 300), 20, "#f5f5f5")


def draw_form(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (330, 170, 870, 500), 26, "#141414", "#3a3a3a", 2)
    for idx, label in enumerate(["Name", "Email", "Message"]):
        y = 225 + idx * 74
        draw.text((380, y - 28), label, fill="#9f9f9f", font=FONTS["xs"])
        rounded(draw, (380, y, 820, y + 44), 13, "#0b0b0b", "#333333")
    rounded(draw, (380, 432, 560, 476), 18, "#f5f5f5")


def draw_badge(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    labels = [clean_name(item), "Active", "New", "UI"]
    x = 300
    y = 300
    for label in labels:
        label = fit_text(label, 14)
        w, _ = text_size(draw, label, FONTS["md"])
        rounded(draw, (x, y, x + w + 52, y + 58), 29, "#181818", "#4a4a4a", 2)
        draw.text((x + 26, y + 13), label, fill="#f5f5f5", font=FONTS["md"])
        x += w + 74


def draw_calendar(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (390, 150, 810, 530), 28, "#141414", "#3f3f3f", 2)
    draw.text((444, 198), "June 2026", fill="#f5f5f5", font=FONTS["md"])
    start_x, start_y = 430, 260
    for i in range(35):
        x = start_x + (i % 7) * 50
        y = start_y + (i // 7) * 42
        fill = "#f5f5f5" if i == 16 else "#2b2b2b"
        rounded(draw, (x, y, x + 32, y + 32), 9, fill)


def draw_sidebar(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (260, 150, 940, 530), 24, "#121212", "#383838", 2)
    rounded(draw, (260, 150, 455, 530), 24, "#0b0b0b", "#2e2e2e", 2)
    for idx in range(7):
        y = 205 + idx * 42
        rounded(draw, (298, y, 415, y + 20), 10, "#f5f5f5" if idx == 1 else "#555555")
    draw_lines(draw, 510, 230, [270, 350, 210], 18, 36)
    rounded(draw, (510, 370, 875, 468), 18, "#242424", "#3c3c3c")


def draw_toast(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    for offset in [0, 34, 68]:
        rounded(draw, (430 + offset, 250 + offset, 900 + offset, 350 + offset), 22, "#151515", "#3d3d3d", 2)
        draw_lines(draw, 470 + offset, 282 + offset, [210, 320], 13, 25)


def draw_globe(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    cx, cy, r = WIDTH // 2, 330, 155
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#111111", outline="#f0f0f0", width=2)
    for offset in [-95, -50, 0, 50, 95]:
        draw.arc((cx - r, cy - r + offset, cx + r, cy + r - offset), 0, 360, fill="#555555", width=2)
        draw.arc((cx - r + offset, cy - r, cx + r - offset, cy + r), 90, 270, fill="#444444", width=2)
    for x, y in [(cx - 70, cy - 30), (cx + 45, cy + 35), (cx + 12, cy - 86)]:
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill="#f5f5f5")


def draw_marquee(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    for col in range(4):
        for row in range(4):
            x = 310 + col * 130 + row * 18
            y = 175 + row * 82 - col * 12
            rounded(draw, (x, y, x + 110, y + 66), 12, "#1c1c1c", "#444444")


def draw_hero(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    draw_lines(draw, 240, 250, [520, 430], 38, 54)
    rounded(draw, (240, 380, 430, 435), 22, "#f5f5f5")
    rounded(draw, (700, 225, 960, 460), 30, "#171717", "#3f3f3f")


def draw_code(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (260, 170, 940, 500), 24, "#101010", "#373737", 2)
    colors = ["#e5e5e5", "#777777", "#555555", "#aaaaaa"]
    for idx in range(12):
        x = 310 + (idx % 3) * 24
        y = 220 + idx * 20
        w = [360, 460, 250, 520][idx % 4]
        rounded(draw, (x, y, x + w, y + 10), 5, colors[idx % len(colors)])


def draw_background(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    _, accent, secondary = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    sr, sg, sb = hex_to_rgb(secondary)
    rng = rng_for(item)
    if has_meta(item, "beam", "beams"):
        for idx in range(12):
            x = 80 + idx * 92
            draw.line((x, 590, x + rng.randint(-160, 180), 95), fill=(ar, ag, ab, 110), width=rng.randint(3, 8))
        for _ in range(7):
            x, y = rng.randint(170, 1010), rng.randint(160, 480)
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(255, 255, 255, 190))
    elif has_meta(item, "grid", "pattern"):
        for step in [28, 56, 112]:
            for x in range(110, 1090, step):
                draw.line((x, 110, x, 590), fill=(ar, ag, ab, 45 if step == 28 else 70), width=1)
            for y in range(110, 590, step):
                draw.line((110, y, 1090, y), fill=(sr, sg, sb, 45 if step == 28 else 70), width=1)
    else:
        for idx in range(8):
            y = 170 + idx * 28
            draw.arc((120, y, 1080, y + 330), 185, 355, fill=(ar, ag, ab, 80 - idx * 5), width=9)
            draw.arc((80, y + 40, 1030, y + 390), 190, 350, fill=(sr, sg, sb, 52), width=5)


def draw_carousel(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    _, accent, secondary = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    sr, sg, sb = hex_to_rgb(secondary)
    cards = [(250, 210, 520, 480), (465, 170, 735, 520), (680, 210, 950, 480)]
    for idx, box in enumerate(cards):
        fill = "#181818" if idx == 1 else "#101010"
        rounded(draw, box, 28, fill, "#444444", 2)
        inset = 36
        rounded(draw, (box[0] + inset, box[1] + 42, box[2] - inset, box[1] + 150), 20, (ar, ag, ab) if idx == 1 else "#242424")
        draw_lines(draw, box[0] + 42, box[3] - 125, [150, 190, 110], 12, 23)
    draw.arc((205, 280, 315, 410), 115, 245, fill=(sr, sg, sb), width=8)
    draw.arc((885, 280, 995, 410), -65, 65, fill=(sr, sg, sb), width=8)


def draw_tooltip(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    _, accent, _ = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    for idx, x in enumerate([410, 500, 590, 680]):
        draw.ellipse((x, 330, x + 64, 394), fill="#1f1f1f", outline="#505050", width=2)
    rounded(draw, (438, 205, 762, 300), 22, "#171717", "#4a4a4a", 2)
    draw.polygon([(590, 300), (620, 300), (606, 326)], fill="#171717", outline="#4a4a4a")
    draw_lines(draw, 475, 238, [120, 210], 12, 24)
    draw.ellipse((536, 168, 582, 214), fill=(ar, ag, ab))


def draw_ascii(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    chars = ["#", "@", "%", "&", "+", ":", "."]
    rng = rng_for(item)
    _, accent, _ = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    for row in range(12):
        for col in range(30):
            dist = abs(col - 15) + abs(row - 6)
            char = chars[min(len(chars) - 1, dist // 3)]
            alpha = max(80, 230 - dist * 12)
            draw.text((240 + col * 24, 165 + row * 28), char if rng.random() > 0.15 else ".", fill=(ar, ag, ab) if dist < 8 else (alpha, alpha, alpha), font=FONTS["mono"])


def draw_file(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    rounded(draw, (330, 170, 870, 505), 28, "#111111", "#3f3f3f", 2)
    rounded(draw, (390, 230, 810, 420), 24, "#0a0a0a", "#555555", 3)
    draw.arc((540, 250, 660, 370), 205, 335, fill="#f5f5f5", width=10)
    draw.line((600, 266, 600, 356), fill="#f5f5f5", width=8)
    draw.line((562, 305, 600, 266), fill="#f5f5f5", width=8)
    draw.line((638, 305, 600, 266), fill="#f5f5f5", width=8)
    draw_lines(draw, 430, 448, [220, 330], 13, 24)


def draw_progress(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    _, accent, _ = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    if has_meta(item, "stepper", "steps"):
        xs = [330, 470, 610, 750, 890]
        for i in range(len(xs) - 1):
            draw.line((xs[i] + 30, 330, xs[i + 1] - 30, 330), fill="#3a3a3a", width=8)
            if i < 2:
                draw.line((xs[i] + 30, 330, xs[i + 1] - 30, 330), fill=(ar, ag, ab), width=8)
        for idx, x in enumerate(xs):
            fill = (ar, ag, ab) if idx < 3 else "#151515"
            draw.ellipse((x - 34, 296, x + 34, 364), fill=fill, outline="#555555", width=3)
    else:
        for idx, w in enumerate([650, 500, 720]):
            y = 250 + idx * 85
            rounded(draw, (250, y, 950, y + 36), 18, "#161616", "#3a3a3a", 2)
            rounded(draw, (250, y, 250 + w, y + 36), 18, (ar, ag, ab))


def draw_rating(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    _, accent, _ = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    cx = 375
    for idx in range(5):
        x = cx + idx * 92
        points = []
        for i in range(10):
            radius = 40 if i % 2 == 0 else 18
            angle = -math.pi / 2 + i * math.pi / 5
            points.append((x + math.cos(angle) * radius, 330 + math.sin(angle) * radius))
        draw.polygon(points, fill=(ar, ag, ab) if idx < 4 else "#252525", outline="#666666")
    rounded(draw, (375, 430, 825, 464), 17, "#181818", "#3a3a3a")


def draw_color(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    swatches = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#06b6d4", "#3b82f6", "#8b5cf6", "#ec4899"]
    for idx, color in enumerate(swatches):
        x = 290 + (idx % 4) * 155
        y = 210 + (idx // 4) * 120
        rounded(draw, (x, y, x + 108, y + 88), 22, color, "#ffffff", 2 if idx == 5 else 0)
    rounded(draw, (370, 470, 830, 508), 19, "#151515", "#454545", 2)


def draw_accordion(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    x0, y = 310, 185
    for idx in range(4):
        h = 68 if idx != 1 else 150
        rounded(draw, (x0, y, 890, y + h), 18, "#141414", "#3a3a3a", 2)
        draw_lines(draw, x0 + 38, y + 27, [230], 13, 0)
        draw.line((830, y + 34, 850, y + 34), fill="#f5f5f5", width=4)
        if idx != 1:
            draw.line((840, y + 24, 840, y + 44), fill="#f5f5f5", width=4)
        else:
            draw_lines(draw, x0 + 38, y + 82, [420, 310], 12, 25)
        y += h + 16


def draw_testimonial(draw: ImageDraw.ImageDraw, item: dict[str, Any]) -> None:
    _, accent, secondary = accent_for(item)
    ar, ag, ab = hex_to_rgb(accent)
    sr, sg, sb = hex_to_rgb(secondary)
    rounded(draw, (300, 180, 900, 505), 28, "#141414", "#3e3e3e", 2)
    for idx, x in enumerate([390, 520, 650]):
        draw.ellipse((x, 245 - idx * 12, x + 92, 337 - idx * 12), fill=(ar, ag, ab) if idx == 1 else "#252525", outline=(sr, sg, sb), width=2)
    draw_lines(draw, 390, 390, [380, 310, 210], 14, 27)


DRAWERS = {
    "button": draw_button,
    "card": draw_card,
    "loader": draw_loader,
    "chart": draw_chart,
    "table": draw_table,
    "modal": draw_modal,
    "navbar": draw_navbar,
    "input": draw_form,
    "form": draw_form,
    "badge": draw_badge,
    "calendar": draw_calendar,
    "sidebar": draw_sidebar,
    "toast": draw_toast,
    "globe": draw_globe,
    "marquee": draw_marquee,
    "hero": draw_hero,
    "code": draw_code,
    "background": draw_background,
    "carousel": draw_carousel,
    "tooltip": draw_tooltip,
    "ascii": draw_ascii,
    "file": draw_file,
    "progress": draw_progress,
    "rating": draw_rating,
    "color": draw_color,
    "accordion": draw_accordion,
    "testimonial": draw_testimonial,
}


def render_preview(item: dict[str, Any], out_path: Path) -> str:
    image, draw = make_canvas(item)
    variant = classify(item)
    DRAWERS.get(variant, draw_code)(draw, item)
    draw_metadata_overlay(image, item)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path, optimize=True)
    return variant


def iter_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    records = [item for item in load_json(METADATA_PATH, []) if isinstance(item, dict) and item.get("id")]
    if args.server:
        records = [item for item in records if str(item.get("server") or "").lower() == args.server.lower()]
    if args.component_id:
        needle = args.component_id.lower()
        records = [
            item for item in records
            if needle in str(item.get("id") or "").lower()
            or needle in str(item.get("clean_id") or "").lower()
            or needle in clean_name(item).lower()
        ]
    if args.limit:
        records = records[: args.limit]
    return records


def update_enriched_preview_urls(records: list[dict[str, Any]]) -> int:
    by_key = {
        f"{str(item.get('server') or '').lower()}::{str(item.get('id') or '')}": preview_url_for(item)
        for item in records
    }
    updated = 0
    for path in ENRICHED_ROOT.rglob("*.json"):
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        component_key = f"{str(payload.get('server') or '').lower()}::{str(payload.get('id') or '')}"
        preview_url = by_key.get(component_key)
        if not preview_url or payload.get("preview_url") == preview_url:
            continue
        payload["preview_url"] = preview_url
        save_json(path, payload)
        updated += 1
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", help="Only generate previews for one source/server.")
    parser.add_argument("--component-id", help="Generate previews whose id/name contains this value.")
    parser.add_argument("--limit", type=int, help="Maximum number of previews to generate.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing preview PNGs.")
    parser.add_argument("--no-update-json", action="store_true", help="Do not write preview_url into enriched JSON files.")
    args = parser.parse_args()

    records = iter_records(args)
    iterator = tqdm(records, desc="Generating previews") if tqdm else records
    generated = 0
    skipped = 0
    variants: dict[str, int] = {}
    for item in iterator:
        out_path = preview_path_for(item)
        if out_path.exists() and not args.force:
            skipped += 1
            continue
        variant = render_preview(item, out_path)
        variants[variant] = variants.get(variant, 0) + 1
        generated += 1

    json_updates = 0 if args.no_update_json else update_enriched_preview_urls(records)
    print(json.dumps({
        "records": len(records),
        "generated": generated,
        "skipped_existing": skipped,
        "json_updated": json_updates,
        "preview_root": str(PREVIEW_ROOT),
        "variants": dict(sorted(variants.items())),
    }, indent=2))


if __name__ == "__main__":
    main()
