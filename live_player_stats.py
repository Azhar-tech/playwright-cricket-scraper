"""Fetch and parse current batter/bowler lines from ESPN match Live pages."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from match_image import BATTER_PATTERN, BOWLER_PATTERN

logger = logging.getLogger(__name__)

_LIVE_SUFFIX = re.compile(
    r"/(?:live-cricket-score|full-scorecard|match-playing-xi)/?$",
    re.IGNORECASE,
)
_NAME_NOISE = re.compile(r"\s*(?:rhb|lhb|†|\(c\)|\(wk\))\s*", re.IGNORECASE)
_TEXT_BATTER_ROW = re.compile(
    r"^([A-Za-z][A-Za-z\s\.\-']+?)(?:\*)?\s+(?:rhb|lhb)?\s+(\d+)\s+(\d+)\s",
    re.IGNORECASE,
)
_TEXT_BOWLER_ROW = re.compile(
    r"^([A-Za-z][A-Za-z\s\.\-']+?)\s+(?:lbg|rf|lf|rm|ob|sla)?\s+"
    r"(\d+(?:\.\d+)?)\s+\d+\s+(\d+)\s+(\d+)\s",
    re.IGNORECASE,
)


@dataclass
class LiveBatterRow:
    name: str
    runs: int
    balls: int
    not_out: bool = True
    is_striker: bool = False


@dataclass
class LiveBowlerRow:
    name: str
    wickets: int
    runs: int
    overs: float
    is_active: bool = False


def live_match_url(match_url: str) -> str:
    base = match_url.rstrip("/")
    base = _LIVE_SUFFIX.sub("", base)
    return f"{base}/live-cricket-score"


def abbrev_player_name(full_name: str) -> str:
    cleaned = _NAME_NOISE.sub(" ", full_name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return full_name.strip()
    if re.match(r"^[A-Z]\.\s", cleaned):
        return cleaned
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0][0]}. {parts[-1]}"


def format_batter_line(
    name: str,
    runs: int,
    balls: int,
    not_out: bool,
    *,
    is_striker: bool = False,
) -> str:
    short = abbrev_player_name(name)
    runs_str = f"{runs}*" if not_out else str(runs)
    line = f"{short}: {runs_str} ({balls})"
    if is_striker:
        return f"• {line}"
    return line


def format_bowler_line(
    name: str,
    wickets: int,
    runs: int,
    overs: float,
    *,
    is_active: bool = False,
) -> str:
    short = abbrev_player_name(name)
    overs_val = float(overs)
    if overs_val.is_integer():
        overs_str = str(int(overs_val))
    else:
        overs_str = f"{overs_val:.1f}".rstrip("0").rstrip(".")
    line = f"{short}: {wickets}/{runs} ({overs_str})"
    if is_active:
        return f"{line} •"
    return line


def _player_name_from_obj(obj: dict[str, Any]) -> str:
    nested = obj.get("player") if isinstance(obj.get("player"), dict) else {}
    for key in ("name", "fullName", "longName", "shortName"):
        value = obj.get(key) or nested.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    batter = obj.get("batter")
    if isinstance(batter, dict):
        for key in ("name", "fullName", "longName", "shortName"):
            value = batter.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _int_field(obj: dict[str, Any], *keys: str, default: int = 0) -> int:
    for key in keys:
        value = obj.get(key)
        if value is None and isinstance(obj.get("player"), dict):
            value = obj["player"].get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def _float_field(obj: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = obj.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _looks_like_batter(obj: dict[str, Any]) -> bool:
    if not _player_name_from_obj(obj):
        return False
    if any(key in obj for key in ("balls", "ballsFaced", "runs", "runsScored", "striker")):
        return True
    batter = obj.get("batter")
    return isinstance(batter, dict) and any(
        key in batter for key in ("balls", "ballsFaced", "runs", "runsScored")
    )


def _looks_like_bowler(obj: dict[str, Any]) -> bool:
    if not _player_name_from_obj(obj):
        return False
    if any(key in obj for key in ("overs", "wickets", "conceded", "runsConceded", "economy")):
        return True
    bowler = obj.get("bowler")
    return isinstance(bowler, dict) and any(
        key in bowler for key in ("overs", "wickets", "conceded", "runsConceded")
    )


def _parse_batter_obj(obj: dict[str, Any]) -> LiveBatterRow | None:
    name = _player_name_from_obj(obj)
    if not name:
        return None
    runs = _int_field(obj, "runs", "runsScored", "r")
    balls = _int_field(obj, "balls", "ballsFaced", "b")
    if runs == 0 and balls == 0 and not any(
        key in obj for key in ("runs", "runsScored", "balls", "ballsFaced")
    ):
        return None
    not_out = not bool(obj.get("isOut") or obj.get("out") or obj.get("dismissed"))
    is_striker = bool(
        obj.get("isStriker") or obj.get("striker") or obj.get("onStrike") or obj.get("active")
    )
    return LiveBatterRow(name=name, runs=runs, balls=balls, not_out=not_out, is_striker=is_striker)


def _parse_bowler_obj(obj: dict[str, Any]) -> LiveBowlerRow | None:
    name = _player_name_from_obj(obj)
    if not name:
        return None
    wickets = _int_field(obj, "wickets", "w")
    runs = _int_field(obj, "runs", "runsConceded", "conceded", "r")
    overs = _float_field(obj, "overs", "o")
    if wickets == 0 and runs == 0 and overs == 0 and not any(
        key in obj for key in ("overs", "wickets", "runsConceded", "conceded")
    ):
        return None
    is_active = bool(
        obj.get("isActive") or obj.get("active") or obj.get("isCurrentBowler") or obj.get("current")
    )
    return LiveBowlerRow(
        name=name,
        wickets=wickets,
        runs=runs,
        overs=overs,
        is_active=is_active,
    )


def _dedupe_batters(rows: list[LiveBatterRow]) -> list[LiveBatterRow]:
    seen: set[str] = set()
    result: list[LiveBatterRow] = []
    for row in rows:
        key = re.sub(r"[^a-z]", "", row.name.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    strikers = [row for row in result if row.is_striker]
    if strikers:
        ordered = strikers + [row for row in result if not row.is_striker]
        return ordered[:2]
    return result[:2]


def _dedupe_bowlers(rows: list[LiveBowlerRow]) -> list[LiveBowlerRow]:
    seen: set[str] = set()
    result: list[LiveBowlerRow] = []
    for row in rows:
        key = re.sub(r"[^a-z]", "", row.name.lower())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    active = [row for row in result if row.is_active]
    if active:
        ordered = active + [row for row in result if not row.is_active]
        return ordered[:2]
    return result[:2]


def _walk_live_stats(obj: Any, batters: list[LiveBatterRow], bowlers: list[LiveBowlerRow]) -> None:
    if isinstance(obj, dict):
        key_lower = {str(k).lower() for k in obj.keys()}
        if "batsmen" in key_lower or "batters" in key_lower:
            for key, value in obj.items():
                if str(key).lower() not in ("batsmen", "batters"):
                    continue
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            parsed = _parse_batter_obj(item)
                            if parsed:
                                batters.append(parsed)
        if "bowlers" in key_lower:
            for key, value in obj.items():
                if str(key).lower() != "bowlers":
                    continue
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            parsed = _parse_bowler_obj(item)
                            if parsed:
                                bowlers.append(parsed)
        if _looks_like_batter(obj) and not _looks_like_bowler(obj):
            parsed = _parse_batter_obj(obj)
            if parsed:
                batters.append(parsed)
        elif _looks_like_bowler(obj):
            parsed = _parse_bowler_obj(obj)
            if parsed:
                bowlers.append(parsed)
        for value in obj.values():
            _walk_live_stats(value, batters, bowlers)
    elif isinstance(obj, list):
        for item in obj:
            _walk_live_stats(item, batters, bowlers)


def parse_live_player_stats_from_next_data(html: str) -> tuple[list[LiveBatterRow], list[LiveBowlerRow]]:
    match = re.search(
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return [], []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return [], []

    batters: list[LiveBatterRow] = []
    bowlers: list[LiveBowlerRow] = []
    _walk_live_stats(data, batters, bowlers)
    return _dedupe_batters(batters), _dedupe_bowlers(bowlers)


def parse_live_player_stats_from_text(text: str) -> tuple[list[LiveBatterRow], list[LiveBowlerRow]]:
    batters: list[LiveBatterRow] = []
    bowlers: list[LiveBowlerRow] = []
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower in ("batters", "batter", "batting"):
            section = "batters"
            continue
        if lower in ("bowlers", "bowler", "bowling"):
            section = "bowlers"
            continue
        if lower.startswith("partnership") or lower.startswith("last wicket"):
            section = ""
            continue

        if section == "batters":
            star = "*" in line
            row_match = _TEXT_BATTER_ROW.match(line)
            if row_match:
                name = row_match.group(1).strip().rstrip("*")
                batters.append(
                    LiveBatterRow(
                        name=name,
                        runs=int(row_match.group(2)),
                        balls=int(row_match.group(3)),
                        not_out=star,
                        is_striker=star,
                    )
                )
        elif section == "bowlers":
            row_match = _TEXT_BOWLER_ROW.match(line)
            if row_match:
                bowlers.append(
                    LiveBowlerRow(
                        name=row_match.group(1).strip(),
                        overs=float(row_match.group(2)),
                        runs=int(row_match.group(3)),
                        wickets=int(row_match.group(4)),
                    )
                )

    if not batters and not bowlers:
        for line in text.splitlines():
            for batter_match in BATTER_PATTERN.finditer(line):
                name = batter_match.group(1).strip()
                runs_token = batter_match.group(2)
                batters.append(
                    LiveBatterRow(
                        name=name,
                        runs=int(runs_token.rstrip("*")),
                        balls=int(batter_match.group(3)),
                        not_out=runs_token.endswith("*"),
                    )
                )
            for bowler_match in BOWLER_PATTERN.finditer(line):
                wickets, runs = bowler_match.group(2).split("/")
                bowlers.append(
                    LiveBowlerRow(
                        name=bowler_match.group(1).strip(),
                        wickets=int(wickets),
                        runs=int(runs),
                        overs=float(bowler_match.group(3)),
                    )
                )

    return _dedupe_batters(batters), _dedupe_bowlers(bowlers)


def parse_live_player_stats_from_html(html: str, text: str) -> tuple[list[str], list[str]]:
    batters, bowlers = parse_live_player_stats_from_next_data(html)
    if not batters and not bowlers:
        batters, bowlers = parse_live_player_stats_from_text(text)

    batter_lines = [
        format_batter_line(row.name, row.runs, row.balls, row.not_out, is_striker=row.is_striker)
        for row in batters[:2]
    ]
    bowler_lines = [
        format_bowler_line(row.name, row.wickets, row.runs, row.overs, is_active=row.is_active)
        for row in bowlers[:2]
    ]
    return batter_lines, bowler_lines


async def fetch_live_player_stats(page: Page, match_url: str) -> tuple[list[str], list[str]]:
    if not match_url:
        return [], []

    url = live_match_url(match_url)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except PlaywrightTimeoutError:
        logger.warning("Live stats navigation timed out: %s", url)
        return [], []

    try:
        html = await page.content()
        body_text = await page.locator("body").inner_text(timeout=8_000)
    except PlaywrightTimeoutError:
        logger.warning("Could not read live stats page body: %s", url)
        return [], []

    batters, bowlers = parse_live_player_stats_from_html(html, body_text)
    if batters or bowlers:
        logger.info(
            "Live stats parsed from ESPN: %d batters, %d bowlers",
            len(batters),
            len(bowlers),
        )
    else:
        logger.info("Live stats skip: no player data on match page")
    return batters, bowlers
