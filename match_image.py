"""Generate preview match announcement images and captions."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
    "India": ("IND", "#FF9933", "#138808"),
    "England": ("ENG", "#FFFFFF", "#CE1124"),
    "Australia": ("AUS", "#00008B", "#FFD700"),
    "New Zealand": ("NZ", "#00247D", "#CC142B"),
    "South Africa": ("SA", "#007A4D", "#FFB612"),
    "Pakistan": ("PAK", "#01411C", "#FFFFFF"),
    "West Indies": ("WI", "#750000", "#D4AF37"),
    "Bangladesh": ("BAN", "#006A4E", "#F42A41"),
    "Sri Lanka": ("SL", "#8D153A", "#FFB500"),
    "Afghanistan": ("AFG", "#000000", "#007A36"),
    "Ireland": ("IRE", "#169B62", "#FFFFFF"),
    "Zimbabwe": ("ZIM", "#DE2010", "#FFD200"),
}

# flagcdn.com ISO codes; england + west-indies use generated PNGs in assets/flags/
FLAGCDN_CODES: dict[str, str] = {
    "India": "in",
    "Australia": "au",
    "New Zealand": "nz",
    "South Africa": "za",
    "Pakistan": "pk",
    "Bangladesh": "bd",
    "Sri Lanka": "lk",
    "Afghanistan": "af",
    "Ireland": "ie",
    "Zimbabwe": "zw",
}

TEAM_SLUGS: dict[str, str] = {
    "India": "india", 
    "England": "england",
    "Australia": "australia",
    "New Zealand": "new-zealand",
    "South Africa": "south-africa",
    "Pakistan": "pakistan",
    "West Indies": "west-indies",
    "Bangladesh": "bangladesh",
    "Sri Lanka": "sri-lanka",
    "Afghanistan": "afghanistan",
    "Ireland": "ireland",
    "Zimbabwe": "zimbabwe",
}

MATCH_LABEL_PATTERN = re.compile(
    r"\d+(?:st|nd|rd|th)\s+(?:T20I?|ODI|One\s*Day|Test)(?:\s*\([^)]+\))?",
    re.IGNORECASE,
)
PRACTICE_FIXTURE_PATTERN = re.compile(
    r"\b(?:tour\s+match|practice\s+(?:test\s+)?match|warm[-\s]?up(?:\s+match)?|unofficial\s+test)\b",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)", re.IGNORECASE)
TODAY_TOMORROW_TIME = re.compile(
    r"^(?:TODAY|TOMORROW),\s*(\d{1,2}:\d{2}\s*(?:AM|PM))",
    re.IGNORECASE,
)
LOCAL_TIME_PATTERN = re.compile(
    r"(\d{1,2}:\d{2}\s*(?:AM|PM))\s*local",
    re.IGNORECASE,
)
STARTS_AT_PATTERN = re.compile(
    r"starts?\s+at\s+(\d{1,2}:\d{2}\s*(?:AM|PM))",
    re.IGNORECASE,
)

# Venue keyword → IANA timezone for start-time conversion.
VENUE_TIMEZONES: list[tuple[str, str]] = [
    ("chester-le-street", "Europe/London"),
    ("manchester", "Europe/London"),
    ("london", "Europe/London"),
    ("birmingham", "Europe/London"),
    ("leeds", "Europe/London"),
    ("southampton", "Europe/London"),
    ("nottingham", "Europe/London"),
    ("cardiff", "Europe/London"),
    ("edinburgh", "Europe/London"),
    ("dublin", "Europe/Dublin"),
    ("north sound", "America/Antigua"),
    ("bridgetown", "America/Barbados"),
    ("gros islet", "America/St_Lucia"),
    ("mumbai", "Asia/Kolkata"),
    ("delhi", "Asia/Kolkata"),
    ("new delhi", "Asia/Kolkata"),
    ("ahmedabad", "Asia/Kolkata"),
    ("chennai", "Asia/Kolkata"),
    ("kolkata", "Asia/Kolkata"),
    ("bengaluru", "Asia/Kolkata"),
    ("hyderabad", "Asia/Kolkata"),
    ("karachi", "Asia/Karachi"),
    ("lahore", "Asia/Karachi"),
    ("rawalpindi", "Asia/Karachi"),
    ("sydney", "Australia/Sydney"),
    ("melbourne", "Australia/Melbourne"),
    ("perth", "Australia/Perth"),
    ("brisbane", "Australia/Brisbane"),
    ("adelaide", "Australia/Adelaide"),
    ("auckland", "Pacific/Auckland"),
    ("wellington", "Pacific/Auckland"),
    ("christchurch", "Pacific/Auckland"),
    ("johannesburg", "Africa/Johannesburg"),
    ("centurion", "Africa/Johannesburg"),
    ("cape town", "Africa/Johannesburg"),
    ("durban", "Africa/Johannesburg"),
    ("colombo", "Asia/Colombo"),
    ("kandy", "Asia/Colombo"),
    ("dhaka", "Asia/Dhaka"),
    ("chattogram", "Asia/Dhaka"),
    ("mirpur", "Asia/Dhaka"),
    ("sylhet", "Asia/Dhaka"),
    ("kabul", "Asia/Kabul"),
    ("harare", "Africa/Harare"),
    ("bulawayo", "Africa/Harare"),
    # Country-level fallbacks — matched when only the country name appears as venue
    ("bangladesh", "Asia/Dhaka"),
    ("sri lanka", "Asia/Colombo"),
    ("zimbabwe", "Africa/Harare"),
    ("afghanistan", "Asia/Kabul"),
    ("ireland", "Europe/Dublin"),
    ("england", "Europe/London"),
    ("pakistan", "Asia/Karachi"),
    ("india", "Asia/Kolkata"),
    ("australia", "Australia/Sydney"),
    ("new zealand", "Pacific/Auckland"),
    ("south africa", "Africa/Johannesburg"),
    ("west indies", "America/Antigua"),
]

TEAM_HOME_VENUE: dict[str, str] = {
    "India": "Delhi",
    "England": "London",
    "Australia": "Sydney",
    "New Zealand": "Auckland",
    "South Africa": "Johannesburg",
    "Pakistan": "Karachi",
    "West Indies": "North Sound",
    "Bangladesh": "Dhaka",
    "Sri Lanka": "Colombo",
    "Afghanistan": "Kabul",
    "Ireland": "Dublin",
    "Zimbabwe": "Harare",
}

TOUR_OF_PATTERN = re.compile(
    r"([\w\s]+?)\s+tour\s+of\s+([\w\s]+?)(?:\s+\d{4})?$",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*,?\s*"
    r"(\d{1,2}\s+\w+\s*,?\s*\d{4}|\w+\s+\d{1,2}\s*,?\s*\d{4})",
    re.IGNORECASE,
)
DATE_RANGE_PATTERN = re.compile(
    r"(\w+)\s+(\d{1,2})\s*-\s*(\d{1,2})\s*,?\s*(\d{4})",
    re.IGNORECASE,
)
FORMAT_PATTERN = re.compile(r"\b(T20I?|ODI|One\s*Day|Test)\b", re.IGNORECASE)
SCORE_PATTERN = re.compile(r"\d+/\d+")
SEASON_SCORE_PATTERN = re.compile(r"^(19|20)\d{2}/\d{2}$")
NON_SCORE_CONTEXT = re.compile(r"\b(?:tour|season|championship)\b", re.IGNORECASE)
CRICKET_IRELAND_ORG = re.compile(r"cricket\s+ireland", re.IGNORECASE)
TOSS_PATTERN = re.compile(
    r"([\w ]+ won the toss[^.\n]*|[\w -]+ (?:chose|opted|elected) to (?:bat|field|bowl)[^.\n]*)",
    re.IGNORECASE,
)
_TOSS_ABBREV = re.compile(
    r"^([\w ]+?)\s+(?:chose|opted|elected)\s+to\s+(bat|field|bowl)\b",
    re.IGNORECASE,
)
OVERS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*ov", re.IGNORECASE)
SIMPLE_OVERS_PATTERN = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*(?:ov|overs?)\s*\)", re.IGNORECASE)
TARGET_PATTERN = re.compile(r"\bT:\s*(\d+)", re.IGNORECASE)
NEED_RUNS_PATTERN = re.compile(
    r"need\s+(\d+)\s+runs?(?:\s+(?:from|off|in)\s+(\d+(?:\.\d+)?)\s+(?:ball|balls|deliveries?))?",
    re.IGNORECASE,
)
NEED_OVERS_PATTERN = re.compile(
    r"need\s+(\d+)\s+runs?\s+in\s+(\d+(?:\.\d+)?)\s+overs?(?:\s+to\s+win)?",
    re.IGNORECASE,
)
REQ_RUNS_PATTERN = re.compile(
    r"(?:req(?:uired)?|need)[:\s]+(\d+)(?:\s+(?:runs?\s+)?(?:from|off|in)\s+(\d+(?:\.\d+)?)\s+(?:ball|balls|deliveries?))?",
    re.IGNORECASE,
)
CRR_PATTERN = re.compile(
    r"(?:CRR|(?:[\w-]+\s+)?Current Run Rate)[:\s]+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
RRR_PATTERN = re.compile(
    r"(?:RRR|(?:Required|Req(?:uired)?)\s+Run Rate)[:\s]+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
BATTER_PATTERN = re.compile(
    r"([A-Z][a-z]*(?:\.\s*[A-Z][a-z]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?))\s+"
    r"(\d+\*?)\s*\(\s*(\d+)\s*\)",
)
BOWLER_PATTERN = re.compile(
    r"([A-Z][a-z]*(?:\.\s*[A-Z][a-z]+|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?))\s+"
    r"(\d+/\d+)\s*\(\s*(\d+(?:\.\d+)?)\s*\)",
)
TEST_DAY_PATTERN = re.compile(
    r"(?:\bday\s*(\d+)\b|(\d+)(?:st|nd|rd|th)\s+day\b|stumps.*?day\s*(\d+)|end\s+of\s+day\s*(\d+))",
    re.IGNORECASE,
)
LUNCH_BREAK_PATTERN = re.compile(r"\blunch(?:\s+break)?\b|\binterval\b", re.IGNORECASE)
TEA_BREAK_PATTERN = re.compile(r"\btea(?:\s+break)?\b", re.IGNORECASE)
STUMPS_BREAK_PATTERN = re.compile(
    r"\bstumps\b|end\s+of\s+(?:day|play)|close\s+of\s+play|play\s+suspended",
    re.IGNORECASE,
)
MATCH_STAGE_PATTERN = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)\s+(?:T20I?|ODI|One\s*Day|Test)(?:\s*\([^)]+\))?|"
    r"(?:semi-final|quarter-final|final|qualifier|group\s+\w+))\b",
    re.IGNORECASE,
)

_BASE_DIR = Path(__file__).resolve().parent
FLAGS_DIR = _BASE_DIR / "assets" / "flags"
FONTS_DIR = _BASE_DIR / "assets" / "fonts"
GENERATED_IMAGES_DIR = _BASE_DIR / "generated_images"

IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1350
BACKGROUND = "#FFFFFF"
TEXT_PRIMARY = "#202124"
TEXT_SECONDARY = "#5F6368"
TEXT_MUTED = "#80868B"

FLAG_WIDTH = 200
FLAG_HEIGHT = 140
FLAG_RADIUS = 12
LEFT_FLAG_CENTER_X = 270
RIGHT_FLAG_CENTER_X = 810
FLAG_Y = 420
VS_Y = 478
NAME_Y = 640
MATCH_LABEL_Y = 780
SERIES_Y = 840
DATETIME_Y = 180

UPDATE_IMAGE_WIDTH = 1080
UPDATE_IMAGE_HEIGHT = 720
LIVE_IMAGE_HEIGHT = 900
UPDATE_FLAG_WIDTH = 120
UPDATE_FLAG_HEIGHT = 84
UPDATE_FLAG_RADIUS = 10
UPDATE_LEFT_X = 140
UPDATE_RIGHT_X = 940
UPDATE_SCORE_LEFT_X = 400
UPDATE_SCORE_RIGHT_X = 680
UPDATE_FLAG_Y = 180
UPDATE_NAME_Y = 280
UPDATE_SCORE_Y = 200
UPDATE_OVERS_Y = 275
UPDATE_HEADLINE_Y = 400
UPDATE_SUBLINE_Y = 455
UPDATE_FOOTER_Y = 540

LIVE_BADGE_Y = 36
LIVE_FLAG_Y = 72
LIVE_NAME_Y = 168
LIVE_SCORE_Y = 200
LIVE_OVERS_Y = 272
LIVE_MID_Y = 340
LIVE_RR_Y = 390
LIVE_FOOTER_Y = 440
LIVE_STATS_Y = 500
LIVE_STATS_LINE_H = 32

STADIUM_ASSET = _BASE_DIR / "assets" / "stadium-silhouette.png"
LIVE_PREMIUM_BG_TOP = "#0b1a12"
LIVE_PREMIUM_BG_MID = "#0d1f18"
LIVE_PREMIUM_BG_BOTTOM = "#0a1628"
LIVE_PREMIUM_PANEL_BAT_BG = (16, 48, 32, 170)
LIVE_PREMIUM_PANEL_BOWL_BG = (16, 32, 64, 170)
LIVE_PREMIUM_TEXT = "#FFFFFF"
LIVE_PREMIUM_MUTED = "#B0BEC5"
LIVE_PREMIUM_SCORE = "#E8F5E9"
LIVE_PREMIUM_BAT_ACCENT = "#4CAF50"
LIVE_PREMIUM_BOWL_ACCENT = "#64B5F6"
LIVE_PREMIUM_BADGE_RED = "#D93025"
LIVE_PREMIUM_LEFT_CX = 200
LIVE_PREMIUM_RIGHT_CX = 880
LIVE_PREMIUM_CENTER_CX = 540
LIVE_PREMIUM_FLAG_Y = 88
LIVE_PREMIUM_TEAM_Y = 178
LIVE_PREMIUM_CENTER_SCORE_Y = 210
LIVE_PREMIUM_CENTER_OVERS_Y = 290
LIVE_PREMIUM_PILL_Y = 372
LIVE_PREMIUM_HEADLINE_Y = 438
LIVE_PREMIUM_PANEL_W = 480
LIVE_PREMIUM_PANEL_GAP = 40
LIVE_PREMIUM_PANEL_INNER_PAD = 16
LIVE_PREMIUM_PANEL_HEADER_H = 40
LIVE_PREMIUM_PANEL_LINE_H = 30
LIVE_PREMIUM_PANEL_GAP_AFTER_PILL = 12
LIVE_PREMIUM_BOTTOM_PAD = 24
LIVE_PREMIUM_HEADLINE_LINE_H = 32
LIVE_PREMIUM_PILL_LINE_H = 44

# ---------------------------------------------------------------------------
# Toss card layout constants
# ---------------------------------------------------------------------------
TOSS_IMAGE_HEIGHT = 540
TOSS_BASE_BG = "#F5F7FA"
TOSS_WASH_ALPHA = 0.42
TOSS_LEFT_X = 300
TOSS_RIGHT_X = 780
TOSS_FLAG_W = 140
TOSS_FLAG_H = 98
TOSS_FLAG_FRAME_RADIUS = 14
TOSS_FLAG_FRAME_PAD = 8
TOSS_FLAG_Y = 72
TOSS_NAME_Y = 188
TOSS_BADGE_Y = 32
TOSS_HEADLINE_Y = 248
TOSS_HEADLINE_PANEL_W = 860
TOSS_HEADLINE_PANEL_H = 96
TOSS_FOOTER_Y = 490
TOSS_CAPTAIN_PHOTO_SIZE = 110
TOSS_SMALL_FLAG_W = 56
TOSS_SMALL_FLAG_H = 40
TOSS_SMALL_FLAG_Y = 58
TOSS_CAPTAIN_PHOTO_Y = 108
TOSS_CAPTAIN_NAME_Y = 228
TOSS_TEAM_LABEL_Y = 168

# ---------------------------------------------------------------------------
# Scorecard image layout constants (premium dark)
# ---------------------------------------------------------------------------
SC_PREMIUM_DARK = "#0a1628"
SC_PREMIUM_HEADER_H = 156
SC_PREMIUM_FLAG_W = 76
SC_PREMIUM_FLAG_H = 52
SC_PREMIUM_FLAG_Y = 18
SC_PREMIUM_LEFT_CX = 200
SC_PREMIUM_RIGHT_CX = 880
SC_PREMIUM_TITLE_Y = 88
SC_PREMIUM_SERIES_Y = 124
SC_PREMIUM_ROW_START = 168
SC_PREMIUM_ROW_H = 46
SC_PREMIUM_DNB_H = 40
SC_PREMIUM_FOOTER_H = 96
SC_PREMIUM_BOTTOM_PAD = 20
SC_PREMIUM_NAME_X = 28
SC_PREMIUM_DIS_X = 340
SC_PREMIUM_R_RIGHT = 900
SC_PREMIUM_B_RIGHT = 980
SC_PREMIUM_TEXT = "#F5F7FA"
SC_PREMIUM_MUTED = "#B0BEC5"
SC_PREMIUM_NOT_OUT = "#4FC3F7"
SC_PREMIUM_TOP_SCORER_BG = (255, 255, 255, 28)
SC_PREMIUM_ROW_DIVIDER = "#1E2D42"

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


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
    day_label: str = "today"
    match_date: date | None = None


@dataclass
class MatchUpdateInfo:
    team1: str
    team2: str
    series: str
    match_label: str
    format_tag: str
    phase: str
    score1: str = ""
    overs1: str = ""
    score2: str = ""
    overs2: str = ""
    headline: str = ""
    subline: str = ""
    match_key: str = ""
    innings_status: str = "live"
    target: int | None = None
    runs_needed: int | None = None
    balls_remaining: int | None = None
    batting_team: str = ""
    bowling_team: str = ""
    current_run_rate: str = ""
    required_run_rate: str = ""
    overs_remaining: str = ""
    opponent_yet_to_bat: bool = False
    batters: list[str] = field(default_factory=list)
    bowlers: list[str] = field(default_factory=list)
    test_day: int = 0
    session_break: str = ""


@dataclass
class CaptainInfo:
    team: str
    name: str
    image_path: Path | None = None


@dataclass
class CaptainTossInfo:
    team1_captain: CaptainInfo
    team2_captain: CaptainInfo


@dataclass
class ScorecardBatter:
    """One batter row from a completed innings scorecard."""

    name: str
    dismissal: str
    runs: int
    balls: int
    fours: int
    sixes: int
    not_out: bool = False


@dataclass
class ScorecardInfo:
    """Data for a batting scorecard post image."""

    team1: str
    team2: str
    batting_team: str
    score: str
    overs: str
    match_label: str
    series: str
    format_tag: str
    batters: list[ScorecardBatter] = field(default_factory=list)
    squad_names: list[str] = field(default_factory=list)
    extras: str = ""
    extras_runs: int = 0
    total_runs: int = 0
    total_detail: str = ""


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


def is_excluded_fixture(block: str) -> bool:
    """Practice / warm-up tour matches — not official numbered internationals."""
    if not PRACTICE_FIXTURE_PATTERN.search(block):
        return False
    if MATCH_LABEL_PATTERN.search(block):
        return False
    return True


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


def _color_luminance(hex_color: str) -> float:
    r, g, b = _hex_rgb(hex_color)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _toss_wash_color(team: str) -> str:
    """Pick a visible wash color; fall back to secondary when primary is too light."""
    primary, secondary = _team_kit_colors(team)
    if _color_luminance(primary) > 200:
        return secondary
    return primary


def _hashtag_token(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text)


def _team_slug(team: str) -> str:
    base = team.replace(" Women", "")
    return TEAM_SLUGS.get(base, re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-"))


def _teams_from_block(block: str) -> list[str]:
    return [_normalize_team_name(line) for line in block.splitlines() if _line_is_tracked_team(line)]


def _is_valid_cricket_score(score: str) -> bool:
    """True for runs/wickets (e.g. 184/2), false for seasons like 2023/24."""
    cleaned = score.strip()
    if not cleaned:
        return False
    if SEASON_SCORE_PATTERN.match(cleaned):
        return False
    match = re.match(r"^(\d+)/(\d+)$", cleaned)
    if not match:
        if re.fullmatch(r"\d+", cleaned):
            return int(cleaned) <= 999
        return False
    runs, wickets = int(match.group(1)), int(match.group(2))
    if wickets > 10:
        return False
    if runs >= 1900 or runs > 999:
        return False
    return True


def _extract_valid_scores_from_line(line: str) -> list[str]:
    masked = OVERS_PATTERN.sub(" ", line)
    return [score for score in SCORE_PATTERN.findall(masked) if _is_valid_cricket_score(score)]


def _line_is_non_score_context(line: str) -> bool:
    lower = line.lower()
    if not NON_SCORE_CONTEXT.search(lower):
        return False
    if OVERS_PATTERN.search(line) or SIMPLE_OVERS_PATTERN.search(line) or re.search(r"\bov\b", lower):
        return False
    return True


def block_contains_valid_score(block: str) -> bool:
    for line in block.splitlines():
        if _extract_valid_scores_from_line(line.strip()):
            return True
    return False


def block_has_valid_live_score(block: str) -> bool:
    if _scores_by_team_detailed(block):
        return True
    target, runs_needed, _, overs_remaining = _parse_chase_meta(block)
    if target is not None or runs_needed is not None or overs_remaining:
        return True
    if TARGET_PATTERN.search(block):
        return True
    for line in block.splitlines():
        if CRR_PATTERN.search(line) and _extract_valid_scores_from_line(line):
            return True
    return False


def _is_score_line(line: str) -> bool:
    lower = line.lower()
    if "won by" in lower or "won the toss" in lower:
        return False
    if _line_is_non_score_context(line):
        return False
    if _extract_valid_scores_from_line(line):
        return True
    if "&" in line and re.search(r"\d", line):
        return True
    if re.fullmatch(r"\d+", line.strip()):
        return True
    return False


def _parse_score_line(line: str, *, innings_complete: bool = False) -> tuple[str, str]:
    line = line.strip()
    if not line:
        return "", ""

    overs_match = OVERS_PATTERN.search(line)
    simple_overs = SIMPLE_OVERS_PATTERN.search(line)
    target_match = TARGET_PATTERN.search(line)
    # Mask overs fractions (e.g. 47.5/50 ov) so they are not mistaken for scores
    masked = OVERS_PATTERN.sub(" ", line)
    scores = [score for score in SCORE_PATTERN.findall(masked) if _is_valid_cricket_score(score)]

    if "&" in line:
        parts = re.findall(r"\d+(?:/\d+)?", line)
        if parts:
            return " & ".join(parts[:3]), ""

    score = scores[0] if scores else ""
    if not score and re.fullmatch(r"\d+", line):
        score = line

    overs_display = ""
    if overs_match:
        completed, total = overs_match.group(1), overs_match.group(2)
        try:
            is_complete = innings_complete or float(completed) >= float(total)
        except ValueError:
            is_complete = innings_complete
        if is_complete:
            overs_display = f"({total})"
        else:
            overs_display = f"({completed})"
    elif simple_overs:
        overs_display = f"({simple_overs.group(1)})"

    if target_match and not score and scores:
        score = scores[0]

    return score, overs_display


def _runs_from_score(score: str) -> int | None:
    match = re.match(r"(\d+)/\d+", score)
    return int(match.group(1)) if match else None


def _parse_chase_meta(block: str) -> tuple[int | None, int | None, int | None, str]:
    target: int | None = None
    runs_needed: int | None = None
    balls_remaining: int | None = None
    overs_remaining = ""

    for line in block.splitlines():
        target_match = TARGET_PATTERN.search(line)
        if target_match:
            target = int(target_match.group(1))

        overs_need = NEED_OVERS_PATTERN.search(line)
        if overs_need:
            runs_needed = int(overs_need.group(1))
            overs_remaining = overs_need.group(2)
            continue

        for pattern in (NEED_RUNS_PATTERN, REQ_RUNS_PATTERN):
            need_match = pattern.search(line)
            if need_match:
                runs_needed = int(need_match.group(1))
                if need_match.lastindex and need_match.lastindex >= 2 and need_match.group(2):
                    balls_remaining = int(float(need_match.group(2)))
                break

    return target, runs_needed, balls_remaining, overs_remaining


def _parse_run_rates(block: str) -> tuple[str, str]:
    crr = ""
    rrr = ""
    for line in block.splitlines():
        crr_match = CRR_PATTERN.search(line)
        if crr_match:
            crr = crr_match.group(1)
        rrr_match = RRR_PATTERN.search(line)
        if rrr_match:
            rrr = rrr_match.group(1)
    return crr, rrr


def _innings_complete(score: str, overs: str, fmt: str) -> bool:
    wickets_match = re.search(r"/(\d+)", score)
    if wickets_match and int(wickets_match.group(1)) >= 10:
        return True
    if not overs:
        return False
    ov = overs.strip("()")
    try:
        bowled = float(ov)
    except ValueError:
        return False
    if fmt == "T20":
        return bowled >= 20
    if fmt == "ODI":
        return bowled >= 50
    if fmt == "TEST":
        return bowled >= 90
    return False


def _extract_batters(block: str) -> list[str]:
    batters: list[str] = []
    for line in block.splitlines():
        for match in BATTER_PATTERN.finditer(line):
            batters.append(f"{match.group(1).strip()} {match.group(2)} ({match.group(3)})")
        if len(batters) >= 2:
            break
    return batters


def _extract_bowlers(block: str) -> list[str]:
    bowlers: list[str] = []
    for line in block.splitlines():
        for match in BOWLER_PATTERN.finditer(line):
            bowlers.append(f"{match.group(1).strip()} {match.group(2)} ({match.group(3)})")
        if len(bowlers) >= 2:
            break
    return bowlers


def _parse_test_day_from_block(block: str) -> int:
    for line in block.splitlines():
        match = TEST_DAY_PATTERN.search(line)
        if match:
            for group in match.groups():
                if group:
                    return int(group)

    range_match = DATE_RANGE_PATTERN.search(block)
    if range_match:
        month_name, day_start, day_end, year = range_match.groups()
        month_num = MONTHS.get(month_name.lower())
        if month_num:
            start = date(int(year), month_num, int(day_start))
            end = date(int(year), month_num, int(day_end))
            today = date.today()
            if start <= today <= end:
                return (today - start).days + 1

    match_start = _parse_match_date(block)
    if match_start and match_start <= date.today():
        return (date.today() - match_start).days + 1
    return 1


def _detect_test_session(block: str) -> tuple[int, str]:
    if _detect_format(block) != "TEST":
        return 0, ""

    session = ""
    if STUMPS_BREAK_PATTERN.search(block):
        session = "stumps"
    elif TEA_BREAK_PATTERN.search(block):
        session = "tea"
    elif LUNCH_BREAK_PATTERN.search(block):
        session = "lunch"
    if not session:
        return 0, ""
    return _parse_test_day_from_block(block), session


def _apply_test_session_headline(info: MatchUpdateInfo, block: str) -> None:
    day = info.test_day or 1
    label = info.session_break.capitalize()
    score_parts: list[str] = []

    if info.score1:
        part = f"{_team_abbrev(info.team1)} {info.score1}"
        if info.overs1:
            part += f" ({info.overs1.strip('()')} ov)"
        score_parts.append(part)
    elif _team_yet_to_bat(block, info.team1):
        score_parts.append(f"{_team_abbrev(info.team1)} yet to bat")

    if info.score2:
        part = f"{_team_abbrev(info.team2)} {info.score2}"
        if info.overs2:
            part += f" ({info.overs2.strip('()')} ov)"
        score_parts.append(part)
    elif _team_yet_to_bat(block, info.team2):
        score_parts.append(f"{_team_abbrev(info.team2)} yet to bat")

    if score_parts:
        info.headline = f"Day {day} — {label}: {', '.join(score_parts)}"
    else:
        info.headline = f"Day {day} — {label}"


def _live_badge_text(info: MatchUpdateInfo) -> str:
    if info.session_break:
        day = info.test_day or 1
        return f"DAY {day} — {info.session_break.upper()}"
    return "LIVE"


def _team_yet_to_bat(block: str, team: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if _normalize_team_name(line) != team:
            continue
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j].lower()
            if _line_is_tracked_team(lines[j]):
                break
            if "yet to bat" in nxt or "yet to bat" in nxt.replace(".", ""):
                return True
    return False


def _finalize_test_session(info: MatchUpdateInfo, block: str, fmt: str) -> None:
    if fmt != "TEST":
        return
    test_day, session_break = _detect_test_session(block)
    if not session_break:
        return
    info.test_day = test_day
    info.session_break = session_break
    _apply_test_session_headline(info, block)


def _chasing_team_from_scores(info: MatchUpdateInfo, fmt: str) -> str | None:
    """Pick the chasing team from innings completeness, not score column order."""
    if not info.score1 or not info.score2:
        return None
    t1_done = _innings_complete(info.score1, info.overs1, fmt)
    t2_done = _innings_complete(info.score2, info.overs2, fmt)
    if t1_done and not t2_done:
        return info.team2
    if t2_done and not t1_done:
        return info.team1
    return None


def _populate_live_context(info: MatchUpdateInfo, block: str) -> None:
    fmt = _detect_format(block)
    target, runs_needed, balls_remaining, overs_remaining = _parse_chase_meta(block)
    crr, rrr = _parse_run_rates(block)
    info.current_run_rate = crr
    info.required_run_rate = rrr
    info.batters = _extract_batters(block)
    info.bowlers = _extract_bowlers(block)

    team1_yet = _team_yet_to_bat(block, info.team1)
    team2_yet = _team_yet_to_bat(block, info.team2)

    chasing_team = _chasing_team_from_scores(info, fmt)
    if chasing_team and fmt in ("T20", "ODI"):
        info.innings_status = "chase"
        info.batting_team = chasing_team
        info.bowling_team = info.team2 if chasing_team == info.team1 else info.team1
        info.target = target
        info.runs_needed = runs_needed
        info.balls_remaining = balls_remaining
        info.overs_remaining = overs_remaining
        if info.runs_needed is None and target is not None:
            chase_score = info.score1 if chasing_team == info.team1 else info.score2
            runs = _runs_from_score(chase_score)
            if runs is not None:
                info.runs_needed = max(target - runs, 0)
        abbrev = _team_abbrev(info.batting_team)
        if info.runs_needed is not None:
            if info.overs_remaining:
                info.headline = (
                    f"{abbrev} need {info.runs_needed} runs in {info.overs_remaining} overs to win"
                )
            elif info.balls_remaining is not None:
                info.headline = (
                    f"{abbrev} need {info.runs_needed} runs from {info.balls_remaining} balls"
                )
            else:
                info.headline = f"{abbrev} need {info.runs_needed} runs to win"
        elif info.headline == "":
            chase_score = info.score1 if chasing_team == info.team1 else info.score2
            chase_overs = info.overs1 if chasing_team == info.team1 else info.overs2
            ov = chase_overs.strip("()") if chase_overs else ""
            first_score = info.score2 if chasing_team == info.team1 else info.score1
            first_overs = info.overs2 if chasing_team == info.team1 else info.overs1
            first_ov = first_overs.strip("()") if first_overs else ""
            info.headline = (
                f"LIVE: {_team_abbrev(info.bowling_team)} {first_score}"
                f"{f' ({first_ov} ov)' if first_ov else ''} vs "
                f"{abbrev} {chase_score}{f' ({ov} ov)' if ov else ''}"
            )
        _finalize_test_session(info, block, fmt)
        return

    if info.score2 or any("T:" in line.upper() for line in block.splitlines()):
        info.innings_status = "chase"
        info.target = target
        info.runs_needed = runs_needed
        info.balls_remaining = balls_remaining
        info.overs_remaining = overs_remaining
        if chasing_team:
            info.batting_team = chasing_team
            info.bowling_team = info.team2 if chasing_team == info.team1 else info.team1
        else:
            info.batting_team = info.team2 if info.score2 else info.team1
            info.bowling_team = info.team1 if info.batting_team == info.team2 else info.team2

        if info.runs_needed is None and target is not None and info.score2:
            runs = _runs_from_score(info.score2)
            if runs is not None:
                info.runs_needed = max(target - runs, 0)

        abbrev = _team_abbrev(info.batting_team)
        if info.runs_needed is not None:
            if info.overs_remaining:
                info.headline = (
                    f"{abbrev} need {info.runs_needed} runs in {info.overs_remaining} overs to win"
                )
            elif info.balls_remaining is not None:
                info.headline = (
                    f"{abbrev} need {info.runs_needed} runs from {info.balls_remaining} balls"
                )
            else:
                info.headline = f"{abbrev} need {info.runs_needed} runs to win"
        _finalize_test_session(info, block, fmt)
        return

    if runs_needed is not None or overs_remaining:
        info.innings_status = "chase"
        info.runs_needed = runs_needed
        info.balls_remaining = balls_remaining
        info.overs_remaining = overs_remaining
        info.batting_team = info.team2 if team1_yet else info.team1
        info.bowling_team = info.team1 if info.batting_team == info.team2 else info.team2
        abbrev = _team_abbrev(info.batting_team)
        if info.overs_remaining:
            info.headline = f"{abbrev} need {runs_needed} runs in {overs_remaining} overs to win"
        _finalize_test_session(info, block, fmt)
        return

    if info.score1 and team2_yet and not info.score2:
        info.opponent_yet_to_bat = True
        info.batting_team = info.team1
        info.bowling_team = info.team2
        if _innings_complete(info.score1, info.overs1, fmt):
            info.innings_status = "innings_break"
            runs = _runs_from_score(info.score1)
            if runs is not None:
                info.target = runs + 1
            ov = info.overs1.strip("()") if info.overs1 else ""
            ov_text = f" ({ov} overs)" if ov else ""
            info.headline = (
                f"{info.team1} {info.score1}{ov_text}. {info.team2} need {info.target} to win"
            )
        else:
            info.innings_status = "first_innings"
            ov = info.overs1.strip("()") if info.overs1 else ""
            if crr:
                info.headline = f"{_team_abbrev(info.team1)} Current Run Rate: {crr}"
            elif ov:
                info.headline = f"LIVE: {_team_abbrev(info.team1)} {info.score1} after {ov} overs"
            else:
                info.headline = f"LIVE: {_team_abbrev(info.team1)} {info.score1}"
        _finalize_test_session(info, block, fmt)
        return

    if info.score2 and team1_yet and not info.score1:
        info.opponent_yet_to_bat = True
        info.batting_team = info.team2
        info.bowling_team = info.team1
        if _innings_complete(info.score2, info.overs2, fmt):
            info.innings_status = "innings_break"
            runs = _runs_from_score(info.score2)
            if runs is not None:
                info.target = runs + 1
            ov = info.overs2.strip("()") if info.overs2 else ""
            ov_text = f" ({ov} overs)" if ov else ""
            info.headline = (
                f"{info.team2} {info.score2}{ov_text}. {info.team1} need {info.target} to win"
            )
        else:
            info.innings_status = "first_innings"
            ov = info.overs2.strip("()") if info.overs2 else ""
            if crr:
                info.headline = f"{_team_abbrev(info.team2)} Current Run Rate: {crr}"
            elif ov:
                info.headline = f"LIVE: {_team_abbrev(info.team2)} {info.score2} after {ov} overs"
            else:
                info.headline = f"LIVE: {_team_abbrev(info.team2)} {info.score2}"
        _finalize_test_session(info, block, fmt)
        return

    if info.score1 and not info.score2:
        info.batting_team = info.team1
        info.bowling_team = info.team2
        ov = info.overs1.strip("()") if info.overs1 else ""
        info.headline = f"LIVE: {_team_abbrev(info.team1)} {info.score1}"
        if ov:
            info.headline += f" after {ov} overs"
    elif info.score2 and not info.score1:
        info.batting_team = info.team2
        info.bowling_team = info.team1
        ov = info.overs2.strip("()") if info.overs2 else ""
        info.headline = f"LIVE: {_team_abbrev(info.team2)} {info.score2}"
        if ov:
            info.headline += f" after {ov} overs"
    else:
        info.headline = "LIVE"
        if info.batters:
            info.subline = ", ".join(info.batters[:2])

    _finalize_test_session(info, block, fmt)


def make_live_signature(info: MatchUpdateInfo) -> str:
    return "|".join(
        [
            str(info.test_day or ""),
            info.session_break,
            info.innings_status,
            info.score1,
            info.overs1,
            info.score2,
            info.overs2,
            str(info.target or ""),
            str(info.runs_needed or ""),
            str(info.balls_remaining or ""),
            info.overs_remaining,
            info.current_run_rate,
            info.required_run_rate,
            info.batting_team,
            ";".join(info.batters[:2]),
            ";".join(info.bowlers[:2]),
        ]
    )


def _scores_by_team_detailed(block: str) -> list[tuple[str, str, str]]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    paired: list[tuple[str, str, str]] = []
    for i, line in enumerate(lines):
        if not _line_is_tracked_team(line):
            continue
        team = _normalize_team_name(line)
        for j in range(i + 1, min(i + 4, len(lines))):
            nxt = lines[j]
            if _line_is_tracked_team(nxt):
                break
            if "won by" in nxt.lower() or "won the toss" in nxt.lower():
                break
            if "yet to bat" in nxt.lower():
                break
            if _is_score_line(nxt):
                overs_match = OVERS_PATTERN.search(nxt)
                innings_complete = False
                if overs_match:
                    try:
                        innings_complete = float(overs_match.group(1)) >= float(overs_match.group(2))
                    except ValueError:
                        innings_complete = False
                score, overs = _parse_score_line(nxt, innings_complete=innings_complete)
                if score and _is_valid_cricket_score(score):
                    paired.append((team, score, overs))
                break
    return paired


def _result_line(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if re.search(r"\bwon by\b", stripped, re.IGNORECASE):
            return stripped.rstrip(".")
    return ""


def _extract_match_label(block: str, fmt: str) -> str:
    for line in block.splitlines():
        match = MATCH_LABEL_PATTERN.search(line)
        if match:
            label = re.sub(r"\s+", " ", match.group(0))
            return label.replace("One Day", "ODI")
        stage = MATCH_STAGE_PATTERN.search(line)
        if stage:
            return re.sub(r"\s+", " ", stage.group(0))
    if fmt == "T20":
        return "T20"
    if fmt == "TEST":
        return "Test"
    return "ODI"


def _match_hashtags(info: MatchUpdateInfo | PreviewMatchInfo) -> str:
    abbrev1 = _team_abbrev(info.team1)
    abbrev2 = _team_abbrev(info.team2)
    series_tag = _hashtag_token(info.series)
    hashtags = [
        f"#{abbrev1}vs{abbrev2}",
        f"#{abbrev2}vs{abbrev1}",
        f"#{info.format_tag}",
        f"#{series_tag}" if series_tag else "",
        f"#Team{_hashtag_token(info.team1.replace(' Women', ''))}",
        f"#Team{_hashtag_token(info.team2.replace(' Women', ''))}",
        "#CricketUpdates",
    ]
    return " ".join(tag for tag in hashtags if tag)


def _normalize_toss_text(text: str) -> str:
    """Convert abbreviated ESPN toss text to a full readable sentence.

    "Bangladesh chose to field" → "Bangladesh won the toss and elected to field"
    "England won the toss and elected to bat" → unchanged
    """
    if "won the toss" in text.lower():
        return text
    m = _TOSS_ABBREV.match(text)
    if m:
        team = m.group(1).strip()
        decision = m.group(2).lower()
        action = "bat" if decision == "bat" else "field"
        return f"{team} won the toss and elected to {action}"
    return text


def parse_match_block(block: str, phase: str) -> MatchUpdateInfo:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    teams = _teams_from_block(block)
    if len(teams) < 2:
        teams = (teams + ["TBD", "TBD"])[:2]

    fixture_line = ""
    for line in lines:
        if MATCH_LABEL_PATTERN.search(line) or MATCH_STAGE_PATTERN.search(line):
            fixture_line = line
            break

    series = _extract_series(lines, fixture_line, teams[0], teams[1])
    fmt = _detect_format(block)
    match_label = _extract_match_label(block, fmt)
    team_key = "-".join(sorted(t.lower().replace(" ", "-") for t in teams[:2]))
    match_key = f"{team_key}|{fmt}|{match_label}"

    info = MatchUpdateInfo(
        team1=teams[0],
        team2=teams[1],
        series=series,
        match_label=match_label,
        format_tag=_format_tag(fmt),
        phase=phase,
        match_key=match_key,
    )

    scores = _scores_by_team_detailed(block)
    if scores:
        info.score1, info.overs1 = scores[0][1], scores[0][2]
        if len(scores) >= 2:
            info.score2, info.overs2 = scores[1][1], scores[1][2]

    if phase == "result":
        info.headline = _result_line(block)
    elif phase == "toss":
        toss_match = TOSS_PATTERN.search(block)
        if toss_match:
            raw = toss_match.group(1).strip().rstrip(".")
            info.headline = _normalize_toss_text(raw)
    elif phase == "live":
        _populate_live_context(info, block)

    return info


def build_result_caption(info: MatchUpdateInfo) -> str:
    headline = (
        f"Full time! {info.team1} vs {info.team2}, {info.match_label}"
    )
    if info.headline:
        headline += f" — {info.headline}."
    else:
        headline += "."
    return f"{headline}\n\n{_match_hashtags(info)}"


def build_live_caption(info: MatchUpdateInfo) -> str:
    player_bits: list[str] = []
    if info.batters:
        player_bits.extend(info.batters[:2])
    if info.bowlers:
        player_bits.extend(info.bowlers[:2])

    if info.session_break and info.headline:
        headline = info.headline if info.headline.endswith(".") else f"{info.headline}."
    elif info.innings_status == "first_innings":
        ov = info.overs1.strip("()") if info.overs1 else ""
        headline = f"LIVE: {info.team1} vs {info.team2} — {_team_abbrev(info.batting_team)} {info.score1}"
        if ov:
            headline += f" after {ov} overs"
        if info.current_run_rate:
            headline += f" (CRR {info.current_run_rate})"
        if player_bits:
            headline += f". {', '.join(player_bits[:2])}"
        headline += "."
    elif info.innings_status == "innings_break" and info.headline:
        headline = f"{info.headline}."
    elif info.innings_status == "chase":
        parts = [
            f"LIVE: {info.team1} {info.score1}",
            f"{info.team2} {info.score2}",
        ]
        if info.overs2:
            parts[1] += f" ({info.overs2.strip('()')} ov)"
        headline = " vs ".join(parts)
        if info.headline:
            headline += f" — {info.headline}"
        rr_bits = []
        if info.current_run_rate:
            rr_bits.append(f"CRR {info.current_run_rate}")
        if info.required_run_rate:
            rr_bits.append(f"RRR {info.required_run_rate}")
        if rr_bits:
            headline += f" ({', '.join(rr_bits)})"
        if player_bits:
            headline += f". {', '.join(player_bits[:2])}"
        headline += "."
    elif info.headline and info.headline != "LIVE":
        headline = f"{info.team1} vs {info.team2}, {info.match_label} — {info.headline}."
    else:
        parts = [f"LIVE: {info.team1} vs {info.team2}, {info.match_label}"]
        batting = info.batting_team or info.team1
        if batting == info.team1 and info.score1:
            ov = info.overs1.strip("()") if info.overs1 else ""
            score_part = f"— {_team_abbrev(info.team1)} {info.score1}"
            if ov:
                score_part += f" after {ov} overs"
            parts.append(score_part)
        elif batting == info.team2 and info.score2:
            ov = info.overs2.strip("()") if info.overs2 else ""
            score_part = f"— {_team_abbrev(info.team2)} {info.score2}"
            if ov:
                score_part += f" after {ov} overs"
            parts.append(score_part)
        if info.subline:
            parts.append(f"({info.subline})")
        headline = " ".join(parts) + "."
    return f"{headline}\n\n{_match_hashtags(info)}"


def build_toss_caption(info: MatchUpdateInfo) -> str:
    headline = f"{info.team1} vs {info.team2}, {info.match_label}"
    if info.headline:
        headline += f" — {info.headline}."
    else:
        headline += "."
    return f"{headline}\n\n{_match_hashtags(info)}"


def build_update_caption(info: MatchUpdateInfo) -> str:
    if info.phase == "result":
        return build_result_caption(info)
    if info.phase == "live":
        return build_live_caption(info)
    if info.phase == "toss":
        return build_toss_caption(info)
    raise ValueError(f"Unsupported phase: {info.phase}")


def _extract_venue_from_line(line: str) -> str:
    if not MATCH_LABEL_PATTERN.search(line):
        return ""

    if "·" in line:
        after_dot = line.split("·", 1)[1].strip()
        candidate = after_dot.split(",")[0].strip()
        if candidate and not _looks_like_date(candidate):
            return candidate

    parts = [part.strip() for part in line.split(",") if part.strip()]
    if len(parts) < 2:
        return ""
    for idx, part in enumerate(parts):
        if DATE_PATTERN.search(part):
            if idx > 0:
                candidate = parts[idx - 1]
                if (
                    not _looks_like_date(candidate)
                    and not DATE_PATTERN.search(candidate)
                    and not TIME_PATTERN.search(candidate)
                ):
                    return candidate
            break
    if len(parts) >= 2 and not _looks_like_date(parts[1]) and not DATE_PATTERN.search(parts[1]):
        return parts[1]
    return ""


def _looks_like_date(text: str) -> bool:
    stripped = text.strip()
    if re.fullmatch(r"\d{4}", stripped):
        return True
    if DATE_PATTERN.search(stripped):
        return True
    if re.fullmatch(r"\w+\s+\d{1,2}", stripped, re.IGNORECASE):
        return True
    if re.fullmatch(r"\d{1,2}\s+\w+", stripped, re.IGNORECASE):
        return True
    return False


def _parse_match_date(text: str) -> date | None:
    range_match = DATE_RANGE_PATTERN.search(text)
    if range_match:
        month_name, day_start, _, year = range_match.groups()
        month_num = MONTHS.get(month_name.lower())
        if month_num:
            return date(int(year), month_num, int(day_start))

    for fmt in ("%d %B %Y", "%d %B, %Y", "%B %d %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue

    date_match = DATE_PATTERN.search(text)
    if date_match:
        raw = date_match.group(1).strip(" ,")
        for fmt in ("%d %B %Y", "%d %B, %Y", "%B %d %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        parts = re.match(r"(\d{1,2})\s+(\w+)\s*,?\s*(\d{4})", raw, re.IGNORECASE)
        if parts:
            day, month_name, year = parts.groups()
            month_num = MONTHS.get(month_name.lower())
            if month_num:
                return date(int(year), month_num, int(day))
        parts = re.match(r"(\w+)\s+(\d{1,2})\s*,?\s*(\d{4})", raw, re.IGNORECASE)
        if parts:
            month_name, day, year = parts.groups()
            month_num = MONTHS.get(month_name.lower())
            if month_num:
                return date(int(year), month_num, int(day))
    return None


def parse_match_date_from_block(block: str) -> date | None:
    for line in block.splitlines():
        parsed = _parse_match_date(line.strip())
        if parsed:
            return parsed
    return _parse_match_date(block)


def _detect_day_label(block: str, match_date: date | None) -> str:
    for line in block.splitlines():
        upper = line.strip().upper()
        if upper.startswith("TOMORROW,"):
            return "tomorrow"
        if upper.startswith("TODAY,"):
            return "today"

    today = date.today()
    if _detect_format(block) == "TEST" and match_date:
        if today > match_date:
            return "today"
        range_match = DATE_RANGE_PATTERN.search(block)
        if range_match:
            month_name, day_start, day_end, year = range_match.groups()
            month_num = MONTHS.get(month_name.lower())
            if month_num:
                start = date(int(year), month_num, int(day_start))
                if today > start:
                    return "today"

    if match_date == today + timedelta(days=1):
        return "tomorrow"
    if match_date == today:
        return "today"
    return "today"


def _is_orphan_widget_time(line: str, line_idx: int, lines: list[str]) -> bool:
    stripped = line.strip()
    if not TIME_PATTERN.search(stripped):
        return False
    if STARTS_AT_PATTERN.search(stripped) or LOCAL_TIME_PATTERN.search(stripped):
        return False
    if TODAY_TOMORROW_TIME.match(stripped):
        return False

    anchor_idx = len(lines)
    for idx, candidate in enumerate(lines):
        if MATCH_LABEL_PATTERN.search(candidate):
            anchor_idx = min(anchor_idx, idx)
        lower = candidate.lower()
        if "tour of" in lower or " tour " in lower:
            anchor_idx = min(anchor_idx, idx)

    return line_idx < anchor_idx


def _infer_venue_from_context(series: str, team1: str, team2: str) -> str:
    tour_match = TOUR_OF_PATTERN.search(series.strip())
    if tour_match:
        host = tour_match.group(2).strip()
        for team, venue in TEAM_HOME_VENUE.items():
            if team.lower() in host.lower():
                return venue
    return ""


def _extract_time(block: str) -> str:
    time_str, _ = _extract_time_with_is_local(block)
    return time_str


def _extract_time_with_is_local(block: str) -> tuple[str, bool]:
    """Return (time_str, is_venue_local).

    is_venue_local=True only when ESPN explicitly labels the time as venue-local
    via the "X local" suffix (e.g. "9:30 PM local").  All other formats
    ("Starts at X", "TODAY/TOMORROW, X", bare "X") are in the browser's timezone
    which is forced to Asia/Karachi via Playwright's timezone_id setting.
    """
    for line in block.splitlines():
        starts_match = STARTS_AT_PATTERN.search(line)
        if starts_match:
            return starts_match.group(1).upper(), False

    for line in block.splitlines():
        header_match = TODAY_TOMORROW_TIME.match(line.strip())
        if header_match:
            return header_match.group(1).upper(), False

    for line in block.splitlines():
        local_match = LOCAL_TIME_PATTERN.search(line)
        if local_match:
            return local_match.group(1).upper(), True

    lines = block.splitlines()
    for idx, line in enumerate(lines):
        if _is_orphan_widget_time(line, idx, lines):
            continue
        time_match = TIME_PATTERN.search(line)
        if time_match:
            return time_match.group(0).upper(), False
    return "TBC", False


def _display_timezone() -> str:
    return os.getenv("DISPLAY_TIMEZONE", "Asia/Karachi").strip() or "Asia/Karachi"


def _venue_timezone(venue: str) -> str:
    lower = venue.lower()
    for keyword, tz_name in VENUE_TIMEZONES:
        if keyword in lower:
            return tz_name
    logger.warning("Unknown venue timezone for %r; assuming UTC", venue)
    return "UTC"


def _format_clock_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _convert_preview_time(
    time_str: str,
    match_date: date | None,
    venue: str,
) -> tuple[str, date | None]:
    if time_str == "TBC" or match_date is None or venue == "TBC":
        return time_str, match_date

    venue_tz_name = _venue_timezone(venue)
    display_tz_name = _display_timezone()

    try:
        venue_tz = ZoneInfo(venue_tz_name)
        display_tz = ZoneInfo(display_tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Invalid timezone for conversion: %s or %s", venue_tz_name, display_tz_name)
        return time_str, match_date

    try:
        naive = datetime.strptime(f"{match_date.isoformat()} {time_str}", "%Y-%m-%d %I:%M %p")
    except ValueError:
        return time_str, match_date

    localized = naive.replace(tzinfo=venue_tz)
    converted = localized.astimezone(display_tz)
    return _format_clock_time(converted), converted.date()


def _series_matches_teams(line: str, team1: str, team2: str) -> int:
    lower = line.lower()
    score = 0
    for team in (team1, team2):
        base = team.replace(" Women", "").lower()
        if base and base in lower:
            score += 2
    if "tour of" in lower or " tour " in lower:
        score += 1
    if re.search(r"\d{4}", line):
        score += 1
    if re.search(r"under-19|under 19|\bu19\b", lower):
        senior_match = not any(
            re.search(r"under-19|under 19|\bu19\b", team, re.IGNORECASE) for team in (team1, team2)
        )
        if senior_match:
            score -= 10
    return score


def _fixture_series_parts(fixture_line: str) -> list[str]:
    if not fixture_line:
        return []
    parts: list[str] = []
    for segment in re.split(r"[,·]", fixture_line):
        part = segment.strip()
        if (
            part
            and len(part) > 5
            and not MATCH_LABEL_PATTERN.search(part)
            and not _looks_like_date(part)
            and not DATE_PATTERN.search(part)
            and (" tour " in part.lower() or "tour of" in part.lower() or re.search(r"\d{4}", part))
        ):
            parts.append(part)
    return parts


def _extract_series(lines: list[str], fixture_line: str, team1: str, team2: str) -> str:
    candidates: list[str] = []
    for line in lines:
        lower = line.lower()
        if any(lower.startswith(p) for p in ("live", "result", "today,", "tomorrow,", "match yet", "match starts")):
            continue
        if _line_is_tracked_team(line) or MATCH_LABEL_PATTERN.search(line):
            continue
        if TIME_PATTERN.search(line) or SCORE_PATTERN.search(line):
            continue
        if STARTS_AT_PATTERN.search(line):
            continue
        if (" tour " in lower or "tour of" in lower or re.search(r"\d{4}", line)) and len(line) > 5:
            candidates.append(line)

    candidates.extend(_fixture_series_parts(fixture_line))

    if fixture_line:
        parts = [part.strip() for part in fixture_line.split(",") if part.strip()]
        for part in reversed(parts):
            if DATE_RANGE_PATTERN.search(part) or DATE_PATTERN.search(part):
                continue
            if MATCH_LABEL_PATTERN.search(part) and len(parts) == 1:
                continue
            if len(part) > 5 and not _line_is_tracked_team(part) and not _looks_like_date(part):
                candidates.append(part)

    if not candidates:
        return "International Cricket"

    best = max(candidates, key=lambda candidate: _series_matches_teams(candidate, team1, team2))
    if _series_matches_teams(best, team1, team2) <= 0:
        return "International Cricket"
    return best


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

    raw_time, is_venue_local = _extract_time_with_is_local(block)
    raw_match_date = parse_match_date_from_block(block)

    if venue == "TBC" and fixture_line:
        extracted = _extract_venue_from_line(fixture_line)
        if extracted:
            venue = extracted

    series = _extract_series(lines, fixture_line, teams[0], teams[1])
    if venue == "TBC":
        inferred = _infer_venue_from_context(series, teams[0], teams[1])
        if inferred:
            venue = inferred

    if is_venue_local:
        # "X local" format = ESPN venue-local time; convert to PKT via venue timezone
        time_str, display_date = _convert_preview_time(raw_time, raw_match_date, venue)
    else:
        # All other formats ("Starts at X", "TODAY/TOMORROW, X", bare X) are already
        # in the browser's timezone which Playwright forces to Asia/Karachi (PKT).
        time_str = raw_time
        display_date = raw_match_date
    match_date = display_date if display_date is not None else raw_match_date
    if match_date:
        date_str = match_date.strftime("%A, %d %B")
    else:
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

    day_label = _detect_day_label(block, raw_match_date)

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
        day_label=day_label,
        match_date=match_date,
    )


def build_preview_caption(info: PreviewMatchInfo) -> str:
    abbrev1 = _team_abbrev(info.team1)
    abbrev2 = _team_abbrev(info.team2)
    series_tag = _hashtag_token(info.series)
    prefix = "Tomorrow!" if info.day_label == "tomorrow" else "Today!"
    headline = (
        f"{prefix} {info.team1} vs {info.team2}, {info.match_label} at {info.time_str} "
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


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    bundled = FONTS_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    candidates = [
        bundled,
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    raise RuntimeError(
        "No TrueType font found. Add DejaVuSans.ttf to assets/fonts/ or install fonts-dejavu-core."
    )


def _hex_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _interpolate_color(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))  # type: ignore[return-value]


def _draw_toss_background(width: int, height: int, team1: str, team2: str) -> Image.Image:
    """Light base with left/right team-color washes from TEAM_KITS."""
    base = _hex_rgb(TOSS_BASE_BG)
    left_c = _hex_rgb(_toss_wash_color(team1))
    right_c = _hex_rgb(_toss_wash_color(team2))
    img = Image.new("RGB", (width, height))
    pixels = img.load()
    half = width / 2.0
    for x in range(width):
        if x < half:
            wash = max(0.0, 1.0 - x / half) * TOSS_WASH_ALPHA
            color = _interpolate_color(base, left_c, wash)
        else:
            wash = max(0.0, (x - half) / half) * TOSS_WASH_ALPHA
            color = _interpolate_color(base, right_c, wash)
        for y in range(height):
            pixels[x, y] = color  # type: ignore[index]
    return img


def _paste_flag_in_colored_frame(
    base: Image.Image,
    team: str,
    center_x: int,
    top_y: int,
) -> None:
    """Paste a team flag inside a rounded rectangle filled with team primary color."""
    primary, _ = _team_kit_colors(team)
    frame_w = TOSS_FLAG_W + TOSS_FLAG_FRAME_PAD * 2
    frame_h = TOSS_FLAG_H + TOSS_FLAG_FRAME_PAD * 2
    frame_left = center_x - frame_w // 2
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle(
        [(frame_left, top_y), (frame_left + frame_w, top_y + frame_h)],
        radius=TOSS_FLAG_FRAME_RADIUS,
        fill=primary,
    )
    inner_pad = 4
    draw.rounded_rectangle(
        [
            (frame_left + inner_pad, top_y + inner_pad),
            (frame_left + frame_w - inner_pad, top_y + frame_h - inner_pad),
        ],
        radius=max(TOSS_FLAG_FRAME_RADIUS - 2, 4),
        fill="#FFFFFF",
    )
    flag = _load_team_flag(
        team,
        TOSS_FLAG_W,
        TOSS_FLAG_H,
        max(TOSS_FLAG_FRAME_RADIUS - 4, 4),
    )
    flag_x = center_x - TOSS_FLAG_W // 2
    flag_y = top_y + TOSS_FLAG_FRAME_PAD
    base.paste(flag, (flag_x, flag_y), flag)


def _toss_winner_team(info: MatchUpdateInfo) -> str:
    headline = (info.headline or "").lower()
    if "won the toss" in headline:
        for team in (info.team1, info.team2):
            if team.lower() in headline:
                return team
    for team in (info.team1, info.team2):
        if headline.startswith(team.lower()) or f"{team.lower()} chose" in headline:
            return team
    return ""


def _load_circular_headshot(
    image_path: Path,
    size: int,
    *,
    ring_color: str = "#FFFFFF",
    ring_width: int = 4,
    highlight: bool = False,
) -> Image.Image:
    with Image.open(image_path) as raw:
        img = raw.convert("RGBA")
    min_side = min(img.size)
    left = (img.width - min_side) // 2
    top = (img.height - min_side) // 2
    img = img.crop((left, top, left + min_side, top + min_side))
    img = img.resize((size, size), Image.Resampling.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, size, size), fill=255)
    img.putalpha(mask)

    outer = size + ring_width * 2
    if highlight:
        ring_width += 2
        outer = size + ring_width * 2
    canvas = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(canvas)
    ring_draw.ellipse((0, 0, outer - 1, outer - 1), fill=ring_color)
    canvas.paste(img, (ring_width, ring_width), img)
    return canvas


def _paste_headshot_centered(
    base: Image.Image,
    image_path: Path,
    center_x: int,
    top_y: int,
    *,
    highlight: bool = False,
    team: str = "",
) -> None:
    ring_color = _team_kit_colors(team)[0] if team else "#FFFFFF"
    headshot = _load_circular_headshot(
        image_path,
        TOSS_CAPTAIN_PHOTO_SIZE,
        ring_color=ring_color,
        ring_width=5 if highlight else 3,
        highlight=highlight,
    )
    x = center_x - headshot.width // 2
    base.paste(headshot, (x, top_y), headshot)


def _paste_small_flag_centered(
    base: Image.Image,
    team: str,
    center_x: int,
    top_y: int,
) -> None:
    flag = _load_team_flag(team, TOSS_SMALL_FLAG_W, TOSS_SMALL_FLAG_H, 6)
    _paste_flag_centered(base, flag, center_x, top_y)


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


def _draw_right_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    right_x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    """Draw text right-aligned so its right edge is at right_x."""
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((right_x - w, y), text, font=font, fill=fill)


def _draw_score_fitted(
    draw: ImageDraw.ImageDraw,
    text: str,
    center_x: int,
    y: int,
    fill: str,
    max_half_width: int,
    max_size: int = 56,
) -> None:
    """Draw a score centered at center_x, shrinking font until it fits.

    Tries font sizes from max_size down; picks the largest whose half-width
    does not exceed max_half_width (so the text stays within image bounds).
    """
    for size in (max_size, 46, 38, 32, 28, 24, 20):
        font = _load_font(size, bold=True)
        bbox = draw.textbbox((0, 0), text, font=font)
        half_w = (bbox[2] - bbox[0]) // 2
        if half_w <= max_half_width:
            draw.text((center_x - half_w, y), text, font=font, fill=fill)
            return
    # Fallback: draw at smallest size regardless
    font = _load_font(20, bold=True)
    bbox = draw.textbbox((0, 0), text, font=font)
    half_w = (bbox[2] - bbox[0]) // 2
    draw.text((center_x - half_w, y), text, font=font, fill=fill)


def _parse_compact_date(date_str: str) -> str | None:
    cleaned = re.sub(
        r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*",
        "",
        date_str.strip(),
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace(" - ", " ").strip(" ,")

    for fmt in ("%d %B %Y", "%d %B, %Y", "%B %d %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return f"{parsed.strftime('%b')} {parsed.day}"
        except ValueError:
            continue

    match = re.search(r"(\d{1,2})\s+(\w+)", cleaned, re.IGNORECASE)
    if match:
        day, month_name = match.groups()
        month_num = MONTHS.get(month_name.lower())
        if month_num:
            return f"{datetime(2000, month_num, int(day)).strftime('%b')} {int(day)}"
    return None


def _compact_datetime(info: PreviewMatchInfo) -> str:
    if info.match_date:
        compact_date = f"{info.match_date.strftime('%b')} {info.match_date.day}"
    else:
        compact_date = _parse_compact_date(info.date_str)
        if not compact_date:
            compact_date = None

    time_part = info.time_str.strip()
    if compact_date and time_part.upper() != "TBC":
        return f"{compact_date}, {time_part.lower()}"
    if compact_date:
        return compact_date
    if time_part.upper() != "TBC":
        return time_part.lower()
    return "TBC"


def _rounded_flag_image(flag: Image.Image, width: int, height: int, radius: int) -> Image.Image:
    flag = flag.convert("RGBA")
    flag = flag.resize((width, height), Image.Resampling.LANCZOS)
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([(0, 0), (width, height)], radius=radius, fill=255)
    rounded = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    rounded.paste(flag, (0, 0), mask)
    return rounded


def _fallback_flag(
    team: str,
    width: int,
    height: int,
    radius: int = FLAG_RADIUS,
) -> Image.Image:
    primary, _ = _team_kit_colors(team)
    img = Image.new("RGBA", (width, height), _hex_rgb(primary) + (255,))
    draw = ImageDraw.Draw(img)
    font_size = max(24, min(width, height) // 3)
    font = _load_font(font_size, bold=True)
    abbrev = _team_abbrev(team)
    bbox = draw.textbbox((0, 0), abbrev, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((width - tw) // 2, (height - th) // 2 - 4), abbrev, font=font, fill="#FFFFFF")
    return _rounded_flag_image(img, width, height, radius)


def _load_team_flag(
    team: str,
    width: int = FLAG_WIDTH,
    height: int = FLAG_HEIGHT,
    radius: int = FLAG_RADIUS,
) -> Image.Image:
    slug = _team_slug(team)
    path = FLAGS_DIR / f"{slug}.png"
    if path.exists():
        try:
            with Image.open(path) as flag:
                return _rounded_flag_image(flag, width, height, radius)
        except OSError:
            pass
    return _fallback_flag(team, width, height, radius)


def _paste_flag_centered(base: Image.Image, flag: Image.Image, center_x: int, top_y: int) -> None:
    x = center_x - flag.width // 2
    base.paste(flag, (x, top_y), flag)


def _draw_flag_card(info: PreviewMatchInfo) -> Image.Image:
    img = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(img)

    datetime_font = _load_font(36, bold=False)
    vs_font = _load_font(48, bold=False)
    name_font = _load_font(52, bold=True)
    match_font = _load_font(40, bold=False)
    series_font = _load_font(32, bold=False)

    _draw_centered_text(draw, _compact_datetime(info), IMAGE_WIDTH // 2, DATETIME_Y, datetime_font, TEXT_SECONDARY)

    left_flag = _load_team_flag(info.team1)
    right_flag = _load_team_flag(info.team2)
    _paste_flag_centered(img, left_flag, LEFT_FLAG_CENTER_X, FLAG_Y)
    _paste_flag_centered(img, right_flag, RIGHT_FLAG_CENTER_X, FLAG_Y)

    draw = ImageDraw.Draw(img)
    _draw_centered_text(draw, "vs", IMAGE_WIDTH // 2, VS_Y, vs_font, TEXT_MUTED)
    _draw_centered_text(draw, info.team1, LEFT_FLAG_CENTER_X, NAME_Y, name_font, TEXT_PRIMARY)
    _draw_centered_text(draw, info.team2, RIGHT_FLAG_CENTER_X, NAME_Y, name_font, TEXT_PRIMARY)

    match_line = info.match_label
    if info.venue and info.venue != "TBC":
        match_line = f"{match_line} · {info.venue}"
    _draw_centered_text(draw, match_line, IMAGE_WIDTH // 2, MATCH_LABEL_Y, match_font, TEXT_SECONDARY)

    series_line = info.series[:70]
    if series_line:
        _draw_centered_text(draw, series_line, IMAGE_WIDTH // 2, SERIES_Y, series_font, TEXT_MUTED)

    return img


def _draw_left_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int = 420,
) -> None:
    display = text if len(text) <= 48 else text[:45] + "..."
    draw.text((x, y), display, font=font, fill=fill)


def _update_footer_line(info: MatchUpdateInfo) -> str:
    footer = info.match_label
    if info.series and info.series not in footer and info.match_label not in info.series:
        footer = f"{footer} · {info.series[:50]}"
    elif info.series and info.match_label in info.series:
        footer = info.series[:80]
    return footer[:80]


def _draw_live_stadium_background(width: int, height: int) -> Image.Image:
    img = _draw_vertical_gradient(
        width,
        height,
        LIVE_PREMIUM_BG_TOP,
        LIVE_PREMIUM_BG_MID,
        LIVE_PREMIUM_BG_BOTTOM,
    )
    if STADIUM_ASSET.exists():
        try:
            with Image.open(STADIUM_ASSET) as stadium:
                stadium = stadium.convert("RGBA")
                target_w = width
                target_h = int(height * 0.72)
                stadium = stadium.resize((target_w, target_h), Image.Resampling.LANCZOS)
                stadium = stadium.filter(ImageFilter.GaussianBlur(radius=2))
                alpha = stadium.split()[3].point(lambda p: int(p * 0.35))
                stadium.putalpha(alpha)
                img.paste(stadium, (0, int(height * 0.12)), stadium)
        except OSError:
            pass
    else:
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for cx, cy, rx, ry in (
            (width // 2, int(height * 0.55), width // 2, int(height * 0.28)),
            (width // 4, int(height * 0.62), width // 5, int(height * 0.18)),
            (3 * width // 4, int(height * 0.62), width // 5, int(height * 0.18)),
        ):
            draw.ellipse(
                [(cx - rx, cy - ry), (cx + rx, cy + ry)],
                fill=(255, 255, 255, 18),
            )
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img


def _draw_live_badge_premium(draw: ImageDraw.ImageDraw, badge_text: str, y: int) -> None:
    font = _load_font(20, bold=True)
    bbox = draw.textbbox((0, 0), badge_text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x, pad_y = 18, 8
    pill_w = text_w + pad_x * 2
    pill_h = text_h + pad_y * 2
    cx = UPDATE_IMAGE_WIDTH // 2
    pill_x = cx - pill_w // 2
    pill_y = y
    draw.rounded_rectangle(
        [(pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h)],
        radius=pill_h // 2,
        fill=LIVE_PREMIUM_BADGE_RED,
    )
    draw.text((pill_x + pad_x, pill_y + pad_y - 1), badge_text, font=font, fill=LIVE_PREMIUM_TEXT)
    arc_y = pill_y + pill_h // 2
    for side, sign in ((pill_x - 14, -1), (pill_x + pill_w + 14, 1)):
        for offset in (0, 8, 16):
            x0 = side + sign * offset
            x1 = side + sign * (offset + 10)
            draw.arc(
                [(min(x0, x1) - 6, arc_y - 10), (max(x0, x1) + 6, arc_y + 10)],
                start=270 if sign < 0 else 90,
                end=90 if sign < 0 else 270,
                fill=LIVE_PREMIUM_TEXT,
                width=2,
            )


def _premium_pill_dimensions(info: MatchUpdateInfo) -> tuple[int, int]:
    pill_font = _load_font(22, bold=False)
    display = _update_footer_line(info)[:64]
    bbox = pill_font.getbbox(display)
    text_h = bbox[3] - bbox[1]
    pad_y = 12
    return LIVE_PREMIUM_PILL_Y, text_h + pad_y * 2


def _premium_subline_bottom(info: MatchUpdateInfo, pill_y: int, pill_h: int) -> int:
    is_chase = info.innings_status in ("chase", "innings_break") and info.score1 and info.score2
    has_rr = is_chase and (info.current_run_rate or info.required_run_rate)
    has_headline = bool(info.headline) and not is_chase
    if has_rr or has_headline:
        return LIVE_PREMIUM_HEADLINE_Y + LIVE_PREMIUM_HEADLINE_LINE_H
    return pill_y + pill_h


def _premium_panel_top(info: MatchUpdateInfo, pill_y: int, pill_h: int) -> int:
    return _premium_subline_bottom(info, pill_y, pill_h) + LIVE_PREMIUM_PANEL_GAP_AFTER_PILL


def _premium_panel_height(info: MatchUpdateInfo) -> int:
    batter_lines = len(info.batters[:2]) if info.batters else 0
    bowler_lines = len(info.bowlers[:2]) if info.bowlers else 0
    line_count = max(batter_lines, bowler_lines, 1)
    return LIVE_PREMIUM_PANEL_HEADER_H + line_count * LIVE_PREMIUM_PANEL_LINE_H + LIVE_PREMIUM_PANEL_INNER_PAD


def _premium_live_card_height(info: MatchUpdateInfo) -> int:
    pill_y, pill_h = _premium_pill_dimensions(info)
    if info.batters or info.bowlers:
        panel_top = _premium_panel_top(info, pill_y, pill_h)
        return panel_top + _premium_panel_height(info) + LIVE_PREMIUM_BOTTOM_PAD
    is_chase = info.innings_status in ("chase", "innings_break") and info.score1 and info.score2
    has_rr = is_chase and (info.current_run_rate or info.required_run_rate)
    has_headline = bool(info.headline) and not is_chase
    if has_rr or has_headline:
        return LIVE_PREMIUM_HEADLINE_Y + LIVE_PREMIUM_HEADLINE_LINE_H + LIVE_PREMIUM_BOTTOM_PAD
    return pill_y + pill_h + LIVE_PREMIUM_BOTTOM_PAD


def _draw_premium_stats_panels(
    base: Image.Image,
    info: MatchUpdateInfo,
    pill_y: int,
    pill_h: int,
) -> Image.Image:
    if not info.batters and not info.bowlers:
        return base

    height = base.height
    overlay = Image.new("RGBA", (UPDATE_IMAGE_WIDTH, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    panel_top = _premium_panel_top(info, pill_y, pill_h)
    panel_h = _premium_panel_height(info)
    left_x = (UPDATE_IMAGE_WIDTH - LIVE_PREMIUM_PANEL_W * 2 - LIVE_PREMIUM_PANEL_GAP) // 2
    right_x = left_x + LIVE_PREMIUM_PANEL_W + LIVE_PREMIUM_PANEL_GAP
    panel_text_right = right_x + LIVE_PREMIUM_PANEL_W - 20
    line_start_y = panel_top + LIVE_PREMIUM_PANEL_HEADER_H

    batting_team = info.batting_team or info.team1
    bowling_team = info.bowling_team or info.team2

    header_font = _load_font(22, bold=True)
    line_font = _load_font(20, bold=False)

    if info.batters:
        draw.rounded_rectangle(
            [(left_x, panel_top), (left_x + LIVE_PREMIUM_PANEL_W, panel_top + panel_h)],
            radius=16,
            fill=LIVE_PREMIUM_PANEL_BAT_BG,
        )
        draw.text(
            (left_x + 20, panel_top + 16),
            f"{_team_abbrev(batting_team)} batting",
            font=header_font,
            fill=LIVE_PREMIUM_BAT_ACCENT,
        )
        for idx, batter in enumerate(info.batters[:2]):
            draw.text(
                (left_x + 20, line_start_y + idx * LIVE_PREMIUM_PANEL_LINE_H),
                f"• {batter.lstrip('• ')}",
                font=line_font,
                fill=LIVE_PREMIUM_TEXT,
            )

    if info.bowlers:
        draw.rounded_rectangle(
            [(right_x, panel_top), (right_x + LIVE_PREMIUM_PANEL_W, panel_top + panel_h)],
            radius=16,
            fill=LIVE_PREMIUM_PANEL_BOWL_BG,
        )
        _draw_right_text(
            draw,
            f"{_team_abbrev(bowling_team)} bowling",
            panel_text_right,
            panel_top + 16,
            header_font,
            LIVE_PREMIUM_BOWL_ACCENT,
        )
        for idx, bowler in enumerate(info.bowlers[:2]):
            line = bowler.strip()
            _draw_right_text(
                draw,
                line,
                panel_text_right,
                line_start_y + idx * LIVE_PREMIUM_PANEL_LINE_H,
                line_font,
                LIVE_PREMIUM_TEXT,
            )

    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")


def _premium_team_label(team: str) -> str:
    return team.replace(" Women", " WOMEN").upper()


def _draw_premium_live_card(info: MatchUpdateInfo) -> Image.Image:
    height = _premium_live_card_height(info)
    img = _draw_live_stadium_background(UPDATE_IMAGE_WIDTH, height)
    draw = ImageDraw.Draw(img)

    name_font = _load_font(22, bold=True)
    status_font = _load_font(20, bold=False)
    side_score_font = _load_font(28, bold=True)
    side_overs_font = _load_font(18, bold=False)

    left_flag = _load_team_flag(info.team1, UPDATE_FLAG_WIDTH, UPDATE_FLAG_HEIGHT, UPDATE_FLAG_RADIUS)
    right_flag = _load_team_flag(info.team2, UPDATE_FLAG_WIDTH, UPDATE_FLAG_HEIGHT, UPDATE_FLAG_RADIUS)
    _paste_flag_centered(img, left_flag, LIVE_PREMIUM_LEFT_CX, LIVE_PREMIUM_FLAG_Y)
    _paste_flag_centered(img, right_flag, LIVE_PREMIUM_RIGHT_CX, LIVE_PREMIUM_FLAG_Y)

    draw = ImageDraw.Draw(img)
    _draw_live_badge_premium(draw, _live_badge_text(info), LIVE_BADGE_Y)
    _draw_centered_text(
        draw,
        _premium_team_label(info.team1),
        LIVE_PREMIUM_LEFT_CX,
        LIVE_PREMIUM_TEAM_Y,
        name_font,
        LIVE_PREMIUM_TEXT,
    )
    _draw_centered_text(
        draw,
        _premium_team_label(info.team2),
        LIVE_PREMIUM_RIGHT_CX,
        LIVE_PREMIUM_TEAM_Y,
        name_font,
        LIVE_PREMIUM_TEXT,
    )

    is_chase = info.innings_status in ("chase", "innings_break") and info.score1 and info.score2
    batting = info.batting_team or info.team1
    if is_chase:
        _draw_score_fitted(
            draw,
            info.score1 or "\u2014",
            LIVE_PREMIUM_LEFT_CX,
            LIVE_PREMIUM_CENTER_SCORE_Y - 10,
            LIVE_PREMIUM_TEXT,
            160,
            max_size=40,
        )
        if info.overs1:
            _draw_centered_text(
                draw,
                info.overs1,
                LIVE_PREMIUM_LEFT_CX,
                LIVE_PREMIUM_CENTER_OVERS_Y - 10,
                side_overs_font,
                LIVE_PREMIUM_MUTED,
            )
        _draw_score_fitted(
            draw,
            info.score2 or "\u2014",
            LIVE_PREMIUM_RIGHT_CX,
            LIVE_PREMIUM_CENTER_SCORE_Y - 10,
            LIVE_PREMIUM_SCORE,
            160,
            max_size=40,
        )
        if info.overs2:
            _draw_centered_text(
                draw,
                info.overs2,
                LIVE_PREMIUM_RIGHT_CX,
                LIVE_PREMIUM_CENTER_OVERS_Y - 10,
                side_overs_font,
                LIVE_PREMIUM_MUTED,
            )
        if info.headline:
            _draw_centered_text(
                draw,
                info.headline[:72],
                LIVE_PREMIUM_CENTER_CX,
                LIVE_PREMIUM_CENTER_SCORE_Y + 8,
                _load_font(24, bold=True),
                LIVE_PREMIUM_TEXT,
            )
    else:
        if batting == info.team1:
            center_score = info.score1 or "\u2014"
            center_overs = info.overs1
            other_yet = not info.score2 or info.opponent_yet_to_bat
        else:
            center_score = info.score2 or "\u2014"
            center_overs = info.overs2
            other_yet = not info.score1 or info.opponent_yet_to_bat

        _draw_score_fitted(
            draw,
            center_score,
            LIVE_PREMIUM_CENTER_CX,
            LIVE_PREMIUM_CENTER_SCORE_Y,
            LIVE_PREMIUM_SCORE,
            220,
            max_size=64,
        )
        if center_overs:
            _draw_centered_text(
                draw,
                center_overs,
                LIVE_PREMIUM_CENTER_CX,
                LIVE_PREMIUM_CENTER_OVERS_Y,
                side_overs_font,
                LIVE_PREMIUM_MUTED,
            )

        if other_yet:
            other_cx = LIVE_PREMIUM_RIGHT_CX if batting == info.team1 else LIVE_PREMIUM_LEFT_CX
            status = "Innings Break" if info.innings_status == "innings_break" else "Yet to Bat"
            _draw_centered_text(
                draw,
                status,
                other_cx,
                LIVE_PREMIUM_CENTER_SCORE_Y + 18,
                status_font,
                LIVE_PREMIUM_MUTED,
            )
        elif info.score1 and info.score2:
            if batting == info.team1:
                _draw_score_fitted(
                    draw,
                    info.score2,
                    LIVE_PREMIUM_RIGHT_CX,
                    LIVE_PREMIUM_CENTER_SCORE_Y - 10,
                    LIVE_PREMIUM_TEXT,
                    160,
                    max_size=36,
                )
                if info.overs2:
                    _draw_centered_text(
                        draw,
                        info.overs2,
                        LIVE_PREMIUM_RIGHT_CX,
                        LIVE_PREMIUM_CENTER_OVERS_Y - 10,
                        side_overs_font,
                        LIVE_PREMIUM_MUTED,
                    )
            else:
                _draw_score_fitted(
                    draw,
                    info.score1,
                    LIVE_PREMIUM_LEFT_CX,
                    LIVE_PREMIUM_CENTER_SCORE_Y - 10,
                    LIVE_PREMIUM_TEXT,
                    160,
                    max_size=36,
                )
                if info.overs1:
                    _draw_centered_text(
                        draw,
                        info.overs1,
                        LIVE_PREMIUM_LEFT_CX,
                        LIVE_PREMIUM_CENTER_OVERS_Y - 10,
                        side_overs_font,
                        LIVE_PREMIUM_MUTED,
                    )

    pill_text = _update_footer_line(info)
    pill_font = _load_font(22, bold=False)
    display = pill_text[:64]
    bbox = draw.textbbox((0, 0), display, font=pill_font)
    text_w = bbox[2] - bbox[0]
    pad_x, pad_y = 28, 12
    pill_w = min(text_w + pad_x * 2, UPDATE_IMAGE_WIDTH - 80)
    pill_x = (UPDATE_IMAGE_WIDTH - pill_w) // 2
    pill_y, pill_h = _premium_pill_dimensions(info)
    pill_overlay = Image.new("RGBA", (UPDATE_IMAGE_WIDTH, height), (0, 0, 0, 0))
    pill_draw = ImageDraw.Draw(pill_overlay)
    pill_draw.rounded_rectangle(
        [(pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h)],
        radius=pill_h // 2,
        fill=(255, 255, 255, 215),
    )
    img = Image.alpha_composite(img.convert("RGBA"), pill_overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    _draw_centered_text(draw, display, UPDATE_IMAGE_WIDTH // 2, pill_y + pad_y - 1, pill_font, "#202124")

    rr_parts = []
    if info.current_run_rate:
        rr_parts.append(f"CRR: {info.current_run_rate}")
    if info.required_run_rate:
        rr_parts.append(f"RRR: {info.required_run_rate}")
    if rr_parts and is_chase:
        _draw_centered_text(
            draw,
            " \u00b7 ".join(rr_parts),
            LIVE_PREMIUM_CENTER_CX,
            LIVE_PREMIUM_HEADLINE_Y,
            _load_font(20, bold=False),
            LIVE_PREMIUM_MUTED,
        )
    elif info.headline and not is_chase:
        _draw_centered_text(
            draw,
            info.headline[:72],
            LIVE_PREMIUM_CENTER_CX,
            LIVE_PREMIUM_HEADLINE_Y,
            _load_font(22, bold=False),
            LIVE_PREMIUM_MUTED,
        )

    return _draw_premium_stats_panels(img, info, pill_y, pill_h)


def _draw_live_player_stats(
    draw: ImageDraw.ImageDraw,
    info: MatchUpdateInfo,
    font: ImageFont.ImageFont,
    label_font: ImageFont.ImageFont,
) -> None:
    if not info.bowlers and not info.batters:
        return

    divider_y = LIVE_STATS_Y - 14
    draw.line([(80, divider_y), (UPDATE_IMAGE_WIDTH - 80, divider_y)], fill="#E0E0E0", width=1)

    bowling_x = UPDATE_LEFT_X - 60
    batting_x = UPDATE_RIGHT_X - 60
    if info.bowling_team == info.team2:
        bowling_x = UPDATE_RIGHT_X - 60
        batting_x = UPDATE_LEFT_X - 60

    y = LIVE_STATS_Y
    if info.bowlers:
        _draw_left_text(
            draw,
            f"{_team_abbrev(info.bowling_team)} bowling",
            bowling_x,
            y,
            label_font,
            TEXT_MUTED,
        )
        for idx, bowler in enumerate(info.bowlers[:2]):
            _draw_left_text(draw, bowler, bowling_x, y + 28 + idx * LIVE_STATS_LINE_H, font, TEXT_PRIMARY)
    if info.batters:
        _draw_left_text(
            draw,
            f"{_team_abbrev(info.batting_team)} batting",
            batting_x,
            y,
            label_font,
            TEXT_MUTED,
        )
        for idx, batter in enumerate(info.batters[:2]):
            _draw_left_text(draw, batter, batting_x, y + 28 + idx * LIVE_STATS_LINE_H, font, TEXT_PRIMARY)


def _draw_first_innings_card(info: MatchUpdateInfo) -> Image.Image:
    img = Image.new("RGB", (UPDATE_IMAGE_WIDTH, LIVE_IMAGE_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(img)

    name_font = _load_font(26, bold=True)
    score_font = _load_font(56, bold=True)
    overs_font = _load_font(26, bold=False)
    mid_font = _load_font(28, bold=False)
    footer_font = _load_font(26, bold=False)
    stats_font = _load_font(22, bold=False)
    stats_label_font = _load_font(20, bold=True)
    badge_font = _load_font(22, bold=True)

    left_flag = _load_team_flag(info.team1, UPDATE_FLAG_WIDTH, UPDATE_FLAG_HEIGHT, UPDATE_FLAG_RADIUS)
    right_flag = _load_team_flag(info.team2, UPDATE_FLAG_WIDTH, UPDATE_FLAG_HEIGHT, UPDATE_FLAG_RADIUS)
    _paste_flag_centered(img, left_flag, UPDATE_LEFT_X, LIVE_FLAG_Y)
    _paste_flag_centered(img, right_flag, UPDATE_RIGHT_X, LIVE_FLAG_Y)

    draw = ImageDraw.Draw(img)
    _draw_centered_text(draw, _live_badge_text(info), UPDATE_IMAGE_WIDTH // 2, LIVE_BADGE_Y, badge_font, "#D93025")
    _draw_centered_text(draw, info.team1, UPDATE_LEFT_X, LIVE_NAME_Y, name_font, TEXT_PRIMARY)
    _draw_centered_text(draw, info.team2, UPDATE_RIGHT_X, LIVE_NAME_Y, name_font, TEXT_PRIMARY)

    if info.batting_team == info.team1:
        _draw_score_fitted(draw, info.score1 or "\u2014", UPDATE_LEFT_X, LIVE_SCORE_Y, TEXT_PRIMARY, UPDATE_LEFT_X)
        if info.overs1:
            _draw_centered_text(draw, info.overs1, UPDATE_LEFT_X, LIVE_OVERS_Y, overs_font, TEXT_MUTED)
        _draw_centered_text(draw, "Yet to Bat", UPDATE_RIGHT_X, LIVE_SCORE_Y + 10, mid_font, TEXT_MUTED)
    else:
        _draw_score_fitted(draw, info.score2 or "\u2014", UPDATE_RIGHT_X, LIVE_SCORE_Y, TEXT_PRIMARY, UPDATE_IMAGE_WIDTH - UPDATE_RIGHT_X)
        if info.overs2:
            _draw_centered_text(draw, info.overs2, UPDATE_RIGHT_X, LIVE_OVERS_Y, overs_font, TEXT_MUTED)
        _draw_centered_text(draw, "Yet to Bat", UPDATE_LEFT_X, LIVE_SCORE_Y + 10, mid_font, TEXT_MUTED)

    if info.headline:
        _draw_centered_text(draw, info.headline[:90], UPDATE_IMAGE_WIDTH // 2, LIVE_MID_Y, mid_font, TEXT_SECONDARY)
    elif info.current_run_rate:
        abbrev = _team_abbrev(info.batting_team)
        _draw_centered_text(
            draw,
            f"{abbrev} Current Run Rate: {info.current_run_rate}",
            UPDATE_IMAGE_WIDTH // 2,
            LIVE_MID_Y,
            mid_font,
            TEXT_SECONDARY,
        )

    _draw_centered_text(draw, _update_footer_line(info), UPDATE_IMAGE_WIDTH // 2, LIVE_FOOTER_Y, footer_font, TEXT_MUTED)
    _draw_live_player_stats(draw, info, stats_font, stats_label_font)
    return img


def _draw_chase_card(info: MatchUpdateInfo) -> Image.Image:
    img = Image.new("RGB", (UPDATE_IMAGE_WIDTH, LIVE_IMAGE_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(img)

    name_font = _load_font(26, bold=True)
    score_font = _load_font(56, bold=True)
    overs_font = _load_font(26, bold=False)
    mid_font = _load_font(28, bold=True)
    rr_font = _load_font(24, bold=False)
    footer_font = _load_font(26, bold=False)
    stats_font = _load_font(22, bold=False)
    stats_label_font = _load_font(20, bold=True)
    badge_font = _load_font(22, bold=True)

    left_flag = _load_team_flag(info.team1, UPDATE_FLAG_WIDTH, UPDATE_FLAG_HEIGHT, UPDATE_FLAG_RADIUS)
    right_flag = _load_team_flag(info.team2, UPDATE_FLAG_WIDTH, UPDATE_FLAG_HEIGHT, UPDATE_FLAG_RADIUS)
    _paste_flag_centered(img, left_flag, UPDATE_LEFT_X, LIVE_FLAG_Y)
    _paste_flag_centered(img, right_flag, UPDATE_RIGHT_X, LIVE_FLAG_Y)

    draw = ImageDraw.Draw(img)
    _draw_centered_text(draw, _live_badge_text(info), UPDATE_IMAGE_WIDTH // 2, LIVE_BADGE_Y, badge_font, "#D93025")
    _draw_centered_text(draw, info.team1, UPDATE_LEFT_X, LIVE_NAME_Y, name_font, TEXT_PRIMARY)
    _draw_centered_text(draw, info.team2, UPDATE_RIGHT_X, LIVE_NAME_Y, name_font, TEXT_PRIMARY)

    _draw_score_fitted(draw, info.score1 or "\u2014", UPDATE_LEFT_X, LIVE_SCORE_Y, TEXT_PRIMARY, UPDATE_LEFT_X)
    _draw_score_fitted(draw, info.score2 or "\u2014", UPDATE_RIGHT_X, LIVE_SCORE_Y, TEXT_PRIMARY, UPDATE_IMAGE_WIDTH - UPDATE_RIGHT_X)
    if info.overs1:
        _draw_centered_text(draw, info.overs1, UPDATE_LEFT_X, LIVE_OVERS_Y, overs_font, TEXT_MUTED)
    if info.overs2:
        _draw_centered_text(draw, info.overs2, UPDATE_RIGHT_X, LIVE_OVERS_Y, overs_font, TEXT_MUTED)

    if info.headline:
        headline = info.headline[:95]
        _draw_centered_text(draw, headline, UPDATE_IMAGE_WIDTH // 2, LIVE_MID_Y, mid_font, TEXT_PRIMARY)

    rr_parts = []
    if info.current_run_rate:
        rr_parts.append(f"CRR: {info.current_run_rate}")
    if info.required_run_rate:
        rr_parts.append(f"RRR: {info.required_run_rate}")
    if rr_parts:
        _draw_centered_text(draw, " · ".join(rr_parts), UPDATE_IMAGE_WIDTH // 2, LIVE_RR_Y, rr_font, TEXT_MUTED)

    _draw_centered_text(draw, _update_footer_line(info), UPDATE_IMAGE_WIDTH // 2, LIVE_FOOTER_Y, footer_font, TEXT_MUTED)
    _draw_live_player_stats(draw, info, stats_font, stats_label_font)
    return img


def _draw_toss_card(info: MatchUpdateInfo) -> Image.Image:
    img = _draw_toss_background(
        UPDATE_IMAGE_WIDTH, TOSS_IMAGE_HEIGHT, info.team1, info.team2
    )
    _paste_flag_in_colored_frame(img, info.team1, TOSS_LEFT_X, TOSS_FLAG_Y)
    _paste_flag_in_colored_frame(img, info.team2, TOSS_RIGHT_X, TOSS_FLAG_Y)

    draw = ImageDraw.Draw(img)
    badge_font = _load_font(22, bold=True)
    name_font = _load_font(28, bold=True)
    headline_font = _load_font(34, bold=True)
    footer_font = _load_font(24, bold=False)

    _draw_centered_text(draw, "TOSS", UPDATE_IMAGE_WIDTH // 2, TOSS_BADGE_Y, badge_font, TEXT_MUTED)
    _draw_centered_text(draw, info.team1, TOSS_LEFT_X, TOSS_NAME_Y, name_font, TEXT_PRIMARY)
    _draw_centered_text(draw, info.team2, TOSS_RIGHT_X, TOSS_NAME_Y, name_font, TEXT_PRIMARY)

    if info.headline:
        headline = info.headline
        if len(headline) > 70:
            headline = headline[:67] + "..."

        panel_x = (UPDATE_IMAGE_WIDTH - TOSS_HEADLINE_PANEL_W) // 2
        panel_y = TOSS_HEADLINE_Y - 20
        overlay = Image.new("RGBA", (UPDATE_IMAGE_WIDTH, TOSS_IMAGE_HEIGHT), (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(overlay)
        panel_draw.rounded_rectangle(
            [
                (panel_x, panel_y),
                (panel_x + TOSS_HEADLINE_PANEL_W, panel_y + TOSS_HEADLINE_PANEL_H),
            ],
            radius=12,
            fill=(255, 255, 255, 210),
        )
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        _draw_centered_text(
            draw,
            headline,
            UPDATE_IMAGE_WIDTH // 2,
            TOSS_HEADLINE_Y,
            headline_font,
            TEXT_PRIMARY,
        )

    _draw_centered_text(
        draw,
        _update_footer_line(info),
        UPDATE_IMAGE_WIDTH // 2,
        TOSS_FOOTER_Y,
        footer_font,
        TEXT_MUTED,
    )
    return img


def _draw_captain_toss_card(info: MatchUpdateInfo, captains: CaptainTossInfo) -> Image.Image:
    img = _draw_toss_background(
        UPDATE_IMAGE_WIDTH, TOSS_IMAGE_HEIGHT, info.team1, info.team2
    )
    winner = _toss_winner_team(info)

    _paste_small_flag_centered(img, info.team1, TOSS_LEFT_X, TOSS_SMALL_FLAG_Y)
    _paste_small_flag_centered(img, info.team2, TOSS_RIGHT_X, TOSS_SMALL_FLAG_Y)

    if captains.team1_captain.image_path:
        _paste_headshot_centered(
            img,
            captains.team1_captain.image_path,
            TOSS_LEFT_X,
            TOSS_CAPTAIN_PHOTO_Y,
            highlight=info.team1 == winner,
            team=info.team1,
        )
    if captains.team2_captain.image_path:
        _paste_headshot_centered(
            img,
            captains.team2_captain.image_path,
            TOSS_RIGHT_X,
            TOSS_CAPTAIN_PHOTO_Y,
            highlight=info.team2 == winner,
            team=info.team2,
        )

    draw = ImageDraw.Draw(img)
    badge_font = _load_font(22, bold=True)
    team_font = _load_font(24, bold=True)
    captain_font = _load_font(22, bold=True)
    headline_font = _load_font(30, bold=True)
    footer_font = _load_font(24, bold=False)

    _draw_centered_text(draw, "TOSS", UPDATE_IMAGE_WIDTH // 2, TOSS_BADGE_Y, badge_font, TEXT_MUTED)
    _draw_centered_text(draw, info.team1, TOSS_LEFT_X, TOSS_TEAM_LABEL_Y, team_font, TEXT_PRIMARY)
    _draw_centered_text(draw, info.team2, TOSS_RIGHT_X, TOSS_TEAM_LABEL_Y, team_font, TEXT_PRIMARY)

    cap1 = captains.team1_captain.name
    cap2 = captains.team2_captain.name
    if len(cap1) > 18:
        cap1 = cap1[:16] + "..."
    if len(cap2) > 18:
        cap2 = cap2[:16] + "..."
    _draw_centered_text(draw, cap1, TOSS_LEFT_X, TOSS_CAPTAIN_NAME_Y, captain_font, TEXT_PRIMARY)
    _draw_centered_text(draw, cap2, TOSS_RIGHT_X, TOSS_CAPTAIN_NAME_Y, captain_font, TEXT_PRIMARY)

    if info.headline:
        headline = info.headline
        if len(headline) > 70:
            headline = headline[:67] + "..."

        panel_x = (UPDATE_IMAGE_WIDTH - TOSS_HEADLINE_PANEL_W) // 2
        panel_y = TOSS_HEADLINE_Y - 8
        overlay = Image.new("RGBA", (UPDATE_IMAGE_WIDTH, TOSS_IMAGE_HEIGHT), (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(overlay)
        panel_draw.rounded_rectangle(
            [
                (panel_x, panel_y),
                (panel_x + TOSS_HEADLINE_PANEL_W, panel_y + TOSS_HEADLINE_PANEL_H),
            ],
            radius=12,
            fill=(255, 255, 255, 210),
        )
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)
        _draw_centered_text(
            draw,
            headline,
            UPDATE_IMAGE_WIDTH // 2,
            TOSS_HEADLINE_Y + 8,
            headline_font,
            TEXT_PRIMARY,
        )

    _draw_centered_text(
        draw,
        _update_footer_line(info),
        UPDATE_IMAGE_WIDTH // 2,
        TOSS_FOOTER_Y,
        footer_font,
        TEXT_MUTED,
    )
    return img


def _draw_compact_update_card(info: MatchUpdateInfo) -> Image.Image:
    img = Image.new("RGB", (UPDATE_IMAGE_WIDTH, UPDATE_IMAGE_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(img)

    name_font = _load_font(28, bold=True)
    score_font = _load_font(64, bold=True)
    overs_font = _load_font(28, bold=False)
    headline_font = _load_font(36, bold=True)
    subline_font = _load_font(26, bold=False)
    footer_font = _load_font(28, bold=False)
    badge_font = _load_font(24, bold=True)

    left_flag = _load_team_flag(
        info.team1,
        UPDATE_FLAG_WIDTH,
        UPDATE_FLAG_HEIGHT,
        UPDATE_FLAG_RADIUS,
    )
    right_flag = _load_team_flag(
        info.team2,
        UPDATE_FLAG_WIDTH,
        UPDATE_FLAG_HEIGHT,
        UPDATE_FLAG_RADIUS,
    )
    _paste_flag_centered(img, left_flag, UPDATE_LEFT_X, UPDATE_FLAG_Y)
    _paste_flag_centered(img, right_flag, UPDATE_RIGHT_X, UPDATE_FLAG_Y)

    draw = ImageDraw.Draw(img)
    _draw_centered_text(draw, info.team1, UPDATE_LEFT_X, UPDATE_NAME_Y, name_font, TEXT_PRIMARY)
    _draw_centered_text(draw, info.team2, UPDATE_RIGHT_X, UPDATE_NAME_Y, name_font, TEXT_PRIMARY)

    score1 = info.score1 or "—"
    score2 = info.score2 or "—"
    _draw_centered_text(draw, score1, UPDATE_SCORE_LEFT_X, UPDATE_SCORE_Y, score_font, TEXT_PRIMARY)
    _draw_centered_text(draw, score2, UPDATE_SCORE_RIGHT_X, UPDATE_SCORE_Y, score_font, TEXT_PRIMARY)
    if info.overs1:
        _draw_centered_text(draw, info.overs1, UPDATE_SCORE_LEFT_X, UPDATE_OVERS_Y, overs_font, TEXT_MUTED)
    if info.overs2:
        _draw_centered_text(draw, info.overs2, UPDATE_SCORE_RIGHT_X, UPDATE_OVERS_Y, overs_font, TEXT_MUTED)

    if info.headline:
        _draw_centered_text(
            draw,
            info.headline,
            UPDATE_IMAGE_WIDTH // 2,
            UPDATE_HEADLINE_Y,
            headline_font,
            TEXT_PRIMARY,
        )

    _draw_centered_text(
        draw,
        _update_footer_line(info),
        UPDATE_IMAGE_WIDTH // 2,
        UPDATE_FOOTER_Y,
        footer_font,
        TEXT_MUTED,
    )

    return img


# ---------------------------------------------------------------------------
# Innings scorecard parsing
# ---------------------------------------------------------------------------

_SC_STATS_TAIL = re.compile(
    r"(?:\s+|^)(\d+)\s+(\d+)\s+\d+\s+(\d+)\s+(\d+)\s+[\d.]+\s*$"
)
_SC_DISMISSAL_KW = re.compile(
    r"\b(c\s+(?!\d)|lbw\s+b\s|lbw\b|b\s+(?=[A-Z])|run\s+out|not\s+out|st\s+|retired\s+)",
    re.IGNORECASE,
)
_SC_SKIP_LINE = re.compile(
    r"^\d|fall\s+of\s+wickets|^batting$|^bowling$|overs?\s*\(|\bRR:",
    re.IGNORECASE,
)
_SC_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\s.\'\u2019\-()†&]+$")
_SC_UI_SKIP = re.compile(
    r"^(?:match\s+flow|info|scorecard|squad|commentary|overs|fall\s+of\s+wickets|"
    r"england\s+innings|india\s+innings|australia\s+innings|pakistan\s+innings|"
    r"bangladesh\s+innings|sri\s+lanka\s+innings|new\s+zealand\s+innings|"
    r"south\s+africa\s+innings|west\s+indies\s+innings|afghanistan\s+innings|"
    r"ireland\s+innings|zimbabwe\s+innings|.*\s+innings)$",
    re.IGNORECASE,
)
_SC_UI_TOKENS = frozenset(
    {"innings", "flow", "info", "batting", "total", "extras", "scorecard", "squad", "commentary"}
)


def _sc_clean_player_name(raw: str) -> str:
    """Strip keeper dagger and captain markers from a player name."""
    clean = re.sub(r"\s*†\s*$", "", raw)
    clean = re.sub(r"\s*\([vc]+\)\s*$", "", clean)
    return clean.strip()


def _sc_team_name_matches(candidate: str, team: str) -> bool:
    if not candidate or not team:
        return False
    c = candidate.replace(" Women", "").strip().lower()
    t = team.replace(" Women", "").strip().lower()
    return c == t or t in c or c in t


def _sc_looks_like_player_name(line: str) -> bool:
    clean = _sc_clean_player_name(line.strip())
    if not clean or len(clean) < 3:
        return False
    if _SC_UI_SKIP.search(clean):
        return False
    lower = clean.lower()
    if lower in _SC_UI_TOKENS:
        return False
    if " innings" in lower or lower.endswith(" innings"):
        return False
    words = clean.split()
    if len(words) < 2:
        return False
    if not all(w[0].isalpha() and w[0].isupper() for w in words[:2] if w):
        return False
    return bool(_SC_NAME_RE.match(clean))


def scorecard_parse_valid(info: ScorecardInfo) -> bool:
    """Reject parsed scorecards with UI junk or too few batters."""
    if len(info.batters) < 3:
        return False
    for batter in info.batters:
        name_lower = batter.name.strip().lower()
        if name_lower in _SC_UI_TOKENS:
            return False
        if " innings" in name_lower or name_lower.endswith(" flow"):
            return False
        last_word = name_lower.split()[-1] if name_lower.split() else ""
        if last_word in _SC_UI_TOKENS:
            return False
    return True


def _sc_trim_post_batting(post_batting: str, batting_team: str, team1: str, team2: str) -> str:
    other = team2 if _sc_team_name_matches(batting_team, team1) else team1
    end = re.search(rf"\b{re.escape(other)}\s*Innings\b", post_batting, re.IGNORECASE)
    if end:
        return post_batting[: end.start()]
    return post_batting


def _sc_parse_multiline_rows(rows_text: str) -> list[ScorecardBatter]:
    """Parse ESPN modern layout: name, dismissal, and stats on separate lines."""
    lines = [line.strip() for line in rows_text.splitlines() if line.strip()]
    batters: list[ScorecardBatter] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^(BATTING|R\s+B\s+M)", line, re.IGNORECASE):
            i += 1
            continue
        if re.match(r"^Extras\b", line, re.IGNORECASE) or re.match(r"^Total\b", line, re.IGNORECASE):
            break

        one_line = _sc_parse_batter_line(line)
        if one_line and _sc_looks_like_player_name(one_line.name):
            batters.append(one_line)
            i += 1
            continue

        if not _sc_looks_like_player_name(line):
            i += 1
            continue

        name = _sc_clean_player_name(line)
        dismissal = ""
        j = i + 1
        if j < len(lines):
            nxt = lines[j]
            if _SC_STATS_TAIL.search(nxt):
                combined = f"{name} not out {nxt}" if "not out" not in nxt.lower() else f"{name} {nxt}"
                parsed = _sc_parse_batter_line(combined) or _sc_parse_name_stats(name, nxt)
                if parsed:
                    batters.append(parsed)
                i = j + 1
                continue
            if _SC_DISMISSAL_KW.search(nxt) or nxt.lower().startswith(
                ("c ", "b ", "lbw", "run out", "st ", "not out")
            ):
                dismissal = nxt
                j += 1
        if j < len(lines) and _SC_STATS_TAIL.search(lines[j]):
            stats_line = lines[j]
            if dismissal:
                parsed = _sc_parse_name_stats(name, f"{dismissal} {stats_line}")
            else:
                parsed = _sc_parse_batter_line(f"{name} {stats_line}")
            if parsed:
                batters.append(parsed)
            i = j + 1
            continue
        i += 1
    return batters


def _sc_batter_from_json_obj(raw: dict) -> ScorecardBatter | None:
    player = raw.get("player") or raw.get("batsman") or {}
    if isinstance(player, str):
        name = player
    else:
        name = player.get("name") or player.get("longName") or player.get("fieldingName") or ""
    if not name:
        name = raw.get("name") or raw.get("playerName") or ""
    if not name:
        return None

    batted_type = str(raw.get("battedType") or raw.get("batted") or "").lower()
    batted = batted_type not in ("", "dnb", "didnotbat", "did not bat")
    is_out = raw.get("isOut")
    if is_out is None:
        is_out = raw.get("out", True)
    dismissal_obj = raw.get("dismissalText") or raw.get("dismissal") or {}
    if isinstance(dismissal_obj, dict):
        dismissal = dismissal_obj.get("long") or dismissal_obj.get("short") or ""
    else:
        dismissal = str(dismissal_obj or "")
    if not batted:
        return None
    if not dismissal:
        dismissal = "not out" if not is_out else "out"
    not_out = not is_out or bool(re.search(r"\bnot\s+out\b", dismissal, re.IGNORECASE))

    def _int_field(*keys: str) -> int:
        for key in keys:
            val = raw.get(key)
            if val is not None and str(val).strip() != "":
                try:
                    return int(float(val))
                except (TypeError, ValueError):
                    continue
        return 0

    return ScorecardBatter(
        name=str(name).strip(),
        dismissal=str(dismissal).strip(),
        runs=_int_field("runs", "r"),
        balls=_int_field("balls", "b"),
        fours=_int_field("fours", "4s"),
        sixes=_int_field("sixes", "6s"),
        not_out=not_out,
    )


def _sc_walk_for_innings(obj, found: list) -> None:
    if isinstance(obj, dict):
        if "inningBatsmen" in obj and isinstance(obj["inningBatsmen"], list):
            found.append(obj)
        for key in ("innings", "scorecard", "content", "pageData", "data"):
            if key in obj:
                _sc_walk_for_innings(obj[key], found)
        for value in obj.values():
            if isinstance(value, (dict, list)):
                _sc_walk_for_innings(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _sc_walk_for_innings(item, found)


def _sc_innings_team_name(innings_obj: dict) -> str:
    for key in ("team", "battingTeam", "teamName", "inningTeam"):
        val = innings_obj.get(key)
        if isinstance(val, dict):
            val = val.get("name") or val.get("teamName")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _sc_extras_from_innings(innings_obj: dict) -> tuple[str, int]:
    extras = innings_obj.get("extras") or innings_obj.get("inningExtras") or {}
    if isinstance(extras, dict):
        total = extras.get("total") or extras.get("runs") or extras.get("extraRuns")
        parts = []
        for label, key in (("lb", "byes"), ("nb", "noBalls"), ("w", "wides"), ("p", "penalties")):
            val = extras.get(key) or extras.get(label)
            if val:
                parts.append(f"{label} {val}")
        detail = ", ".join(parts)
        runs = 0
        if total is not None:
            try:
                runs = int(total)
            except (TypeError, ValueError):
                runs = 0
        extras_str = f"({detail})  {runs}" if detail else str(runs)
        return extras_str, runs
    if isinstance(extras, (int, float)):
        return str(int(extras)), int(extras)
    return "", 0


def _sc_total_from_innings(innings_obj: dict, score: str) -> tuple[int, str]:
    total_obj = innings_obj.get("total") or innings_obj.get("runs") or {}
    total_runs = 0
    total_detail = ""
    if isinstance(total_obj, dict):
        total_runs = int(total_obj.get("runs") or total_obj.get("total") or 0)
        overs = total_obj.get("overs") or total_obj.get("over")
        rr = total_obj.get("runRate") or total_obj.get("rr")
        if overs:
            total_detail = f"({overs} Ov" + (f", RR: {rr})" if rr else ")")
    elif isinstance(total_obj, (int, float)):
        total_runs = int(total_obj)
    if not total_runs and score:
        num_m = re.match(r"(\d+)", score)
        if num_m:
            total_runs = int(num_m.group(1))
    wickets = innings_obj.get("wickets")
    if wickets is not None and score and "/" not in score and total_runs:
        score = f"{total_runs}/{wickets}"
    return total_runs, total_detail


def parse_innings_scorecard_from_html(
    html: str,
    batting_team: str,
    team1: str,
    team2: str,
    score: str,
    overs: str,
    match_label: str,
    series: str,
    format_tag: str,
) -> ScorecardInfo | None:
    """Parse innings scorecard from ESPN __NEXT_DATA__ JSON embedded in HTML."""
    match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    innings_blocks: list[dict] = []
    _sc_walk_for_innings(data, innings_blocks)

    # Also try direct path used by ESPN scorecard pages
    page_props = data.get("props", {}).get("pageProps", {})
    for container_key in ("data", "dehydratedState"):
        container = page_props.get(container_key, {})
        if not isinstance(container, dict):
            continue
        page_data = container.get("pageData") or container.get("data") or container
        if isinstance(page_data, dict):
            content = page_data.get("content") or page_data
            scorecard = content.get("scorecard") if isinstance(content, dict) else None
            if isinstance(scorecard, dict):
                innings_raw = scorecard.get("innings")
                if isinstance(innings_raw, dict):
                    for inn in innings_raw.values():
                        if isinstance(inn, dict) and "inningBatsmen" in inn:
                            innings_blocks.append(inn)
                elif isinstance(innings_raw, list):
                    innings_blocks.extend(i for i in innings_raw if isinstance(i, dict))

    seen_ids: set[int] = set()
    unique_blocks: list[dict] = []
    for block in innings_blocks:
        block_id = id(block)
        if block_id not in seen_ids:
            seen_ids.add(block_id)
            unique_blocks.append(block)

    target_innings: dict | None = None
    for inn in unique_blocks:
        team_name = _sc_innings_team_name(inn)
        if _sc_team_name_matches(team_name, batting_team):
            target_innings = inn
            break

    # Fallback: first innings block when team name missing from JSON
    if target_innings is None and unique_blocks:
        if batting_team == team1 or len(unique_blocks) == 1:
            target_innings = unique_blocks[0]
        elif len(unique_blocks) >= 2:
            target_innings = unique_blocks[0]

    if not target_innings:
        return None

    batsmen_raw = target_innings.get("inningBatsmen") or target_innings.get("batsmen") or []
    batters: list[ScorecardBatter] = []
    squad_names: list[str] = []
    for raw in batsmen_raw:
        if not isinstance(raw, dict):
            continue
        player = raw.get("player") or raw.get("batsman") or {}
        if isinstance(player, dict):
            pname = player.get("name") or player.get("longName") or ""
            if pname:
                squad_names.append(str(pname))
        batter = _sc_batter_from_json_obj(raw)
        if batter:
            batters.append(batter)

    if len(batters) < 3:
        return None

    extras_str, extras_runs = _sc_extras_from_innings(target_innings)
    total_runs, total_detail = _sc_total_from_innings(target_innings, score)
    if not total_detail and overs:
        total_detail = f"({overs} ov)"

    return ScorecardInfo(
        team1=team1,
        team2=team2,
        batting_team=batting_team,
        score=score,
        overs=overs,
        match_label=match_label,
        series=series,
        format_tag=format_tag,
        batters=batters,
        squad_names=squad_names,
        extras=extras_str,
        extras_runs=extras_runs,
        total_runs=total_runs,
        total_detail=total_detail,
    )


def _sc_build_scorecard_info(
    batters: list[ScorecardBatter],
    *,
    team1: str,
    team2: str,
    batting_team: str,
    score: str,
    overs: str,
    match_label: str,
    series: str,
    format_tag: str,
    squad_names: list[str],
    post_batting: str,
) -> ScorecardInfo:
    extras_m = re.search(r"^Extras\b", post_batting, re.IGNORECASE | re.MULTILINE)
    total_m = re.search(r"^Total\b", post_batting, re.IGNORECASE | re.MULTILINE)
    extras_str = ""
    extras_runs = 0
    if extras_m:
        ex_line = post_batting[extras_m.start() : extras_m.start() + 200]
        ex_detail = re.search(r"\(([^)]+)\)", ex_line)
        ex_num = re.search(r"\)\s*(\d+)", ex_line)
        if not ex_num:
            ex_num = re.search(r"Extras\s+\S*\s+(\d+)", ex_line, re.IGNORECASE)
        if ex_num:
            extras_runs = int(ex_num.group(1))
        extras_str = (
            f"({ex_detail.group(1)})  {extras_runs}" if ex_detail else str(extras_runs)
        )

    total_runs = 0
    total_detail = ""
    if total_m:
        t_line = post_batting[total_m.start() : total_m.start() + 150]
        t_num = re.search(r"Total\s+(\d+)", t_line, re.IGNORECASE)
        if t_num:
            total_runs = int(t_num.group(1))
        t_ov = re.search(r"\(([^)]+(?:Ov|ov)[^)]*)\)", t_line)
        if t_ov:
            total_detail = f"({t_ov.group(1)})"
    if not total_runs and score:
        num_m = re.match(r"(\d+)", score)
        if num_m:
            total_runs = int(num_m.group(1))
    if not total_detail and overs:
        total_detail = f"({overs} ov)"

    return ScorecardInfo(
        team1=team1,
        team2=team2,
        batting_team=batting_team,
        score=score,
        overs=overs,
        match_label=match_label,
        series=series,
        format_tag=format_tag,
        batters=batters,
        squad_names=squad_names,
        extras=extras_str,
        extras_runs=extras_runs,
        total_runs=total_runs,
        total_detail=total_detail,
    )


def _sc_parse_batter_line(line: str) -> ScorecardBatter | None:
    """Parse a complete batting row: name + dismissal + R B M 4s 6s SR on one line."""
    stats = _SC_STATS_TAIL.search(line)
    if not stats:
        return None
    runs = int(stats.group(1))
    balls = int(stats.group(2))
    fours = int(stats.group(3))
    sixes = int(stats.group(4))
    name_dis = line[: stats.start()].strip()
    dis_kw = _SC_DISMISSAL_KW.search(name_dis)
    if dis_kw:
        name = name_dis[: dis_kw.start()].strip()
        dismissal = name_dis[dis_kw.start() :].strip()
    else:
        name = name_dis
        dismissal = ""
    not_out = bool(re.search(r"\bnot\s+out\b", dismissal, re.IGNORECASE))
    if not name:
        return None
    return ScorecardBatter(
        name=name,
        dismissal=dismissal,
        runs=runs,
        balls=balls,
        fours=fours,
        sixes=sixes,
        not_out=not_out,
    )


def _sc_parse_name_stats(name: str, dis_stats_line: str) -> ScorecardBatter | None:
    """Parse when player name is separate from the dismissal+stats line."""
    stats = _SC_STATS_TAIL.search(dis_stats_line)
    if not stats:
        return None
    runs = int(stats.group(1))
    balls = int(stats.group(2))
    fours = int(stats.group(3))
    sixes = int(stats.group(4))
    dismissal = dis_stats_line[: stats.start()].strip()
    not_out = bool(re.search(r"\bnot\s+out\b", dismissal, re.IGNORECASE))
    return ScorecardBatter(
        name=name,
        dismissal=dismissal,
        runs=runs,
        balls=balls,
        fours=fours,
        sixes=sixes,
        not_out=not_out,
    )


def parse_innings_scorecard_text(
    body_text: str,
    batting_team: str,
    team1: str,
    team2: str,
    score: str,
    overs: str,
    match_label: str,
    series: str,
    format_tag: str,
) -> ScorecardInfo | None:
    """Parse an ESPN Cricinfo full-scorecard body text into a ScorecardInfo.

    Supports two Playwright inner_text layouts:
      - Combined: "Name  dismissal  R B M 4s 6s SR" on a single line per batter.
      - Split: player names listed before the BATTING header, stats rows after it.
    """
    # Locate the batting team's innings section
    team_hdr = re.compile(
        rf"\b{re.escape(batting_team)}\s*(?:Innings|\()",
        re.IGNORECASE,
    )
    m = team_hdr.search(body_text)
    if m:
        section = body_text[m.start() :]
    else:
        idx = body_text.lower().find(batting_team.lower())
        if idx < 0:
            logger.warning("Scorecard: could not find innings section for %s", batting_team)
            return None
        section = body_text[idx:]

    # Locate BATTING header
    bat_hdr = re.search(r"\bBATTING\b", section, re.IGNORECASE)
    if not bat_hdr:
        logger.warning("Scorecard: no BATTING header found for %s", batting_team)
        return None

    pre_batting = section[: bat_hdr.start()]
    post_batting = _sc_trim_post_batting(section[bat_hdr.end() :], batting_team, team1, team2)

    player_names: list[str] = []
    for raw_line in pre_batting.splitlines():
        raw_line = raw_line.strip()
        if not raw_line or len(raw_line) < 3 or len(raw_line) > 45:
            continue
        if _SC_SKIP_LINE.search(raw_line):
            continue
        if raw_line.lower().startswith(batting_team.lower()[:6]):
            continue
        if _sc_looks_like_player_name(raw_line):
            clean = _sc_clean_player_name(raw_line)
            if clean and clean not in player_names:
                player_names.append(clean)

    extras_m = re.search(r"^Extras\b", post_batting, re.IGNORECASE | re.MULTILINE)
    rows_end = extras_m.start() if extras_m else min(len(post_batting), 2500)
    rows_text = post_batting[:rows_end]

    batters: list[ScorecardBatter] = []
    for line in rows_text.splitlines():
        line = line.strip()
        if not line or re.match(r"^(BATTING|R\s+B\s+M)", line, re.IGNORECASE):
            continue
        batter = _sc_parse_batter_line(line)
        if batter and _sc_looks_like_player_name(batter.name):
            batters.append(batter)

    if len(batters) < 3:
        multiline = _sc_parse_multiline_rows(rows_text)
        if len(multiline) > len(batters):
            batters = multiline

    valid_names = [n for n in player_names if _sc_looks_like_player_name(n)]
    if len(batters) < 3 and len(valid_names) >= 5:
        dis_lines: list[str] = []
        for line in rows_text.splitlines():
            line = line.strip()
            if not line or re.match(r"^(BATTING|R\s+B\s+M)", line, re.IGNORECASE):
                continue
            if _SC_STATS_TAIL.search(line):
                dis_lines.append(line)
        if dis_lines:
            zipped: list[ScorecardBatter] = []
            for name, dl in zip(valid_names[:11], dis_lines[:11]):
                b = _sc_parse_name_stats(name, dl)
                if b:
                    zipped.append(b)
            if len(zipped) > len(batters):
                batters = zipped

    if not batters:
        return None

    return _sc_build_scorecard_info(
        batters,
        team1=team1,
        team2=team2,
        batting_team=batting_team,
        score=score,
        overs=overs,
        match_label=match_label,
        series=series,
        format_tag=format_tag,
        squad_names=player_names,
        post_batting=post_batting,
    )


# ---------------------------------------------------------------------------
# Scorecard image drawing
# ---------------------------------------------------------------------------


def _draw_vertical_gradient(
    width: int,
    height: int,
    top: str,
    mid: str,
    bottom: str,
) -> Image.Image:
    img = Image.new("RGB", (width, height))
    c_top = _hex_rgb(top)
    c_mid = _hex_rgb(mid)
    c_bottom = _hex_rgb(bottom)
    split = int(height * 0.55)
    pixels = img.load()
    for y in range(height):
        if y <= split:
            t = y / max(split, 1)
            color = _interpolate_color(c_top, c_mid, t)
        else:
            t = (y - split) / max(height - split, 1)
            color = _interpolate_color(c_mid, c_bottom, t)
        for x in range(width):
            pixels[x, y] = color  # type: ignore[index]
    return img


def _sc_player_token(name: str) -> str:
    parts = [p for p in re.sub(r"[^A-Za-z\s]", "", name).split() if p]
    if not parts:
        return name.upper()[:10]
    if len(parts) == 1:
        return parts[0].upper()
    return parts[0].upper() if len(parts[0]) >= 4 else parts[-1].upper()


def _abbrev_dismissal(dismissal: str) -> str:
    if not dismissal or re.search(r"\bnot\s+out\b", dismissal, re.IGNORECASE):
        return "NOT OUT"
    d = dismissal.strip()
    if re.match(r"run\s+out", d, re.IGNORECASE):
        return "run out"
    m = re.match(r"^c\s+(.+?)\s+b\s+(.+)$", d, re.IGNORECASE)
    if m:
        return f"c {_sc_player_token(m.group(1))} b {_sc_player_token(m.group(2))}"
    m = re.match(r"^st\s+(.+?)\s+b\s+(.+)$", d, re.IGNORECASE)
    if m:
        return f"st {_sc_player_token(m.group(1))} b {_sc_player_token(m.group(2))}"
    m = re.match(r"^lbw\s+b\s+(.+)$", d, re.IGNORECASE)
    if m:
        return f"lbw b {_sc_player_token(m.group(1))}"
    m = re.match(r"^b\s+(.+)$", d, re.IGNORECASE)
    if m:
        return f"b {_sc_player_token(m.group(1))}"
    return d.upper()[:36]


def _format_dnb_players(squad_names: list[str], batters: list[ScorecardBatter]) -> str:
    if not squad_names:
        return ""
    batted = {_sc_clean_player_name(b.name).upper() for b in batters}
    dnb: list[str] = []
    for name in squad_names:
        clean = _sc_clean_player_name(name)
        if clean.upper() not in batted:
            dnb.append(clean.upper())
    if not dnb:
        return ""
    return ", ".join(dnb) + "  DNB"


def _sc_batter_display_name(name: str) -> str:
    clean = _sc_clean_player_name(name)
    parts = clean.split()
    if not parts:
        return name.upper()[:14]
    if len(parts) == 1:
        return parts[0].upper()
    return parts[-1].upper()


def _draw_scorecard_card(info: ScorecardInfo) -> Image.Image:
    """Draw a premium dark team-branded batting innings scorecard."""
    batters = info.batters[:11]
    num_rows = len(batters)
    dnb_text = _format_dnb_players(info.squad_names, batters)
    dnb_h = SC_PREMIUM_DNB_H if dnb_text else 0
    footer_y = SC_PREMIUM_ROW_START + num_rows * SC_PREMIUM_ROW_H + dnb_h
    img_h = footer_y + SC_PREMIUM_FOOTER_H + SC_PREMIUM_BOTTOM_PAD

    primary, secondary = _team_kit_colors(info.batting_team)
    img = _draw_vertical_gradient(
        UPDATE_IMAGE_WIDTH, img_h, primary, secondary, SC_PREMIUM_DARK
    )
    draw = ImageDraw.Draw(img)

    header_font = _load_font(30, bold=True)
    series_font = _load_font(20)
    name_font_bold = _load_font(22, bold=True)
    name_font_reg = _load_font(22)
    dis_font = _load_font(19)
    stat_font = _load_font(24, bold=True)
    stat_font_reg = _load_font(20)
    dnb_font = _load_font(18)
    footer_label_font = _load_font(20)
    footer_total_font = _load_font(44, bold=True)

    left_flag = _load_team_flag(
        info.team1, SC_PREMIUM_FLAG_W, SC_PREMIUM_FLAG_H, 6
    )
    right_flag = _load_team_flag(
        info.team2, SC_PREMIUM_FLAG_W, SC_PREMIUM_FLAG_H, 6
    )
    _paste_flag_centered(img, left_flag, SC_PREMIUM_LEFT_CX, SC_PREMIUM_FLAG_Y)
    _paste_flag_centered(img, right_flag, SC_PREMIUM_RIGHT_CX, SC_PREMIUM_FLAG_Y)

    draw = ImageDraw.Draw(img)
    header_line = (
        f"{_team_abbrev(info.team1)}  v  {_team_abbrev(info.team2)}"
    )
    _draw_centered_text(
        draw, header_line, UPDATE_IMAGE_WIDTH // 2, SC_PREMIUM_TITLE_Y, header_font, SC_PREMIUM_TEXT
    )
    series_line = info.series.upper() if info.series else info.match_label.upper()
    if info.format_tag and info.format_tag not in series_line:
        series_line = f"{series_line}  {info.format_tag}"
    _draw_centered_text(
        draw,
        series_line[:58],
        UPDATE_IMAGE_WIDTH // 2,
        SC_PREMIUM_SERIES_Y,
        series_font,
        SC_PREMIUM_MUTED,
    )

    draw.line(
        [(24, SC_PREMIUM_ROW_START - 8), (UPDATE_IMAGE_WIDTH - 24, SC_PREMIUM_ROW_START - 8)],
        fill=SC_PREMIUM_ROW_DIVIDER,
        width=1,
    )

    top_runs = max((b.runs for b in batters), default=0)
    overlay = Image.new("RGBA", (UPDATE_IMAGE_WIDTH, img_h), (0, 0, 0, 0))

    for i, batter in enumerate(batters):
        row_y = SC_PREMIUM_ROW_START + i * SC_PREMIUM_ROW_H
        if batter.runs == top_runs and top_runs > 0:
            row_draw = ImageDraw.Draw(overlay)
            row_draw.rectangle(
                [(0, row_y), (UPDATE_IMAGE_WIDTH, row_y + SC_PREMIUM_ROW_H)],
                fill=SC_PREMIUM_TOP_SCORER_BG,
            )
        text_y = row_y + (SC_PREMIUM_ROW_H - 22) // 2
        stat_y = row_y + (SC_PREMIUM_ROW_H - 24) // 2
        name_display = _sc_batter_display_name(batter.name)
        dis_display = _abbrev_dismissal(batter.dismissal)

        if batter.not_out:
            draw.text(
                (SC_PREMIUM_NAME_X, text_y),
                name_display,
                font=name_font_bold,
                fill=SC_PREMIUM_NOT_OUT,
            )
            draw.text(
                (SC_PREMIUM_DIS_X, text_y),
                dis_display,
                font=dis_font,
                fill=SC_PREMIUM_NOT_OUT,
            )
        else:
            draw.text(
                (SC_PREMIUM_NAME_X, text_y),
                name_display,
                font=name_font_reg,
                fill=SC_PREMIUM_TEXT,
            )
            draw.text(
                (SC_PREMIUM_DIS_X, text_y),
                dis_display[:34],
                font=dis_font,
                fill=SC_PREMIUM_MUTED,
            )

        _draw_right_text(
            draw, str(batter.runs), SC_PREMIUM_R_RIGHT, stat_y, stat_font, SC_PREMIUM_TEXT
        )
        _draw_right_text(
            draw,
            str(batter.balls),
            SC_PREMIUM_B_RIGHT,
            stat_y,
            stat_font_reg,
            SC_PREMIUM_MUTED,
        )
        draw.line(
            [
                (SC_PREMIUM_NAME_X, row_y + SC_PREMIUM_ROW_H - 1),
                (UPDATE_IMAGE_WIDTH - SC_PREMIUM_NAME_X, row_y + SC_PREMIUM_ROW_H - 1),
            ],
            fill=SC_PREMIUM_ROW_DIVIDER,
            width=1,
        )

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    if dnb_text:
        dnb_y = SC_PREMIUM_ROW_START + num_rows * SC_PREMIUM_ROW_H
        draw.text(
            (SC_PREMIUM_NAME_X, dnb_y + (SC_PREMIUM_DNB_H - 18) // 2),
            dnb_text[:72],
            font=dnb_font,
            fill=SC_PREMIUM_MUTED,
        )
        draw.line(
            [(24, dnb_y), (UPDATE_IMAGE_WIDTH - 24, dnb_y)],
            fill=SC_PREMIUM_ROW_DIVIDER,
            width=1,
        )

    draw.line(
        [(24, footer_y), (UPDATE_IMAGE_WIDTH - 24, footer_y)],
        fill=SC_PREMIUM_ROW_DIVIDER,
        width=2,
    )

    overs_label = info.overs or ""
    if overs_label and not overs_label.lower().endswith("ov"):
        overs_label = f"{overs_label} ov"
    extras_label = f"EXTRAS {info.extras_runs}" if info.extras_runs else "EXTRAS"
    footer_mid_y = footer_y + (SC_PREMIUM_FOOTER_H - 44) // 2
    draw.text(
        (SC_PREMIUM_NAME_X, footer_mid_y + 14),
        f"OVERS {overs_label.upper()}" if overs_label else "",
        font=footer_label_font,
        fill=SC_PREMIUM_MUTED,
    )
    draw.text(
        (SC_PREMIUM_NAME_X + 280, footer_mid_y + 14),
        extras_label,
        font=footer_label_font,
        fill=SC_PREMIUM_MUTED,
    )
    total_display = info.score if info.score else str(info.total_runs)
    _draw_right_text(
        draw,
        total_display,
        UPDATE_IMAGE_WIDTH - 28,
        footer_mid_y,
        footer_total_font,
        SC_PREMIUM_TEXT,
    )

    return img


def _draw_match_update_card(info: MatchUpdateInfo) -> Image.Image:
    if info.phase == "toss":
        return _draw_toss_card(info)
    if info.phase == "live":
        return _draw_premium_live_card(info)
    return _draw_compact_update_card(info)


def generate_match_image(info: MatchUpdateInfo, captains: CaptainTossInfo | None = None) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", info.match_key)[:80]
    output_path = GENERATED_IMAGES_DIR / f"{info.phase}_{safe_key}.png"
    if (
        info.phase == "toss"
        and captains
        and captains.team1_captain.image_path
        and captains.team2_captain.image_path
    ):
        _draw_captain_toss_card(info, captains).save(output_path, "PNG")
    else:
        _draw_match_update_card(info).save(output_path, "PNG")
    return output_path


def generate_captain_toss_image(info: MatchUpdateInfo, captains: CaptainTossInfo) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", info.match_key)[:80]
    output_path = GENERATED_IMAGES_DIR / f"toss_captain_{safe_key}.png"
    _draw_captain_toss_card(info, captains).save(output_path, "PNG")
    return output_path


def generate_preview_image(info: PreviewMatchInfo) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", info.match_key)[:80]
    output_path = GENERATED_IMAGES_DIR / f"preview_{safe_key}.png"
    _draw_flag_card(info).save(output_path, "PNG")
    return output_path


def generate_scorecard_image(info: ScorecardInfo) -> Path:
    """Generate a batting scorecard image and return its path."""
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", f"{info.team1}_{info.team2}_{info.batting_team}")[:80]
    output_path = GENERATED_IMAGES_DIR / f"scorecard_{safe_key}.png"
    _draw_scorecard_card(info).save(output_path, "PNG")
    return output_path


def build_scorecard_caption(info: ScorecardInfo) -> str:
    """Build the Facebook post caption for a batting scorecard post."""
    abbrev1 = _team_abbrev(info.team1)
    abbrev2 = _team_abbrev(info.team2)
    ov_part = f" ({info.overs} ov)" if info.overs else ""
    headline = (
        f"Innings scorecard \u2014 {info.batting_team} {info.score}{ov_part}. "
        f"{info.match_label}, {info.series}."
    )
    series_tag = _hashtag_token(info.series)
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
