"""Generate preview match announcement images and captions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
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

TEAM_STYLES: dict[str, tuple[str, str]] = {
    "India": ("IND", "#138808"),
    "England": ("ENG", "#1E3A8A"),
    "Australia": ("AUS", "#FFCD00"),
    "New Zealand": ("NZ", "#000000"),
    "South Africa": ("SA", "#007A4D"),
    "Pakistan": ("PAK", "#006600"),
    "West Indies": ("WI", "#750000"),
    "Bangladesh": ("BAN", "#006A4E"),
    "Sri Lanka": ("SL", "#003DA5"),
    "Afghanistan": ("AFG", "#0066CC"),
    "Ireland": ("IRE", "#009A44"),
    "Zimbabwe": ("ZIM", "#C8102E"),
}

BACKGROUND_PALETTES = [
    ("#FFB347", "#FFCC33"),
    ("#667EEA", "#764BA2"),
    ("#11998E", "#38EF7D"),
    ("#FC466B", "#3F5EFB"),
    ("#F7971E", "#FFD200"),
    ("#8360C3", "#2EBF91"),
]

MATCH_LABEL_PATTERN = re.compile(
    r"\d+(?:st|nd|rd|th)\s+(?:T20I?|ODI|One\s*Day|Test)",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*-?\s*"
    r"(\d{1,2}\s+\w+\s*,?\s*\d{4}|\w+\s+\d{1,2}\s*,?\s*\d{4})",
    re.IGNORECASE,
)
FORMAT_PATTERN = re.compile(r"\b(T20I?|ODI|One\s*Day|Test)\b", re.IGNORECASE)
SCORE_PATTERN = re.compile(r"\d+/\d+")
CRICKET_IRELAND_ORG = re.compile(r"cricket\s+ireland", re.IGNORECASE)

GENERATED_IMAGES_DIR = Path(__file__).resolve().parent / "generated_images"
IMAGE_SIZE = 1080


@dataclass
class PreviewMatchInfo:
    team1: str
    team2: str
    series: str
    match_label: str
    date_str: str
    time_str: str
    format_tag: str
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
    if base in TEAM_STYLES:
        abbrev, _ = TEAM_STYLES[base]
        if "Women" in team:
            return f"{abbrev}-W"
        return abbrev
    cleaned = re.sub(r"[^A-Za-z]", "", team)
    return (cleaned[:3] or "TBD").upper()


def _team_color(team: str) -> str:
    base = team.replace(" Women", "")
    if base in TEAM_STYLES:
        return TEAM_STYLES[base][1]
    return "#555555"


def _hashtag_token(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text)


def parse_preview_block(block: str) -> PreviewMatchInfo:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    teams = [_normalize_team_name(line) for line in lines if _line_is_tracked_team(line)]
    if len(teams) < 2:
        teams = (teams + ["TBD", "TBD"])[:2]

    match_label = ""
    for line in lines:
        if MATCH_LABEL_PATTERN.search(line):
            match_label = MATCH_LABEL_PATTERN.search(line).group(0)  # type: ignore[union-attr]
            break

    time_str = "TBC"
    for line in lines:
        time_match = TIME_PATTERN.search(line)
        if time_match:
            time_str = time_match.group(0).upper()
            break

    date_str = datetime.now().strftime("%A - %d %B, %Y")
    for line in lines:
        date_match = DATE_PATTERN.search(line)
        if date_match:
            raw = date_match.group(0).strip(" -")
            if not raw.lower().startswith(
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
            ):
                date_str = f"{datetime.now():%A} - {raw}"
            else:
                date_str = raw
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
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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


def _draw_gradient(draw: ImageDraw.ImageDraw, size: int, top: str, bottom: str) -> None:
    r1, g1, b1 = _hex_rgb(top)
    r2, g2, b2 = _hex_rgb(bottom)
    for y in range(size):
        ratio = y / size
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b))


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: str,
    outline: str | None = None,
    width: int = 0,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


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


def _draw_diagonal_label(text: str) -> Image.Image:
    font = _load_font(28, bold=True)
    temp = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    temp_draw = ImageDraw.Draw(temp)
    temp_draw.text((10, 40), text, font=font, fill="#1A1A1A")
    rotated = temp.rotate(32, expand=True, resample=Image.Resampling.BICUBIC)
    return rotated  # caller pastes


def generate_preview_image(info: PreviewMatchInfo) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", info.match_key)[:80]
    output_path = GENERATED_IMAGES_DIR / f"preview_{safe_key}.png"

    palette_idx = hash(info.match_key) % len(BACKGROUND_PALETTES)
    top_color, bottom_color = BACKGROUND_PALETTES[palette_idx]

    img = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), top_color)
    draw = ImageDraw.Draw(img)
    _draw_gradient(draw, IMAGE_SIZE, top_color, bottom_color)

    label_img = _draw_diagonal_label("Cricket Updates")
    img.paste(label_img, (30, 30), label_img)

    title_font = _load_font(34, bold=True)
    sub_font = _load_font(28, bold=True)
    pill_font = _load_font(30, bold=True)
    badge_font = _load_font(52, bold=True)
    vs_font = _load_font(36, bold=True)
    name_font = _load_font(22, bold=True)

    _draw_centered_text(draw, info.series[:55], IMAGE_SIZE // 2, 120, title_font, "#1A1A1A")

    date_bbox = draw.textbbox((0, 0), info.date_str, font=sub_font)
    date_w = date_bbox[2] - date_bbox[0] + 48
    date_x = (IMAGE_SIZE - date_w) // 2
    _draw_rounded_rect(draw, (date_x, 190, date_x + date_w, 250), 25, "#FFD54F", "#F4B400", 3)
    _draw_centered_text(draw, info.date_str, IMAGE_SIZE // 2, 200, sub_font, "#1A1A1A")

    card_margin = 80
    card_top = 300
    card_bottom = 780
    _draw_rounded_rect(
        draw,
        (card_margin, card_top, IMAGE_SIZE - card_margin, card_bottom),
        20,
        "#FFFFFF",
        "#F4B400",
        4,
    )

    badge_w, badge_h = 200, 200
    left_x = card_margin + 70
    right_x = IMAGE_SIZE - card_margin - 70 - badge_w
    badge_y = card_top + 120

    for team, x in ((info.team1, left_x), (info.team2, right_x)):
        color = _team_color(team)
        _draw_rounded_rect(draw, (x, badge_y, x + badge_w, badge_y + badge_h), 24, color, "#333333", 2)
        abbrev = _team_abbrev(team)
        _draw_centered_text(draw, abbrev, x + badge_w // 2, badge_y + 68, badge_font, "#FFFFFF")
        name_y = badge_y + badge_h + 18
        display = team if len(team) <= 16 else team[:14] + "…"
        _draw_centered_text(draw, display, x + badge_w // 2, name_y, name_font, "#1A1A1A")

    vs_x = IMAGE_SIZE // 2
    vs_y = badge_y + 70
    _draw_rounded_rect(draw, (vs_x - 55, vs_y - 10, vs_x + 55, vs_y + 70), 30, "#FFD54F", "#F4B400", 3)
    _draw_centered_text(draw, "VS", vs_x, vs_y + 8, vs_font, "#1A1A1A")

    match_line = info.match_label
    _draw_centered_text(draw, match_line, IMAGE_SIZE // 2, card_top + 30, sub_font, "#444444")

    time_text = info.time_str
    time_bbox = draw.textbbox((0, 0), time_text, font=pill_font)
    time_w = time_bbox[2] - time_bbox[0] + 56
    time_x = (IMAGE_SIZE - time_w) // 2
    _draw_rounded_rect(draw, (time_x, 860, time_x + time_w, 930), 28, "#FF9800", "#E65100", 3)
    _draw_centered_text(draw, time_text, IMAGE_SIZE // 2, 872, pill_font, "#FFFFFF")

    img.save(output_path, "PNG")
    return output_path
