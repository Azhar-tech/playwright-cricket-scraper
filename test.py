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
import re
import sys
from datetime import date
from pathlib import Path

import requests
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from match_image import (
    _abbrev_dismissal,
    _extract_time,
    _extract_venue_from_line,
    _is_score_line,
    _is_valid_cricket_score,
    _load_font,
    _parse_score_line,
    block_contains_valid_score,
    block_has_valid_live_score,
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
    scorecard_parse_valid,
    is_excluded_fixture,
)
from playing_xi import (
    build_playing_xi_caption,
    build_playing_xi_info,
    captain_display_name,
    captains_from_squads,
    find_team_captain,
    generate_playing_xi_image,
    make_playing_xi_key,
    match_playing_xi_urls,
    parse_playing_xi_from_match_text,
    parse_playing_xi_table_rows,
    enrich_captain_roles_from_text,
    _match_label_from_block,
)
from post_builder import build_match_post, build_result_post
from main import (
    TRACKED_TEAMS,
    PostState,
    PLAYING_XI_AFTER_TOSS_DELAY,
    PLAYING_XI_PENDING_INTERVAL,
    _best_block_for_playing_xi,
    _first_innings_batting_team,
    _playing_xi_allowed,
    _playing_xi_blocked_by_live,
    _playing_xi_pending,
    _scorecard_skip_reason,
    _should_post_innings_scorecard,
    _teams_from_block_names,
    _toss_announced,
    _toss_xi_delay_remaining,
    block_match_phase,
    detect_format,
    extract_all_postable_blocks,
    extract_tracked_match_data,
    generate_post,
    get_sleep_interval,
    get_xi_pending_sleep_interval,
    is_postable_block,
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

SAMPLE_PAK_WI_TOUR_MATCH = """
LIVE
Pakistan tour of West Indies 2026
Tour Match, Tarouba, July 18 - 21, 2026
Pakistan
184/2
West Indies
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

SAMPLE_TOSS_WI_NZ_BLOCK = """
New Zealand tour of West Indies 2026
2nd ODI (D/N)
West Indies
New Zealand
New Zealand won the toss and elected to field
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

SAMPLE_BAN_ZIM_CHASE_BLOCK = """
LIVE
ZIM vs BAN, 2nd T20I, Bulawayo
Zimbabwe
141/10
Bangladesh
45/2 (10 ov)
"""

SAMPLE_BAN_ZIM_CHASE_REVERSED = """
LIVE
BAN vs ZIM, 2nd T20I, Bulawayo
Bangladesh
45/2 (10 ov)
Zimbabwe
141/10
"""

SAMPLE_ENGLAND_ODI_SCORECARD_MODERN = """\
England Innings
India Innings
Match Flow
Info
BATTING
R B M 4s 6s SR
Ben Duckett
c & b Prince Yadav
141 135 172 16 4 103.70
Jacob Bethell
c Sharma b Prasidh Krishna
91 93 132 8 2 97.84
Joe Root
not out
74 48 72 8 1 154.16
Harry Brook (c)
c Kohli b Prasidh Krishna
14 12 18 1 0 116.66
Jos Buttler
not out
41 13 16 4 3 315.38
Extras (lb 9, nb 1, w 16) 26
Total 387 (50 Ov, RR: 7.74)
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

SAMPLE_NZ_WI_XI_TABLE = """
| | West Indies | New Zealand |
| 1 | Ackeem Auguste top-order batter | Henry Nicholls top-order batter |
| 2 | Justin Greaves allrounder | Will Young top-order batter |
| 3 | Keacy Carty batter | Nick Kelly top-order batter |
| 4 | Shai Hope † (c) wicketkeeper batter | Mark Chapman allrounder |
| 5 | Sherfane Rutherford middle-order batter | Tom Latham † wicketkeeper batter |
| 6 | Shimron Hetmyer middle-order batter | Michael Bracewell batting allrounder |
| 7 | Gudakesh Motie bowler | Mitchell Santner (c) bowling allrounder |
| 8 | Matthew Forde bowler | Nathan Smith bowling allrounder |
| 9 | Alzarri Joseph bowler | Kristian Clarke bowler |
| 10 | Jayden Seales bowler | Jacob Duffy bowler |
| 11 | Vitel Lawes bowler | Jayden Lennox bowler |
"""

SAMPLE_NZ_WI_LIVE_WITH_TOSS = """
LIVE
New Zealand tour of West Indies 2026
5th ODI (D/N), Bridgetown, July 21, 2026
West Indies
New Zealand
4/0
(0.3 ov)
West Indies won the toss and elected to field
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


def test_excluded_practice_fixtures() -> bool:
    print("\n=== 3c2. Practice / tour match exclusion ===")
    tour_block = SAMPLE_PAK_WI_TOUR_MATCH.strip()
    official_test = SAMPLE_LIVE_TEST_WITH_TOSS.strip()
    official_odi = SAMPLE_PREVIEW_GOOGLE_ZIM_BAN.strip()

    excluded = is_excluded_fixture(tour_block)
    not_postable = not is_postable_block(tour_block)
    official_ok = not is_excluded_fixture(official_test)
    odi_ok = not is_excluded_fixture(official_odi)
    odi_postable = is_postable_block(official_odi)

    _status("PAK vs WI Tour Match excluded", excluded)
    _status("Tour match not postable", not_postable)
    _status("Official 2nd Test not excluded", official_ok)
    _status("Official 1st ODI not excluded", odi_ok)
    _status("Official ODI preview still postable", odi_postable)

    return all([excluded, not_postable, official_ok, odi_ok, odi_postable])


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
    _status("XI gate blocks live block without toss", blocked)

    state_with_toss = PostState()
    state_with_toss.toss_posted.add(match_key)
    live_blocked = not _playing_xi_allowed(match_key, block, state_with_toss)
    _status("XI blocked when match is live (even with toss text)", live_blocked)

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

    # /live-cricket-score suffix must be stripped before building candidate URLs
    live_score_base = (
        "https://www.espncricinfo.com/series/bangladesh-in-zimbabwe-2026-1538288/"
        "zimbabwe-vs-bangladesh-2nd-odi-1538299/live-cricket-score"
    )
    live_score_urls = match_playing_xi_urls(live_score_base)
    expected_xi = (
        "https://www.espncricinfo.com/series/bangladesh-in-zimbabwe-2026-1538288/"
        "zimbabwe-vs-bangladesh-2nd-odi-1538299/match-playing-xi"
    )
    live_score_ok = live_score_urls[0] == expected_xi
    _status(
        "live-cricket-score URL stripped before building XI URL",
        live_score_ok,
        live_score_urls[0],
    )

    return live_only and toss_visible and xi_pending and blocked and live_blocked and url_ok and suffix_ok and live_score_ok


def test_playing_xi_live_guard() -> bool:
    print("\n=== 3k2. Playing XI live match guard ===")
    from datetime import datetime, timedelta

    live_block = SAMPLE_LIVE_WITH_TOSS_BLOCK.strip()
    toss_block = SAMPLE_TOSS_BLOCK.strip()
    match_key = make_match_key(toss_block)

    state = PostState()
    state.toss_posted.add(match_key)
    allowed_live = _playing_xi_allowed(match_key, live_block, state)
    abandoned = match_key in state.playing_xi_abandoned
    _status("Live block with toss posted is blocked but not abandoned", not allowed_live and not abandoned)

    state2 = PostState()
    state2.toss_posted.add(match_key)
    state2.toss_posted_at[match_key] = datetime.now().isoformat()
    allowed_before_delay = _playing_xi_allowed(match_key, toss_block, state2)
    delay_remaining = _toss_xi_delay_remaining(match_key, state2)
    _status(
        "XI allowed before delay wait (delay handled in post_playing_xi_if_ready)",
        allowed_before_delay and delay_remaining > 100,
        f"remaining={int(delay_remaining)}s",
    )

    state3 = PostState()
    state3.toss_posted.add(match_key)
    past = datetime.now() - timedelta(seconds=PLAYING_XI_AFTER_TOSS_DELAY + 10)
    state3.toss_posted_at[match_key] = past.isoformat()
    allowed_after_delay = _playing_xi_allowed(match_key, toss_block, state3)
    _status("XI allowed after toss delay on pre-match block", allowed_after_delay)

    best = _best_block_for_playing_xi([live_block, toss_block], state3)
    best_ok = best == toss_block
    _status("Best XI block prefers toss over live", best_ok, best or "none")

    dirty_label = "1st T20I, Bulawayo — (14.4/20 ov) 121/4"
    clean_block = f"Bangladesh tour of Zimbabwe 2026\n{dirty_label}\nBangladesh\nZimbabwe"
    label_ok = _match_label_from_block(clean_block) == "1st T20I, Bulawayo"
    _status("Match label strips live score suffix", label_ok, _match_label_from_block(clean_block))

    return (
        not allowed_live
        and not abandoned
        and allowed_before_delay
        and delay_remaining > 100
        and allowed_after_delay
        and best_ok
        and label_ok
    )


def test_playing_xi_retry_interval() -> bool:
    print("\n=== 3k3. Playing XI retry interval ===")
    from datetime import datetime

    toss_block = SAMPLE_TOSS_BLOCK.strip()
    match_key = make_match_key(toss_block)
    team1, team2 = _teams_from_block_names(toss_block)

    state = PostState()
    state.toss_posted.add(match_key)
    state.toss_posted_at[match_key] = datetime.now().isoformat()

    pending = _playing_xi_pending(match_key, team1, team2, state)
    xi_interval = get_xi_pending_sleep_interval(toss_block, state)
    sleep_with_pending = get_sleep_interval(toss_block, state)
    sleep_default = get_sleep_interval(toss_block)

    _status("XI still pending after toss", pending)
    _status(
        "Shorter interval when XI pending",
        xi_interval == PLAYING_XI_PENDING_INTERVAL,
        str(xi_interval),
    )
    _status(
        "get_sleep_interval uses XI pending interval",
        sleep_with_pending == PLAYING_XI_PENDING_INTERVAL,
        str(sleep_with_pending),
    )
    _status(
        "Default interval without pending state",
        sleep_default != PLAYING_XI_PENDING_INTERVAL or not pending,
        str(sleep_default),
    )

    done_state = PostState()
    done_state.toss_posted.add(match_key)
    for team in (team1, team2):
        done_state.playing_xi_posted.add(make_playing_xi_key(match_key, team))
    no_interval = get_xi_pending_sleep_interval(toss_block, done_state)
    _status("No short interval when both XI posted", no_interval is None)

    return (
        pending
        and xi_interval == PLAYING_XI_PENDING_INTERVAL
        and sleep_with_pending == PLAYING_XI_PENDING_INTERVAL
        and no_interval is None
    )


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
                if phase == "live":
                    from match_image import _premium_live_card_height

                    expected_h = _premium_live_card_height(info)
                else:
                    expected_h = 540 if phase == "toss" else 720
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


def test_toss_card_colors() -> bool:
    print("\n=== 3g2. Toss card per-team colors ===")
    from PIL import Image

    from match_image import generate_match_image, parse_match_block

    cases = [
        ("eng_ind", SAMPLE_TOSS_BLOCK.strip()),
        ("wi_nz", SAMPLE_TOSS_WI_NZ_BLOCK.strip()),
    ]
    pixels: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {}
    all_ok = True

    for key, block in cases:
        try:
            info = parse_match_block(block, "toss")
            path = generate_match_image(info)
        except Exception as exc:
            _status(f"Generate toss colors ({key})", False, str(exc))
            all_ok = False
            continue

        ok = False
        if path.exists() and path.stat().st_size >= 10_000:
            with Image.open(path) as img:
                if img.size == (1080, 540):
                    left_px = img.getpixel((50, 450))
                    right_px = img.getpixel((1030, 450))
                    pixels[key] = (left_px, right_px)
                    ok = True
        _status(f"Generate toss colors ({key})", ok, str(path))
        if not ok:
            all_ok = False
        else:
            path.unlink(missing_ok=True)

    diff_ok = False
    if "eng_ind" in pixels and "wi_nz" in pixels:
        eng_left, eng_right = pixels["eng_ind"]
        wi_left, wi_right = pixels["wi_nz"]
        diff_ok = eng_left != wi_left or eng_right != wi_right
    _status(
        "Toss background differs between matchups",
        diff_ok,
        f"ENG/IND left={pixels.get('eng_ind', ('?', '?'))[0]} "
        f"WI/NZ left={pixels.get('wi_nz', ('?', '?'))[0]}",
    )

    return all_ok and diff_ok


def _ensure_fixture_headshot(name: str, color: tuple[int, int, int]) -> Path:
    from PIL import Image

    from captain_toss import CAPTAIN_CACHE_DIR

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    CAPTAIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTAIN_CACHE_DIR / f"{slug}.jpg"
    if not path.exists() or path.stat().st_size < 1024:
        Image.new("RGB", (220, 220), color).save(path, "JPEG", quality=90)
    return path


def test_captain_from_squads() -> bool:
    print("\n=== 3g3. Captain extraction from Playing XI ===")
    squads = parse_playing_xi_from_match_text(
        SAMPLE_PLAYING_XI_TEXT.strip(), "England", "India"
    )
    eng_captain = find_team_captain(squads["England"])
    ind_captain = find_team_captain(squads["India"])
    captains = captains_from_squads(squads, "England", "India")

    eng_ok = eng_captain is not None and eng_captain.roles == "C+WK"
    ind_ok = ind_captain is not None and ind_captain.roles == "C"
    names_ok = (
        captain_display_name(eng_captain) == "Jos Buttler"
        and captain_display_name(ind_captain) == "Rohit Sharma"
    )
    both_ok = set(captains.keys()) == {"England", "India"}

    _status("England captain role parsed", eng_ok, eng_captain.roles if eng_captain else "")
    _status("India captain role parsed", ind_ok, ind_captain.roles if ind_captain else "")
    _status("Captain display names cleaned", names_ok)
    _status("Both captains found in squads", both_ok)

    return eng_ok and ind_ok and names_ok and both_ok


def test_nz_wi_captain_parse() -> bool:
    print("\n=== 3g3b. NZ vs WI ESPN table captain parsing ===")
    from playing_xi import PlayingXiPlayer

    table_squads = parse_playing_xi_table_rows(
        SAMPLE_NZ_WI_XI_TABLE.strip(), "West Indies", "New Zealand"
    )
    wi_ok = len(table_squads.get("West Indies", [])) == 11
    nz_ok = len(table_squads.get("New Zealand", [])) == 11
    wi_cap = find_team_captain(table_squads["West Indies"])
    nz_cap = find_team_captain(table_squads["New Zealand"])
    hope_ok = wi_cap is not None and "HOPE" in wi_cap.name and wi_cap.roles in ("C", "C+WK")
    santner_ok = nz_cap is not None and "SANTNER" in nz_cap.name and nz_cap.roles == "C"

    json_like = {
        "West Indies": [
            PlayingXiPlayer(number=i, name=f"PLAYER {i}", roles="")
            for i in range(1, 12)
        ],
        "New Zealand": [
            PlayingXiPlayer(number=i, name=f"PLAYER {i}", roles="")
            for i in range(1, 12)
        ],
    }
    json_like["West Indies"][3] = PlayingXiPlayer(number=4, name="SHAI HOPE", roles="")
    json_like["New Zealand"][6] = PlayingXiPlayer(number=7, name="MITCHELL SANTNER", roles="")
    enrich_captain_roles_from_text(json_like, SAMPLE_NZ_WI_XI_TABLE)
    enriched_wi = find_team_captain(json_like["West Indies"])
    enriched_nz = find_team_captain(json_like["New Zealand"])
    enrich_ok = (
        enriched_wi is not None
        and enriched_wi.roles in ("C", "C+WK")
        and enriched_nz is not None
        and enriched_nz.roles == "C"
    )

    _status("WI table XI parsed (11 players)", wi_ok)
    _status("NZ table XI parsed (11 players)", nz_ok)
    _status("Shai Hope captain from table", hope_ok, wi_cap.name if wi_cap else "")
    _status("Mitchell Santner captain from table", santner_ok, nz_cap.name if nz_cap else "")
    _status("JSON squads enriched with captain roles", enrich_ok)

    return wi_ok and nz_ok and hope_ok and santner_ok and enrich_ok


def test_toss_before_live_order() -> bool:
    print("\n=== 3g3c. Toss-before-live XI catch-up ===")
    from datetime import datetime

    toss_block = SAMPLE_TOSS_BLOCK.strip()
    live_block = SAMPLE_NZ_WI_LIVE_WITH_TOSS.strip()
    match_key = make_match_key(toss_block)

    state = PostState()
    state.toss_posted.add(match_key)
    state.live_last[match_key] = {
        "at": datetime.now().isoformat(),
        "text": "live",
        "signature": "live",
    }

    blocked_live = _playing_xi_blocked_by_live(live_block, state, match_key)
    allowed_toss = _playing_xi_allowed(match_key, toss_block, state)
    _status("Live block still blocked after toss posted", blocked_live)
    _status("Toss block allowed for XI catch-up after live started", allowed_toss)

    state_no_toss = PostState()
    state_no_toss.live_last[match_key] = state.live_last[match_key]
    blocked_without_toss = _playing_xi_blocked_by_live(toss_block, state_no_toss, match_key)
    _status("Toss block blocked when live started before toss posted", blocked_without_toss)

    return blocked_live and allowed_toss and blocked_without_toss


def test_captain_toss_image(keep_image: bool = False) -> bool:
    print("\n=== 3g4. Captain toss graphic ===")
    from PIL import Image

    from match_image import CaptainInfo, CaptainTossInfo, generate_captain_toss_image, parse_match_block

    buttler_path = _ensure_fixture_headshot("Jos Buttler", (30, 80, 160))
    rohit_path = _ensure_fixture_headshot("Rohit Sharma", (180, 40, 40))
    info = parse_match_block(SAMPLE_TOSS_BLOCK.strip(), "toss")
    captains = CaptainTossInfo(
        team1_captain=CaptainInfo(team="England", name="Jos Buttler", image_path=buttler_path),
        team2_captain=CaptainInfo(team="India", name="Rohit Sharma", image_path=rohit_path),
    )

    try:
        path = generate_captain_toss_image(info, captains)
        ok = path.exists() and path.stat().st_size >= 10_000
        if ok:
            with Image.open(path) as img:
                ok = img.size == (1080, 540)
        _status("Generate captain toss PNG", ok, str(path))
        if ok and not keep_image:
            path.unlink(missing_ok=True)
        return ok
    except Exception as exc:
        _status("Generate captain toss PNG", False, str(exc))
        return False


def test_toss_fallback() -> bool:
    print("\n=== 3g5. Toss flag fallback without captain images ===")
    from PIL import Image

    from match_image import CaptainInfo, CaptainTossInfo, generate_match_image, parse_match_block

    info = parse_match_block(SAMPLE_TOSS_BLOCK.strip(), "toss")
    partial = CaptainTossInfo(
        team1_captain=CaptainInfo(team="England", name="Jos Buttler", image_path=None),
        team2_captain=CaptainInfo(team="India", name="Rohit Sharma", image_path=None),
    )
    path = generate_match_image(info, partial)
    ok = path.exists() and path.stat().st_size >= 10_000
    if ok:
        with Image.open(path) as img:
            ok = img.size == (1080, 540)
    _status("Missing captain images fall back to flag toss card", ok, str(path))
    if ok:
        path.unlink(missing_ok=True)
    return ok


def test_google_captain_lookup() -> bool:
    print("\n=== 3g6. Google captain lookup ===")
    from captain_toss import (
        _extract_captain_name_from_text,
        _format_captain_query,
        _normalize_captain_name,
        try_build_captain_toss_info,
    )

    ok_query = _format_captain_query("India", "T20") == "India T20I cricket team captain"
    ok_women = (
        _format_captain_query("Sri Lanka Women", "ODI")
        == "Sri Lanka Women ODI cricket team captain"
    )
    _status("Format-aware captain query", ok_query and ok_women)

    ok_extract = (
        _extract_captain_name_from_text("The current captain is Hardik Pandya", "India")
        == "Hardik Pandya"
    )
    _status("Extract captain from snippet", ok_extract)

    ok_norm = (
        _normalize_captain_name("Hardik Pandya - India T20I captain", "India") == "Hardik Pandya"
    )
    _status("Normalize captain name", ok_norm)

    async def _run_mock() -> bool:
        from unittest.mock import AsyncMock, patch

        info = parse_match_block(SAMPLE_TOSS_BLOCK.strip(), "toss")
        buttler_path = _ensure_fixture_headshot("Jos Buttler", (30, 80, 160))
        rohit_path = _ensure_fixture_headshot("Rohit Sharma", (180, 40, 40))
        with patch("captain_toss.lookup_captain_name_via_google", new_callable=AsyncMock) as google:
            with patch("captain_toss.fetch_captain_headshot", new_callable=AsyncMock) as headshot:
                google.side_effect = (
                    lambda page, team, fmt: "Jos Buttler" if team == "England" else "Rohit Sharma"
                )
                headshot.side_effect = (
                    lambda page, name, team: buttler_path if "Buttler" in name else rohit_path
                )
                result = await try_build_captain_toss_info(None, None, info, "T20")
                return (
                    result is not None
                    and result.team1_captain.name == "Jos Buttler"
                    and result.team2_captain.name == "Rohit Sharma"
                )

    mock_ok = asyncio.run(_run_mock())
    _status("try_build uses Google names without ESPN", mock_ok)

    return all([ok_query, ok_women, ok_extract, ok_norm, mock_ok])


def test_toss_retry_window() -> bool:
    print("\n=== 3g7. Captain toss retry window ===")
    from datetime import datetime, timedelta

    from main import (
        CAPTAIN_TOSS_RETRY_SECONDS,
        _captain_toss_pending_remaining,
        _captain_toss_should_wait,
        _generate_toss_image_or_wait,
    )

    state = PostState()
    match_key = "test-match"

    ok_first = _captain_toss_should_wait(match_key, state)
    _status("First attempt should wait", ok_first)

    state.toss_captain_pending_at[match_key] = datetime.now().isoformat()
    ok_recent = _captain_toss_should_wait(match_key, state)
    _status("Recent pending should wait", ok_recent)

    old = datetime.now() - timedelta(seconds=CAPTAIN_TOSS_RETRY_SECONDS + 1)
    state.toss_captain_pending_at[match_key] = old.isoformat()
    ok_expired = not _captain_toss_should_wait(match_key, state)
    _status("Expired pending should not wait", ok_expired)

    remaining = _captain_toss_pending_remaining(match_key, state)
    ok_remaining = remaining == 0.0
    _status("Expired pending has 0s remaining", ok_remaining)

    async def _run_wait() -> bool:
        from unittest.mock import AsyncMock, patch

        state2 = PostState()
        block = SAMPLE_TOSS_BLOCK.strip()
        mk = make_match_key(block)
        info = parse_match_block(block, "toss")
        with patch("main.try_build_captain_toss_info", new_callable=AsyncMock) as build:
            build.return_value = None
            path = await _generate_toss_image_or_wait(None, block, mk, {}, info, state2)
            return path is None and mk in state2.toss_captain_pending_at

    wait_ok = asyncio.run(_run_wait())
    _status("Generate toss returns None while waiting", wait_ok)

    return all([ok_first, ok_recent, ok_expired, ok_remaining, wait_ok])


SAMPLE_LIVE_STATS_JSON = """
<script id="__NEXT_DATA__" type="application/json">{
  "match": {
    "innings": [{
      "batsmen": [
        {"player": {"name": "Wessly Madhevere"}, "runs": 30, "balls": 28, "isStriker": true},
        {"player": {"name": "Tadiwanashe Marumani"}, "runs": 10, "balls": 9}
      ],
      "bowlers": [
        {"player": {"name": "Ashok Sharma"}, "wickets": 0, "runsConceded": 29, "overs": 4.0},
        {"player": {"name": "Ravi Bishnoi"}, "wickets": 1, "runsConceded": 24, "overs": 3.5, "isActive": true}
      ]
    }]
  }
}</script>
"""


def test_live_player_stats() -> bool:
    print("\n=== 3g8. Live batter/bowler stats ===")
    from live_player_stats import (
        abbrev_player_name,
        format_batter_line,
        format_bowler_line,
        parse_live_player_stats_from_html,
        parse_live_player_stats_from_next_data,
    )

    ok_batter_fmt = format_batter_line("Wessly Madhevere", 30, 28, True, is_striker=True) == (
        "• W. Madhevere: 30* (28)"
    )
    _status("Batter line format", ok_batter_fmt)

    ok_bowler_fmt = format_bowler_line("Ravi Bishnoi", 1, 24, 3.5, is_active=True) == (
        "R. Bishnoi: 1/24 (3.5) •"
    )
    _status("Bowler line format", ok_bowler_fmt)

    ok_abbrev = abbrev_player_name("Hardik Pandya") == "H. Pandya"
    _status("Abbreviate player name", ok_abbrev)

    batters, bowlers = parse_live_player_stats_from_next_data(SAMPLE_LIVE_STATS_JSON)
    ok_json = (
        len(batters) == 2
        and batters[0].name == "Wessly Madhevere"
        and batters[0].is_striker
        and len(bowlers) == 2
        and any(b.name == "Ravi Bishnoi" for b in bowlers)
    )
    _status("Parse batters/bowlers from NEXT_DATA", ok_json)

    lines_b, lines_w = parse_live_player_stats_from_html(SAMPLE_LIVE_STATS_JSON, "")
    ok_lines = (
        any("Madhevere" in line for line in lines_b)
        and any("Bishnoi" in line for line in lines_w)
    )
    _status("Formatted live stat lines", ok_lines, f"{lines_b} | {lines_w}")

    info = parse_match_block(SAMPLE_FIRST_INNINGS_ODI.strip(), "live")
    info.batters = lines_b or ["R. Sharma: 98* (122)"]
    info.bowlers = lines_w or ["T. Boult: 1/51 (9.0)"]
    info.batting_team = info.team1
    info.bowling_team = info.team2
    try:
        from PIL import Image

        path = generate_match_image(info)
        with Image.open(path) as img:
            from match_image import _premium_live_card_height

            img_ok = img.size == (1080, _premium_live_card_height(info))
        _status("Live image with player stats uses premium layout", img_ok, str(img.size))
        path.unlink(missing_ok=True)
    except Exception as exc:
        _status("Live image with player stats uses tall layout", False, str(exc))
        img_ok = False

    async def _run_skip_fetch() -> bool:
        from unittest.mock import AsyncMock, patch

        from main import _enrich_live_player_stats

        info2 = parse_match_block(SAMPLE_FIRST_INNINGS_ODI.strip(), "live")
        with patch("main.fetch_live_player_stats", new_callable=AsyncMock) as fetch:
            await _enrich_live_player_stats(None, info2, "test", {"test": "http://example.com"})
            return fetch.await_count == 0

    skip_ok = asyncio.run(_run_skip_fetch())
    _status("Skip ESPN fetch when block already has stats", skip_ok)

    return all([ok_batter_fmt, ok_bowler_fmt, ok_abbrev, ok_json, ok_lines, img_ok, skip_ok])


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


def test_odi_overs_score_parsing() -> bool:
    print("\n=== 3h2. ODI overs/score parsing ===")
    score_a, overs_a = _parse_score_line("50/5 (47.5/50 ov)")
    ok_a = score_a == "50/5" and overs_a == "(47.5)"
    _status("50/5 not confused with 5/50 from overs", ok_a, f"{score_a} {overs_a}")

    score_b, overs_b = _parse_score_line("136/3 (36/50 ov)")
    ok_b = score_b == "136/3" and overs_b == "(36)"
    _status("136/3 not confused with 36/50 from overs", ok_b, f"{score_b} {overs_b}")

    score_c, overs_c = _parse_score_line("50/5 (47.5 ov)")
    ok_c = score_c == "50/5" and overs_c == "(47.5)"
    _status("Simple overs format still parses", ok_c, f"{score_c} {overs_c}")

    return ok_a and ok_b and ok_c


def test_season_score_rejection() -> bool:
    print("\n=== 3h3. Season score rejection ===")
    tour_line = "Pakistan Women tour of Sri Lanka 2023/24"
    ok_tour_line = not _is_score_line(tour_line)
    tour_score, tour_overs = _parse_score_line(tour_line)
    ok_tour_parse = tour_score == "" and tour_overs == ""
    _status(
        "Tour season line rejected",
        ok_tour_line and ok_tour_parse,
        f"is_score={_is_score_line(tour_line)} score={tour_score!r}",
    )

    score_a, overs_a = _parse_score_line("45/2 (10.2 ov)")
    ok_a = score_a == "45/2" and overs_a == "(10.2)"
    _status("45/2 with overs parses", ok_a, f"{score_a} {overs_a}")

    ok_valid = _is_valid_cricket_score("184/2")
    _status("184/2 is valid cricket score", ok_valid)

    score_b, overs_b = _parse_score_line("50/5 (47.5/50 ov)")
    ok_b = score_b == "50/5" and overs_b == "(47.5)"
    _status("50/5 ODI overs still valid", ok_b, f"{score_b} {overs_b}")

    season_block = """
LIVE
Sri Lanka Women
Pakistan Women
Pakistan Women tour of Sri Lanka 2023/24
R Premadasa Stadium, Colombo
""".strip()
    live_info = parse_match_block(season_block, "live")
    ok_block = live_info.score1 == "" and "2023/24" not in live_info.headline
    _status(
        "Season-only live block has empty score1",
        ok_block,
        f"score1={live_info.score1!r} headline={live_info.headline!r}",
    )

    ok_gate = not block_has_valid_live_score(season_block)
    _status("block_has_valid_live_score on season block", ok_gate)

    ok_contains = not block_contains_valid_score(season_block)
    _status("block_contains_valid_score on season block", ok_contains)

    return all(
        [
            ok_tour_line,
            ok_tour_parse,
            ok_a,
            ok_valid,
            ok_b,
            ok_block,
            ok_gate,
            ok_contains,
        ]
    )


def test_premium_live_card(keep_image: bool = False) -> bool:
    print("\n=== 3i0. Premium live card ===")
    from PIL import Image

    from match_image import MatchUpdateInfo, _premium_live_card_height

    img_ok = True

    first_info = parse_match_block(SAMPLE_FIRST_INNINGS_ODI.strip(), "live")
    first_expected = (1080, _premium_live_card_height(first_info))
    try:
        first_path = generate_match_image(first_info)
        with Image.open(first_path) as img:
            first_ok = img.size == first_expected and first_path.stat().st_size >= 10_000
        _status("First innings premium PNG", first_ok, f"{img.size} expected {first_expected}")
        if not first_ok:
            img_ok = False
        elif keep_image:
            print(f"       Saved first innings premium at: {first_path}")
        else:
            first_path.unlink(missing_ok=True)
    except Exception as exc:
        _status("First innings premium PNG", False, str(exc))
        img_ok = False

    chase_info = parse_match_block(SAMPLE_CHASE_ODI.strip(), "live")
    chase_expected = (1080, _premium_live_card_height(chase_info))
    try:
        chase_path = generate_match_image(chase_info)
        with Image.open(chase_path) as img:
            chase_ok = (
                img.size == chase_expected
                and chase_path.stat().st_size >= 10_000
                and chase_info.score1
                and chase_info.score2
            )
        _status("Chase premium PNG (both scores parsed)", chase_ok, f"{img.size} expected {chase_expected}")
        if not chase_ok:
            img_ok = False
        elif keep_image:
            print(f"       Saved chase premium at: {chase_path}")
        else:
            chase_path.unlink(missing_ok=True)
    except Exception as exc:
        _status("Chase premium PNG (both scores parsed)", False, str(exc))
        img_ok = False

    with_stats = parse_match_block(SAMPLE_FIRST_INNINGS_ODI.strip(), "live")
    with_stats.batters = ["R. Sharma: 98* (122)", "S. Gill: 45 (52)"]
    with_stats.bowlers = ["T. Boult: 1/51 (9.0)", "M. Santner: 0/28 (8.0)"]
    with_stats.batting_team = with_stats.team1
    with_stats.bowling_team = with_stats.team2
    without_stats = parse_match_block(SAMPLE_FIRST_INNINGS_ODI.strip(), "live")
    without_stats.batters = []
    without_stats.bowlers = []
    without_stats.match_key = f"{without_stats.match_key}|no-stats"

    try:
        stats_path = generate_match_image(with_stats)
        plain_path = generate_match_image(without_stats)
        with Image.open(stats_path) as stats_img, Image.open(plain_path) as plain_img:
            stats_expected = (1080, _premium_live_card_height(with_stats))
            plain_expected = (1080, _premium_live_card_height(without_stats))
            panels_ok = (
                stats_img.size == stats_expected
                and plain_img.size == plain_expected
                and stats_img.height > plain_img.height
                and stats_path.stat().st_size > plain_path.stat().st_size
            )
        _status(
            "Stats panels increase output size",
            panels_ok,
            f"{stats_img.size} vs {plain_img.size} ({stats_path.stat().st_size} vs {plain_path.stat().st_size})",
        )
        if not panels_ok:
            img_ok = False
        elif keep_image:
            print(f"       Saved with-stats premium at: {stats_path}")
        else:
            stats_path.unlink(missing_ok=True)
            plain_path.unlink(missing_ok=True)
    except Exception as exc:
        _status("Stats panels increase output size", False, str(exc))
        img_ok = False

    long_score_info = MatchUpdateInfo(
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
    try:
        stumps_path = generate_match_image(long_score_info)
        stumps_expected = (1080, _premium_live_card_height(long_score_info))
        with Image.open(stumps_path) as img:
            stumps_ok = img.size == stumps_expected and stumps_path.stat().st_size >= 5_000
        _status("Test stumps premium PNG", stumps_ok, f"{img.size} expected {stumps_expected}")
        if not stumps_ok:
            img_ok = False
        elif not keep_image:
            stumps_path.unlink(missing_ok=True)
    except Exception as exc:
        _status("Test stumps premium PNG", False, str(exc))
        img_ok = False

    return img_ok


def test_innings_layouts(keep_image: bool = False) -> bool:
    print("\n=== 3i. Innings-aware live layouts ===")
    from PIL import Image

    from match_image import MatchUpdateInfo, _premium_live_card_height

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
                expected = (1080, _premium_live_card_height(info))
                ok = img.size == expected and path.stat().st_size >= 10_000
            _status(f"Generate {label} layout PNG", ok, f"{img.size} expected {expected}")
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
            long_expected = (1080, _premium_live_card_height(long_score_info))
            long_score_ok = img_ls.size == long_expected and long_path.stat().st_size >= 5_000
        _status("Long Test score renders without clipping", long_score_ok, f"{img_ls.size} expected {long_expected}")
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

    # --- Image generation (premium dark layout) ---
    if info_combined:
        try:
            img_path = generate_scorecard_image(info_combined)
            img_exists = img_path.exists()
            img_size = img_path.stat().st_size if img_exists else 0
            _sc("Scorecard image generated", img_exists, str(img_path))
            if img_exists:
                from PIL import Image

                with Image.open(img_path) as img:
                    expected_h = (
                        168
                        + 11 * 46
                        + 96
                        + 20
                    )
                    _sc(
                        "Premium image height fits 11 rows",
                        img.height >= expected_h - 10,
                        f"{img.width}x{img.height}",
                    )
                    _sc(
                        "Premium image size >= 10KB",
                        img_size >= 10_000,
                        f"{img_size} bytes",
                    )
            _sc(
                "Dismissal abbrev",
                _abbrev_dismissal("c Mosaddek Hossain b Taskin Ahmed")
                == "c MOSADDEK b TASKIN",
                _abbrev_dismissal("c Mosaddek Hossain b Taskin Ahmed"),
            )
            if img_exists and not keep_image:
                try:
                    img_path.unlink()
                except OSError:
                    pass
        except Exception as exc:
            _sc("Scorecard image generated", False, str(exc))

    return all_ok


def test_scorecard_trigger_window() -> bool:
    print("\n=== 4e. Innings Scorecard Trigger Window ===")
    all_ok = True

    def _tw(label: str, cond: bool, detail: str = "") -> None:
        nonlocal all_ok
        _status(label, cond, detail)
        if not cond:
            all_ok = False

    state = PostState()

    break_info = parse_match_block(SAMPLE_INNINGS_BREAK_BLOCK.strip(), "live")
    break_info.match_key = make_match_key(SAMPLE_INNINGS_BREAK_BLOCK.strip())
    _tw(
        "Innings break triggers scorecard",
        _should_post_innings_scorecard(
            break_info, state, SAMPLE_INNINGS_BREAK_BLOCK.strip()
        ),
    )
    _tw(
        "First innings team at break",
        _first_innings_batting_team(break_info) == "Pakistan",
        _first_innings_batting_team(break_info),
    )

    chase_info = parse_match_block(SAMPLE_CHASE_BLOCK.strip(), "live")
    chase_info.match_key = make_match_key(SAMPLE_CHASE_BLOCK.strip())
    _tw(
        "Chase triggers scorecard",
        _should_post_innings_scorecard(chase_info, state, SAMPLE_CHASE_BLOCK.strip()),
    )
    _tw(
        "First innings team during chase",
        _first_innings_batting_team(chase_info) == "Pakistan",
        _first_innings_batting_team(chase_info),
    )

    finished_block = """
LIVE
PAK vs IND, 2nd T20I
Pakistan
180/7
India
(20/20 ov) 181/3
IND won by 7 wickets
"""
    finished_info = parse_match_block(finished_block.strip(), "live")
    finished_info.match_key = make_match_key(finished_block.strip())
    _tw(
        "Result / won-by blocks scorecard",
        not _should_post_innings_scorecard(finished_info, state, finished_block.strip()),
    )

    sc_key = f"{break_info.match_key}|{_first_innings_batting_team(break_info)}"
    state.scorecard_innings_posted.add(sc_key)
    _tw(
        "Already posted blocks duplicate",
        not _should_post_innings_scorecard(
            break_info, state, SAMPLE_INNINGS_BREAK_BLOCK.strip()
        ),
    )

    return all_ok


def test_ban_zim_chase_scorecard() -> bool:
    print("\n=== 4f. BAN vs ZIM Chase Scorecard Detection ===")
    all_ok = True

    def _bz(label: str, cond: bool, detail: str = "") -> None:
        nonlocal all_ok
        _status(label, cond, detail)
        if not cond:
            all_ok = False

    state = PostState()

    for label, sample in (
        ("Zimbabwe listed first", SAMPLE_BAN_ZIM_CHASE_BLOCK.strip()),
        ("Bangladesh listed first", SAMPLE_BAN_ZIM_CHASE_REVERSED.strip()),
    ):
        info = parse_match_block(sample, "live")
        info.match_key = make_match_key(sample)
        _bz(
            f"{label}: chase detected",
            info.innings_status == "chase",
            info.innings_status,
        )
        _bz(
            f"{label}: Bangladesh chasing",
            info.batting_team == "Bangladesh",
            info.batting_team,
        )
        _bz(
            f"{label}: Zimbabwe is first innings team",
            _first_innings_batting_team(info) == "Zimbabwe",
            _first_innings_batting_team(info),
        )
        _bz(
            f"{label}: scorecard eligible",
            _should_post_innings_scorecard(info, state, sample),
        )
        skip = _scorecard_skip_reason(info, state, sample)
        _bz(f"{label}: no skip reason", skip is None, skip or "")

    return all_ok


def test_scorecard_catchup_on_live_cooldown() -> bool:
    print("\n=== 4g. Scorecard Catch-up During Live Cooldown ===")
    all_ok = True

    def _cd(label: str, cond: bool, detail: str = "") -> None:
        nonlocal all_ok
        _status(label, cond, detail)
        if not cond:
            all_ok = False

    state = PostState()
    block = SAMPLE_BAN_ZIM_CHASE_BLOCK.strip()
    match_key = make_match_key(block)
    info = parse_match_block(block, "live")
    info.match_key = match_key

    from datetime import datetime, timedelta

    state.live_last[match_key] = {
        "at": (datetime.now() - timedelta(seconds=60)).isoformat(),
        "signature": make_live_signature(info),
        "text": "prior live post",
    }

    live_allowed = should_post_live(
        block,
        make_live_signature(info),
        state,
        fmt="T20",
    )
    _cd("Live update blocked on cooldown", not live_allowed)
    _cd(
        "Scorecard still eligible during chase",
        _should_post_innings_scorecard(info, state, block),
    )
    _cd(
        "Skip reason absent while chase live",
        _scorecard_skip_reason(info, state, block) is None,
    )

    return all_ok


def test_england_modern_scorecard() -> bool:
    print("\n=== 4h. England ODI Modern Scorecard Parse ===")
    all_ok = True

    def _eng(label: str, cond: bool, detail: str = "") -> None:
        nonlocal all_ok
        _status(label, cond, detail)
        if not cond:
            all_ok = False

    info = parse_innings_scorecard_text(
        body_text=SAMPLE_ENGLAND_ODI_SCORECARD_MODERN,
        batting_team="England",
        team1="England",
        team2="India",
        score="387/3",
        overs="50",
        match_label="3rd ODI",
        series="India tour of England 2026",
        format_tag="ODI",
    )
    _eng("Parse modern ESPN layout", info is not None)
    if info:
        names = [b.name for b in info.batters]
        _eng("Five batters parsed", len(info.batters) == 5, str(len(info.batters)))
        _eng("Duckett parsed", "Duckett" in names[0], names[0])
        _eng("Bethell parsed", any("Bethell" in n for n in names), str(names))
        _eng("Root not out", info.batters[2].not_out and info.batters[2].runs == 74)
        _eng("No UI tab labels", not any(n in {"Innings", "Flow", "Info"} for n in names), str(names))
        _eng("Validation passes", scorecard_parse_valid(info))
        _eng("Extras runs", info.extras_runs == 26, str(info.extras_runs))
        _eng("Total runs", info.total_runs == 387, str(info.total_runs))
        try:
            img_path = generate_scorecard_image(info)
            from PIL import Image

            with Image.open(img_path) as img:
                _eng("Image height fits 5 rows", img.height >= 500, f"{img.width}x{img.height}")
            img_path.unlink(missing_ok=True)
        except Exception as exc:
            _eng("Image generation", False, str(exc))

    junk = parse_innings_scorecard_text(
        body_text="""England Innings
India Innings
Match Flow
Info
BATTING
R B M 4s 6s SR
not out
74 48 72 8 1 154.16
not out
41 13 16 4 3 315.38
Extras (lb 9) 26
Total 387 (50 Ov)
""",
        batting_team="England",
        team1="England",
        team2="India",
        score="387/3",
        overs="50",
        match_label="3rd ODI",
        series="India tour of England 2026",
        format_tag="ODI",
    )
    if junk and len(junk.batters) < 3:
        _eng("Junk partial parse rejected by validation", not scorecard_parse_valid(junk))
    else:
        _eng("Junk tab-zip layout blocked", junk is None or not scorecard_parse_valid(junk))

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
    practice_exclude_ok = test_excluded_practice_fixtures()
    caption_ok = test_preview_caption()
    tomorrow_ok = test_preview_tomorrow()
    timezone_ok = test_preview_timezone()
    font_ok = test_preview_fonts()
    image_ok = test_preview_image_generation(keep_image=args.preview_image)
    match_image_ok = test_match_image_generation(keep_image=args.match_image)
    toss_colors_ok = test_toss_card_colors()
    captain_squads_ok = test_captain_from_squads()
    nz_wi_captain_ok = test_nz_wi_captain_parse()
    toss_before_live_ok = test_toss_before_live_order()
    captain_toss_ok = test_captain_toss_image(keep_image=args.match_image)
    toss_fallback_ok = test_toss_fallback()
    google_captain_ok = test_google_captain_lookup()
    toss_retry_ok = test_toss_retry_window()
    live_stats_ok = test_live_player_stats()
    premium_live_ok = test_premium_live_card(keep_image=args.match_image)
    live_flow_ok = test_live_posting_flow()
    score_parse_ok = test_odi_overs_score_parsing()
    season_score_ok = test_season_score_rejection()
    innings_layout_ok = test_innings_layouts(keep_image=args.match_image)
    playing_xi_ok = test_playing_xi(keep_image=args.playing_xi_image)
    playing_xi_trigger_ok = test_playing_xi_triggers()
    playing_xi_guard_ok = test_playing_xi_live_guard()
    playing_xi_retry_ok = test_playing_xi_retry_interval()
    test_session_ok = test_test_session_posting()
    scorecard_ok = test_scorecard_parsing(keep_image=args.match_image)
    scorecard_trigger_ok = test_scorecard_trigger_window()
    ban_zim_sc_ok = test_ban_zim_chase_scorecard()
    scorecard_cooldown_ok = test_scorecard_catchup_on_live_cooldown()
    england_sc_ok = test_england_modern_scorecard()
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
        and toss_colors_ok
        and captain_squads_ok
        and nz_wi_captain_ok
        and toss_before_live_ok
        and captain_toss_ok
        and toss_fallback_ok
        and google_captain_ok
        and toss_retry_ok
        and live_stats_ok
        and premium_live_ok
        and live_flow_ok
        and score_parse_ok
        and season_score_ok
        and innings_layout_ok
        and playing_xi_ok
        and playing_xi_trigger_ok
        and playing_xi_guard_ok
        and playing_xi_retry_ok
        and test_session_ok
        and scorecard_ok
        and scorecard_trigger_ok
        and ban_zim_sc_ok
        and scorecard_cooldown_ok
        and england_sc_ok
    )
    if fb_ok and multi_ok and rules_ok and practice_exclude_ok and preview_ok and posts_ok:
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
