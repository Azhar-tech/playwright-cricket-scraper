"""Captain headshot fetch (Google Images) and toss graphic orchestration."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote_plus

import requests
from playwright.async_api import Page

from match_image import CaptainInfo, CaptainTossInfo, MatchUpdateInfo
from playing_xi import (
    PlayingXiPlayer,
    captain_display_name,
    captains_from_squads,
    find_team_captain,
)

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
CAPTAIN_CACHE_DIR = _BASE_DIR / "assets" / "captain_cache"
CAPTAIN_NAME_CACHE_DIR = CAPTAIN_CACHE_DIR / "names"
GENERATED_IMAGES_DIR = _BASE_DIR / "generated_images"

_FORMAT_LABELS = {"T20": "T20I", "ODI": "ODI", "TEST": "Test"}
_NAME_NOISE = re.compile(
    r"\b(?:captain|skipper|cricket|team|squad|profile|wikipedia|espn|cricinfo|"
    r"current|who is|the|of|and|vs|v)\b",
    re.IGNORECASE,
)
_PERSON_NAME = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b"
)


def _player_cache_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:80] or "captain"


def _team_format_slug(team: str, fmt: str) -> str:
    return f"{_player_cache_slug(team)}-{_player_cache_slug(fmt)}"


def _cached_headshot_path(name: str) -> Path:
    return CAPTAIN_CACHE_DIR / f"{_player_cache_slug(name)}.jpg"


def _cached_captain_name_path(team: str, fmt: str) -> Path:
    return CAPTAIN_NAME_CACHE_DIR / f"{_team_format_slug(team, fmt)}.txt"


def _format_captain_query(team: str, fmt: str) -> str:
    label = _FORMAT_LABELS.get(fmt, "cricket")
    return f"{team} {label} cricket team captain"


def _normalize_captain_name(raw: str, team: str) -> str:
    cleaned = raw.strip()
    cleaned = re.sub(r"\s*[-–|•]\s*.*$", "", cleaned)
    cleaned = _NAME_NOISE.sub(" ", cleaned)
    team_base = team.replace(" Women", "")
    for token in (team, team_base, f"{team_base} Women"):
        cleaned = re.sub(re.escape(token), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
    if not cleaned:
        return ""
    parts = cleaned.split()
    if len(parts) > 4:
        cleaned = " ".join(parts[:4])
    return cleaned.title()


def _extract_captain_name_from_text(text: str, team: str) -> str:
    if not text:
        return ""
    lower = text.lower()
    for marker in ("captain is", "captain:", "skipper is", "skipper:", "captain -", "captain –"):
        idx = lower.find(marker)
        if idx >= 0:
            fragment = text[idx + len(marker) : idx + len(marker) + 80]
            name = _normalize_captain_name(fragment, team)
            if name:
                return name
    for match in _PERSON_NAME.finditer(text):
        name = _normalize_captain_name(match.group(1), team)
        if name and len(name.split()) >= 2:
            return name
    return ""


def captain_toss_ready(captains: CaptainTossInfo | None) -> bool:
    if captains is None:
        return False
    for captain in (captains.team1_captain, captains.team2_captain):
        if not captain.name or captain.image_path is None or not captain.image_path.exists():
            return False
    return True


def _download_image(url: str, dest: Path) -> bool:
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        if response.status_code != 200 or len(response.content) < 1024:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return dest.exists() and dest.stat().st_size >= 1024
    except Exception as exc:
        logger.warning("Captain headshot download failed for %s: %s", url[:80], exc)
        return False


async def lookup_captain_name_via_google(page: Page, team: str, fmt: str) -> str | None:
    """Look up the current format captain via Google text search."""
    cache_path = _cached_captain_name_path(team, fmt)
    if cache_path.exists():
        cached = cache_path.read_text(encoding="utf-8").strip()
        if cached:
            logger.info("Using cached captain name for %s (%s): %s", team, fmt, cached)
            return cached

    query = _format_captain_query(team, fmt)
    search_url = f"https://www.google.com/search?q={quote_plus(query)}"

    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.warning("Google captain search navigation failed for %s: %s", team, exc)
        return None

    if "google.com/sorry" in page.url:
        logger.warning("Google CAPTCHA during captain name search for %s", team)
        return None

    snippet = await page.evaluate(
        """
        () => {
          const selectors = [
            '[data-attrid="title"]',
            '.wDYxhc',
            '.kp-header',
            '.Z0LcW',
            '.hgKElc',
            '.LGOjhe',
            '[data-tts="answers"]',
            '.VwiC3b',
            '.kno-rdesc span',
          ];
          for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el && el.textContent && el.textContent.trim().length > 3) {
              return el.textContent.trim();
            }
          }
          const headings = Array.from(document.querySelectorAll('h3'));
          for (const h3 of headings) {
            const text = (h3.textContent || '').trim();
            if (text.length >= 5 && text.length <= 60) return text;
          }
          return '';
        }
        """
    )

    name = _extract_captain_name_from_text(snippet if isinstance(snippet, str) else "", team)
    if not name:
        try:
            body_text = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            body_text = ""
        name = _extract_captain_name_from_text(body_text[:2500], team)

    if not name:
        logger.info("No Google captain name result for %s (%s)", team, fmt)
        return None

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(name, encoding="utf-8")
    logger.info("Captain name from Google: %s -> %s", team, name)
    return name


async def fetch_captain_headshot(page: Page, name: str, team: str) -> Path | None:
    """Fetch a captain headshot via Google Images; cache locally by player name."""
    cache_path = _cached_headshot_path(name)
    if cache_path.exists() and cache_path.stat().st_size >= 1024:
        logger.info("Using cached captain headshot for %s", name)
        return cache_path

    query = quote_plus(f"{name} {team} cricket player")
    search_url = f"https://www.google.com/search?q={query}&tbm=isch"

    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.warning("Google Images navigation failed for %s: %s", name, exc)
        return None

    if "google.com/sorry" in page.url:
        logger.warning("Google CAPTCHA during captain image search for %s", name)
        return None

    image_url = await page.evaluate(
        """
        () => {
          const skip = (src) =>
            !src ||
            src.startsWith('data:') ||
            src.includes('gstatic.com/images/branding') ||
            src.includes('googlelogo');
          const imgs = Array.from(document.querySelectorAll('img'));
          for (const img of imgs) {
            const src = img.currentSrc || img.src || '';
            if (skip(src)) continue;
            if (img.naturalWidth >= 80 && img.naturalHeight >= 80) return src;
          }
          for (const img of imgs) {
            const src = img.currentSrc || img.src || '';
            if (!skip(src)) return src;
          }
          return '';
        }
        """
    )

    if not image_url or not isinstance(image_url, str):
        logger.info("No Google Images result for %s", name)
        return None

    if _download_image(image_url, cache_path):
        logger.info("Cached captain headshot for %s", name)
        return cache_path

    return None


def _resolve_captain_name_from_espn(
    team: str,
    squads: dict[str, list[PlayingXiPlayer]],
) -> str | None:
    players = squads.get(team)
    if not players:
        return None
    captain = find_team_captain(players)
    if not captain:
        return None
    espn_name = captain_display_name(captain)
    logger.info("Captain name from ESPN XI: %s -> %s", team, espn_name)
    return espn_name


async def _build_captain_toss_info(
    page: Page,
    update_info: MatchUpdateInfo,
    captain_names: dict[str, str],
) -> CaptainTossInfo:
    team1, team2 = update_info.team1, update_info.team2
    captains: dict[str, CaptainInfo] = {}
    for team in (team1, team2):
        display_name = captain_names[team]
        image_path = await fetch_captain_headshot(page, display_name, team)
        captains[team] = CaptainInfo(team=team, name=display_name, image_path=image_path)
    return CaptainTossInfo(
        team1_captain=captains[team1],
        team2_captain=captains[team2],
    )


async def try_build_captain_toss_info(
    page: Page,
    match_url: str | None,
    update_info: MatchUpdateInfo,
    fmt: str,
) -> CaptainTossInfo | None:
    """Build captain toss metadata: Google captain names first, ESPN XI fallback."""
    team1, team2 = update_info.team1, update_info.team2
    if not team1 or not team2:
        return None

    squads: dict[str, list[PlayingXiPlayer]] = {}
    captain_names: dict[str, str] = {}

    for team in (team1, team2):
        name = await lookup_captain_name_via_google(page, team, fmt)
        if name:
            captain_names[team] = name

    missing = [team for team in (team1, team2) if team not in captain_names]
    if missing and match_url:
        from main import fetch_playing_xi  # lazy import avoids circular dependency

        squads = await fetch_playing_xi(page, match_url, team1, team2)
        for team in missing:
            name = _resolve_captain_name_from_espn(team, squads)
            if name:
                captain_names[team] = name

    for team in (team1, team2):
        if team not in captain_names:
            logger.info("Captain toss skip: no captain name for %s after Google+ESPN", team)
            return None

    return await _build_captain_toss_info(page, update_info, captain_names)
