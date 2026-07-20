"""Captain headshot fetch (Google Images) and toss graphic orchestration."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote_plus

import requests
from playwright.async_api import Page

from match_image import CaptainInfo, CaptainTossInfo, MatchUpdateInfo
from playing_xi import captain_display_name, captains_from_squads

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
CAPTAIN_CACHE_DIR = _BASE_DIR / "assets" / "captain_cache"
GENERATED_IMAGES_DIR = _BASE_DIR / "generated_images"


def _player_cache_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:80] or "captain"


def _cached_headshot_path(name: str) -> Path:
    return CAPTAIN_CACHE_DIR / f"{_player_cache_slug(name)}.jpg"


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


async def try_build_captain_toss_info(
    page: Page,
    match_url: str,
    update_info: MatchUpdateInfo,
) -> CaptainTossInfo | None:
    """Build captain toss metadata from ESPN squads and Google headshots."""
    if not match_url:
        logger.info("Captain toss skip: no ESPN match URL")
        return None

    from main import fetch_playing_xi  # lazy import avoids circular dependency

    team1, team2 = update_info.team1, update_info.team2
    squads = await fetch_playing_xi(page, match_url, team1, team2)
    captain_players = captains_from_squads(squads, team1, team2)
    if team1 not in captain_players or team2 not in captain_players:
        logger.info(
            "Captain toss skip: missing captain in squads (%s, %s)",
            team1 in captain_players,
            team2 in captain_players,
        )
        return None

    captains: dict[str, CaptainInfo] = {}
    for team in (team1, team2):
        player = captain_players[team]
        display_name = captain_display_name(player)
        image_path = await fetch_captain_headshot(page, display_name, team)
        captains[team] = CaptainInfo(team=team, name=display_name, image_path=image_path)

    return CaptainTossInfo(
        team1_captain=captains[team1],
        team2_captain=captains[team2],
    )
