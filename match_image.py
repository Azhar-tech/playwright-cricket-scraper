"""Generate preview match announcement images and captions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

TRACKED_TEAMS = [
    "India",
    "England",
    "Australia",
    "New Zealand",
    "South Africa",
    "Pakistan",
    "West Indies",
    "Bangladesh",
    "Sri Lanka",
    "Afghanistan",
    "Ireland",
    "Zimbabwe",
]

TEAM_KITS: dict[str, tuple[str, str, str]] = {
    "India": ("IND", "#004BA0", "#FF9933"),
    "England": ("ENG", "#1E3A8A", "#FFFFFF"),
    "Australia": ("AUS", "#FFCD00", "#00843D"),
    "New Zealand": ("NZ", "#000000", "#FFFFFF"),
    "South Africa": ("SA", "#007A4D", "#FFB612"),
    "Pakistan": ("PAK", "#006600", "#FFFFFF"),
    "West Indies": ("WI", "#750000", "#FFD700"),
    "Bangladesh": ("BAN", "#006A4E", "#E30A17"),
    "Sri Lanka": ("SL", "#003DA5", "#FFB81C"),
    "Afghanistan": ("AFG", "#0066CC", "#000000"),
    "Ireland": ("IRE", "#009A44", "#FFFFFF"),
    "Zimbabwe": ("ZIM", "#C8102E", "#FFD700"),
}

THEME_TOP = "#E91E8C"
THEME_BOTTOM = "#FF4081"
THEME_PANEL = "#0A1E3A"
THEME_DATE = "#4DD0E1"
BANNER_LEFT = ("#E91E8C", "#9C27B0")
BANNER_RIGHT = ("#1565C0", "#0D47A1")

MATCH_LABEL_PATTERN = re.compile(
    r"\d+(?:st|nd|rd|th)\s+(?:T20I?|ODI|One\s*Day|Test)(?:\s*\([^)]+\))?",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*,?\s*"
    r"(\d{1,2}\s+\w+\s*,?\s*\d{4}|\w+\s+\d{1,2}\s*,?\s*\d{4})",
    re.IGNORECASE,
)
FORMAT_PATTERN = re.compile(r"\b(T20I?|ODI|One\s*Day|Test)\b", re.IGNORECASE)
SCORE_PATTERN = re.compile(r"\d+/\d+")
CRICKET_IRELAND_ORG = re.compile(r"cricket\s+ireland", re.IGNORECASE)

_BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = _BASE_DIR / "assets" / "preview"
GENERATED_IMAGES_DIR = _BASE_DIR / "generated_images"
IMAGE_SIZE = 1080
TOP_SECTION_HEIGHT = 670


@dataclass
class PreviewMatchInfo:
    team1: str
    team2: str
    series: str
    match_label: str
    date_str: str
    time_str: str
    format_tag: str
    venue: str = "TBC"
    match_key: str = ""


def _line_is_tracked_team(line: str) -> bool:
    stripped = line.strip()
    if not stripped or SCORE_PATTERN.search(stripped):
        return False
    if CRICKET_IRELAND_ORG.search(stripped):
        return False
    for team in TRACKED_TEAMS:
        if team.lower() == "ireland":
            if re.fullmatch(r"Ireland(\s+Women)?", stripped, re.IGNORECASE):
                return True
            continue
        if re.fullmatch(rf"{re.escape(team)}(\s+Women)?", stripped, re.IGNORECASE):
            return True
    return False


def _normalize_team_name(line: str) -> str:
    for team in TRACKED_TEAMS:
        if re.fullmatch(rf"{re.escape(team)}(\s+Women)?", line.strip(), re.IGNORECASE):
            if "women" in line.lower():
                return f"{team} Women"
            return team
    return line.strip()


def _detect_format(text: str) -> str:
    match = FORMAT_PATTERN.search(text)
    if not match:
        return "ODI"
    token = match.group(1).lower().replace(" ", "")
    if token.startswith("t20"):
        return "T20"
    if token in ("odi", "oneday"):
        return "ODI"
    if token == "test":
        return "TEST"
    return "ODI"


def _format_tag(fmt: str) -> str:
    if fmt == "T20":
        return "T20I"
    if fmt == "TEST":
        return "Test"
    return "ODI"


def _team_abbrev(team: str) -> str:
    base = team.replace(" Women", "")
    if base in TEAM_KITS:
        abbrev, _, _ = TEAM_KITS[base]
        if "Women" in team:
            return f"{abbrev}-W"
        return abbrev
    cleaned = re.sub(r"[^A-Za-z]", "", team)
    return (cleaned[:3] or "TBD").upper()


def _team_kit_colors(team: str) -> tuple[str, str]:
    base = team.replace(" Women", "")
    if base in TEAM_KITS:
        _, primary, secondary = TEAM_KITS[base]
        return primary, secondary
    return "#555555", "#FFFFFF"


def _hashtag_token(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text)


def _extract_venue_from_line(line: str) -> str:
    if not MATCH_LABEL_PATTERN.search(line):
        return ""
    parts = [part.strip() for part in line.split(",") if part.strip()]
    if len(parts) < 2:
        return ""
    for idx, part in enumerate(parts):
        if DATE_PATTERN.search(part):
            if idx > 0:
                candidate = parts[idx - 1]
                if not DATE_PATTERN.search(candidate) and not TIME_PATTERN.search(candidate):
                    return candidate
            break
    if len(parts) >= 2 and not DATE_PATTERN.search(parts[1]):
        return parts[1]
    return ""


def _banner_team_name(team: str) -> str:
    name = team.upper()
    if len(name) > 18:
        abbrev = _team_abbrev(team)
        suffix = " WOMEN" if "Women" in team else ""
        return f"{abbrev}{suffix}".strip()
    return name


def parse_preview_block(block: str) -> PreviewMatchInfo:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    teams = [_normalize_team_name(line) for line in lines if _line_is_tracked_team(line)]
    if len(teams) < 2:
        teams = (teams + ["TBD", "TBD"])[:2]

    match_label = ""
    venue = "TBC"
    fixture_line = ""
    for line in lines:
        if MATCH_LABEL_PATTERN.search(line):
            fixture_line = line
            match_label = MATCH_LABEL_PATTERN.search(line).group(0)  # type: ignore[union-attr]
            extracted = _extract_venue_from_line(line)
            if extracted:
                venue = extracted
            break

    time_str = "TBC"
    for line in lines:
        time_match = TIME_PATTERN.search(line)
        if time_match:
            time_str = time_match.group(0).upper()
            break

    date_str = datetime.now().strftime("%A, %d %B")
    for line in lines:
        date_match = DATE_PATTERN.search(line)
        if date_match:
            raw = date_match.group(0).strip(" ,")
            if raw.lower().startswith(
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
            ):
                date_str = raw
            else:
                date_str = f"{datetime.now():%A}, {raw}"
            break

    series = "International Cricket"
    skip_prefixes = ("live", "result", "today,", "tomorrow,", "match yet", "match starts")
    for line in lines:
        lower = line.lower()
        if any(lower.startswith(p) for p in skip_prefixes):
            continue
        if _line_is_tracked_team(line) or MATCH_LABEL_PATTERN.search(line):
            continue
        if TIME_PATTERN.search(line) or SCORE_PATTERN.search(line):
            continue
        if len(line) > 5:
            series = line
            break

    if venue == "TBC" and fixture_line:
        extracted = _extract_venue_from_line(fixture_line)
        if extracted:
            venue = extracted

    fmt = _detect_format(block)
    team_key = "-".join(sorted(t.lower().replace(" ", "-") for t in teams[:2]))
    match_key = f"{team_key}|{fmt}|{match_label}"

    return PreviewMatchInfo(
        team1=teams[0],
        team2=teams[1],
        series=series,
        match_label=match_label or f"{fmt} Match",
        date_str=date_str,
        time_str=time_str,
        format_tag=_format_tag(fmt),
        venue=venue,
        match_key=match_key,
    )


def build_preview_caption(info: PreviewMatchInfo) -> str:
    abbrev1 = _team_abbrev(info.team1)
    abbrev2 = _team_abbrev(info.team2)
    series_tag = _hashtag_token(info.series)
    headline = (
        f"Today! {info.team1} vs {info.team2}, {info.match_label} at {info.time_str} "
        f"— {info.series}."
    )
    hashtags = [
        f"#{abbrev1}vs{abbrev2}",
        f"#{abbrev2}vs{abbrev1}",
        f"#{info.format_tag}",
        f"#{series_tag}" if series_tag else "",
        f"#Team{_hashtag_token(info.team1.replace(' Women', ''))}",
        f"#Team{_hashtag_token(info.team2.replace(' Women', ''))}",
        "#CricketUpdates",
    ]
    tags = " ".join(tag for tag in hashtags if tag)
    return f"{headline}\n\n{tags}"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _blend(c1: tuple[int, int, int], c2: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * ratio),
        int(c1[1] + (c2[1] - c1[1]) * ratio),
        int(c1[2] + (c2[2] - c1[2]) * ratio),
    )


def _darken(color: tuple[int, int, int], factor: float = 0.55) -> tuple[int, int, int]:
    return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))


def _draw_vertical_gradient(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    top: str,
    bottom: str,
    y_offset: int = 0,
) -> None:
    r1, g1, b1 = _hex_rgb(top)
    r2, g2, b2 = _hex_rgb(bottom)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        draw.line([(0, y_offset + y), (width, y_offset + y)], fill=(r, g, b))


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center_x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    draw.text((center_x - width // 2, y), text, font=font, fill=fill)


def _tint_sprite(image: Image.Image, primary: str, secondary: str) -> Image.Image:
    primary_rgb = _hex_rgb(primary)
    secondary_rgb = _hex_rgb(secondary)
    dark_rgb = _darken(primary_rgb)
    white_rgb = (245, 245, 245)
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            lum = int(0.299 * r + 0.587 * g + 0.114 * b)
            if lum < 70:
                color = dark_rgb
            elif lum < 150:
                color = primary_rgb
            elif lum < 210:
                color = secondary_rgb
            else:
                color = white_rgb
            pixels[x, y] = (*color, a)
    return rgba


def _draw_batsman_silhouette(facing: str) -> Image.Image:
    """Draw a simple batsman silhouette; facing is 'left' or 'right'."""
    w, h = 400, 520
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    flip = facing == "right"
    cx = w // 2

    def box(x1: int, y1: int, x2: int, y2: int) -> tuple[int, int, int, int]:
        if flip:
            x1, x2 = w - x1, w - x2
        return min(x1, x2), y1, max(x1, x2), y2

    def point(x: int, y: int) -> tuple[int, int]:
        return (w - x, y) if flip else (x, y)

    draw.ellipse(box(cx - 90, h - 40, cx + 90, h - 10), fill=(40, 40, 40, 180))
    draw.rounded_rectangle(box(cx - 55, h - 200, cx - 15, h - 70), radius=12, fill=(230, 230, 230, 255))
    draw.rounded_rectangle(box(cx + 15, h - 200, cx + 55, h - 70), radius=12, fill=(230, 230, 230, 255))
    draw.polygon(
        [point(cx - 50, h - 280), point(cx + 50, h - 280), point(cx + 40, h - 120), point(cx - 40, h - 120)],
        fill=(90, 90, 90, 255),
    )
    draw.rounded_rectangle(box(cx - 65, h - 400, cx + 65, h - 270), radius=20, fill=(140, 140, 140, 255))
    draw.ellipse(box(cx - 95, h - 390, cx - 45, h - 310), fill=(130, 130, 130, 255))
    draw.ellipse(box(cx + 45, h - 390, cx + 95, h - 310), fill=(130, 130, 130, 255))
    draw.ellipse(box(cx - 45, h - 470, cx + 45, h - 395), fill=(160, 160, 160, 255))
    draw.rectangle(box(cx - 40, h - 430, cx + 40, h - 405), fill=(200, 200, 200, 255))
    bat_x = cx + 70 if not flip else cx - 70
    draw.rounded_rectangle(box(bat_x - 12, h - 420, bat_x + 12, h - 180), radius=6, fill=(180, 180, 180, 255))
    draw.ellipse(box(bat_x - 18, h - 450, bat_x + 18, h - 410), fill=(200, 200, 200, 255))
    draw.line([point(cx + 60, h - 350), point(bat_x, h - 400)], fill=(120, 120, 120, 255), width=14)
    draw.line([point(cx - 60, h - 350), point(cx - 30, h - 320)], fill=(120, 120, 120, 255), width=12)
    return img


def _draw_stadium_overlay() -> Image.Image:
    w, h = IMAGE_SIZE, TOP_SECTION_HEIGHT
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Stadium bowl arcs
    for i, alpha in enumerate((60, 45, 30)):
        y = 80 + i * 40
        draw.arc([80 - i * 30, y, w - 80 + i * 30, h - 60 + i * 20], 200, 340, fill=(255, 255, 255, alpha), width=3)
    # Floodlight towers
    for x in (120, 280, w - 280, w - 120):
        draw.line([(x, 40), (x, 200)], fill=(255, 255, 255, 50), width=4)
        draw.line([(x - 30, 50), (x + 30, 50)], fill=(255, 255, 255, 40), width=3)
        draw.ellipse([x - 8, 35, x + 8, 51], fill=(255, 255, 255, 70))
    # Crowd hint lines
    for y in range(250, 420, 18):
        draw.line([(100, y), (w - 100, y)], fill=(255, 255, 255, 25), width=2)
    return img


def _ensure_preview_assets() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    assets = {
        "batsman_left.png": lambda: _draw_batsman_silhouette("left"),
        "batsman_right.png": lambda: _draw_batsman_silhouette("right"),
        "stadium.png": _draw_stadium_overlay,
    }
    for name, factory in assets.items():
        path = ASSETS_DIR / name
        if not path.exists():
            factory().save(path, "PNG")


def _load_asset(name: str) -> Image.Image:
    _ensure_preview_assets()
    path = ASSETS_DIR / name
    if path.exists():
        return Image.open(path).convert("RGBA")
    if name == "batsman_left.png":
        return _draw_batsman_silhouette("left")
    if name == "batsman_right.png":
        return _draw_batsman_silhouette("right")
    return _draw_stadium_overlay()


def _draw_skewed_banner(
    base: Image.Image,
    text: str,
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    colors: tuple[str, str],
    skew: int,
) -> None:
    banner = Image.new("RGBA", (width + abs(skew) + 20, height + 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(banner)
    left, top = 10 + max(skew, 0), 10
    points = [
        (left, top),
        (left + width, top),
        (left + width - skew, top + height),
        (left - skew, top + height),
    ]
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = _blend(_hex_rgb(colors[0]), _hex_rgb(colors[1]), ratio)
        y_min = top + y
        x_start = left - skew + int(skew * y / max(height - 1, 1))
        x_end = left + width - int(skew * y / max(height - 1, 1))
        draw.line([(x_start, y_min), (x_end, y_min)], fill=(*color, 255), width=1)
    draw.polygon(points, fill=None, outline=(255, 255, 255, 40))
    font = _load_font(26, bold=True)
    text_draw = ImageDraw.Draw(banner)
    bbox = text_draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text_draw.text(
        (left + (width - tw) // 2, top + (height - th) // 2 - 2),
        text,
        font=font,
        fill="#FFFFFF",
    )
    paste_x = center_x - banner.width // 2
    paste_y = center_y - banner.height // 2
    base.paste(banner, (paste_x, paste_y), banner)


def _draw_vs_emblem(draw: ImageDraw.ImageDraw, center_x: int, center_y: int) -> None:
    font = _load_font(110, bold=True)
    shadow_font = _load_font(112, bold=True)
    _draw_centered_text(draw, "VS", center_x + 3, center_y + 3, shadow_font, "#555555")
    _draw_centered_text(draw, "VS", center_x, center_y, font, "#F0F0F0")


def _draw_corner_shards(draw: ImageDraw.ImageDraw, panel_top: int) -> None:
    pink = (233, 30, 140, 200)
    cyan = (77, 208, 225, 200)
    draw.polygon([(0, panel_top + 80), (0, panel_top + 220), (90, panel_top + 150)], fill=pink)
    draw.polygon([(0, panel_top + 220), (0, panel_top + 360), (70, panel_top + 290)], fill=cyan)
    draw.polygon(
        [(IMAGE_SIZE, panel_top + 80), (IMAGE_SIZE, panel_top + 220), (IMAGE_SIZE - 90, panel_top + 150)],
        fill=pink,
    )
    draw.polygon(
        [(IMAGE_SIZE, panel_top + 220), (IMAGE_SIZE, panel_top + 360), (IMAGE_SIZE - 70, panel_top + 290)],
        fill=cyan,
    )


def _draw_info_panel(draw: ImageDraw.ImageDraw, info: PreviewMatchInfo, panel_top: int) -> None:
    draw.rectangle([(0, panel_top), (IMAGE_SIZE, IMAGE_SIZE)], fill=THEME_PANEL)
    _draw_corner_shards(draw, panel_top)

    label_font = _load_font(32, bold=True)
    date_font = _load_font(36, bold=True)
    time_font = _load_font(72, bold=True)
    venue_font = _load_font(28, bold=False)

    y = panel_top + 50
    _draw_centered_text(draw, info.match_label, IMAGE_SIZE // 2, y, label_font, "#FFFFFF")
    y += 55
    _draw_centered_text(draw, info.date_str, IMAGE_SIZE // 2, y, date_font, THEME_DATE)
    y += 65
    _draw_centered_text(draw, info.time_str, IMAGE_SIZE // 2, y, time_font, "#FFFFFF")
    y += 95
    venue_text = f"📍 {info.venue}"
    _draw_centered_text(draw, venue_text, IMAGE_SIZE // 2, y, venue_font, "#FFFFFF")


def generate_preview_image(info: PreviewMatchInfo) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", info.match_key)[:80]
    output_path = GENERATED_IMAGES_DIR / f"preview_{safe_key}.png"

    img = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), THEME_TOP)
    draw = ImageDraw.Draw(img)
    _draw_vertical_gradient(draw, IMAGE_SIZE, TOP_SECTION_HEIGHT, THEME_TOP, THEME_BOTTOM)

    stadium = _load_asset("stadium.png")
    if stadium.size != (IMAGE_SIZE, TOP_SECTION_HEIGHT):
        stadium = stadium.resize((IMAGE_SIZE, TOP_SECTION_HEIGHT), Image.Resampling.LANCZOS)
    img.paste(stadium, (0, 0), stadium)

    primary1, secondary1 = _team_kit_colors(info.team1)
    primary2, secondary2 = _team_kit_colors(info.team2)
    left_player = _tint_sprite(_load_asset("batsman_left.png"), primary1, secondary1)
    right_player = _tint_sprite(_load_asset("batsman_right.png"), primary2, secondary2)

    img_rgba = img.convert("RGBA")
    img_rgba.paste(left_player, (40, 180), left_player)
    img_rgba.paste(right_player, (IMAGE_SIZE - 440, 180), right_player)
    img = img_rgba.convert("RGB")
    draw = ImageDraw.Draw(img)

    series_font = _load_font(24, bold=True)
    series_line = info.series[:60]
    _draw_centered_text(draw, series_line, IMAGE_SIZE // 2, 28, series_font, "#FFFFFF")

    _draw_vs_emblem(draw, IMAGE_SIZE // 2, 470)
    _draw_skewed_banner(
        img,
        _banner_team_name(info.team1),
        270,
        560,
        300,
        52,
        BANNER_LEFT,
        skew=18,
    )
    _draw_skewed_banner(
        img,
        _banner_team_name(info.team2),
        IMAGE_SIZE - 270,
        560,
        300,
        52,
        BANNER_RIGHT,
        skew=-18,
    )

    _draw_info_panel(draw, info, TOP_SECTION_HEIGHT)

    img.save(output_path, "PNG")
    return output_path
