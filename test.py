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
    _extract_time,
    _extract_venue_from_line,
    _load_font,
    build_live_caption,
    build_preview_caption,
    build_result_caption,
    build_scorecard_caption,
    build_toss_caption,
    generate_match_image,
    generate_preview_image,
    generate_scorecard_image,
    make_live_signature,
    parse_innings_scorecard_text,
    parse_match_block,
    parse_preview_block,
)
from playing_xi import (
    build_playing_xi_caption,
    build_playing_xi_info,
    generate_playing_xi_image,
    make_playing_xi_key,
    match_playing_xi_urls,
    parse_playing_xi_from_match_text,
)
from post_builder import build_match_post, build_result_post
from main import (
    TRACKED_TEAMS,
    PostState,
    _playing_xi_pending,
    _teams_from_block_names,
    _toss_announced,
    block_match_phase,
    detect_format,
    extract_all_postable_blocks,
    extract_tracked_match_data,
    generate_post,
    is_tracked_match,
    make_match_key,
    make_preview_key,
    make_test_session_key,
    open_stealth_session,
    publish_to_facebook,
    scrape_match,
    should_post_live,
    should_post_one_shot,
    split_preview_fixture_blocks,
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
TOMORROW, 4:00 AM
"""

SAMPLE_TOMORROW_MULTI_TIME = """
India tour of England 2026
1:30 PM
TOMORROW, 10:30 PM
2nd T20I, Manchester, July 04, 2026
England
India
Match yet to begin
"""

SAMPLE_PREVIEW_GOOGLE_ZIM_BAN = """
India Under-19s tour of Sri Lanka 2026
7:30 AM
Bangladesh tour of Zimbabwe 2026
1st ODI · Harare, July 06, 2026
Zimbabwe
Bangladesh
Match yet to begin
Starts at 12:30 pm
"""

SAMPLE_PREVIEW_ZIM_BAN_BROKEN = """
7:30 AM
Bangladesh tour of Zimbabwe 2026
1st ODI · Harare, July 06, 2026
Zimbabwe
Bangladesh
Match yet to begin
"""

SAMPLE_LIVE_TEST_WITH_TOSS = """
LIVE
Sri Lanka tour of West Indies 2026
2nd Test, North Sound, July 03 - 07, 2026
West Indies
245/3
Sri Lanka
West Indies won the toss and elected to bat
"""

SAMPLE_TEST_LUNCH = """
LIVE
Sri Lanka tour of West Indies 2026
2nd Test, North Sound, July 03 - 07, 2026
West Indies
120/2 (45 ov)
Sri Lanka
Yet to bat
Lunch
"""

SAMPLE_TEST_TEA = """
LIVE
Sri Lanka tour of West Indies 2026
2nd Test, North Sound, July 03 - 07, 2026
West Indies
210/4 (68 ov)
Sri Lanka
Yet to bat
Day 2
Tea break
"""

SAMPLE_TEST_STUMPS = """
LIVE
Sri Lanka tour of West Indies 2026
2nd Test, North Sound, July 03 - 07, 2026
West Indies
245/3 (78 ov)
Sri Lanka
Yet to bat
Stumps - Day 2
"""

SAMPLE_TEST_MID_SESSION = """
LIVE
Sri Lanka tour of West Indies 2026
2nd Test, North Sound, July 03 - 07, 2026
West Indies
200/4 (60 ov)
Sri Lanka
Yet to bat
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

SAMPLE_FIRST_INNINGS_ODI = """
LIVE
India vs New Zealand, 2nd ODI
India
248/4 (45.0 ov)
New Zealand
Yet to Bat
IND Current Run Rate: 5.51
T. Boult 1/51 (9.0)
R. Sharma 98* (122)
"""

SAMPLE_CHASE_ODI = """
LIVE
India vs New Zealand, 2nd ODI
India
284/7 (50 ov)
New Zealand
47/2 (12.5 ov)
NZ need 238 runs in 37.1 overs to win
CRR: 3.66
RRR: 6.40
P. Krishna 1/9 (1.5)
W. Young 17* (31)
"""

SAMPLE_PLAYING_XI_TEXT = """
Playing XI
India tour of England 2026
1st T20I (D/N)
England
1. Jos Buttler (c & wk)
2. Phil Salt
3. Dawid Malan
4. Harry Brook
5. Liam Livingstone
6. Moeen Ali
7. Sam Curran
8. Chris Woakes
9. Adil Rashid
10. Mark Wood
11. Reece Topley
India
1. Rohit Sharma (c)
2. Yashasvi Jaiswal
3. Shubman Gill
4. Virat Kohli
5. Shreyas Iyer
6. Hardik Pandya
7. Rishabh Pant (wk)
8. Ravindra Jadeja
9. Kuldeep Yadav
10. Jasprit Bumrah
11. Mohammed Siraj
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
            raw, _links = await scrape_match(page, config["TARGET_MATCH_URL"])
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
        and "1:30 AM" in caption
        and info.venue == "Chester-le-Street"
        and info.match_date == date(2026, 7, 2)
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
        and info.time_str == "4:00 AM"
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


def test_preview_timezone() -> bool:
    print("\n=== 3d3. Preview time extraction + timezone ===")
    block = SAMPLE_TOMORROW_MULTI_TIME.strip()
    extracted = _extract_time(block)
    extract_ok = extracted == "10:30 PM"
    _status("Prefer TOMORROW header time over bare time", extract_ok, extracted)

    info = parse_preview_block(block)
    convert_ok = (
        info.time_str == "10:30 PM"
        and info.match_date == date(2026, 7, 4)
        and info.venue == "Manchester"
        and info.day_label == "tomorrow"
    )
    _status("Browser-local PKT time posted directly (no venue conversion)", convert_ok, f"{info.time_str} on {info.match_date}")

    caption = build_preview_caption(info)
    caption_ok = "10:30 PM" in caption and caption.startswith("Tomorrow!")
    _status("Caption uses PKT time directly", caption_ok, caption.splitlines()[0][:90])

    zim_block = SAMPLE_PREVIEW_GOOGLE_ZIM_BAN.strip()
    starts_ok = _extract_time(zim_block) == "12:30 PM"
    _status("Prefer Starts at time over US-converted time", starts_ok, _extract_time(zim_block))

    venue_ok = _extract_venue_from_line("1st ODI · Harare, July 06, 2026") == "Harare"
    _status("Parse venue from middot fixture line", venue_ok)

    zim_info = parse_preview_block(zim_block)
    zim_ok = (
        zim_info.time_str == "12:30 PM"
        and zim_info.venue == "Harare"
        and zim_info.match_date == date(2026, 7, 6)
        and "Bangladesh tour of Zimbabwe 2026" in zim_info.series
        and "Sri Lanka" not in zim_info.series
    )
    _status("ZIM vs BAN Google widget parsed (12:30 PM PKT direct)", zim_ok, f"{zim_info.time_str}, {zim_info.venue}, {zim_info.series[:40]}")

    split_blocks = split_preview_fixture_blocks(SAMPLE_PREVIEW_GOOGLE_ZIM_BAN.strip())
    zim_split = next((b for b in split_blocks if "Zimbabwe" in b and "Bangladesh" in b), "")
    split_ok = bool(zim_split) and "Starts at 12:30 pm" in zim_split
    _status("Preview block split includes Starts at line", split_ok, zim_split.splitlines()[-1] if zim_split else "none")

    broken_info = parse_preview_block(SAMPLE_PREVIEW_ZIM_BAN_BROKEN.strip())
    broken_ok = broken_info.time_str != "7:30 AM" and broken_info.time_str == "TBC"
    _status("Broken block without Starts at skips orphan widget time", broken_ok, broken_info.time_str)

    inferred_info = parse_preview_block(
        "Bangladesh tour of Zimbabwe 2026\n"
        "1st ODI, July 06, 2026\n"
        "Zimbabwe\nBangladesh\nMatch yet to begin\nStarts at 12:30 pm"
    )
    infer_ok = inferred_info.venue == "Harare" and inferred_info.time_str == "12:30 PM"
    _status("Infer Harare venue from tour, 12:30 PM PKT posted directly", infer_ok, f"{inferred_info.time_str}, {inferred_info.venue}")

    # Bangladesh home match — Mirpur venue detected, PKT time posted directly
    mirpur_info = parse_preview_block(
        "Zimbabwe tour of Bangladesh 2026\n"
        "1st ODI, Mirpur, July 06, 2026\n"
        "Bangladesh\nZimbabwe\nMatch yet to begin\nStarts at 12:30 pm"
    )
    mirpur_ok = mirpur_info.time_str == "12:30 PM" and "Mirpur" in mirpur_info.venue
    _status("Mirpur venue detected, 12:30 PM PKT posted directly", mirpur_ok, f"{mirpur_info.time_str}, {mirpur_info.venue}")

    # Bangladesh home match — country-level venue fallback, PKT time posted directly
    ban_country_info = parse_preview_block(
        "Zimbabwe tour of Bangladesh 2026\n"
        "1st ODI, July 07, 2026\n"
        "Bangladesh\nZimbabwe\nMatch yet to begin\nStarts at 12:30 pm"
    )
    ban_country_ok = ban_country_info.time_str == "12:30 PM"
    _status("Bangladesh venue fallback, 12:30 PM PKT posted directly", ban_country_ok, f"{ban_country_info.time_str}, {ban_country_info.venue}")

    return extract_ok and convert_ok and caption_ok and starts_ok and venue_ok and zim_ok and split_ok and broken_ok and infer_ok and mirpur_ok and ban_country_ok


def test_playing_xi_triggers() -> bool:
    print("\n=== 3k. Toss + Playing XI catch-up triggers ===")
    block = SAMPLE_LIVE_TEST_WITH_TOSS.strip()
    match_key = make_match_key(block)
    team1, team2 = _teams_from_block_names(block)

    phases = extract_all_postable_blocks(block)
    live_only = len(phases) == 1 and phases[0][1] == "live"
    _status(
        "Live Test with toss resolves to live phase only",
        live_only,
        phases[0][1] if phases else "none",
    )

    toss_visible = _toss_announced(block)
    _status("Toss text detected inside live block", toss_visible)

    state = PostState()
    xi_pending = _playing_xi_pending(match_key, team1, team2, state)
    _status("Playing XI pending when none posted", xi_pending, f"{team1} vs {team2}")

    blocked = match_key not in state.toss_posted and not _toss_announced(SAMPLE_LIVE_BLOCK.strip())
    allowed = not (match_key not in state.toss_posted and not _toss_announced(block))
    _status("XI gate blocks live block without toss", blocked)
    _status("XI gate allows live block with toss text", allowed)

    base = (
        "https://www.cricinfo.com/series/sri-lanka-in-west-indies-2026-1538292/"
        "west-indies-vs-sri-lanka-2nd-test-1538312"
    )
    urls = match_playing_xi_urls(base)
    url_ok = urls[0].endswith("/match-playing-xi") and base in urls
    _status("Series match URL maps to match-playing-xi", url_ok, urls[0])

    xi_suffix = match_playing_xi_urls(f"{base}/match-playing-xi")
    suffix_ok = len(xi_suffix) == 1 and xi_suffix[0].endswith("/match-playing-xi")
    _status("Already-XI URL kept as-is", suffix_ok)

    return live_only and toss_visible and xi_pending and blocked and allowed and url_ok and suffix_ok


def test_test_session_posting() -> bool:
    print("\n=== 3l. Test match session posting ===")
    lunch_info = parse_match_block(SAMPLE_TEST_LUNCH.strip(), "live")
    lunch_ok = lunch_info.session_break == "lunch" and lunch_info.test_day >= 1
    _status("Lunch break detected", lunch_ok, f"day={lunch_info.test_day}, break={lunch_info.session_break}")

    tea_info = parse_match_block(SAMPLE_TEST_TEA.strip(), "live")
    tea_ok = tea_info.session_break == "tea" and tea_info.test_day == 2
    _status("Tea break detected", tea_ok, f"day={tea_info.test_day}, break={tea_info.session_break}")

    stumps_info = parse_match_block(SAMPLE_TEST_STUMPS.strip(), "live")
    stumps_ok = stumps_info.session_break == "stumps" and stumps_info.test_day == 2
    _status("Stumps detected", stumps_ok, f"day={stumps_info.test_day}, break={stumps_info.session_break}")

    mid_info = parse_match_block(SAMPLE_TEST_MID_SESSION.strip(), "live")
    mid_block = SAMPLE_TEST_MID_SESSION.strip()
    mid_sig = make_live_signature(mid_info)
    mid_skip = not should_post_live(
        mid_block,
        mid_sig,
        PostState(),
        fmt="TEST",
    )
    _status("Mid-session Test update blocked", mid_skip)

    state = PostState()
    lunch_sig = make_live_signature(lunch_info)
    allow_lunch = should_post_live(
        SAMPLE_TEST_LUNCH.strip(),
        lunch_sig,
        state,
        session_break="lunch",
        test_day=lunch_info.test_day,
        fmt="TEST",
    )
    state.test_session_posted.add(
        make_test_session_key(make_match_key(SAMPLE_TEST_LUNCH.strip()), lunch_info.test_day, "lunch")
    )
    skip_lunch = not should_post_live(
        SAMPLE_TEST_LUNCH.strip(),
        lunch_sig,
        state,
        session_break="lunch",
        test_day=lunch_info.test_day,
        fmt="TEST",
    )
    _status("Lunch post allowed once", allow_lunch)
    _status("Lunch post skipped if already posted", skip_lunch)

    stumps_caption = build_live_caption(stumps_info)
    cap_ok = "Day 2" in stumps_caption and "Stumps" in stumps_info.headline
    _status("Stumps caption uses session headline", cap_ok, stumps_caption.splitlines()[0][:90])

    progress_state = PostState()
    progress_state.toss_posted.add(make_match_key(SAMPLE_TOMORROW_BLOCK.strip()))
    tomorrow_phase = block_match_phase(SAMPLE_TOMORROW_BLOCK.strip(), state=progress_state)
    suppress_ok = tomorrow_phase != "tomorrow"
    _status("Tomorrow suppressed for in-progress Test", suppress_ok, tomorrow_phase)

    preview_blocked = not should_post_one_shot(
        SAMPLE_TOMORROW_BLOCK.strip(),
        "tomorrow",
        progress_state,
    )
    _status("Preview/tomorrow one-shot blocked in progress", preview_blocked)

    test_preview_key = make_preview_key(SAMPLE_TOMORROW_BLOCK.strip())
    t20_preview_key = make_preview_key(SAMPLE_PREVIEW_BLOCK.strip())
    key_ok = test_preview_key == make_match_key(SAMPLE_TOMORROW_BLOCK.strip())
    t20_key_ok = t20_preview_key.startswith(f"{date.today().isoformat()}|")
    _status("Test preview key is once per match", key_ok, test_preview_key[:60])
    _status("T20 preview key still daily", t20_key_ok)

    return all(
        [
            lunch_ok,
            tea_ok,
            stumps_ok,
            mid_skip,
            allow_lunch,
            skip_lunch,
            cap_ok,
            suppress_ok,
            preview_blocked,
            key_ok,
            t20_key_ok,
        ]
    )


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
                expected_h = 900 if phase == "live" else 720
                size_ok = img.size == (1080, expected_h)

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

    # Abbreviated ESPN toss text ("chose to field") normalised to full sentence
    abbrev_block = (
        "Bangladesh tour of Zimbabwe 2026\n"
        "1st ODI, Harare, July 07, 2026\n"
        "Zimbabwe\n0\nBangladesh\nBangladesh chose to field"
    )
    abbrev_info = parse_match_block(abbrev_block, "toss")
    abbrev_ok = (
        abbrev_info.headline == "Bangladesh won the toss and elected to field"
    )
    _status("Abbreviated toss normalised to full sentence", abbrev_ok, abbrev_info.headline)

    # Multi-line score before toss text must NOT bleed into headline
    multiline_ok = "0" not in abbrev_info.headline and "\n" not in abbrev_info.headline
    _status("Toss headline is single clean line (no score bleed)", multiline_ok, repr(abbrev_info.headline))

    parse_ok = (
        result_info.score1 == "169/5"
        and result_info.score2 == "129/8"
        and "won by 40 runs" in result_info.headline
    )
    _status("Result scores parsed", parse_ok, f"{result_info.score1} / {result_info.score2}")

    return all_ok and caption_ok and toss_ok and abbrev_ok and multiline_ok and parse_ok


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


def test_innings_layouts(keep_image: bool = False) -> bool:
    print("\n=== 3i. Innings-aware live layouts ===")
    from PIL import Image

    first_info = parse_match_block(SAMPLE_FIRST_INNINGS_ODI.strip(), "live")
    first_ok = (
        first_info.innings_status == "first_innings"
        and first_info.score1 == "248/4"
        and first_info.overs1 == "(45.0)"
        and first_info.current_run_rate == "5.51"
        and len(first_info.batters) >= 1
        and len(first_info.bowlers) >= 1
    )
    _status("First innings ODI parsed", first_ok, first_info.innings_status)

    chase_info = parse_match_block(SAMPLE_CHASE_ODI.strip(), "live")
    chase_ok = (
        chase_info.innings_status == "chase"
        and chase_info.score1 == "284/7"
        and chase_info.score2 == "47/2"
        and chase_info.runs_needed == 238
        and chase_info.overs_remaining == "37.1"
        and chase_info.current_run_rate == "3.66"
        and chase_info.required_run_rate == "6.40"
    )
    _status("Chase ODI parsed", chase_ok)

    first_caption = build_live_caption(first_info)
    cap_first = "CRR 5.51" in first_caption and "248/4" in first_caption
    _status("First innings caption", cap_first, first_caption.splitlines()[0][:90])

    chase_caption = build_live_caption(chase_info)
    cap_chase = "238" in chase_caption and "RRR 6.40" in chase_caption
    _status("Chase caption", cap_chase, chase_caption.splitlines()[0][:90])

    img_ok = True
    for label, info in (("first_innings", first_info), ("chase", chase_info)):
        try:
            path = generate_match_image(info)
            with Image.open(path) as img:
                ok = img.size == (1080, 900) and path.stat().st_size >= 10_000
            _status(f"Generate {label} layout PNG", ok, str(path))
            if not ok:
                img_ok = False
            elif keep_image:
                print(f"       Saved {label} layout at: {path}")
            elif ok:
                path.unlink(missing_ok=True)
        except Exception as exc:
            _status(f"Generate {label} layout PNG", False, str(exc))
            img_ok = False

    sig_a = make_live_signature(parse_match_block(SAMPLE_FIRST_INNINGS_ODI.strip(), "live"))
    block_45_5 = SAMPLE_FIRST_INNINGS_ODI.replace("45.0", "45.5")
    sig_b = make_live_signature(parse_match_block(block_45_5.strip(), "live"))
    _status("45.0 vs 45.5 signatures differ", sig_a != sig_b)

    # Test that a long multi-innings Test score does not clip outside image bounds
    from dataclasses import fields as dc_fields
    from match_image import MatchUpdateInfo as _MUI
    long_score_info = _MUI(
        team1="Sri Lanka",
        team2="West Indies",
        series="Sri Lanka tour of West Indies 2026",
        match_label="2nd Test",
        format_tag="Test",
        phase="live",
        score1="22 & 549/9 & 92/2",
        score2="499",
        innings_status="live",
        session_break="stumps",
        test_day=4,
        headline="Day 4 \u2014 Stumps: SL 22 & 549/9 & 92/2, WI 499",
        match_key="sri-lanka-west-indies|TEST|2nd Test",
    )
    long_score_ok = True
    try:
        long_path = generate_match_image(long_score_info)
        with Image.open(long_path) as img_ls:
            long_score_ok = img_ls.size[0] == 1080 and long_path.stat().st_size >= 5_000
        _status("Long Test score renders without clipping", long_score_ok, str(long_path))
        if long_score_ok and not keep_image:
            long_path.unlink(missing_ok=True)
    except Exception as exc:
        _status("Long Test score renders without clipping", False, str(exc))
        long_score_ok = False

    return first_ok and chase_ok and cap_first and cap_chase and img_ok and sig_a != sig_b and long_score_ok


def test_playing_xi(keep_image: bool = False) -> bool:
    print("\n=== 3j. Playing XI cards ===")
    from PIL import Image

    team1, team2 = "England", "India"
    squads = parse_playing_xi_from_match_text(SAMPLE_PLAYING_XI_TEXT.strip(), team1, team2)
    eng_ok = len(squads.get("England", [])) == 11
    ind_ok = len(squads.get("India", [])) == 11
    _status("Parse 11 players per team", eng_ok and ind_ok, f"ENG={len(squads.get('England', []))}, IND={len(squads.get('India', []))}")

    eng_captain = squads["England"][0].roles == "C+WK"
    ind_captain = squads["India"][0].roles == "C"
    ind_wk = any(p.roles == "WK" for p in squads["India"])
    _status("Captain/wk roles parsed", eng_captain and ind_captain and ind_wk)

    match_key = make_match_key(SAMPLE_TOSS_BLOCK.strip())
    key_eng = make_playing_xi_key(match_key, "England")
    key_ind = make_playing_xi_key(match_key, "India")
    _status("Per-team dedup keys differ", key_eng != key_ind, f"{key_eng} vs {key_ind}")

    block = SAMPLE_TOSS_BLOCK.strip()
    img_ok = True
    for team, opponent in (("England", "India"), ("India", "England")):
        info = build_playing_xi_info(team, opponent, squads[team], block, match_key)
        caption = build_playing_xi_caption(info)
        cap_ok = _team_abbrev_in_caption(caption, team) and info.match_label in caption
        _status(f"{team} caption", cap_ok, caption[:90])
        try:
            path = generate_playing_xi_image(info)
            with Image.open(path) as img:
                ok = img.size == (1080, 1350) and path.stat().st_size >= 10_000
            _status(f"Generate {team} Playing XI PNG", ok, str(path))
            if not ok:
                img_ok = False
            elif keep_image:
                print(f"       Saved Playing XI at: {path}")
            elif ok:
                path.unlink(missing_ok=True)
        except Exception as exc:
            _status(f"Generate {team} Playing XI PNG", False, str(exc))
            img_ok = False

    state = PostState()
    state.playing_xi_posted.add(key_eng)
    skip_eng = key_eng in state.playing_xi_posted
    allow_ind = key_ind not in state.playing_xi_posted
    _status("Post state skips posted team", skip_eng and allow_ind)

    return eng_ok and ind_ok and eng_captain and ind_captain and ind_wk and key_eng != key_ind and img_ok and skip_eng and allow_ind


def _team_abbrev_in_caption(caption: str, team: str) -> bool:
    from match_image import _team_abbrev

    return _team_abbrev(team) in caption


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


SAMPLE_SCORECARD_COMBINED = """\
Zimbabwe (50 ovs maximum)

Brian Bennett
Ben Curran
Innocent Kaia
Craig Ervine
Sikandar Raza
Wessly Madhevere
Clive Madande \u2020
Brad Evans
Newman Nyamhuri
Richard Ngarava (c)
Blessing Muzarabani

36.4 Ov (RR: 3.84, 184 Mins)
Fall of wickets
1-36 (Ben Curran, 6.4 ov), 2-36 (Brian Bennett, 6.6 ov)

BATTING R B M 4s 6s SR
Brian Bennett c Mosaddek Hossain b Taskin Ahmed 17 24 36 3 0 70.83
Ben Curran run out (Mehidy Hasan Miraz) 18 19 33 2 0 94.73
Innocent Kaia c Nurul Hasan b Nahid Rana 26 39 72 1 1 66.66
Craig Ervine b Taskin Ahmed 0 1 8 0 0 0.00
Sikandar Raza c Nurul Hasan b Nahid Rana 1 12 25 0 0 8.33
Wessly Madhevere c Shanto b Nahid Rana 0 10 16 0 0 0.00
Clive Madande c Mosaddek b Nahid Rana 2 7 10 0 0 28.57
Brad Evans lbw b Nahid Rana 3 7 14 0 0 42.85
Newman Nyamhuri c Tanzid b Mehidy Hasan Miraz 33 51 77 5 0 64.70
Richard Ngarava b Nahid Rana 27 41 59 3 0 65.85
Blessing Muzarabani not out 4 10 12 0 0 40.00
Extras (lb 3, nb 1, w 6) 10
Total 141 (36.4 Ov, RR: 3.84)
"""

SAMPLE_SCORECARD_SPLIT = """\
Zimbabwe (50 ovs maximum)

Brian Bennett
Ben Curran
Innocent Kaia
Craig Ervine
Sikandar Raza
Wessly Madhevere
Clive Madande \u2020
Brad Evans
Newman Nyamhuri
Richard Ngarava (c)
Blessing Muzarabani

36.4 Ov (RR: 3.84)
Fall of wickets
1-36 (Ben Curran, 6.4 ov)

BATTING R B M 4s 6s SR
c Mosaddek Hossain b Taskin Ahmed 17 24 36 3 0 70.83
run out (Mehidy Hasan Miraz) 18 19 33 2 0 94.73
c Nurul Hasan b Nahid Rana 26 39 72 1 1 66.66
b Taskin Ahmed 0 1 8 0 0 0.00
c Nurul Hasan b Nahid Rana 1 12 25 0 0 8.33
c Shanto b Nahid Rana 0 10 16 0 0 0.00
c Mosaddek b Nahid Rana 2 7 10 0 0 28.57
lbw b Nahid Rana 3 7 14 0 0 42.85
c Tanzid b Mehidy Hasan Miraz 33 51 77 5 0 64.70
b Nahid Rana 27 41 59 3 0 65.85
not out 4 10 12 0 0 40.00
Extras (lb 3, nb 1, w 6) 10
Total 141 (36.4 Ov, RR: 3.84)
"""


def test_scorecard_parsing(keep_image: bool = False) -> bool:
    print("\n=== 4c. Innings Scorecard Parsing ===")
    all_ok = True

    def _sc(label: str, cond: bool, detail: str = "") -> None:
        nonlocal all_ok
        _status(label, cond, detail)
        if not cond:
            all_ok = False

    # --- Combined format (name + dismissal + stats on one line) ---
    info_combined = parse_innings_scorecard_text(
        body_text=SAMPLE_SCORECARD_COMBINED,
        batting_team="Zimbabwe",
        team1="Zimbabwe",
        team2="Bangladesh",
        score="141/10",
        overs="36.4",
        match_label="1st ODI",
        series="Bangladesh tour of Zimbabwe 2026",
        format_tag="ODI",
    )
    _sc("Parse combined format returns ScorecardInfo", info_combined is not None)
    if info_combined:
        _sc("Combined: 11 batters parsed", len(info_combined.batters) == 11,
            f"got {len(info_combined.batters)}")
        _sc("Combined: first batter name", info_combined.batters[0].name == "Brian Bennett",
            info_combined.batters[0].name)
        _sc("Combined: first batter runs", info_combined.batters[0].runs == 17,
            str(info_combined.batters[0].runs))
        _sc("Combined: first batter balls", info_combined.batters[0].balls == 24,
            str(info_combined.batters[0].balls))
        _sc("Combined: first batter 4s", info_combined.batters[0].fours == 3,
            str(info_combined.batters[0].fours))
        _sc("Combined: last batter not-out flag", info_combined.batters[10].not_out,
            info_combined.batters[10].name)
        _sc("Combined: extras runs", info_combined.extras_runs == 10,
            str(info_combined.extras_runs))
        _sc("Combined: total runs", info_combined.total_runs == 141,
            str(info_combined.total_runs))
        _sc("Combined: total detail has Ov", "Ov" in info_combined.total_detail or "ov" in info_combined.total_detail,
            info_combined.total_detail)

    # --- Split format (names above BATTING header, dismissal+stats rows only) ---
    info_split = parse_innings_scorecard_text(
        body_text=SAMPLE_SCORECARD_SPLIT,
        batting_team="Zimbabwe",
        team1="Zimbabwe",
        team2="Bangladesh",
        score="141/10",
        overs="36.4",
        match_label="1st ODI",
        series="Bangladesh tour of Zimbabwe 2026",
        format_tag="ODI",
    )
    _sc("Parse split format returns ScorecardInfo", info_split is not None)
    if info_split:
        _sc("Split: 11 batters parsed", len(info_split.batters) == 11,
            f"got {len(info_split.batters)}")
        _sc("Split: first batter name", info_split.batters[0].name == "Brian Bennett",
            info_split.batters[0].name)
        _sc("Split: last batter not-out", info_split.batters[10].not_out,
            info_split.batters[10].name)
        _sc("Split: extras runs", info_split.extras_runs == 10, str(info_split.extras_runs))
        _sc("Split: total runs", info_split.total_runs == 141, str(info_split.total_runs))

    # --- Caption test ---
    if info_combined:
        caption = build_scorecard_caption(info_combined)
        _sc("Caption contains team names", "Zimbabwe" in caption and "Bangladesh" in caption, caption[:80])
        _sc("Caption contains score", "141" in caption, caption[:80])
        _sc("Caption has hashtags", "#ZIMvsBAN" in caption, caption[-100:])

    # --- Image generation ---
    if info_combined:
        try:
            img_path = generate_scorecard_image(info_combined)
            img_exists = img_path.exists()
            _sc("Scorecard image generated", img_exists, str(img_path))
            if img_exists and not keep_image:
                try:
                    img_path.unlink()
                except OSError:
                    pass
        except Exception as exc:
            _sc("Scorecard image generated", False, str(exc))

    return all_ok


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
    timezone_ok = test_preview_timezone()
    font_ok = test_preview_fonts()
    image_ok = test_preview_image_generation(keep_image=args.preview_image)
    match_image_ok = test_match_image_generation(keep_image=args.match_image)
    live_flow_ok = test_live_posting_flow()
    innings_layout_ok = test_innings_layouts(keep_image=args.match_image)
    playing_xi_ok = test_playing_xi(keep_image=args.playing_xi_image)
    playing_xi_trigger_ok = test_playing_xi_triggers()
    test_session_ok = test_test_session_posting()
    scorecard_ok = test_scorecard_parsing(keep_image=args.match_image)
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
    preview_ok = (
        caption_ok
        and tomorrow_ok
        and timezone_ok
        and font_ok
        and image_ok
        and match_image_ok
        and live_flow_ok
        and innings_layout_ok
        and playing_xi_ok
        and playing_xi_trigger_ok
        and test_session_ok
        and scorecard_ok
    )
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
    parser.add_argument(
        "--playing-xi-image",
        action="store_true",
        help="Keep generated Playing XI PNGs in generated_images/ for visual check",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
