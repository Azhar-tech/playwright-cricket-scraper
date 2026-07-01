"""
Test harness for the Cricket Live Score Facebook Agent.

Usage:
    python test.py                  # run all steps (Facebook = token check only)
    python test.py --post           # also publish a test post to Facebook
    python test.py --skip-scrape      # skip Playwright; use sample score data
    python test.py --skip-facebook    # skip Facebook step entirely
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import requests
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from match_image import build_preview_caption, generate_preview_image, parse_preview_block
from post_builder import build_match_post, build_result_post
from main import (
    TRACKED_TEAMS,
    PostState,
    block_match_phase,
    detect_format,
    extract_all_postable_blocks,
    extract_tracked_match_data,
    generate_post,
    is_tracked_match,
    make_match_key,
    make_preview_key,
    open_stealth_session,
    publish_to_facebook,
    scrape_match,
    should_post_live,
    should_post_one_shot,
    validate_env,
)

SAMPLE_SCORE_DATA = """
PAK vs IND, 2nd T20
Dubai International Stadium
PAKISTAN 41/1 (6.0 overs)
Babar Azam 23 (12), Mohammad Rizwan 19 (13)
India yet to bat.
"""

SAMPLE_MULTI_MATCH_DATA = """
RESULT
ICC Women's T20 World Cup
West Indies Women
125/7
Australia Women
(13/20 ov, T:126) 127/2
AUS Women won by 8 wickets

India tour of England 2026
1st T20I
England
India
Match yet to begin
7:00 PM local
"""

SAMPLE_PREVIEW_BLOCK = """
India tour of England 2026
1st T20I (D/N), Chester-le-Street, July 01, 2026
England
India
Match yet to begin
9:30 PM local
"""

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def _status(label: str, ok: bool, detail: str = "") -> None:
    mark = PASS if ok else FAIL
    line = f"[{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)


def test_environment() -> dict | None:
    print("\n=== 1. Environment ===")
    try:
        config = validate_env()
    except SystemExit:
        _status("Load environment variables", False, "see error above")
        return None

    _status("Load environment variables", True)
    for key in ("TARGET_MATCH_URL", "FACEBOOK_PAGE_ID", "FACEBOOK_ACCESS_TOKEN"):
        masked = config[key][:8] + "..." if len(config[key]) > 8 else config[key]
        print(f"       {key} = {masked}")
    print(f"       TARGET_MATCH_URL = {config['TARGET_MATCH_URL']}")
    if config.get("GEMINI_API_KEY"):
        print("       GEMINI_API_KEY = (set, optional)")
    else:
        print("       GEMINI_API_KEY = (not set, optional)")
    return config


async def test_scrape(config: dict) -> str | None:
    print("\n=== 2. Scrape live scores ===")
    async with Stealth().use_async(async_playwright()) as playwright:
        browser, persistent_context = await open_stealth_session(playwright)
        ephemeral_context = None
        if persistent_context is not None:
            page = await persistent_context.new_page()
        else:
            from main import new_stealth_page

            page, ephemeral_context = await new_stealth_page(browser)  # type: ignore[arg-type]
        try:
            raw = await scrape_match(page, config["TARGET_MATCH_URL"])
            final_url = page.url
        finally:
            await page.close()
            if ephemeral_context is not None:
                await ephemeral_context.close()
            if persistent_context is not None:
                await persistent_context.close()
            elif browser is not None:
                await browser.close()

    if "google.com/sorry" in final_url:
        _status(
            "Scrape page",
            False,
            "Google CAPTCHA blocked the bot — use ESPN Cricinfo URL instead",
        )
        return None

    if not raw:
        _status("Scrape page", False, "no score data found on page")
        return None

    _status("Scrape page", True, f"{len(raw)} chars extracted")
    print("       Preview:")
    for line in raw.splitlines()[:8]:
        print(f"         {line}")
    if len(raw.splitlines()) > 8:
        print("         ...")
    return raw


def test_filter(raw_data: str) -> bool:
    print("\n=== 3. Team filter ===")
    candidates = extract_all_postable_blocks(raw_data)
    match_data = extract_tracked_match_data(raw_data)
    tracked = bool(candidates)
    fmt = detect_format(match_data or raw_data)

    if tracked:
        phases = ", ".join(f"{phase}({make_match_key(block)[:24]})" for block, phase in candidates)
        _status("Tracked team check", True, f"{len(candidates)} fixture(s): {phases}")
        print("       Matched fixtures:")
        for block, phase in candidates:
            print(f"         [{phase}] {make_match_key(block)}")
            for line in block.splitlines()[:4]:
                print(f"           {line}")
    else:
        _status(
            "Tracked team check",
            False,
            "no live/result/toss/preview update for a tracked team — agent would wait 5 min",
        )

    _status("Format detection", True, f"detected as {fmt}")
    return tracked


def test_multi_match_extraction() -> bool:
    print("\n=== 3b. Multi-match extraction ===")
    candidates = extract_all_postable_blocks(SAMPLE_MULTI_MATCH_DATA.strip())
    phases = {phase for _, phase in candidates}
    ok = len(candidates) >= 2 and "result" in phases and "preview" in phases
    _status(
        "Result + preview both found",
        ok,
        f"found {len(candidates)} blocks: {', '.join(p for _, p in candidates)}",
    )
    return ok


def test_post_rules() -> bool:
    print("\n=== 3c. Per-match post rules ===")
    state = PostState()
    result_block = SAMPLE_MULTI_MATCH_DATA.strip().split("\n\n")[0]
    preview_block = SAMPLE_MULTI_MATCH_DATA.strip().split("\n\n")[1]

    ok_result = should_post_one_shot(result_block, "result", state)
    ok_preview = should_post_one_shot(preview_block, "preview", state)
    _status("Result post allowed (first time)", ok_result)
    _status("Preview post allowed (first time)", ok_preview)

    state.result_posted.add(make_match_key(result_block))
    state.preview_posted.add(make_preview_key(preview_block))
    ok_result_skip = not should_post_one_shot(result_block, "result", state)
    ok_preview_skip = not should_post_one_shot(preview_block, "preview", state)
    _status("Result skipped after posted", ok_result_skip)
    _status("Preview skipped after posted", ok_preview_skip)

    live_block = "LIVE\nPAK vs IND\nPakistan\n41/1 (6 ov)\nIndia yet to bat"
    ok_live_first = should_post_live(live_block, "PAK 41/1 after 6 ov", state)
    from datetime import datetime, timedelta

    state.live_last[make_match_key(live_block)] = {
        "at": datetime.now().isoformat(),
        "text": "PAK 41/1 after 6 ov",
    }
    ok_live_cooldown = not should_post_live(live_block, "PAK 41/1 after 6 ov", state)
    _status("Live post allowed (first time)", ok_live_first)
    _status("Live skipped during 30-min cooldown", ok_live_cooldown)

    return all(
        [ok_result, ok_preview, ok_result_skip, ok_preview_skip, ok_live_first, ok_live_cooldown]
    )


def test_preview_caption() -> bool:
    print("\n=== 3d. Preview caption ===")
    info = parse_preview_block(SAMPLE_PREVIEW_BLOCK.strip())
    caption = build_preview_caption(info)
    ok = (
        "Today!" in caption
        and "#ENGvsIND" in caption
        and "#CricketUpdates" in caption
        and "9:30 PM" in caption
    )
    _status("Hashtag caption built", ok)
    if ok:
        print(f"       Caption preview: {caption.splitlines()[0]}")
    return ok


def test_preview_image_generation(keep_image: bool = False) -> bool:
    print("\n=== 3e. Preview image generation ===")
    info = parse_preview_block(SAMPLE_PREVIEW_BLOCK.strip())
    try:
        path = generate_preview_image(info)
    except Exception as exc:
        _status("Generate preview PNG", False, str(exc))
        return False

    ok = path.exists() and path.stat().st_size > 1000
    _status("Generate preview PNG", ok, str(path))
    if ok and keep_image:
        print(f"       Saved preview image at: {path}")
    elif ok and not keep_image:
        path.unlink(missing_ok=True)
    return ok


def test_rule_based_posts() -> bool:
    print("\n=== 4. Rule-based post generation ===")
    result_block = SAMPLE_MULTI_MATCH_DATA.strip().split("\n\n")[0]
    result_post = build_result_post(result_block)
    ok_result = bool(result_post) and "won by" in result_post.lower()
    _status("Build result post", ok_result, result_post or "none")

    live_block = """LIVE
PAK vs IND, 2nd T20
Pakistan
41/1 (6 ov)
India
yet to bat"""
    live_post = build_match_post(live_block, "live")
    ok_live = bool(live_post) and "41/1" in (live_post or "")
    _status("Build live post", ok_live, live_post or "none")
    return ok_result and ok_live


async def test_gemini(raw_data: str) -> str | None:
    print("\n=== 4b. Gemini post generation (optional) ===")
    post = await generate_post(raw_data)
    if not post:
        _status("Generate Facebook post via Gemini", False, "skipped or quota exhausted — using rule-based posts in main.py")
        return "rule-based"

    _status("Generate Facebook post via Gemini", True)
    print(f"       Post: {post}")
    return post


def test_facebook(config: dict, post: bool) -> bool:
    print("\n=== 5. Facebook ===")
    page_id = config["FACEBOOK_PAGE_ID"]
    token = config["FACEBOOK_ACCESS_TOKEN"]
    url = f"https://graph.facebook.com/v20.0/{page_id}"

    try:
        response = requests.get(
            url,
            params={"fields": "name,id", "access_token": token},
            timeout=30,
        )
    except requests.RequestException as exc:
        _status("Verify page token", False, str(exc))
        return False

    if not response.ok:
        _status(
            "Verify page token",
            False,
            "token cannot read page metadata (may still be able to post — try --post)",
        )
        print(f"       Detail: {response.text[:200]}")
        return False

    page_name = response.json().get("name", page_id)
    _status("Verify page token", True, f"page: {page_name}")

    if not post:
        print(f"       [{SKIP}] Publish test post (run with --post to send one)")
        return True

    message = "[Test] Cricket agent connectivity check — please ignore."
    ok = publish_to_facebook(message, page_id, token)
    _status("Publish test post", ok)
    if ok:
        print("       Check your Facebook Page feed for the test post.")
    return ok


async def run(args: argparse.Namespace) -> int:
    print("Cricket Agent — diagnostic test")
    print("=" * 40)

    config = test_environment()
    if not config:
        return 1

    if args.skip_scrape:
        print(f"\n=== 2. Scrape live scores ===")
        _status("Scrape page", True, "skipped — using sample data")
        raw_data = SAMPLE_SCORE_DATA.strip()
        scrape_ok = True
    else:
        raw_data = await test_scrape(config)
        scrape_ok = raw_data is not None
        if not raw_data:
            print("\n--- Tip: use --skip-scrape to skip Playwright entirely ---")
            raw_data = SAMPLE_SCORE_DATA.strip()
            print("       Falling back to sample score data for remaining steps.")

    test_filter(raw_data)
    multi_ok = test_multi_match_extraction()
    rules_ok = test_post_rules()
    caption_ok = test_preview_caption()
    image_ok = test_preview_image_generation(keep_image=args.preview_image)
    posts_ok = test_rule_based_posts()
    gemini_input = extract_tracked_match_data(raw_data) or raw_data
    post_text = await test_gemini(gemini_input.split("\n\n---\n\n")[0])

    fb_ok = True
    if args.skip_facebook:
        print("\n=== 5. Facebook ===")
        _status("Facebook step", True, "skipped")
    else:
        fb_ok = test_facebook(config, post=args.post)

    print("\n" + "=" * 40)
    preview_ok = caption_ok and image_ok
    if fb_ok and multi_ok and rules_ok and preview_ok and posts_ok:
        if post_text and post_text != "rule-based":
            print("Summary: core pipeline OK (rule-based posts + optional Gemini).")
        else:
            print("Summary: core pipeline OK (rule-based posts, no Gemini needed).")
        if not args.skip_scrape and not scrape_ok:
            return 1
        return 0

    print("Summary: one or more steps failed — fix those before running main.py.")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Test cricket agent components")
    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip Playwright scraping; use sample score data",
    )
    parser.add_argument(
        "--skip-facebook",
        action="store_true",
        help="Skip Facebook token check and posting",
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help="Publish a test post to Facebook (token check always runs unless --skip-facebook)",
    )
    parser.add_argument(
        "--preview-image",
        action="store_true",
        help="Keep generated preview PNG in generated_images/ for visual check",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
