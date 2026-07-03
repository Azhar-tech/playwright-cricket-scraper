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
from datetime import date

import requests
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from match_image import (
    _load_font,
    build_live_caption,
    build_preview_caption,
    build_result_caption,
    build_toss_caption,
    generate_match_image,
    generate_preview_image,
    make_live_signature,
    parse_match_block,
    parse_preview_block,
)
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

SAMPLE_TOMORROW_BLOCK = """
Sri Lanka tour of West Indies 2026
2nd Test, North Sound, July 03 - 07, 2026, Sri Lanka tour of West Indies
West Indies
Sri Lanka
Match yet to begin
TOMORROW, 7:00 PM
"""

SAMPLE_RESULT_BLOCK = """
RESULT
ICC Women's T20 World Cup
Semi-final · T20 32 of 33
England Women
169/5
South Africa Women
129/8
ENG Women won by 40 runs
"""

SAMPLE_LIVE_BLOCK = """
LIVE
PAK vs IND, 2nd T20
Pakistan
41/1 (6/20 ov)
Babar Azam 23 (12), Mohammad Rizwan 19 (13)
India
yet to bat
"""

SAMPLE_TOSS_BLOCK = """
India tour of England 2026
1st T20I (D/N)
England
India
England won the toss and elected to bat
Match starts 7:00 PM
"""

SAMPLE_LIVE_WITH_TOSS_BLOCK = """
LIVE
PAK vs IND, 2nd T20I
Pakistan
52/2 (6 ov)
India
Pakistan won the toss and elected to bowl first
"""

SAMPLE_LIVE_11_OV_BLOCK = """
LIVE
PAK vs IND, 2nd T20I
Pakistan
115/5 (11 ov)
India
Pakistan won the toss and elected to bowl first
"""

SAMPLE_INNINGS_BREAK_BLOCK = """
LIVE
PAK vs IND, 2nd T20I
Pakistan
180/7 (20 ov)
India
yet to bat
"""

SAMPLE_CHASE_BLOCK = """
LIVE
PAK vs IND, 2nd T20I
Pakistan
180/7
India
(4/20 ov, T:181) 35/1
India need 146 runs from 94 balls
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
    sig = "live|41/1|(6)|||||Pakistan"
    ok_live_first = should_post_live(live_block, sig, state)
    from datetime import datetime, timedelta

    state.live_last[make_match_key(live_block)] = {
        "at": datetime.now().isoformat(),
        "text": "caption",
        "signature": sig,
    }
    ok_live_cooldown = not should_post_live(live_block, sig, state)

    state.live_last[make_match_key(live_block)] = {
        "at": (datetime.now() - timedelta(seconds=1900)).isoformat(),
        "text": "caption",
        "signature": sig,
    }
    ok_live_changed = should_post_live(live_block, "live|52/2|(6)|||||Pakistan", state)
    _status("Live post allowed (first time)", ok_live_first)
    _status("Live skipped during 30-min cooldown", ok_live_cooldown)
    _status("Live allowed when score signature changed", ok_live_changed)

    return all(
        [
            ok_result,
            ok_preview,
            ok_result_skip,
            ok_preview_skip,
            ok_live_first,
            ok_live_cooldown,
            ok_live_changed,
        ]
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
        and info.venue == "Chester-le-Street"
    )
    _status("Hashtag caption built", ok)
    if ok:
        print(f"       Caption preview: {caption.splitlines()[0]}")
        print(f"       Venue parsed: {info.venue}")
    return ok


def test_preview_tomorrow() -> bool:
    print("\n=== 3d2. Tomorrow preview ===")
    block = SAMPLE_TOMORROW_BLOCK.strip()
    phase_ok = block_match_phase(block) == "tomorrow"
    _status("Tomorrow block phase detection", phase_ok, block_match_phase(block))

    info = parse_preview_block(block)

    parse_ok = (
        info.day_label == "tomorrow"
        and info.match_date == date(2026, 7, 3)
        and info.time_str == "7:00 PM"
        and "Sri Lanka tour of West Indies 2026" in info.series
        and info.venue == "North Sound"
    )
    _status("Tomorrow match parsed", parse_ok)
    if parse_ok:
        print(f"       Series: {info.series}")
        print(f"       Match date: {info.match_date}")

    caption = build_preview_caption(info)
    caption_ok = caption.startswith("Tomorrow!") and "West Indies vs Sri Lanka" in caption
    _status("Tomorrow caption prefix", caption_ok, caption.splitlines()[0] if caption_ok else caption[:80])
    return phase_ok and parse_ok and caption_ok


def test_preview_fonts() -> bool:
    print("\n=== 3e. Preview font loading ===")
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = _load_font(52, bold=True)
    except RuntimeError as exc:
        _status("Load bundled TrueType font", False, str(exc))
        return False

    is_truetype = isinstance(font, ImageFont.FreeTypeFont)
    draw = ImageDraw.Draw(Image.new("RGB", (400, 100)))
    bbox = draw.textbbox((0, 0), "England", font=font)
    height = bbox[3] - bbox[1]
    ok = is_truetype and height > 30
    _status("Load bundled TrueType font", ok, f"bbox height={height}px")
    return ok


def test_preview_image_generation(keep_image: bool = False) -> bool:
    print("\n=== 3f. Preview image generation ===")
    info = parse_preview_block(SAMPLE_PREVIEW_BLOCK.strip())
    try:
        path = generate_preview_image(info)
    except Exception as exc:
        _status("Generate preview PNG", False, str(exc))
        return False

    from PIL import Image

    size_ok = False
    if path.exists():
        with Image.open(path) as img:
            size_ok = img.size == (1080, 1350)

    ok = path.exists() and path.stat().st_size >= 30_000 and size_ok
    _status("Generate preview PNG", ok, str(path))
    if ok and keep_image:
        print(f"       Saved preview image at: {path}")
    elif ok and not keep_image:
        path.unlink(missing_ok=True)
    return ok


def test_match_image_generation(keep_image: bool = False) -> bool:
    print("\n=== 3g. Match update image generation ===")
    from PIL import Image

    cases = [
        ("result", SAMPLE_RESULT_BLOCK.strip()),
        ("live", SAMPLE_LIVE_BLOCK.strip()),
        ("toss", SAMPLE_TOSS_BLOCK.strip()),
    ]
    all_ok = True
    for phase, block in cases:
        try:
            info = parse_match_block(block, phase)
            path = generate_match_image(info)
        except Exception as exc:
            _status(f"Generate {phase} PNG", False, str(exc))
            all_ok = False
            continue

        size_ok = False
        if path.exists():
            with Image.open(path) as img:
                size_ok = img.size == (1080, 720)

        ok = path.exists() and path.stat().st_size >= 10_000 and size_ok
        _status(f"Generate {phase} PNG", ok, str(path))
        if not ok:
            all_ok = False
        elif keep_image:
            print(f"       Saved {phase} image at: {path}")
        elif ok:
            path.unlink(missing_ok=True)

    result_info = parse_match_block(SAMPLE_RESULT_BLOCK.strip(), "result")
    caption_ok = (
        "Full time!" in build_result_caption(result_info)
        and "won by 40 runs" in build_result_caption(result_info)
    )
    _status("Result caption built", caption_ok)

    toss_info = parse_match_block(SAMPLE_TOSS_BLOCK.strip(), "toss")
    toss_ok = "won the toss" in build_toss_caption(toss_info).lower()
    _status("Toss caption built", toss_ok)

    parse_ok = (
        result_info.score1 == "169/5"
        and result_info.score2 == "129/8"
        and "won by 40 runs" in result_info.headline
    )
    _status("Result scores parsed", parse_ok, f"{result_info.score1} / {result_info.score2}")

    return all_ok and caption_ok and toss_ok and parse_ok


def test_live_posting_flow() -> bool:
    print("\n=== 3h. Live posting flow ===")
    toss_only = SAMPLE_TOSS_BLOCK.strip()
    live_with_toss = SAMPLE_LIVE_WITH_TOSS_BLOCK.strip()

    phase_toss = block_match_phase(toss_only) == "toss"
    phase_live = block_match_phase(live_with_toss) == "live"
    _status("Pre-match block is toss", phase_toss, block_match_phase(toss_only))
    _status("Live block with toss text is live", phase_live, block_match_phase(live_with_toss))

    info_6 = parse_match_block(live_with_toss, "live")
    overs_ok = info_6.overs1 == "(6)" and info_6.score1 == "52/2"
    _status("6-over score parsed", overs_ok, f"{info_6.score1} {info_6.overs1}")

    info_11 = parse_match_block(SAMPLE_LIVE_11_OV_BLOCK.strip(), "live")
    sig_6 = make_live_signature(info_6)
    sig_11 = make_live_signature(info_11)
    sig_ok = sig_6 != sig_11
    _status("6 ov vs 11 ov signatures differ", sig_ok)

    caption_6 = build_live_caption(info_6)
    caption_ok = "after 6 overs" in caption_6 and "52/2" in caption_6
    _status("Live caption mentions after 6 overs", caption_ok, caption_6.splitlines()[0][:80])

    break_info = parse_match_block(SAMPLE_INNINGS_BREAK_BLOCK.strip(), "live")
    break_ok = (
        break_info.innings_status == "innings_break"
        and break_info.target == 181
        and "need 181 to win" in break_info.headline
    )
    _status("Innings break parsed", break_ok, break_info.headline[:80] if break_info.headline else "")

    state = PostState()
    break_key = make_match_key(SAMPLE_INNINGS_BREAK_BLOCK.strip())
    ok_break_first = should_post_live(
        SAMPLE_INNINGS_BREAK_BLOCK.strip(),
        make_live_signature(break_info),
        state,
        innings_break=True,
    )
    state.innings_break_posted.add(break_key)
    ok_break_skip = not should_post_live(
        SAMPLE_INNINGS_BREAK_BLOCK.strip(),
        make_live_signature(break_info),
        state,
        innings_break=True,
    )
    _status("Innings break post allowed once", ok_break_first)
    _status("Innings break skipped if already posted", ok_break_skip)

    chase_info = parse_match_block(SAMPLE_CHASE_BLOCK.strip(), "live")
    chase_ok = (
        chase_info.innings_status == "chase"
        and chase_info.score2 == "35/1"
        and chase_info.runs_needed == 146
        and chase_info.balls_remaining == 94
    )
    chase_caption = build_live_caption(chase_info)
    _status("Chase parsed", chase_ok, chase_caption.splitlines()[0][:90])

    return all(
        [
            phase_toss,
            phase_live,
            overs_ok,
            sig_ok,
            caption_ok,
            break_ok,
            ok_break_first,
            ok_break_skip,
            chase_ok,
            "146" in chase_caption,
        ]
    )


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
    tomorrow_ok = test_preview_tomorrow()
    font_ok = test_preview_fonts()
    image_ok = test_preview_image_generation(keep_image=args.preview_image)
    match_image_ok = test_match_image_generation(keep_image=args.match_image)
    live_flow_ok = test_live_posting_flow()
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
    preview_ok = caption_ok and tomorrow_ok and font_ok and image_ok and match_image_ok and live_flow_ok
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
    parser.add_argument(
        "--match-image",
        action="store_true",
        help="Keep generated result/live/toss PNGs in generated_images/ for visual check",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
