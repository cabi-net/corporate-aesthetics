"""
Tests for the text handling, validation, Wayback fallback and saving in
scrape.py. Nothing here touches the network or opens a browser (the
in-page extractor is covered in test_extract.py).

The clean_chunks tests run against the archive itself (snapshots/), to
prove the cleaning rules remove the noise that was actually recorded in
format-1 snapshots and keep the policy text that changed.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

import scrape
from scrape import (
    ScrapeError,
    TARGETS,
    build_snapshot,
    clean_chunks,
    normalize,
    save_snapshot,
    scrape_wayback,
    text_to_blocks,
    validate,
    wayback_captures,
)

SNAPSHOTS = Path(__file__).parent.parent / "snapshots"


def archived(name, date):
    """Chunks of an archived snapshot, minus any Wayback header."""
    text = (SNAPSHOTS / name / f"{date}.txt").read_text(encoding="utf-8")
    return [c for c in text.split("\n\n") if c and not c.startswith("[")]


def recleaned(name, date):
    return clean_chunks(archived(name, date), TARGETS[name].get("drop", ()))


# ─── normalize ───────────────────────────────────────────────────────────────

def test_normalize_collapses_whitespace():
    assert normalize("  a \n\t b  ") == "a b"


def test_normalize_treats_nbsp_as_space():
    assert normalize("Privacy Policy") == normalize("Privacy Policy")


def test_normalize_strips_zero_width_characters():
    assert normalize("pol​icy﻿") == "policy"


# ─── text_to_blocks ──────────────────────────────────────────────────────────

def test_blocks_keep_every_line_however_short():
    assert text_to_blocks("location\n\ndevice names\nNote") == ["location", "device names", "Note"]


def test_blocks_keep_heading_and_list_markers():
    assert text_to_blocks("# Change log\n- Sign into Azure") == ["# Change log", "- Sign into Azure"]


def test_marker_on_its_own_line_attaches_to_the_next_line():
    assert text_to_blocks("•\nInformation that is available on the internet") == [
        "- Information that is available on the internet"
    ]
    assert text_to_blocks("#\n\nWhat is generative AI?") == ["# What is generative AI?"]


def test_stacked_markers_collapse_and_heading_wins():
    assert text_to_blocks("- • Privacy and security") == ["- Privacy and security"]
    assert text_to_blocks("-\n# A heading inside a list item") == ["# A heading inside a list item"]


def test_marker_does_not_leak_past_a_line_it_was_used_on():
    assert text_to_blocks("- item\nplain paragraph") == ["- item", "plain paragraph"]


def test_table_rows_become_pipe_separated():
    assert text_to_blocks("Date\tChanges\n3 October 2025\tRenamed") == [
        "Date | Changes", "3 October 2025 | Renamed",
    ]


def test_lines_without_letters_or_digits_are_skipped():
    assert text_to_blocks("{\n.\n\u2060\nreal text\n}") == ["real text"]


def test_blocks_apply_drop_patterns_and_strip_strings():
    text = "Copy\nExecute Agreement (opens in a new window)\nCopy this value"
    assert text_to_blocks(text, drop=[r"^Copy$"], strip=["(opens in a new window)"]) == [
        "Execute Agreement", "Copy this value",
    ]


def test_blocks_normalise_whitespace():
    assert text_to_blocks("Privacy\u00a0Policy   and\u200b more") == ["Privacy Policy and more"]


def test_blocks_of_empty_text():
    assert text_to_blocks("") == []
    assert text_to_blocks("\n \n\t\n") == []


# ─── clean_chunks ────────────────────────────────────────────────────────────

def test_clean_removes_exact_duplicates_keeping_first():
    assert clean_chunks(["a", "b", "a"]) == ["a", "b"]


def test_clean_drops_boilerplate_and_the_links_inside_it():
    chunks = ["Menu: What we collect How we share", "What we collect", "Real policy text"]
    assert clean_chunks(chunks, drop=[r"^Menu:"]) == ["Real policy text"]


def test_clean_keeps_whole_list_and_drops_its_repeated_items():
    chunks = ["• first item • second item", "• first item", "first item", "second item"]
    assert clean_chunks(chunks) == ["• first item • second item"]


def test_clean_keeps_a_single_bulleted_line():
    assert clean_chunks(["• a lone bullet"]) == ["• a lone bullet"]


# ─── clean_chunks against the archive ────────────────────────────────────────

def test_meta_nav_flicker_no_longer_registers_as_a_change():
    # These two weeks differ only because the side nav gained a
    # "Privacy notice for United States residents" link.
    assert archived("meta", "2026-10-26") != archived("meta", "2026-11-02")
    assert recleaned("meta", "2026-10-26") == recleaned("meta", "2026-11-02")


def test_meta_real_rewrite_is_still_a_change():
    # 13 July 2026: Meta rewrote its description of training data sources,
    # adding data from apps built on its free model APIs.
    phrase = "external developers using our free models provided through APIs"
    before, after = recleaned("meta", "2026-07-06"), recleaned("meta", "2026-07-13")
    assert before != after
    assert any(phrase in c for c in after)
    assert not any(phrase in c for c in before)


def test_meta_cleaning_loses_no_sentences():
    raw, cleaned = archived("meta", "2026-11-09"), recleaned("meta", "2026-11-09")
    body = " ".join(cleaned)
    nav = [c for c in raw if c.startswith(("Privacy Policy What is", "Other policies and articles"))]
    for chunk in raw:
        if any(chunk in n for n in nav):
            continue
        assert chunk.lstrip("• ").strip() in body


def test_microsoft_sign_in_banner_is_dropped():
    raw = archived("microsoft", "2026-11-09")
    assert any(c.startswith("Access to this page requires authorization") for c in raw)
    cleaned = recleaned("microsoft", "2026-11-09")
    assert not any("requires authorization" in c for c in cleaned)
    assert cleaned[0].startswith("Data, privacy, and security")


def test_microsoft_rename_is_still_a_change():
    # 25 May 2026: "Azure Direct Models" became "Models sold by Azure".
    assert recleaned("microsoft", "2026-05-18") != recleaned("microsoft", "2026-05-25")


def test_google_related_articles_list_is_dropped():
    assert not any(c[0].isdigit() and " of 11 " in c for c in recleaned("google", "2026-11-09"))


@pytest.mark.parametrize("name", ["google", "meta", "microsoft"])
def test_latest_archived_snapshot_still_validates_after_cleaning(name):
    text = "\n\n".join(recleaned(name, "2026-11-09"))
    assert validate(name, text, TARGETS[name]["min_chars"]) == text


# ─── validate ────────────────────────────────────────────────────────────────

def test_validate_rejects_short_text():
    with pytest.raises(ScrapeError, match="only 5 chars"):
        validate("x", "hello", min_chars=100)


@pytest.mark.parametrize("marker", scrape.BLOCK_MARKERS)
def test_validate_rejects_every_block_marker(marker):
    with pytest.raises(ScrapeError, match="block page"):
        validate("x", "padding " * 500 + marker.upper(), min_chars=10)


def test_validate_rejects_the_block_page_that_was_archived_as_openai():
    # 2 and 9 Nov 2026: the Wayback capture was itself a bot wall, and the
    # old scraper saved it as if it were the policy.
    text = (SNAPSHOTS / "rejected" / "openai" / "2026-11-09.txt").read_text(encoding="utf-8")
    with pytest.raises(ScrapeError):
        validate("openai", text, TARGETS["openai"]["min_chars"])


def test_validate_accepts_a_real_openai_snapshot():
    text = "\n\n".join(archived("openai", "2026-10-26"))
    assert validate("openai", text, TARGETS["openai"]["min_chars"])


def test_validate_requires_headings():
    text = "# one\n\n" + "body text " * 50
    assert validate("x", text, min_chars=10, min_headings=1)
    with pytest.raises(ScrapeError, match="only 1 headings found, expected at least 3"):
        validate("x", text, min_chars=10, min_headings=3)


def test_validate_does_not_count_a_hash_inside_a_line_as_a_heading():
    with pytest.raises(ScrapeError, match="only 0 headings"):
        validate("x", "issue # 4 is tracked " * 20, min_chars=10, min_headings=1)


# ─── build_snapshot ──────────────────────────────────────────────────────────

RENDERED = "# Title\n\n" + "\n".join(f"# Section {i}\nParagraph {i} of policy text." for i in range(400))


def test_build_snapshot_writes_header_then_blocks():
    snapshot = build_snapshot("google", RENDERED, "https://support.google.com/x")
    lines = snapshot.split("\n\n")
    assert lines[0] == "[format: 2]\n[source: https://support.google.com/x]"
    assert lines[1:4] == ["# Title", "# Section 0", "Paragraph 0 of policy text."]


def test_build_snapshot_puts_extra_header_lines_after_source():
    snapshot = build_snapshot("google", RENDERED, "u", extra_header=("[Wayback snapshot: 2026]",))
    assert snapshot.startswith("[format: 2]\n[source: u]\n[Wayback snapshot: 2026]\n\n")


def test_build_snapshot_rejects_an_empty_page():
    with pytest.raises(ScrapeError):
        build_snapshot("google", "", "u")


def test_build_snapshot_rejects_a_page_with_no_headings():
    with pytest.raises(ScrapeError, match="headings"):
        build_snapshot("google", "plain line of text\n" * 600, "u")


# ─── wayback ─────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, text="", json_data=None):
        self.text = text
        self.content = text.encode()
        self._json = json_data

    def json(self):
        return self._json


GOOD_PAGE = "\n".join(f"# {i}. Clause\nA sentence of data processing addendum text." for i in range(400))
BLOCK_PAGE = "We recognize this might be a false positive designation, sorry about that."


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    """Wayback tests feed rendered text straight through instead of
    launching Chromium; rendering itself is tested in test_extract.py."""
    async def fake_render(html, cfg):
        return html
    monkeypatch.setattr(scrape, "render_html", fake_render)


def run_wayback(get):
    import asyncio
    return asyncio.run(scrape_wayback("openai", get=get))
CDX_ROWS = [
    ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
    ["k", "20261001000000", "https://openai.com/p/", "text/html", "200", "d", "50000"],
    ["k", "20261020000000", "https://openai.com/p/", "text/html", "200", "d", "50000"],
    ["k", "20261028000000", "https://openai.com/p/", "text/html", "200", "d", "50000"],
]


def fake_get(pages, cdx=CDX_ROWS):
    def get(url, params=None, timeout=None):
        if "cdx" in url:
            return FakeResponse(text="x", json_data=cdx)
        timestamp = url.split("/web/")[1].split("/")[0]
        return FakeResponse(text=pages[timestamp])
    return get


def test_wayback_captures_are_newest_first_without_header():
    captures = wayback_captures("openai.com/p/", get=fake_get({}))
    assert [t for t, _ in captures] == ["20261028000000", "20261020000000", "20261001000000"]


def test_wayback_captures_skip_ones_too_small_to_be_the_page():
    rows = [
        CDX_ROWS[0],
        ["k", "20261001000000", "https://openai.com/p/", "text/html", "200", "d", "61234"],
        ["k", "20261020000000", "https://openai.com/p/", "text/html", "200", "d", "2140"],   # bot wall
        ["k", "20261028000000", "https://openai.com/p/", "text/html", "200", "d", "-"],      # size unknown
    ]
    captures = wayback_captures("openai.com/p/", get=fake_get({}, cdx=rows))
    assert [t for t, _ in captures] == ["20261028000000", "20261001000000"]


def test_wayback_looks_far_enough_back_to_get_past_weeks_of_bot_walls():
    asked = {}
    def get(url, params=None, timeout=None):
        asked.update(params)
        return FakeResponse(text="x", json_data=CDX_ROWS)
    wayback_captures("openai.com/p/", get=get)
    assert asked["limit"] == "-80" and asked["filter"] == "statuscode:200"


def test_wayback_gives_up_after_a_bounded_number_of_fetches():
    stamps = [f"202610{d:02d}000000" for d in range(1, 21)]
    rows = [CDX_ROWS[0]] + [["k", t, "https://openai.com/p/", "text/html", "200", "d", "50000"] for t in stamps]
    fetched = []
    def get(url, params=None, timeout=None):
        if "cdx" in url:
            return FakeResponse(text="x", json_data=rows)
        fetched.append(url)
        return FakeResponse(text=BLOCK_PAGE)
    with pytest.raises(ScrapeError, match="no usable capture in the last 8"):
        run_wayback(get=get)
    assert len(fetched) == scrape.MAX_CAPTURE_FETCHES


def test_wayback_captures_empty_response():
    assert wayback_captures("x", get=lambda *a, **k: FakeResponse(text="  ")) == []


def test_wayback_skips_block_page_and_uses_previous_capture():
    pages = {"20261028000000": BLOCK_PAGE, "20261020000000": GOOD_PAGE, "20261001000000": GOOD_PAGE}
    result = run_wayback(get=fake_get(pages))
    assert result.startswith(
        "[format: 2]\n[source: https://openai.com/p/]\n"
        "[Wayback snapshot: 20261020000000]\n[URL: https://web.archive.org/web/20261020000000/"
    )


def test_wayback_fails_when_every_capture_is_blocked():
    pages = dict.fromkeys(["20261028000000", "20261020000000", "20261001000000"], BLOCK_PAGE)
    with pytest.raises(ScrapeError, match="no usable capture"):
        run_wayback(get=fake_get(pages))


def test_wayback_fails_when_there_are_no_captures():
    with pytest.raises(ScrapeError, match="no Wayback captures"):
        run_wayback(get=fake_get({}, cdx=CDX_ROWS[:1]))


def test_wayback_lookup_error_becomes_scrape_error():
    def boom(*a, **k):
        raise ConnectionError("down")
    with pytest.raises(ScrapeError, match="CDX lookup failed"):
        run_wayback(get=boom)


# ─── save_snapshot ───────────────────────────────────────────────────────────

NOW = datetime(2026, 11, 16, 9, 5, tzinfo=timezone.utc)


def test_save_writes_dated_file(tmp_path):
    path = save_snapshot("meta", "text", now=NOW, base=str(tmp_path))
    assert Path(path) == tmp_path / "meta" / "2026-11-16.txt"
    assert Path(path).read_text(encoding="utf-8") == "text"


def test_second_save_on_same_day_goes_to_catches_and_keeps_the_first(tmp_path):
    first = save_snapshot("meta", "first", now=NOW, base=str(tmp_path))
    second = save_snapshot("meta", "second", now=NOW, base=str(tmp_path))
    assert Path(second) == tmp_path / "catches" / "meta" / "2026-11-16-0905.txt"
    assert Path(first).read_text(encoding="utf-8") == "first"


# ─── main ────────────────────────────────────────────────────────────────────

def test_main_saves_good_targets_and_exits_nonzero_when_one_fails(monkeypatch, tmp_path, capsys):
    async def fake_scrape(name):
        if name == "openai":
            raise ScrapeError("openai: looks like a block page")
        return f"{name} policy"

    monkeypatch.setattr(scrape, "scrape", fake_scrape)
    monkeypatch.setattr(scrape, "SNAPSHOT_DIR", str(tmp_path))
    monkeypatch.setattr(
        scrape, "save_snapshot",
        lambda name, content: save_snapshot(name, content, now=NOW, base=str(tmp_path)),
    )

    import asyncio
    assert asyncio.run(scrape.main()) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["anthropic", "google", "meta", "microsoft"]
    assert "1 target(s) failed: openai" in capsys.readouterr().out
