"""Playing XI image generation, parsing, and captions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from match_image import (
    FORMAT_PATTERN,
    GENERATED_IMAGES_DIR,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    MATCH_LABEL_PATTERN,
    TRACKED_TEAMS,
    _detect_format,
    _hex_rgb,
    _line_is_tracked_team,
    _load_font,
    _load_team_flag,
    _normalize_team_name,
    _paste_flag_centered,
    _team_abbrev,
    _team_slug,
)

_BASE_DIR = Path(__file__).resolve().parent
STADIUM_ASSET = _BASE_DIR / "assets" / "stadium-silhouette.png"
DARK_NAVY = "#0a1628"

XI_FLAG_WIDTH = 100
XI_FLAG_HEIGHT = 70
XI_FLAG_RADIUS = 8
XI_FLAG_Y = 80
XI_TITLE_Y = 170
XI_STAGE_Y = 240
XI_OPPONENT_Y = 285
XI_PANEL_TOP = 340
XI_PANEL_WIDTH = 756
XI_ROW_HEIGHT = 58
XI_NUMBER_X = 220
XI_NAME_X = 280

PLAYING_XI_HEADER = re.compile(r"playing\s+xi|playing\s+11|confirmed\s+xi|squad", re.IGNORECASE)
PLAYER_LINE = re.compile(r"^\s*(\d+)\.\s+(.+)$")
ROLE_SUFFIX = re.compile(
    r"\s*\(\s*(c\s*(?:&|and)\s*wk|c\s*&\s*wk|captain\s*(?:&|and)\s*w+k+|c\+wk|c\s*&\s*wk|c|wk|w+k)\s*\)\s*$",
    re.IGNORECASE,
)


@dataclass
class PlayingXiPlayer:
    number: int
    name: str
    roles: str = ""


@dataclass
class PlayingXiInfo:
    team: str
    opponent: str
    players: list[PlayingXiPlayer] = field(default_factory=list)
    match_label: str = ""
    series: str = ""
    match_key: str = ""
    format_tag: str = "ODI"


def make_playing_xi_key(match_key: str, team: str) -> str:
    return f"{match_key}|{_team_slug(team)}"


def find_team_captain(players: list[PlayingXiPlayer]) -> PlayingXiPlayer | None:
    for player in players:
        if player.roles in ("C", "C+WK"):
            return player
    return None


def captain_display_name(player: PlayingXiPlayer) -> str:
    name = re.sub(r"\s*\([^)]*\)\s*$", "", player.name).strip()
    return name.title()


def captains_from_squads(
    squads: dict[str, list[PlayingXiPlayer]],
    team1: str,
    team2: str,
) -> dict[str, PlayingXiPlayer]:
    result: dict[str, PlayingXiPlayer] = {}
    for team in (team1, team2):
        players = squads.get(team)
        if not players:
            continue
        captain = find_team_captain(players)
        if captain:
            result[team] = captain
    return result


def match_playing_xi_urls(match_url: str) -> list[str]:
    base = match_url.rstrip("/")
    # Strip known ESPN page-specific suffixes so we always start from the base match URL
    base = re.sub(r"/live-cricket-score$", "", base, flags=re.IGNORECASE)
    if base.endswith("/match-playing-xi"):
        return [base]
    candidates = [
        f"{base}/match-playing-xi",
        base,
    ]
    if "/full-scorecard" in base:
        candidates.insert(0, base.replace("/full-scorecard", "/match-playing-xi"))
    return list(dict.fromkeys(candidates))


def _parse_player_roles(raw_name: str) -> tuple[str, str]:
    match = ROLE_SUFFIX.search(raw_name)
    if not match:
        return raw_name.strip(), ""
    role_token = match.group(1).lower().replace(" ", "").replace("and", "")
    name = ROLE_SUFFIX.sub("", raw_name).strip()
    if "c" in role_token and "wk" in role_token:
        return name, "C+WK"
    if role_token.startswith("c") or "captain" in role_token:
        return name, "C"
    if "wk" in role_token:
        return name, "WK"
    return name, ""


def _player_from_line(number: int, raw_line: str) -> PlayingXiPlayer:
    name, roles = _parse_player_roles(raw_line)
    display = name.upper()
    if roles:
        display = f"{display} ({roles})"
    return PlayingXiPlayer(number=number, name=display, roles=roles)


def _team_matches(candidate: str, team: str) -> bool:
    return _normalize_team_name(candidate) == _normalize_team_name(team)


def _extract_team_players(lines: list[str], start: int, team: str) -> list[PlayingXiPlayer]:
    players: list[PlayingXiPlayer] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if _line_is_tracked_team(stripped):
            other = _normalize_team_name(stripped)
            if other != _normalize_team_name(team):
                break
        match = PLAYER_LINE.match(stripped)
        if match:
            players.append(_player_from_line(int(match.group(1)), match.group(2)))
            if len(players) >= 11:
                break
            continue
        if players and not re.match(r"^\d+\.", stripped) and len(stripped) > 2:
            if _line_is_tracked_team(stripped):
                break
    return players[:11]


def parse_playing_xi_from_match_text(
    text: str,
    team1: str,
    team2: str,
) -> dict[str, list[PlayingXiPlayer]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    squads: dict[str, list[PlayingXiPlayer]] = {}

    in_xi_section = False
    for i, line in enumerate(lines):
        if PLAYING_XI_HEADER.search(line):
            in_xi_section = True
        if not in_xi_section and not PLAYING_XI_HEADER.search(text[:2000]):
            in_xi_section = True

        if _team_matches(line, team1):
            players = _extract_team_players(lines, i, team1)
            if len(players) >= 11:
                squads[team1] = players
        elif _team_matches(line, team2):
            players = _extract_team_players(lines, i, team2)
            if len(players) >= 11:
                squads[team2] = players

    return squads


def _player_from_json_obj(obj: dict, index: int) -> PlayingXiPlayer | None:
    name = (
        obj.get("name")
        or obj.get("fullName")
        or obj.get("longName")
        or (obj.get("player") or {}).get("name")
        or (obj.get("player") or {}).get("longName")
    )
    if not name or not isinstance(name, str):
        return None

    roles = ""
    if obj.get("isCaptain") and obj.get("isKeeper"):
        roles = "C+WK"
    elif obj.get("isCaptain"):
        roles = "C"
    elif obj.get("isKeeper"):
        roles = "WK"
    else:
        role_field = str(obj.get("role") or obj.get("playerRole") or "").lower()
        if "captain" in role_field and "keeper" in role_field:
            roles = "C+WK"
        elif "captain" in role_field:
            roles = "C"
        elif "keeper" in role_field or "wicket" in role_field:
            roles = "WK"

    display = name.strip().upper()
    if roles:
        display = f"{display} ({roles})"
    return PlayingXiPlayer(number=index, name=display, roles=roles)


def _normalize_json_team_name(raw: str) -> str:
    cleaned = re.sub(r"\s+Women$", " Women", raw.strip(), flags=re.IGNORECASE)
    for team in TRACKED_TEAMS:
        if team.lower() in cleaned.lower():
            if "women" in cleaned.lower():
                return f"{team} Women"
            return team
    return cleaned


def _squads_from_player_groups(groups: list) -> dict[str, list[PlayingXiPlayer]]:
    squads: dict[str, list[PlayingXiPlayer]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        team_raw = group.get("team")
        if isinstance(team_raw, dict):
            team_name = team_raw.get("name")
        else:
            team_name = group.get("teamName") or group.get("name") or team_raw
        players_raw = (
            group.get("players")
            or group.get("teamPlayers")
            or group.get("playingXI")
            or group.get("playing11")
            or group.get("squad")
        )
        if not team_name or not isinstance(players_raw, list):
            continue
        team = _normalize_json_team_name(str(team_name))
        players: list[PlayingXiPlayer] = []
        for idx, item in enumerate(players_raw[:11], start=1):
            if isinstance(item, str):
                players.append(_player_from_line(idx, item))
            elif isinstance(item, dict):
                parsed = _player_from_json_obj(item, idx)
                if parsed:
                    players.append(parsed)
        if len(players) >= 11:
            squads[team] = players[:11]
    return squads


def _walk_for_squads(obj, found: list) -> None:
    if isinstance(obj, dict):
        for key in ("teamPlayers", "playingXI", "playing11", "squads", "squad"):
            if key in obj and isinstance(obj[key], list) and obj[key]:
                found.append(obj[key])
        for value in obj.values():
            _walk_for_squads(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_squads(item, found)


def parse_playing_xi_from_next_data(html: str) -> dict[str, list[PlayingXiPlayer]]:
    match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {}

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}

    groups: list = []
    _walk_for_squads(data, groups)

    squads: dict[str, list[PlayingXiPlayer]] = {}
    for group in groups:
        if not group:
            continue
        if isinstance(group[0], dict) and (
            "teamName" in group[0] or "team" in group[0] or "name" in group[0]
        ):
            squads.update(_squads_from_player_groups(group))
            continue
        if isinstance(group[0], dict):
            team_name = group[0].get("teamName") or group[0].get("team")
            if isinstance(team_name, dict):
                team_name = team_name.get("name")
            if team_name:
                players: list[PlayingXiPlayer] = []
                for idx, item in enumerate(group[:11], start=1):
                    if isinstance(item, dict):
                        parsed = _player_from_json_obj(item, idx)
                        if parsed:
                            players.append(parsed)
                if len(players) >= 11:
                    squads[_normalize_json_team_name(str(team_name))] = players

    page_props = data.get("props", {}).get("pageProps", {})
    for container_key in ("data", "dehydratedState", "match"):
        container = page_props.get(container_key)
        if isinstance(container, dict):
            for side_key in ("team1", "team2", "homeTeam", "awayTeam"):
                side = container.get(side_key)
                if not isinstance(side, dict):
                    continue
                team_name = side.get("name") or side.get("teamName")
                players_raw = side.get("players") or side.get("teamPlayers")
                if team_name and isinstance(players_raw, list) and len(players_raw) >= 11:
                    players = []
                    for idx, item in enumerate(players_raw[:11], start=1):
                        if isinstance(item, dict):
                            parsed = _player_from_json_obj(item, idx)
                            if parsed:
                                players.append(parsed)
                        elif isinstance(item, str):
                            players.append(_player_from_line(idx, item))
                    if len(players) >= 11:
                        squads[_normalize_json_team_name(str(team_name))] = players[:11]

    return squads


def parse_playing_xi_from_html(
    html: str,
    text: str,
    team1: str,
    team2: str,
) -> dict[str, list[PlayingXiPlayer]]:
    squads = parse_playing_xi_from_next_data(html)
    if team1 in squads and team2 in squads:
        return squads

    normalized: dict[str, list[PlayingXiPlayer]] = {}
    for key, players in squads.items():
        norm = _normalize_team_name(key)
        normalized[norm] = players
    if team1 in normalized and team2 in normalized:
        return normalized

    text_squads = parse_playing_xi_from_match_text(text, team1, team2)
    for team in (team1, team2):
        if team not in normalized and team in text_squads:
            normalized[team] = text_squads[team]
    return normalized


def _interpolate_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))  # type: ignore[return-value]


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


def _apply_stadium_watermark(base: Image.Image) -> None:
    if not STADIUM_ASSET.exists():
        return
    try:
        with Image.open(STADIUM_ASSET) as stadium:
            stadium = stadium.convert("RGBA")
            target_w = base.width
            target_h = int(base.height * 0.45)
            stadium = stadium.resize((target_w, target_h), Image.Resampling.LANCZOS)
            alpha = stadium.split()[3].point(lambda p: int(p * 0.15))
            stadium.putalpha(alpha)
            base.paste(stadium, (0, 60), stadium)
    except OSError:
        pass


def _draw_glow_line(draw: ImageDraw.ImageDraw, y: int, x1: int, x2: int) -> None:
    mid = (x1 + x2) // 2
    draw.line([(x1, y + 2), (mid, y + 2)], fill="#666666", width=1)
    draw.line([(mid, y + 2), (x2, y + 2)], fill="#666666", width=1)
    draw.line([(x1 + 40, y + 1), (mid, y + 1)], fill="#AAAAAA", width=1)
    draw.line([(mid, y + 1), (x2 - 40, y + 1)], fill="#AAAAAA", width=1)
    draw.line([(x1 + 80, y), (x2 - 80, y)], fill="#FFFFFF", width=1)


def _stage_line(info: PlayingXiInfo) -> str:
    label = info.match_label.strip()
    if label:
        return label.upper()
    if info.format_tag:
        return f"{info.format_tag} MATCH"
    return "INTERNATIONAL MATCH"


def _draw_playing_xi_card(info: PlayingXiInfo) -> Image.Image:
    primary, secondary = _team_kit_colors(info.team)
    base = _draw_vertical_gradient(IMAGE_WIDTH, IMAGE_HEIGHT, primary, secondary, DARK_NAVY)
    _apply_stadium_watermark(base)

    overlay = Image.new("RGBA", (IMAGE_WIDTH, IMAGE_HEIGHT), (0, 0, 0, 0))
    panel_left = (IMAGE_WIDTH - XI_PANEL_WIDTH) // 2
    panel_height = 11 * XI_ROW_HEIGHT + 40
    panel_draw = ImageDraw.Draw(overlay)
    panel_draw.rounded_rectangle(
        [(panel_left, XI_PANEL_TOP), (panel_left + XI_PANEL_WIDTH, XI_PANEL_TOP + panel_height)],
        radius=16,
        fill=(0, 0, 0, 140),
    )
    base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(base)

    flag = _load_team_flag(info.team, XI_FLAG_WIDTH, XI_FLAG_HEIGHT, XI_FLAG_RADIUS)
    _paste_flag_centered(base, flag, IMAGE_WIDTH // 2, XI_FLAG_Y)

    title_font = _load_font(52, bold=True)
    stage_font = _load_font(28, bold=False)
    opponent_font = _load_font(32, bold=True)
    number_font = _load_font(30, bold=True)
    name_font = _load_font(28, bold=True)

    abbrev = _team_abbrev(info.team)
    _draw_centered_text(draw, f"PLAYING XI {abbrev}", IMAGE_WIDTH // 2, XI_TITLE_Y, title_font, "#FFFFFF")
    _draw_centered_text(draw, _stage_line(info), IMAGE_WIDTH // 2, XI_STAGE_Y, stage_font, "#E8EAED")
    _draw_centered_text(
        draw,
        f"AGAINST {_team_abbrev(info.opponent)}",
        IMAGE_WIDTH // 2,
        XI_OPPONENT_Y,
        opponent_font,
        "#FFFFFF",
    )

    row_y = XI_PANEL_TOP + 24
    panel_right = panel_left + XI_PANEL_WIDTH
    for player in info.players[:11]:
        draw.text((XI_NUMBER_X, row_y), f"{player.number}.", font=number_font, fill="#FFFFFF")
        draw.text((XI_NAME_X, row_y), player.name, font=name_font, fill="#FFFFFF")
        divider_y = row_y + XI_ROW_HEIGHT - 12
        if player.number < 11:
            _draw_glow_line(draw, divider_y, panel_left + 40, panel_right - 40)
        row_y += XI_ROW_HEIGHT

    return base


def _team_kit_colors(team: str) -> tuple[str, str]:
    from match_image import TEAM_KITS

    base = team.replace(" Women", "")
    if base in TEAM_KITS:
        _, primary, secondary = TEAM_KITS[base]
        return primary, secondary
    return "#800020", "#003366"


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


def _series_from_block(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or _line_is_tracked_team(stripped):
            continue
        if MATCH_LABEL_PATTERN.search(stripped) or FORMAT_PATTERN.search(stripped):
            continue
        if "won the toss" in stripped.lower() or "match starts" in stripped.lower():
            continue
        if len(stripped) > 8 and not stripped.upper().startswith(("LIVE", "RESULT", "TODAY")):
            return stripped
    return ""


def _clean_match_label(line: str) -> str:
    return re.sub(r"\s*[—–-]\s*\([^)]*ov[^)]*\).*$", "", line, flags=re.IGNORECASE).strip()


def _match_label_from_block(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if MATCH_LABEL_PATTERN.search(stripped):
            return _clean_match_label(stripped)
    return ""


def build_playing_xi_info(
    team: str,
    opponent: str,
    players: list[PlayingXiPlayer],
    block: str,
    match_key: str,
) -> PlayingXiInfo:
    fmt = _detect_format(block)
    format_tag = "T20I" if fmt == "T20" else ("Test" if fmt == "TEST" else "ODI")
    return PlayingXiInfo(
        team=team,
        opponent=opponent,
        players=players[:11],
        match_label=_match_label_from_block(block),
        series=_series_from_block(block),
        match_key=match_key,
        format_tag=format_tag,
    )


def build_playing_xi_caption(info: PlayingXiInfo) -> str:
    abbrev = _team_abbrev(info.team)
    opponent = _team_abbrev(info.opponent)
    label = info.match_label or info.format_tag
    series = f" — {info.series}" if info.series else ""
    return f"Playing XI: {info.team} vs {info.opponent}, {label}{series} — {abbrev} squad announced."


def generate_playing_xi_image(info: PlayingXiInfo) -> Path:
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^\w\-]", "_", make_playing_xi_key(info.match_key, info.team))[:90]
    output_path = GENERATED_IMAGES_DIR / f"playing_xi_{safe_key}.png"
    _draw_playing_xi_card(info).save(output_path, "PNG")
    return output_path
