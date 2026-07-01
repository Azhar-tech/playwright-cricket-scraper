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
from datetime import date, datetime

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

from match_image import build_preview_caption, generate_preview_image, parse_preview_block
from post_builder import build_match_post



ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH)



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



FORMAT_INTERVALS = {"T20": 1800, "ODI": 3600, "TEST": 10800}
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

POST_STATE_PATH = Path(__file__).resolve().parent / ".post_state.json"
LEGACY_PREVIEW_STORE_PATH = Path(__file__).resolve().parent / ".posted_previews.json"
PHASE_SORT_ORDER = {"live": 0, "toss": 1, "preview": 2, "result": 3}

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
        payload = {
            "preview_posted": sorted(state.preview_posted),
            "result_posted": sorted(state.result_posted),
            "toss_posted": sorted(state.toss_posted),
            "live_last": state.live_last,
        }
        POST_STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not save post state: %s", exc)





def validate_env() -> dict[str, str]:

    if not ENV_PATH.exists():

        logger.error(".env file not found at %s", ENV_PATH)

        sys.exit(1)



    if ENV_PATH.stat().st_size == 0:

        logger.error(

            ".env file exists but is empty. Save your variables in %s and try again.",

            ENV_PATH,

        )

        sys.exit(1)



    required = [

        "TARGET_MATCH_URL",

        "FACEBOOK_PAGE_ID",

        "FACEBOOK_ACCESS_TOKEN",

        "GEMINI_API_KEY",

    ]

    config = {key: os.getenv(key, "").strip() for key in required}

    missing = [key for key, value in config.items() if not value]

    if missing:

        logger.error("Missing required environment variables: %s", ", ".join(missing))

        logger.error("Loaded .env from: %s", ENV_PATH)

        logger.error("If values are in your editor, save the .env file (Ctrl+S) and rerun.")

        sys.exit(1)



    url = config["TARGET_MATCH_URL"]

    if "..." in url or "your target match link" in url.lower():

        logger.error(

            "TARGET_MATCH_URL is still a placeholder. Set a real URL in .env, e.g. "

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

        _genai_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

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

    )

    await context.add_init_script(WEBDRIVER_OVERRIDE_SCRIPT)

    page = await context.new_page()

    return page, context





async def scrape_match(page: Page, match_url: str) -> Optional[str]:

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
        return None

    if "access denied" in (await page.title()).lower():
        logger.warning("Access denied by site — try HEADLESS=false or stealth settings")
        return None

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

        return None



    raw_text = await _extract_scorecard_text(page, match_url)

    if not raw_text or not SCORE_PATTERN.search(raw_text):

        logger.warning("Scraped text missing score data")

        logger.debug("Partial text: %s", raw_text[:500] if raw_text else "(empty)")

        return None



    return raw_text





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


def block_match_phase(block: str) -> str:
    """Classify block as live, result, toss, preview, tomorrow, or other."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    first_line = lines[0] if lines else ""
    first_upper = first_line.upper()

    if first_upper == "RESULT" or re.search(r"\bwon by\b", block, re.IGNORECASE):
        return "result"
    if first_upper.startswith("TODAY,"):
        return "preview"
    if first_upper.startswith("TOMORROW,"):
        return "tomorrow"
    if TOSS_PATTERN.search(block):
        return "toss"
    if UPCOMING_PATTERN.search(block) or "match yet to begin" in block.lower():
        return "preview"
    if first_upper == "LIVE" or (
        SCORE_PATTERN.search(block) and "won by" not in block.lower()
    ):
        if first_upper != "RESULT" and not UPCOMING_PATTERN.search(block):
            return "live"
    return "other"


def is_postable_block(block: str) -> bool:
    """Tracked national-team fixtures: live, finished, toss, or today's preview."""
    if not block_has_tracked_team(block):
        return False
    if "NOT COVERED LIVE" in block.upper():
        return False

    phase = block_match_phase(block)
    if phase in ("live", "result", "toss", "preview"):
        return True
    return False


def extract_all_postable_blocks(text: str) -> list[tuple[str, str]]:
    """Return all postable (block, phase) pairs, deduped by match key."""
    blocks = normalize_match_blocks(text)
    by_key: dict[str, tuple[str, str]] = {}
    for block in blocks:
        if not is_postable_block(block):
            continue
        phase = block_match_phase(block)
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
    return f"{date.today().isoformat()}|{make_match_key(block)}"


def should_post_one_shot(block: str, phase: str, state: PostState) -> bool:
    if phase == "preview":
        return make_preview_key(block) not in state.preview_posted
    if phase == "result":
        return make_match_key(block) not in state.result_posted
    if phase == "toss":
        return make_match_key(block) not in state.toss_posted
    return True


def should_post_live(block: str, post_text: str, state: PostState) -> bool:
    key = make_match_key(block)
    last = state.live_last.get(key)
    if not last:
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
    if last.get("text") == post_text:
        logger.info("Live match %s unchanged since last post; skipping", key)
        return False
    return True


def record_post_state(block: str, phase: str, post_text: str, state: PostState) -> None:
    if phase == "preview":
        state.preview_posted.add(make_preview_key(block))
    elif phase == "result":
        state.result_posted.add(make_match_key(block))
    elif phase == "toss":
        state.toss_posted.add(make_match_key(block))
    elif phase == "live":
        state.live_last[make_match_key(block)] = {
            "at": datetime.now().isoformat(),
            "text": post_text,
        }
    save_post_state(state)




def get_sleep_interval(_text: str) -> int:
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

        raw_data = await scrape_match(page, config["TARGET_MATCH_URL"])

        if not raw_data:

            return ERROR_RETRY_INTERVAL



        logger.info("Scraped match data (%d chars)", len(raw_data))

        candidates = extract_all_postable_blocks(raw_data)
        if not candidates:
            logger.info(
                "No live/result/toss/preview update for tracked teams; waiting %ds",
                UNTRACKED_INTERVAL,
            )
            return UNTRACKED_INTERVAL

        logger.info("Found %d tracked fixture(s)", len(candidates))
        posted_count = 0

        for block, phase in candidates:
            match_key = make_match_key(block)
            logger.info("Evaluating %s fixture (%s)", phase, match_key)

            if phase != "live" and not should_post_one_shot(block, phase, _post_state):
                logger.info("Skipping %s (%s) — already posted", match_key, phase)
                continue

            image_path: Path | None = None
            if phase == "preview":
                try:
                    info = parse_preview_block(block)
                    if not info.match_key:
                        info.match_key = match_key
                    image_path = generate_preview_image(info)
                    post_text = build_preview_caption(info)
                except Exception as exc:
                    logger.error("Preview image generation failed: %s", exc)
                    continue
            else:
                post_text = build_match_post(block, phase=phase)
                if not post_text:
                    logger.warning("Could not build %s post for %s", phase, match_key)
                    continue

            if phase == "live" and not should_post_live(block, post_text, _post_state):
                continue

            if posted_count > 0:
                logger.info(
                    "Waiting %ds before next match post",
                    INTER_MATCH_POST_DELAY,
                )
                await asyncio.sleep(INTER_MATCH_POST_DELAY)

            if phase == "preview" and image_path is not None:
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

            record_post_state(block, phase, post_text, _post_state)
            posted_count += 1
            logger.info("Posted (%s): %s", phase, post_text[:120])

        if posted_count == 0:
            logger.info("No new posts this cycle; waiting %ds", UNTRACKED_INTERVAL)
            return UNTRACKED_INTERVAL

        logger.info(
            "Published %d post(s); next check in %ds (30 min)",
            posted_count,
            POST_INTERVAL,
        )
        return POST_INTERVAL



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


