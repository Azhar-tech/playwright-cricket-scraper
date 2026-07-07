"""

Cricket Live Score Facebook Agent



Setup:

    pip install -r requirements.txt

    playwright install chromium



Run:
    python main.py

Optional .env:
    HEADLESS=false              # visible browser — helps bypass Google CAPTCHA
    BROWSER_PROFILE_DIR=.browser_profile   # save cookies after manual CAPTCHA solve

Test each component:

    python test.py

    python test.py --skip-scrape     # test Gemini/Facebook without scraping

    python test.py --post            # also send a test post to Facebook

"""



from __future__ import annotations



import asyncio

import json

import logging

import os

import random

import re

import sys

import traceback

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from pathlib import Path

from typing import Optional

from urllib.parse import urlparse



import requests

from dotenv import load_dotenv

from google import genai

from google.genai import types

from playwright.async_api import (

    Browser,

    BrowserContext,

    Page,

    TimeoutError as PlaywrightTimeoutError,

    async_playwright,

)

from playwright_stealth import Stealth

from match_image import (
    MatchUpdateInfo,
    ScorecardInfo,
    build_preview_caption,
    build_scorecard_caption,
    build_update_caption,
    generate_match_image,
    generate_preview_image,
    generate_scorecard_image,
    make_live_signature,
    parse_innings_scorecard_text,
    parse_match_block,
    parse_match_date_from_block,
    parse_preview_block,
)
from playing_xi import (
    PlayingXiPlayer,
    build_playing_xi_caption,
    build_playing_xi_info,
    generate_playing_xi_image,
    make_playing_xi_key,
    match_playing_xi_urls,
    parse_playing_xi_from_html,
)
from post_builder import build_match_post



_BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = _BASE_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH)
else:
    load_dotenv()



logging.basicConfig(

    level=logging.INFO,

    format="%(asctime)s [%(levelname)s] %(message)s",

    datefmt="%Y-%m-%d %H:%M:%S",

)

logger = logging.getLogger(__name__)



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



FORMAT_INTERVALS = {"T20": 1800, "ODI": 3600, "TEST": 1800}
POST_INTERVAL = 1800  # 30 minutes between scrape cycles
INTER_MATCH_POST_DELAY = 60  # 1 minute between posts in the same cycle
FACEBOOK_BG_CHAR_LIMIT = 130

FACEBOOK_BG_PRESETS = [
    "1881421442117417",  # Solid black
    "106018623298955",  # Solid purple
    "1365883126823705",  # Solid blue
    "219266485227663",  # Solid magenta
    "204187940028597",  # Solid red
    "1289741387813798",  # Solid dark red
    "145893972683590",  # Solid dark purple
    "301029513638534",  # Solid teal
    "249307305544279",  # Red/blue gradient
    "1777259169190672",  # Purple/magenta gradient
    "446330032368780",  # Red gradient
    "122708641613922",  # Dark grey/black gradient
]

UNTRACKED_INTERVAL = 300

ERROR_RETRY_INTERVAL = 300



NAVIGATION_TIMEOUT_MS = 30_000

WIDGET_WAIT_TIMEOUT_MS = 20_000



USER_AGENT = (

    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "

    "AppleWebKit/537.36 (KHTML, like Gecko) "

    "Chrome/131.0.0.0 Safari/537.36"

)

VIEWPORT = {"width": 1920, "height": 1080}

STEALTH_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]



WEBDRIVER_OVERRIDE_SCRIPT = """

Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

"""



SYSTEM_PROMPT = (
    "You are a cricket sports updater. Take this raw live score data and generate a short "
    "Facebook post. Use the correct format for the situation:\n"
    "- Live scores: 'PAK vs IND, 2nd T20 - PAK 41/1 after 6 overs. Babar on 23(12), Rizwan on 19(13).'\n"
    "- Toss: 'PAK vs IND, 2nd T20 - IND won the toss and elected to bat first.'\n"
    "- Match ended: ALWAYS include BOTH teams' final scores (runs/wickets and overs) from the data, "
    "then the result. Example: 'WI Women vs AUS Women, 1st SF - WI 145/10 (48.3 ov), AUS 149/2 (35.1 ov). "
    "AUS won by 8 wickets.' If the winner chased, show their score with overs used and wickets lost. "
    "For Tests, include both innings totals when available.\n"
    "Only write about international matches involving the national teams in the provided data. "
    "Never post about domestic, provincial, or franchise teams. "
    "The post must be 130 characters or fewer (Facebook background limit). One line only, no hashtags. "
    "Use short team abbreviations and 'ov' for overs to save space. "
    "Keep it brief, accurate to the provided data, and do not use generic AI filler."
)

PREVIEW_SYSTEM_PROMPT = (
    "You are a cricket page admin. Write a Facebook preview post for a match happening TODAY.\n"
    "Line 1: announce teams, match number, format, start time (if in data), and series name. "
    "Example: 'Today! India vs England, 1st T20I at 7:00 PM — India tour of England 2026.'\n"
    "Then add 6-10 hashtags on the next lines, like:\n"
    "#INDvsENG #ENGvIND #T20I #IndiaTourOfEngland #TeamIndia #ENG #CricketUpdates\n"
    "Include: team-vs-team tag, tournament/series tag, format tag, both team tags, and #CricketUpdates.\n"
    "Only use international national teams from the provided data. No generic AI filler."
)

PHASE_POST_HINTS = {
    "result": (
        "FINISHED MATCH — include both teams' final scores (runs/wickets and overs) from the raw data, "
        "then who won and how. Do NOT post only 'Team X won by Y wickets/runs' without the scores."
    ),
    "live": (
        "LIVE MATCH — include current score, overs bowled, and key batters if available in the data."
    ),
    "toss": (
        "TOSS UPDATE — report which team won the toss and what they chose (bat or bowl)."
    ),
    "preview": (
        "TODAY'S MATCH PREVIEW — this match has not started yet. Announce it is on today with "
        "teams, format, match number, start time if available, and series. Add relevant hashtags."
    ),
}

DATA_DIR = Path(os.getenv("DATA_DIR", str(_BASE_DIR)))
POST_STATE_PATH = DATA_DIR / ".post_state.json"
LEGACY_PREVIEW_STORE_PATH = _BASE_DIR / ".posted_previews.json"
PHASE_SORT_ORDER = {"live": 0, "toss": 1, "preview": 2, "tomorrow": 2, "result": 3}

SCORE_PATTERN = re.compile(r"\d+/\d+")
FORMAT_PATTERN = re.compile(r"\b(T20I?|ODI|One\s*Day|Test)\b", re.IGNORECASE)
ESPN_MATCH_START = re.compile(
    r"^(LIVE|RESULT|NOT COVERED LIVE|TODAY,|TOMORROW,)",
    re.IGNORECASE,
)
CRICKET_IRELAND_ORG = re.compile(r"cricket\s+ireland", re.IGNORECASE)
TOSS_PATTERN = re.compile(
    r"won the toss|elected to (?:bat|field|bowl)|chose to (?:bat|field|bowl)|opt(?:ed)? to (?:bat|field|bowl)",
    re.IGNORECASE,
)
UPCOMING_PATTERN = re.compile(
    r"match yet to begin|match starts in|starts in \d|\d+ hr \d+ min|\d+ mins?\)",
    re.IGNORECASE,
)



@dataclass
class PostState:
    preview_posted: set[str] = field(default_factory=set)
    result_posted: set[str] = field(default_factory=set)
    toss_posted: set[str] = field(default_factory=set)
    innings_break_posted: set[str] = field(default_factory=set)
    playing_xi_posted: set[str] = field(default_factory=set)
    test_session_posted: set[str] = field(default_factory=set)
    scorecard_innings_posted: set[str] = field(default_factory=set)
    live_last: dict[str, dict[str, str]] = field(default_factory=dict)


_post_state = PostState()

_genai_client: Optional[genai.Client] = None


def prune_old_preview_keys(keys: set[str]) -> set[str]:
    today = date.today()
    kept: set[str] = set()
    for key in keys:
        date_part = key.split("|", 1)[0]
        try:
            if (today - date.fromisoformat(date_part)).days <= 7:
                kept.add(key)
        except ValueError:
            kept.add(key)
    return kept


def load_post_state() -> PostState:
    state = PostState()
    if POST_STATE_PATH.exists():
        try:
            data = json.loads(POST_STATE_PATH.read_text(encoding="utf-8"))
            state.preview_posted = prune_old_preview_keys(set(data.get("preview_posted", [])))
            state.result_posted = set(data.get("result_posted", []))
            state.toss_posted = set(data.get("toss_posted", []))
            state.innings_break_posted = set(data.get("innings_break_posted", []))
            state.playing_xi_posted = set(data.get("playing_xi_posted", []))
            state.test_session_posted = set(data.get("test_session_posted", []))
            state.scorecard_innings_posted = set(data.get("scorecard_innings_posted", []))
            state.live_last = dict(data.get("live_last", {}))
            return state
        except (json.JSONDecodeError, OSError, TypeError):
            logger.warning("Could not load post state; starting fresh")

    if LEGACY_PREVIEW_STORE_PATH.exists():
        try:
            data = json.loads(LEGACY_PREVIEW_STORE_PATH.read_text(encoding="utf-8"))
            keys = set(data) if isinstance(data, list) else set()
            state.preview_posted = prune_old_preview_keys(keys)
        except (json.JSONDecodeError, OSError):
            pass
    return state


def save_post_state(state: PostState) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "preview_posted": sorted(state.preview_posted),
            "result_posted": sorted(state.result_posted),
            "toss_posted": sorted(state.toss_posted),
            "innings_break_posted": sorted(state.innings_break_posted),
            "playing_xi_posted": sorted(state.playing_xi_posted),
            "test_session_posted": sorted(state.test_session_posted),
            "scorecard_innings_posted": sorted(state.scorecard_innings_posted),
            "live_last": state.live_last,
        }
        POST_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not save post state: %s", exc)





def validate_env() -> dict[str, str]:
    if ENV_PATH.exists():
        logger.info("Loaded .env from %s", ENV_PATH)
    else:
        logger.info("Using environment variables (no .env file)")

    required = [
        "TARGET_MATCH_URL",
        "FACEBOOK_PAGE_ID",
        "FACEBOOK_ACCESS_TOKEN",
    ]
    config = {key: os.getenv(key, "").strip() for key in required}
    missing = [key for key, value in config.items() if not value]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        if ENV_PATH.exists():
            logger.error("Check variables in %s or set them in your deployment dashboard.", ENV_PATH)
        else:
            logger.error("Set them in your deployment dashboard or create a local .env file.")
        sys.exit(1)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        config["GEMINI_API_KEY"] = gemini_key
    else:
        logger.info("GEMINI_API_KEY not set; rule-based posting will be used (Gemini optional)")

    display_tz = os.getenv("DISPLAY_TIMEZONE", "Asia/Karachi").strip() or "Asia/Karachi"
    logger.info("Preview display timezone: %s", display_tz)

    url = config["TARGET_MATCH_URL"]

    if "..." in url or "your target match link" in url.lower():

        logger.error(

            "TARGET_MATCH_URL is still a placeholder. Set a real URL, e.g. "

            "https://www.espncricinfo.com/live-cricket-score"

        )

        sys.exit(1)



    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:

        logger.error("TARGET_MATCH_URL must be a valid http(s) URL")

        sys.exit(1)



    return config





def get_genai_client() -> genai.Client:

    global _genai_client

    if _genai_client is None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set; Gemini post generation is unavailable")
        _genai_client = genai.Client(api_key=api_key)

    return _genai_client





def is_headless() -> bool:
    return os.getenv("HEADLESS", "true").strip().lower() not in ("0", "false", "no", "off")


async def launch_stealth_browser(playwright) -> Browser:
    headless = is_headless()
    logger.info("Launching Chromium (headless=%s)", headless)
    return await playwright.chromium.launch(headless=headless, args=STEALTH_LAUNCH_ARGS)


async def open_stealth_session(playwright) -> tuple[Browser | None, BrowserContext | None]:
    """Return (browser, context). Exactly one will be set."""
    profile_dir = os.getenv("BROWSER_PROFILE_DIR", "").strip()
    headless = is_headless()

    if profile_dir:
        profile_path = Path(profile_dir)
        profile_path.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Using persistent browser profile at %s (headless=%s)",
            profile_path.resolve(),
            headless,
        )
        context = await playwright.chromium.launch_persistent_context(
            str(profile_path),
            headless=headless,
            viewport=VIEWPORT,
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="Asia/Karachi",
            args=STEALTH_LAUNCH_ARGS,
        )
        await context.add_init_script(WEBDRIVER_OVERRIDE_SCRIPT)
        return None, context

    return await launch_stealth_browser(playwright), None





async def new_stealth_page(browser: Browser) -> tuple[Page, BrowserContext]:

    context = await browser.new_context(

        viewport=VIEWPORT,

        user_agent=USER_AGENT,

        locale="en-US",

        timezone_id="Asia/Karachi",

    )

    await context.add_init_script(WEBDRIVER_OVERRIDE_SCRIPT)

    page = await context.new_page()

    return page, context





async def scrape_match(page: Page, match_url: str) -> tuple[str | None, dict[str, str]]:

    try:

        await page.goto(

            match_url,

            wait_until="domcontentloaded",

            timeout=NAVIGATION_TIMEOUT_MS,

        )

        await page.wait_for_load_state("networkidle", timeout=WIDGET_WAIT_TIMEOUT_MS)

    except PlaywrightTimeoutError:

        logger.warning("Navigation or network idle timed out; continuing with partial load")



    if "google.com/sorry" in page.url:
        logger.warning("Google CAPTCHA detected — bot blocked at %s", page.url)
        return None, {}

    if "access denied" in (await page.title()).lower():
        logger.warning("Access denied by site — try HEADLESS=false or stealth settings")
        return None, {}

    await _dismiss_cookie_banner(page)



    delay = random.uniform(2, 5)

    logger.info("Waiting %.1fs before extracting scores (human-like delay)", delay)

    await asyncio.sleep(delay)



    try:

        await page.wait_for_function(

            """() => {

                const body = document.body?.innerText || '';

                return /\\d+\\/\\d+/.test(body) || /\\d+(\\.\\d+)?\\s*overs?/i.test(body);

            }""",

            timeout=WIDGET_WAIT_TIMEOUT_MS,

        )

    except PlaywrightTimeoutError:

        logger.warning("Score widget did not appear within timeout")

        title = await page.title()

        logger.info("Page title: %s | URL: %s", title, page.url)

        return None, {}



    raw_text = await _extract_scorecard_text(page, match_url)

    if not raw_text or not SCORE_PATTERN.search(raw_text):

        logger.warning("Scraped text missing score data")

        logger.debug("Partial text: %s", raw_text[:500] if raw_text else "(empty)")

        return None, {}



    match_links: dict[str, str] = {}
    if "espncricinfo.com" in match_url.lower():
        match_links = await extract_espn_match_links(page)

    return raw_text, match_links





async def _dismiss_cookie_banner(page: Page) -> None:
    for label in ("Not Now", "Accept all", "Accept All", "I agree", "Agree"):

        try:

            button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))

            if await button.count() > 0:

                await button.first.click(timeout=3000)

                await asyncio.sleep(0.5)

                return

        except PlaywrightTimeoutError:

            continue





async def _extract_scorecard_text(page: Page, match_url: str) -> str:
    chunks: list[str] = []
    url_lower = match_url.lower()

    if "google." in url_lower:
        selectors = [
            "[data-attrid*='sports']",
            "[data-attrid*='game']",
            "[data-attrid='kc:/sports/game:live_game']",
            "div[data-sokoban-container]",
            "#search",
            "main",
        ]
        stop_markers = (
            "WATCH HIGHLIGHTS",
            "HIGHLIGHTS",
            "RELATED VIDEOS",
            "PEOPLE ALSO ASK",
            "MORE RESULTS",
            "TOP STORIES",
        )
    elif "espncricinfo.com" in url_lower:
        selectors = [
            "main",
            "[class*='live-score']",
            "[class*='LiveScore']",
            "[class*='match']",
            "article",
        ]
        stop_markers = (
            "TERMS OF USE",
            "PRIVACY POLICY",
            "FOLLOW US ON",
            "© ESPN",
        )
    else:
        selectors = [
            "main",
            "[class*='scorecard']",
            "[class*='Scorecard']",
            "[class*='match-center']",
            "[class*='MatchCenter']",
            "[data-testid*='score']",
            "article",
        ]
        stop_markers = ("WATCH HIGHLIGHTS", "HIGHLIGHTS", "RELATED VIDEOS")



    for selector in selectors:

        try:

            locator = page.locator(selector).first

            if await locator.count() == 0:

                continue

            text = await locator.inner_text(timeout=5000)

            if text and SCORE_PATTERN.search(text):

                chunks.append(text.strip())

        except PlaywrightTimeoutError:

            continue



    if not chunks:

        try:

            body_text = await page.locator("body").inner_text(timeout=5000)

            chunks.append(body_text.strip())

        except PlaywrightTimeoutError:

            return ""



    combined = "\n\n".join(dict.fromkeys(chunks))
    lines = [line.strip() for line in combined.splitlines() if line.strip()]

    if "espncricinfo.com" in url_lower:
        for i, line in enumerate(lines):
            if line.lower() == "live cricket matches":
                lines = lines[i:]
                break

    trimmed: list[str] = []
    for line in lines:
        if line.upper() in stop_markers:
            break
        trimmed.append(line)

    return "\n".join(trimmed or lines)


async def extract_espn_match_links(page: Page) -> dict[str, str]:
    """Map make_match_key values to ESPN match-page URLs from the live-score page."""
    try:
        entries = await page.evaluate(
            """
            () => {
              const results = [];
              const seen = new Set();
              const seriesMatch = /\\/series\\/[^/]+\\/[^/]+-\\d+/i;
              for (const a of document.querySelectorAll('a[href]')) {
                const href = a.href;
                if (!href || seen.has(href)) continue;
                const isMatchLink =
                  href.includes('/game/') ||
                  href.includes('/full-scorecard') ||
                  href.includes('/match-playing-xi') ||
                  seriesMatch.test(href);
                if (!isMatchLink) continue;
                seen.add(href);
                let el = a.closest('article, li, section, div');
                for (let i = 0; i < 4 && el; i++) {
                  const text = (el.innerText || '').trim();
                  if (text.length > 20) break;
                  el = el.parentElement;
                }
                const text = el ? el.innerText : (a.innerText || '');
                results.push({ href, text: text.slice(0, 1200) });
              }
              return results;
            }
            """
        )
    except Exception as exc:
        logger.warning("Could not extract ESPN match links: %s", exc)
        return {}

    links: dict[str, str] = {}
    for entry in entries or []:
        href = str(entry.get("href", "")).strip()
        text = str(entry.get("text", "")).strip()
        if not href or not text or not block_has_tracked_team(text):
            continue
        href = re.sub(r"/match-playing-xi/?$", "", href, flags=re.IGNORECASE)
        href = re.sub(r"/full-scorecard/?$", "", href, flags=re.IGNORECASE)
        key = make_match_key(text)
        if key not in links:
            links[key] = href
    return links


async def fetch_playing_xi(
    page: Page,
    match_url: str,
    team1: str,
    team2: str,
) -> dict[str, list[PlayingXiPlayer]]:
    if not match_url:
        return {}

    for url in match_playing_xi_urls(match_url):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
            await page.wait_for_load_state("networkidle", timeout=WIDGET_WAIT_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            logger.warning("Playing XI page load timed out: %s", url)
            continue

        await _dismiss_cookie_banner(page)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        try:
            html = await page.content()
            body_text = await page.locator("body").inner_text(timeout=8000)
        except PlaywrightTimeoutError:
            logger.warning("Could not read Playing XI page content: %s", url)
            continue

        squads = parse_playing_xi_from_html(html, body_text, team1, team2)
        complete = {team: players for team, players in squads.items() if len(players) >= 11}
        if complete:
            logger.info("Fetched Playing XI from %s", url)
            return complete

    return {}


async def fetch_innings_scorecard(
    page: Page,
    match_url: str,
    info: MatchUpdateInfo,
) -> ScorecardInfo | None:
    """Navigate to the ESPN full-scorecard page and parse the completed innings."""
    if not match_url:
        return None

    sc_url = match_url.rstrip("/") + "/full-scorecard"
    try:
        await page.goto(sc_url, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        await page.wait_for_load_state("networkidle", timeout=WIDGET_WAIT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning("Scorecard page load timed out: %s", sc_url)
        return None

    await _dismiss_cookie_banner(page)
    await asyncio.sleep(random.uniform(1.5, 3.0))

    try:
        body_text = await page.locator("body").inner_text(timeout=10000)
    except PlaywrightTimeoutError:
        logger.warning("Could not read scorecard page body: %s", sc_url)
        return None

    score = info.score1 if info.batting_team == info.team1 else info.score2
    overs_raw = info.overs1 if info.batting_team == info.team1 else info.overs2
    overs = overs_raw.strip("()") if overs_raw else ""

    return parse_innings_scorecard_text(
        body_text=body_text,
        batting_team=info.batting_team,
        team1=info.team1,
        team2=info.team2,
        score=score,
        overs=overs,
        match_label=info.match_label,
        series=info.series,
        format_tag=info.format_tag,
    )


def _resolve_match_url(match_key: str, match_links: dict[str, str]) -> str:
    if match_key in match_links:
        return match_links[match_key]
    key_teams = set(match_key.split("|")[0].split("-"))
    for key, url in match_links.items():
        link_teams = set(key.split("|")[0].split("-"))
        if key_teams == link_teams:
            return url
    return ""


def _playing_xi_pending(match_key: str, team1: str, team2: str, state: PostState) -> bool:
    for team in (team1, team2):
        if make_playing_xi_key(match_key, team) not in state.playing_xi_posted:
            return True
    return False


def _toss_announced(block: str) -> bool:
    return bool(TOSS_PATTERN.search(block))


async def _publish_toss_update(
    block: str,
    match_key: str,
    config: dict[str, str],
    state: PostState,
) -> bool:
    try:
        update_info = parse_match_block(block, "toss")
        if not update_info.match_key:
            update_info.match_key = match_key
        image_path = generate_match_image(update_info)
        post_text = build_update_caption(update_info)
    except Exception as exc:
        logger.error("Toss image generation failed for %s: %s", match_key, exc)
        post_text = build_match_post(block, phase="toss")
        if not post_text:
            return False
        image_path = None

    if image_path is not None:
        published = publish_photo_to_facebook(
            post_text,
            image_path,
            config["FACEBOOK_PAGE_ID"],
            config["FACEBOOK_ACCESS_TOKEN"],
        )
        if published:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        published = publish_to_facebook(
            post_text,
            config["FACEBOOK_PAGE_ID"],
            config["FACEBOOK_ACCESS_TOKEN"],
            use_background=True,
        )

    if not published:
        if image_path is not None:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False

    record_post_state(block, "toss", post_text, state)
    logger.info("Posted (toss): %s", post_text[:120])
    return True


async def post_playing_xi_if_ready(
    block: str,
    match_links: dict[str, str],
    page: Page,
    config: dict[str, str],
    state: PostState,
    *,
    posted_count: int = 0,
) -> int:
    match_key = make_match_key(block)
    if match_key not in state.toss_posted and not _toss_announced(block):
        phase = block_match_phase(block)
        if phase not in ("live", "result", "toss"):
            return posted_count

    try:
        match_info = parse_match_block(block, "toss")
    except Exception:
        return posted_count

    team1, team2 = match_info.team1, match_info.team2
    if not team1 or not team2:
        return posted_count

    if not _playing_xi_pending(match_key, team1, team2, state):
        return posted_count

    match_url = _resolve_match_url(match_key, match_links)
    if not match_url:
        logger.info("No ESPN match URL for %s; cannot fetch Playing XI yet", match_key)
        return posted_count

    logger.info("Fetching Playing XI from %s", match_url)
    squads = await fetch_playing_xi(page, match_url, team1, team2)
    if not squads:
        logger.info("Playing XI not available yet for %s", match_key)
        return posted_count

    for team in (team1, team2):
        xi_key = make_playing_xi_key(match_key, team)
        if xi_key in state.playing_xi_posted:
            continue
        players = squads.get(team)
        if not players or len(players) < 11:
            logger.info("Incomplete Playing XI for %s (%d players)", team, len(players or []))
            continue

        opponent = team2 if team == team1 else team1
        info = build_playing_xi_info(team, opponent, players, block, match_key)
        try:
            image_path = generate_playing_xi_image(info)
            caption = build_playing_xi_caption(info)
        except Exception as exc:
            logger.error("Playing XI image failed for %s: %s", team, exc)
            continue

        if posted_count > 0:
            logger.info("Waiting %ds before Playing XI post", INTER_MATCH_POST_DELAY)
            await asyncio.sleep(INTER_MATCH_POST_DELAY)

        published = publish_photo_to_facebook(
            caption,
            image_path,
            config["FACEBOOK_PAGE_ID"],
            config["FACEBOOK_ACCESS_TOKEN"],
        )
        if published:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass
            state.playing_xi_posted.add(xi_key)
            save_post_state(state)
            posted_count += 1
            logger.info("Posted (playing_xi): %s", caption[:120])
        else:
            try:
                image_path.unlink(missing_ok=True)
            except OSError:
                pass

    return posted_count


async def post_missed_toss_and_playing_xi(
    raw_data: str,
    match_links: dict[str, str],
    page: Page,
    config: dict[str, str],
    state: PostState,
    *,
    posted_count: int = 0,
    xi_attempted: set[str] | None = None,
) -> int:
    attempted = xi_attempted if xi_attempted is not None else set()
    blocks_by_key: dict[str, list[str]] = {}
    for block in normalize_match_blocks(raw_data):
        if not block_has_tracked_team(block):
            continue
        blocks_by_key.setdefault(make_match_key(block), []).append(block)

    for match_key, blocks in blocks_by_key.items():
        if match_key in attempted:
            continue

        toss_block = next((block for block in blocks if _toss_announced(block)), None)
        xi_block = toss_block or blocks[0]
        toss_done = match_key in state.toss_posted
        toss_visible = toss_block is not None
        team1, team2 = _teams_from_block_names(xi_block)
        xi_pending = _playing_xi_pending(match_key, team1, team2, state)

        if not toss_done and not toss_visible and not xi_pending:
            continue

        if not toss_done and toss_visible and toss_block:
            if posted_count > 0:
                logger.info("Waiting %ds before missed toss post", INTER_MATCH_POST_DELAY)
                await asyncio.sleep(INTER_MATCH_POST_DELAY)
            if await _publish_toss_update(toss_block, match_key, config, state):
                posted_count += 1
            elif xi_pending:
                logger.info(
                    "Missed toss post failed for %s; will still try Playing XI if available",
                    match_key,
                )
            else:
                continue

        if toss_done or toss_visible or xi_pending:
            attempted.add(match_key)
            if posted_count > 0 and _playing_xi_pending(match_key, team1, team2, state):
                logger.info("Waiting %ds before Playing XI post", INTER_MATCH_POST_DELAY)
                await asyncio.sleep(INTER_MATCH_POST_DELAY)
            posted_count = await post_playing_xi_if_ready(
                xi_block,
                match_links,
                page,
                config,
                state,
                posted_count=posted_count,
            )

    return posted_count


def _teams_from_block_names(block: str) -> tuple[str, str]:
    teams = sorted(
        {_normalize_team_name(line) for line in block.splitlines() if _line_is_tracked_team(line)}
    )
    if len(teams) < 2:
        return "TBD", "TBD"
    return teams[0], teams[1]


def _normalize_team_name(line: str) -> str:
    for team in TRACKED_TEAMS:
        if re.fullmatch(rf"{re.escape(team)}(\s+Women)?", line.strip(), re.IGNORECASE):
            if "women" in line.lower():
                return f"{team} Women"
            return team
    return line.strip()


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


def split_espn_match_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if ESPN_MATCH_START.match(line.strip()):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def block_has_tracked_team(block: str) -> bool:
    return any(_line_is_tracked_team(line) for line in block.splitlines())


def split_preview_fixture_blocks(text: str) -> list[str]:
    """Extract upcoming fixtures that lack LIVE/RESULT/TODAY headers."""
    lines = [line.strip() for line in text.splitlines()]
    blocks: list[str] = []
    for i, line in enumerate(lines):
        if not line or not UPCOMING_PATTERN.search(line):
            continue
        chunk: list[str] = []
        for j in range(i, max(-1, i - 12), -1):
            prev = lines[j]
            if j < i and (
                ESPN_MATCH_START.match(prev)
                or re.search(r"\bwon by\b", prev, re.IGNORECASE)
                or SCORE_PATTERN.search(prev)
            ):
                break
            if prev:
                chunk.insert(0, prev)
        for k in range(i + 1, min(len(lines), i + 5)):
            nxt = lines[k]
            if not nxt:
                break
            if ESPN_MATCH_START.match(nxt):
                break
            if SCORE_PATTERN.search(nxt) or re.search(r"\bwon by\b", nxt, re.IGNORECASE):
                break
            if k > i + 1 and _line_is_tracked_team(nxt):
                break
            chunk.append(nxt)
        block = "\n".join(chunk)
        if block_has_tracked_team(block):
            blocks.append(block)
    return blocks


def split_mixed_block(block: str) -> list[str]:
    """Split a block that contains both a finished result and an upcoming fixture."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    preview_idx = next(
        (i for i, line in enumerate(lines) if UPCOMING_PATTERN.search(line)),
        None,
    )
    if preview_idx is None or not re.search(r"\bwon by\b", block, re.IGNORECASE):
        return [block]

    start = preview_idx
    for j in range(preview_idx - 1, -1, -1):
        if re.search(r"\bwon by\b", lines[j], re.IGNORECASE) or SCORE_PATTERN.search(lines[j]):
            start = j + 1
            break
        start = j

    parts: list[str] = []
    result_part = "\n".join(lines[:start]).strip()
    preview_part = "\n".join(lines[start:]).strip()
    if result_part:
        parts.append(result_part)
    if preview_part:
        parts.append(preview_part)
    return parts or [block]


def normalize_match_blocks(text: str) -> list[str]:
    blocks = split_espn_match_blocks(text)
    normalized: list[str] = []
    for block in blocks:
        subs = split_espn_match_blocks(block)
        for sub in (subs if len(subs) > 1 else [block]):
            normalized.extend(split_mixed_block(sub))
    if not normalized:
        normalized = split_mixed_block(text)
    seen = set(normalized)
    for preview_block in split_preview_fixture_blocks(text):
        if preview_block not in seen:
            normalized.append(preview_block)
            seen.add(preview_block)
    return normalized


def block_match_phase(block: str, state: PostState | None = None) -> str:
    """Classify block as live, result, toss, preview, tomorrow, or other."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    first_line = lines[0] if lines else ""
    first_upper = first_line.upper()
    match_key = make_match_key(block)
    test_in_progress = (
        state is not None
        and detect_format(block) == "TEST"
        and _test_match_in_progress(match_key, block, state)
    )

    if first_upper == "RESULT" or re.search(r"\bwon by\b", block, re.IGNORECASE):
        return "result"
    if first_upper.startswith("TODAY,"):
        if test_in_progress:
            return "live" if SCORE_PATTERN.search(block) else "other"
        return "preview"
    if any(line.upper().startswith("TOMORROW,") for line in lines):
        if test_in_progress:
            return "live" if SCORE_PATTERN.search(block) else "other"
        return "tomorrow"

    match_date = parse_match_date_from_block(block)
    tomorrow = date.today() + timedelta(days=1)
    if match_date == tomorrow:
        if test_in_progress:
            return "live" if SCORE_PATTERN.search(block) else "other"
        return "tomorrow"

    if UPCOMING_PATTERN.search(block) or "match yet to begin" in block.lower():
        if test_in_progress:
            return "live" if SCORE_PATTERN.search(block) else "other"
        if match_date == date.today():
            return "preview"
        if match_date == tomorrow:
            return "tomorrow"
        return "preview"

    if first_upper == "LIVE" or (
        SCORE_PATTERN.search(block)
        and "won by" not in block.lower()
        and not UPCOMING_PATTERN.search(block)
        and "match yet to begin" not in block.lower()
    ):
        return "live"

    if TOSS_PATTERN.search(block):
        return "toss"

    return "other"


def is_postable_block(block: str, state: PostState | None = None) -> bool:
    """Tracked national-team fixtures: live, finished, toss, or today's preview."""
    if not block_has_tracked_team(block):
        return False
    if "NOT COVERED LIVE" in block.upper():
        return False

    phase = block_match_phase(block, state=state)
    if phase in ("live", "result", "toss", "preview", "tomorrow"):
        return True
    return False


def extract_all_postable_blocks(text: str, state: PostState | None = None) -> list[tuple[str, str]]:
    """Return all postable (block, phase) pairs, deduped by match key."""
    blocks = normalize_match_blocks(text)
    by_key: dict[str, tuple[str, str]] = {}
    for block in blocks:
        if not is_postable_block(block, state=state):
            continue
        phase = block_match_phase(block, state=state)
        key = make_match_key(block)
        existing = by_key.get(key)
        if existing is None or PHASE_SORT_ORDER[phase] < PHASE_SORT_ORDER[existing[1]]:
            by_key[key] = (block, phase)

    candidates = list(by_key.values())
    candidates.sort(key=lambda item: PHASE_SORT_ORDER.get(item[1], 99))
    return candidates


def extract_tracked_match_data(text: str) -> Optional[str]:
    """Return all postable match blocks joined (backward compatible for tests)."""
    candidates = extract_all_postable_blocks(text)
    if not candidates:
        return None
    return "\n\n---\n\n".join(block for block, _ in candidates)


def is_tracked_match(text: str) -> bool:
    return extract_tracked_match_data(text) is not None





def detect_format(text: str) -> str:

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




def _match_label_from_block(block: str) -> str:
    for line in block.splitlines():
        stripped = line.strip()
        if re.search(r"\d+(?:st|nd|rd|th)\s+(?:T20|ODI|Test|One Day)", stripped, re.IGNORECASE):
            return stripped
    return ""


def _teams_from_block(block: str) -> list[str]:
    return sorted({line.lower() for line in block.splitlines() if _line_is_tracked_team(line)})


def make_match_key(block: str) -> str:
    teams = _teams_from_block(block)
    fmt = detect_format(block)
    match_label = _match_label_from_block(block)
    return f"{'-'.join(teams)}|{fmt}|{match_label}"


def make_preview_key(block: str) -> str:
    if detect_format(block) == "TEST":
        return make_match_key(block)
    return f"{date.today().isoformat()}|{make_match_key(block)}"


def make_test_session_key(match_key: str, test_day: int, session_break: str) -> str:
    return f"{match_key}|d{test_day or 1}|{session_break}"


def _test_match_in_progress(match_key: str, block: str, state: PostState) -> bool:
    if match_key in state.toss_posted:
        return True
    if match_key in state.live_last:
        return True
    if any(key.startswith(f"{match_key}|d") for key in state.test_session_posted):
        return True
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    first_upper = lines[0].upper() if lines else ""
    if first_upper == "LIVE" and SCORE_PATTERN.search(block):
        return True
    if (
        SCORE_PATTERN.search(block)
        and not UPCOMING_PATTERN.search(block)
        and "match yet to begin" not in block.lower()
        and "won by" not in block.lower()
    ):
        return True
    return False


def should_post_one_shot(block: str, phase: str, state: PostState) -> bool:
    if phase in ("preview", "tomorrow"):
        if detect_format(block) == "TEST" and _test_match_in_progress(make_match_key(block), block, state):
            return False
        return make_preview_key(block) not in state.preview_posted
    if phase == "result":
        return make_match_key(block) not in state.result_posted
    if phase == "toss":
        return make_match_key(block) not in state.toss_posted
    return True


def should_post_live(
    block: str,
    signature: str,
    state: PostState,
    *,
    innings_break: bool = False,
    session_break: str = "",
    test_day: int = 0,
    fmt: str = "",
) -> bool:
    key = make_match_key(block)
    match_fmt = fmt or detect_format(block)

    if match_fmt == "TEST":
        if innings_break:
            if key in state.innings_break_posted:
                logger.info("Innings break already posted for %s; skipping", key)
                return False
            return True
        if session_break in ("lunch", "tea", "stumps"):
            session_key = make_test_session_key(key, test_day, session_break)
            if session_key in state.test_session_posted:
                logger.info("Test session %s already posted; skipping", session_key)
                return False
            return True
        logger.info("Test match %s — no session break (lunch/tea/stumps); skipping live post", key)
        return False

    if innings_break and key in state.innings_break_posted:
        logger.info("Innings break already posted for %s; skipping", key)
        return False

    last = state.live_last.get(key)
    if not last:
        return True

    if innings_break:
        return True

    try:
        last_at = datetime.fromisoformat(last["at"])
    except ValueError:
        return True

    elapsed = (datetime.now() - last_at).total_seconds()
    if elapsed < POST_INTERVAL:
        logger.info(
            "Live match %s on cooldown (%.0fs / %ds remaining)",
            key,
            elapsed,
            max(0, POST_INTERVAL - int(elapsed)),
        )
        return False
    if last.get("signature") == signature or last.get("text") == signature:
        logger.info("Live match %s unchanged since last post; skipping", key)
        return False
    return True


def record_post_state(
    block: str,
    phase: str,
    post_text: str,
    state: PostState,
    *,
    live_signature: str = "",
    innings_break: bool = False,
    session_break: str = "",
    test_day: int = 0,
) -> None:
    if phase in ("preview", "tomorrow"):
        state.preview_posted.add(make_preview_key(block))
    elif phase == "result":
        state.result_posted.add(make_match_key(block))
    elif phase == "toss":
        state.toss_posted.add(make_match_key(block))
    elif phase == "live":
        key = make_match_key(block)
        state.live_last[key] = {
            "at": datetime.now().isoformat(),
            "text": post_text,
            "signature": live_signature or post_text,
        }
        if innings_break:
            state.innings_break_posted.add(key)
        if session_break in ("lunch", "tea", "stumps"):
            state.test_session_posted.add(make_test_session_key(key, test_day, session_break))
    save_post_state(state)


def get_sleep_interval(text: str) -> int:
    for block in normalize_match_blocks(text):
        if block_has_tracked_team(block) and detect_format(block) == "TEST":
            if SCORE_PATTERN.search(block) and not UPCOMING_PATTERN.search(block):
                return FORMAT_INTERVALS["TEST"]
    return POST_INTERVAL





async def generate_post(raw_data: str, phase: str = "live") -> Optional[str]:

    try:

        client = get_genai_client()
        hint = PHASE_POST_HINTS.get(phase, "")
        contents = f"{hint}\n\nRaw match data:\n{raw_data}" if hint else raw_data
        system_instruction = PREVIEW_SYSTEM_PROMPT if phase == "preview" else SYSTEM_PROMPT

        response = await client.aio.models.generate_content(

            model="gemini-2.5-flash",

            contents=contents,

            config=types.GenerateContentConfig(

                system_instruction=system_instruction,

                temperature=0.2,

            ),

        )

        text = (response.text or "").strip()

        return text or None

    except Exception as exc:

        logger.error("Gemini API error: %s", exc)

        return None





def prepare_background_message(text: str) -> str:
    text = text.strip()
    if len(text) <= FACEBOOK_BG_CHAR_LIMIT:
        return text

    suffix = "..."
    limit = FACEBOOK_BG_CHAR_LIMIT - len(suffix)
    truncated = text[:limit]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + suffix


def publish_to_facebook(
    message: str,
    page_id: str,
    access_token: str,
    *,
    use_background: bool = True,
) -> bool:

    url = f"https://graph.facebook.com/v20.0/{page_id}/feed"

    if use_background:
        prepared = prepare_background_message(message)
        preset_id = random.choice(FACEBOOK_BG_PRESETS)
        payload = {
            "message": prepared,
            "access_token": access_token,
            "text_format_preset_id": preset_id,
        }
    else:
        prepared = message.strip()
        preset_id = None
        payload = {"message": prepared, "access_token": access_token}

    try:

        response = requests.post(

            url,

            data=payload,

            timeout=30,

        )

        if response.ok:

            if preset_id:
                logger.info(
                    "Published to Facebook (preset %s): %s",
                    preset_id,
                    response.json().get("id", "ok"),
                )
            else:
                logger.info(
                    "Published to Facebook (preview + hashtags): %s",
                    response.json().get("id", "ok"),
                )

            return True



        logger.error("Facebook API error (%s): %s", response.status_code, response.text)

        return False

    except requests.RequestException as exc:

        logger.error("Facebook request failed: %s", exc)

        return False


def publish_photo_to_facebook(
    caption: str,
    image_path: Path,
    page_id: str,
    access_token: str,
) -> bool:
    url = f"https://graph.facebook.com/v20.0/{page_id}/photos"
    try:
        with image_path.open("rb") as image_file:
            response = requests.post(
                url,
                data={"message": caption.strip(), "access_token": access_token},
                files={"source": image_file},
                timeout=60,
            )
        if response.ok:
            logger.info(
                "Published photo to Facebook: %s",
                response.json().get("id", "ok"),
            )
            return True
        logger.error("Facebook photo API error (%s): %s", response.status_code, response.text)
        return False
    except (OSError, requests.RequestException) as exc:
        logger.error("Facebook photo upload failed: %s", exc)
        return False


async def run_cycle(
    config: dict[str, str],
    browser: Browser | None = None,
    persistent_context: BrowserContext | None = None,
) -> int:

    global _post_state

    ephemeral_context: BrowserContext | None = None

    if persistent_context is not None:
        page = await persistent_context.new_page()
    else:
        page, ephemeral_context = await new_stealth_page(browser)  # type: ignore[arg-type]

    try:

        raw_data, match_links = await scrape_match(page, config["TARGET_MATCH_URL"])

        if not raw_data:

            return ERROR_RETRY_INTERVAL



        logger.info("Scraped match data (%d chars)", len(raw_data))
        if match_links:
            logger.info("Found %d ESPN match page link(s) for Playing XI", len(match_links))

        candidates = extract_all_postable_blocks(raw_data, state=_post_state)
        if not candidates:
            logger.info(
                "No live/result/toss/preview update for tracked teams; waiting %ds",
                UNTRACKED_INTERVAL,
            )
            return UNTRACKED_INTERVAL

        logger.info("Found %d tracked fixture(s)", len(candidates))
        posted_count = 0
        xi_attempted: set[str] = set()

        for block, phase in candidates:
            match_key = make_match_key(block)
            logger.info("Evaluating %s fixture (%s)", phase, match_key)

            if phase != "live" and not should_post_one_shot(block, phase, _post_state):
                logger.info("Skipping %s (%s) — already posted", match_key, phase)
                continue

            image_path: Path | None = None
            post_text: str | None = None
            use_photo = False
            live_signature = ""
            innings_break = False
            session_break = ""
            test_day = 0
            match_fmt = detect_format(block)

            if phase in ("preview", "tomorrow"):
                try:
                    info = parse_preview_block(block)
                    if not info.match_key:
                        info.match_key = match_key
                    image_path = generate_preview_image(info)
                    post_text = build_preview_caption(info)
                    use_photo = True
                except Exception as exc:
                    logger.error("Preview image generation failed: %s", exc)
                    continue
            elif phase in ("result", "live", "toss"):
                try:
                    update_info = parse_match_block(block, phase)
                    update_info.match_key = match_key
                    if phase == "live":
                        live_signature = make_live_signature(update_info)
                        innings_break = update_info.innings_status == "innings_break"
                        session_break = update_info.session_break
                        test_day = update_info.test_day
                    image_path = generate_match_image(update_info)
                    post_text = build_update_caption(update_info)
                    use_photo = True
                except Exception as exc:
                    logger.error("%s image generation failed: %s", phase, exc)
                    post_text = build_match_post(block, phase=phase)
                    if not post_text:
                        logger.warning("Could not build %s post for %s", phase, match_key)
                        continue
            else:
                post_text = build_match_post(block, phase=phase)
                if not post_text:
                    logger.warning("Could not build %s post for %s", phase, match_key)
                    continue

            if phase == "live" and post_text and not should_post_live(
                block,
                live_signature or post_text,
                _post_state,
                innings_break=innings_break,
                session_break=session_break,
                test_day=test_day,
                fmt=match_fmt,
            ):
                if image_path is not None:
                    try:
                        image_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                continue

            if posted_count > 0:
                logger.info(
                    "Waiting %ds before next match post",
                    INTER_MATCH_POST_DELAY,
                )
                await asyncio.sleep(INTER_MATCH_POST_DELAY)

            if use_photo and image_path is not None:
                published = publish_photo_to_facebook(
                    post_text,
                    image_path,
                    config["FACEBOOK_PAGE_ID"],
                    config["FACEBOOK_ACCESS_TOKEN"],
                )
                if published:
                    try:
                        image_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            else:
                published = publish_to_facebook(
                    post_text,
                    config["FACEBOOK_PAGE_ID"],
                    config["FACEBOOK_ACCESS_TOKEN"],
                    use_background=True,
                )

            if not published:
                continue

            record_post_state(
                block,
                phase,
                post_text,
                _post_state,
                live_signature=live_signature,
                innings_break=innings_break,
                session_break=session_break,
                test_day=test_day,
            )
            posted_count += 1
            logger.info("Posted (%s): %s", phase, post_text[:120])

            if phase == "toss":
                xi_attempted.add(match_key)
                posted_count = await post_playing_xi_if_ready(
                    block,
                    match_links,
                    page,
                    config,
                    _post_state,
                    posted_count=posted_count,
                )

            if phase == "live" and innings_break and match_fmt in ("T20", "ODI"):
                sc_key = f"{match_key}|{update_info.batting_team}"
                if sc_key not in _post_state.scorecard_innings_posted:
                    match_url = _resolve_match_url(match_key, match_links)
                    if match_url:
                        try:
                            sc_info = await fetch_innings_scorecard(
                                page, match_url, update_info
                            )
                            if sc_info and sc_info.batters:
                                sc_image = generate_scorecard_image(sc_info)
                                sc_text = build_scorecard_caption(sc_info)
                                await asyncio.sleep(INTER_MATCH_POST_DELAY)
                                sc_published = publish_photo_to_facebook(
                                    sc_text,
                                    sc_image,
                                    config["FACEBOOK_PAGE_ID"],
                                    config["FACEBOOK_ACCESS_TOKEN"],
                                )
                                if sc_published:
                                    try:
                                        sc_image.unlink(missing_ok=True)
                                    except OSError:
                                        pass
                                    _post_state.scorecard_innings_posted.add(sc_key)
                                    save_post_state(_post_state)
                                    posted_count += 1
                                    logger.info("Posted innings scorecard for %s", sc_key)
                                else:
                                    logger.warning(
                                        "Scorecard post failed (Facebook error) for %s", sc_key
                                    )
                            else:
                                logger.warning(
                                    "Scorecard parse returned no batters for %s", sc_key
                                )
                        except Exception as exc:
                            logger.error(
                                "Scorecard post error for %s: %s", sc_key, exc
                            )

        posted_count = await post_missed_toss_and_playing_xi(
            raw_data,
            match_links,
            page,
            config,
            _post_state,
            posted_count=posted_count,
            xi_attempted=xi_attempted,
        )

        if posted_count == 0:
            sleep_seconds = get_sleep_interval(raw_data)
            logger.info("No new posts this cycle; waiting %ds", sleep_seconds)
            return sleep_seconds

        sleep_seconds = get_sleep_interval(raw_data)
        logger.info(
            "Published %d post(s); next check in %ds",
            posted_count,
            sleep_seconds,
        )
        return sleep_seconds



    finally:

        await page.close()

        if ephemeral_context is not None:
            await ephemeral_context.close()





async def main() -> None:

    global _post_state

    config = validate_env()
    _post_state = load_post_state()

    logger.info("Starting Cricket Live Score Facebook Agent")

    logger.info("Target URL: %s", config["TARGET_MATCH_URL"])

    logger.info("Tracked teams: %s", ", ".join(TRACKED_TEAMS))



    async with Stealth().use_async(async_playwright()) as playwright:

        browser, persistent_context = await open_stealth_session(playwright)

        try:

            while True:

                try:

                    sleep_seconds = await run_cycle(
                        config,
                        browser=browser,
                        persistent_context=persistent_context,
                    )

                except Exception:

                    logger.error("Unexpected error in cycle:\n%s", traceback.format_exc())

                    sleep_seconds = ERROR_RETRY_INTERVAL



                logger.info("Sleeping for %d seconds", sleep_seconds)

                await asyncio.sleep(sleep_seconds)

        except KeyboardInterrupt:

            logger.info("Shutting down...")

        finally:

            if persistent_context is not None:
                try:
                    await persistent_context.close()
                except Exception:
                    logger.debug("Browser context already closed")
            elif browser is not None:
                try:
                    await browser.close()
                except Exception:
                    logger.debug("Browser already closed")





if __name__ == "__main__":

    asyncio.run(main())


