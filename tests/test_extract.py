"""
Tests for the in-page extractor (EXTRACT_JS) — these run real Chromium.

Each fixture in tests/fixtures/ is a small page modelled on the structure of
one target's live page, as mapped in a browser in November 2026. Everything
runs offline; no test touches the real sites.
"""

import asyncio
from functools import lru_cache
from pathlib import Path

import pytest

import scrape
from scrape import TARGETS, render_html, text_to_blocks

FIXTURES = Path(__file__).parent / "fixtures"


@lru_cache(maxsize=None)
def blocks(name):
    """Fixture page → blocks, through the same path a real scrape takes."""
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    cfg = TARGETS[name]
    rendered = asyncio.run(render_html(html, cfg))
    return tuple(text_to_blocks(rendered, cfg.get("drop", ()), cfg.get("strip", ())))


# ─── google ──────────────────────────────────────────────────────────────────

def test_google_keeps_short_list_items():
    # the old scraper dropped anything of 40 characters or fewer, which
    # lost the list of what data Assistant uses
    assert "- location (learn more)" in blocks("google")
    assert "- device names" in blocks("google")
    assert "- tasks, events, and alarms" in blocks("google")


def test_google_keeps_faq_questions_and_short_headings_as_headings():
    assert "# You asked: Is Google Assistant accessing my emails?" in blocks("google")
    assert "# Remove Voice Match from all devices" in blocks("google")


def test_google_opens_accordions_and_keeps_their_titles():
    b = blocks("google")
    title = b.index("# Choose what you share with Google Assistant")
    assert b[title + 1] == "You can choose to turn these settings on or off at any time:"
    assert b[title + 2 : title + 5] == ("- Web & App Activity", "- Voice and audio activity", "- Personal results")


def test_google_nested_list_keeps_parent_and_child_separate():
    b = blocks("google")
    parent = b.index("- It won't give you personal results like:")
    assert b[parent + 1] == "- Tasks and calendar events on shared devices."


def test_google_ignores_everything_outside_the_content_container():
    text = "\n".join(blocks("google"))
    assert "1 of 11" not in text
    assert "©2026 Google" not in text
    assert "Google Assistant Help" not in text


# ─── meta ────────────────────────────────────────────────────────────────────

def test_meta_keeps_headings_with_no_content_container():
    b = blocks("meta")
    assert b[0] == "# How Meta uses information for generative AI models and features"
    assert "# AI at Meta" in b
    assert "# Where does Meta get training information?" in b
    assert "# Privacy and generative AI" in b


def test_meta_opens_collapsed_sections():
    assert "We have a robust internal Privacy Review process." in blocks("meta")


def test_meta_bullets_drawn_as_their_own_block_become_list_items():
    b = blocks("meta")
    assert "- Information that is available on the internet" in b
    assert "•" not in b


def test_meta_inline_links_stay_inside_their_sentence():
    b = blocks("meta")
    assert "- Information from third parties that provide content, including contributor tier models" in b
    assert "We do not use the content of your private messages unless you share them with our AIs." in b


def test_meta_navigation_is_neither_opened_nor_kept():
    text = "\n".join(blocks("meta"))
    assert "Privacy Policy" not in text
    assert "What is the Privacy Policy" not in text


def test_meta_hidden_duplicate_copy_is_not_read_twice():
    assert blocks("meta").count("We are committed to building responsibly.") == 1


# ─── microsoft ───────────────────────────────────────────────────────────────

def test_microsoft_reads_every_content_block_in_order():
    b = blocks("microsoft")
    assert b[0] == "# Data, privacy, and security for Models sold by Azure in Microsoft Foundry"
    assert b[1] == "Important"


def test_microsoft_keeps_the_short_guarantee_the_old_scraper_dropped():
    assert "- are NOT available to other customers." in blocks("microsoft")


def test_microsoft_keeps_the_change_log_table_row_by_row():
    b = blocks("microsoft")
    assert "# Change log" in b
    assert "Date | Changes" in b
    assert "3 October 2025 | Updated for Microsoft Foundry naming." in b


def test_microsoft_drops_hidden_sign_in_notice_outline_and_buttons():
    b = blocks("microsoft")
    text = "\n".join(b)
    assert "requires authorization" not in text
    assert "What data does Foundry process?" not in text  # the in-page outline
    assert "Copy" not in b and "Expand table" not in b and "JSON" not in b
    assert "© Microsoft" not in text


def test_microsoft_keeps_code_sample_values():
    assert '"value":"false"' in blocks("microsoft")


# ─── openai (wayback) ────────────────────────────────────────────────────────

def test_openai_keeps_the_dates_the_old_scraper_dropped():
    b = blocks("openai")
    assert "Updated: December 1, 2025" in b
    assert "Effective: January 1, 2026" in b


def test_openai_keeps_short_section_headings_and_labels():
    b = blocks("openai")
    assert "# OpenAI Data Processing Addendum" in b
    assert "# 1. Details." in b
    assert "Schedule 1" in b and "2. Duration:" in b


def test_openai_clauses_wrapped_in_list_items_come_out_once():
    b = blocks("openai")
    assert "- 1.1 Scope and Roles. OpenAI may Process Customer Data." in b
    assert sum("1.1 Scope and Roles" in x for x in b) == 1


def test_openai_drops_language_picker_and_screen_reader_labels():
    b = blocks("openai")
    text = "\n".join(b)
    assert "Türkçe" not in text and "Select language" not in text
    assert "opens in a new window" not in text
    assert "Execute Data Processing Agreement" in b
    assert "Download PDF" not in b


def test_openai_ignores_wayback_toolbar_and_site_chrome():
    text = "\n".join(blocks("openai"))
    assert "Wayback Machine toolbar" not in text
    assert "Back to main menu" not in text
    assert "ChatGPT Enterprise" not in text


def test_page_scripts_do_not_run_on_saved_html():
    assert "client-side rendering wiped the page" not in "\n".join(blocks("openai"))


# ─── whole pipeline ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["google", "meta", "microsoft", "openai"])
def test_fixture_snapshot_has_a_format_header_and_source(name, monkeypatch):
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    monkeypatch.setitem(TARGETS[name], "min_chars", 50)
    monkeypatch.setitem(TARGETS[name], "min_headings", 2)
    rendered = asyncio.run(render_html(html, TARGETS[name]))
    snapshot = scrape.build_snapshot(name, rendered, "https://example.test/page")
    header, _, body = snapshot.partition("\n\n")
    assert header == "[format: 2]\n[source: https://example.test/page]"
    assert body.split("\n\n") == list(blocks(name))
