"""Rule-based Facebook post text for live, result, and toss updates (no Gemini)."""

from __future__ import annotations

import re
from typing import Optional

from match_image import (
    FORMAT_PATTERN,
    MATCH_LABEL_PATTERN,
    SCORE_PATTERN,
    _line_is_tracked_team,
    _normalize_team_name,
    _team_abbrev,
)

FACEBOOK_BG_CHAR_LIMIT = 130

TOSS_PATTERN = re.compile(
    r"((?:[\w\s]+)\s+won the toss|(?:[\w\s-]+)\s+(?:chose|opted|elected) to (?:bat|field|bowl)[^.]*)",
    re.IGNORECASE,
)
OVERS_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+)\s*ov", re.IGNORECASE)
BATTER_PATTERN = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(\d+)\s*\(\s*(\d+)\s*\)",
)


def _detect_format(text: str) -> str:
    if re.search(r"\bwon by an innings\b", text, re.IGNORECASE):
        return "TEST"
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


def _result_line(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if re.search(r"\bwon by\b", stripped, re.IGNORECASE):
            return stripped.rstrip(".")
    return ""


def _teams_from_block(block: str) -> list[str]:
    return [_normalize_team_name(line) for line in block.splitlines() if _line_is_tracked_team(line)]


def _short_match_label(block: str) -> str:
    for line in block.splitlines():
        match = MATCH_LABEL_PATTERN.search(line)
        if match:
            label = match.group(0)
            label = re.sub(r"\s+", " ", label)
            return label.replace("One Day", "ODI")
    fmt = _detect_format(block)
    if fmt == "T20":
        return "T20"
    if fmt == "TEST":
        return "Test"
    return "ODI"


def _compact_score(line: str) -> str:
    line = line.strip()
    if not line:
        return ""

    overs = OVERS_PATTERN.search(line)
    scores = SCORE_PATTERN.findall(line)
    test_innings = re.findall(r"\d+(?:/\d+)?", line)

    if "&" in line and len(test_innings) >= 2:
        parts = re.findall(r"\d+(?:/\d+)?", line)
        if parts:
            return " & ".join(parts[:3])

    if scores:
        score = scores[-1]
        if overs:
            ov_num = overs.group(1)
            return f"{score} ({ov_num} ov)"
        return score

    if re.fullmatch(r"\d+", line.strip()):
        return line.strip()
    return line[:24]


def _scores_by_team(block: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    paired: list[tuple[str, str]] = []
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
            if _is_score_line(nxt):
                score = _compact_score(nxt)
                if score:
                    paired.append((team, score))
                break
    return paired


def _truncate(text: str, limit: int = FACEBOOK_BG_CHAR_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    suffix = "..."
    trimmed = text[: limit - len(suffix)]
    last_space = trimmed.rfind(" ")
    if last_space > 0:
        trimmed = trimmed[:last_space]
    return trimmed + suffix


def build_result_post(block: str) -> Optional[str]:
    teams = _teams_from_block(block)
    if len(teams) < 2:
        return None

    abbrev1, abbrev2 = _team_abbrev(teams[0]), _team_abbrev(teams[1])
    label = _short_match_label(block)
    result_line = _result_line(block)

    scores = _scores_by_team(block)
    header = f"{abbrev1} vs {abbrev2}, {label}"

    if len(scores) >= 2:
        s1 = f"{_team_abbrev(scores[0][0])} {scores[0][1]}"
        s2 = f"{_team_abbrev(scores[1][0])} {scores[1][1]}"
        if result_line:
            post = f"{header} - {s1}, {s2}. {result_line}."
        else:
            post = f"{header} - {s1}, {s2}."
    elif result_line:
        post = f"{header} - {result_line}."
    else:
        return None

    return _truncate(post)


def build_live_post(block: str) -> Optional[str]:
    teams = _teams_from_block(block)
    if len(teams) < 2:
        return None

    abbrev1, abbrev2 = _team_abbrev(teams[0]), _team_abbrev(teams[1])
    label = _short_match_label(block)
    scores = _scores_by_team(block)

    batters: list[str] = []
    for line in block.splitlines():
        for match in BATTER_PATTERN.finditer(line):
            name_parts = match.group(1).split()
            short = name_parts[-1]
            batters.append(f"{short} {match.group(2)}({match.group(3)})")
        if len(batters) >= 2:
            break

    header = f"{abbrev1} vs {abbrev2}, {label}"

    if scores:
        active = scores[0]
        post = f"{header} - {_team_abbrev(active[0])} {active[1]}."
        if batters:
            post = f"{header} - {_team_abbrev(active[0])} {active[1]}. {batters[0]}, {batters[1]}."
    elif batters:
        post = f"{header} - {batters[0]}, {batters[1]}."
    else:
        post = f"{header} - live update."

    return _truncate(post)


def build_toss_post(block: str) -> Optional[str]:
    teams = _teams_from_block(block)
    if len(teams) < 2:
        return None

    abbrev1, abbrev2 = _team_abbrev(teams[0]), _team_abbrev(teams[1])
    label = _short_match_label(block)
    toss_match = TOSS_PATTERN.search(block)
    if not toss_match:
        return None

    toss_line = toss_match.group(1).strip().rstrip(".")
    post = f"{abbrev1} vs {abbrev2}, {label} - {toss_line}."
    return _truncate(post)


def build_match_post(block: str, phase: str) -> Optional[str]:
    if phase == "result":
        return build_result_post(block)
    if phase == "live":
        return build_live_post(block)
    if phase == "toss":
        return build_toss_post(block)
    return None
