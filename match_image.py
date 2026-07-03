"""Generate preview match announcement images and captions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*(?:AM|PM)", re.IGNORECASE)
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
CRICKET_IRELAND_ORG = re.compile(r"cricket\s+ireland", re.IGNORECASE)
TOSS_PATTERN = re.compile(
    r"((?:[\w\s]+)\s+won the toss|(?:[\w\s-]+)\s+(?:chose|opted|elected) to (?:bat|field|bowl)[^.]*)",
    re.IGNORECASE,
)
OVERS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*ov", re.IGNORECASE)
SIMPLE_OVERS_PATTERN = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*(?:ov|overs?)\s*\)", re.IGNORECASE)
TARGET_PATTERN = re.compile(r"\bT:\s*(\d+)", re.IGNORECASE)
NEED_RUNS_PATTERN = re.compile(
    r"need\s+(\d+)\s+runs?(?:\s+(?:from|off|in)\s+(\d+)\s+(?:ball|balls|deliveries?))?",
    re.IGNORECASE,
)
REQ_RUNS_PATTERN = re.compile(
    r"(?:req(?:uired)?|need)[:\s]+(\d+)(?:\s+(?:runs?\s+)?(?:from|off|in)\s+(\d+)\s+(?:ball|balls|deliveries?))?",
    re.IGNORECASE,
)
BATTER_PATTERN = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(\d+)\s*\(\s*(\d+)\s*\)",
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


def _team_slug(team: str) -> str:
    base = team.replace(" Women", "")
    return TEAM_SLUGS.get(base, re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-"))


def _teams_from_block(block: str) -> list[str]:
    return [_normalize_team_name(line) for line in block.splitlines() if _line_is_tracked_team(line)]


def _is_score_line(line: str) -> bool:
    lower = line.lower()
    if "won by" in lower or "won the toss" in lower:
        return False
    if SCORE_PATTERN.search(line):
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
    scores = SCORE_PATTERN.findall(line)

    if "&" in line:
        parts = re.findall(r"\d+(?:/\d+)?", line)
        if parts:
            return " & ".join(parts[:3]), ""

    score = scores[-1] if scores else ""
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
        score = scores[-1]

    return score, overs_display


def _runs_from_score(score: str) -> int | None:
    match = re.match(r"(\d+)/\d+", score)
    return int(match.group(1)) if match else None


def _parse_chase_meta(block: str) -> tuple[int | None, int | None, int | None]:
    target: int | None = None
    runs_needed: int | None = None
    balls_remaining: int | None = None

    for line in block.splitlines():
        target_match = TARGET_PATTERN.search(line)
        if target_match:
            target = int(target_match.group(1))

        for pattern in (NEED_RUNS_PATTERN, REQ_RUNS_PATTERN):
            need_match = pattern.search(line)
            if need_match:
                runs_needed = int(need_match.group(1))
                if need_match.lastindex and need_match.lastindex >= 2 and need_match.group(2):
                    balls_remaining = int(need_match.group(2))
                break

    return target, runs_needed, balls_remaining


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


def _populate_live_context(info: MatchUpdateInfo, block: str) -> None:
    target, runs_needed, balls_remaining = _parse_chase_meta(block)

    team2_yet_to_bat = _team_yet_to_bat(block, info.team2)
    if info.score1 and team2_yet_to_bat and not info.score2:
        info.innings_status = "innings_break"
        runs = _runs_from_score(info.score1)
        if runs is not None:
            info.target = runs + 1
        info.headline = (
            f"{info.team1} {info.score1}"
            + (f" {info.overs1}" if info.overs1 else "")
            + f". {info.team2} need {info.target} to win"
        )
        return

    chase_line = any("T:" in line.upper() for line in block.splitlines())
    if chase_line or target is not None or runs_needed is not None:
        info.innings_status = "chase"
        info.target = target
        info.runs_needed = runs_needed
        info.balls_remaining = balls_remaining

        if info.score2:
            info.batting_team = info.team2
            active_score, active_overs = info.score2, info.overs2
        elif info.score1:
            info.batting_team = info.team1
            active_score, active_overs = info.score1, info.overs1
        else:
            active_score, active_overs = "", ""

        if info.runs_needed is None and target is not None and active_score:
            runs = _runs_from_score(active_score)
            if runs is not None:
                info.runs_needed = max(target - runs, 0)

        parts = [f"LIVE: {_team_abbrev(info.batting_team)} {active_score}"]
        if active_overs:
            ov = active_overs.strip("()")
            parts[0] += f" after {ov} overs"
        if info.runs_needed is not None:
            chase_part = f"need {info.runs_needed} runs"
            if info.balls_remaining is not None:
                chase_part += f" from {info.balls_remaining} balls"
            parts.append(chase_part)
        info.headline = " — ".join(parts)
        return

    if info.score1 and not info.score2:
        info.batting_team = info.team1
        ov = info.overs1.strip("()") if info.overs1 else ""
        info.headline = f"LIVE: {_team_abbrev(info.team1)} {info.score1}"
        if ov:
            info.headline += f" after {ov} overs"
    elif info.score2 and not info.score1:
        info.batting_team = info.team2
        ov = info.overs2.strip("()") if info.overs2 else ""
        info.headline = f"LIVE: {_team_abbrev(info.team2)} {info.score2}"
        if ov:
            info.headline += f" after {ov} overs"
    else:
        info.headline = "LIVE"
        batters = _extract_batters(block)
        if batters:
            info.subline = ", ".join(batters[:2])


def make_live_signature(info: MatchUpdateInfo) -> str:
    return "|".join(
        [
            info.innings_status,
            info.score1,
            info.overs1,
            info.score2,
            info.overs2,
            str(info.target or ""),
            str(info.runs_needed or ""),
            str(info.balls_remaining or ""),
            info.batting_team,
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
                if score:
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


def _extract_batters(block: str) -> list[str]:
    batters: list[str] = []
    for line in block.splitlines():
        for match in BATTER_PATTERN.finditer(line):
            name_parts = match.group(1).split()
            short = name_parts[-1]
            batters.append(f"{short} {match.group(2)}({match.group(3)})")
        if len(batters) >= 2:
            break
    return batters


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

    series = _extract_series(lines, fixture_line)
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
            info.headline = toss_match.group(1).strip().rstrip(".")
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
    if info.innings_status == "innings_break" and info.headline:
        headline = f"{info.headline}."
    elif info.innings_status == "chase" and info.headline:
        headline = f"{info.headline}."
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
    if match_date == today + timedelta(days=1):
        return "tomorrow"
    if match_date == today:
        return "today"
    return "today"


def _extract_time(block: str) -> str:
    for line in block.splitlines():
        time_match = TIME_PATTERN.search(line)
        if time_match:
            return time_match.group(0).upper()
    return "TBC"


def _extract_series(lines: list[str], fixture_line: str) -> str:
    for line in lines:
        lower = line.lower()
        if any(lower.startswith(p) for p in ("live", "result", "today,", "tomorrow,", "match yet", "match starts")):
            continue
        if _line_is_tracked_team(line) or MATCH_LABEL_PATTERN.search(line):
            continue
        if TIME_PATTERN.search(line) or SCORE_PATTERN.search(line):
            continue
        if (" tour " in lower or "tour of" in lower or re.search(r"\d{4}", line)) and len(line) > 5:
            return line

    if fixture_line:
        parts = [part.strip() for part in fixture_line.split(",") if part.strip()]
        for part in reversed(parts):
            if DATE_RANGE_PATTERN.search(part) or DATE_PATTERN.search(part):
                continue
            if MATCH_LABEL_PATTERN.search(part) and len(parts) == 1:
                continue
            if len(part) > 5 and not _line_is_tracked_team(part):
                return part

    return "International Cricket"


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

    time_str = _extract_time(block)

    match_date = parse_match_date_from_block(block)
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

    series = _extract_series(lines, fixture_line)
    day_label = _detect_day_label(block, match_date)

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


def _draw_match_update_card(info: MatchUpdateInfo) -> Image.Image:
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

    if info.phase == "toss":
        _draw_centered_text(draw, "TOSS", UPDATE_IMAGE_WIDTH // 2, UPDATE_SCORE_Y, badge_font, TEXT_MUTED)
        if info.headline:
            headline = info.headline
            if len(headline) > 70:
                headline = headline[:67] + "..."
            _draw_centered_text(
                draw,
                headline,
                UPDATE_IMAGE_WIDTH // 2,
                UPDATE_HEADLINE_Y,
                headline_font,
                TEXT_PRIMARY,
            )
    else:
        score1 = info.score1 or "—"
        score2 = info.score2 or "—"
        _draw_centered_text(draw, score1, UPDATE_SCORE_LEFT_X, UPDATE_SCORE_Y, score_font, TEXT_PRIMARY)
        _draw_centered_text(draw, score2, UPDATE_SCORE_RIGHT_X, UPDATE_SCORE_Y, score_font, TEXT_PRIMARY)
        if info.overs1:
            _draw_centered_text(draw, info.overs1, UPDATE_SCORE_LEFT_X, UPDATE_OVERS_Y, overs_font, TEXT_MUTED)
        if info.overs2:
            _draw_centered_text(draw, info.overs2, UPDATE_SCORE_RIGHT_X, UPDATE_OVERS_Y, overs_font, TEXT_MUTED)

        if info.phase == "live":
            if info.innings_status == "innings_break":
                headline = info.headline
                if len(headline) > 90:
                    headline = headline[:87] + "..."
                _draw_centered_text(
                    draw,
                    headline,
                    UPDATE_IMAGE_WIDTH // 2,
                    UPDATE_HEADLINE_Y,
                    headline_font,
                    TEXT_PRIMARY,
                )
            else:
                _draw_centered_text(
                    draw, "LIVE", UPDATE_IMAGE_WIDTH // 2, UPDATE_HEADLINE_Y - 30, badge_font, "#D93025"
                )
                display = info.headline if info.headline and info.headline != "LIVE" else info.subline
                if display:
                    if len(display) > 90:
                        display = display[:87] + "..."
                    _draw_centered_text(
                        draw,
                        display,
                        UPDATE_IMAGE_WIDTH // 2,
                        UPDATE_HEADLINE_Y,
                        subline_font if info.subline and info.headline == "LIVE" else headline_font,
                        TEXT_SECONDARY if info.subline and info.headline == "LIVE" else TEXT_PRIMARY,
                    )
        elif info.headline:
            _draw_centered_text(
                draw,
                info.headline,
                UPDATE_IMAGE_WIDTH // 2,
                UPDATE_HEADLINE_Y,
                headline_font,
                TEXT_PRIMARY,
            )

    footer = info.match_label
    if info.series and info.series not in footer and info.match_label not in info.series:
        footer = f"{footer} · {info.series[:50]}"
    elif info.series and info.match_label in info.series:
        footer = info.series[:80]
    _draw_centered_text(
        draw,
        footer[:80],
        UPDATE_IMAGE_WIDTH // 2,
        UPDATE_FOOTER_Y,
        footer_font,
        TEXT_MUTED,
    )

    return img


def generate_match_image(info: MatchUpdateInfo) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", info.match_key)[:80]
    output_path = GENERATED_IMAGES_DIR / f"{info.phase}_{safe_key}.png"
    _draw_match_update_card(info).save(output_path, "PNG")
    return output_path


def generate_preview_image(info: PreviewMatchInfo) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", info.match_key)[:80]
    output_path = GENERATED_IMAGES_DIR / f"preview_{safe_key}.png"
    _draw_flag_card(info).save(output_path, "PNG")
    return output_path
