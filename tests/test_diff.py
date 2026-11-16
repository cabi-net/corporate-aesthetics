"""Tests for the diff viewer (diff.py). No network; the archive tests read snapshots/."""

from datetime import datetime, timezone

import diff
from diff import (
    build,
    diff_paragraphs,
    find_changes,
    load_snapshots,
    parse_snapshot,
    render_institution,
    render_ops,
    word_diff,
)


def write(tmp_path, name, date, text):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    (folder / f"{date}.txt").write_text(text, encoding="utf-8")


# ─── parse_snapshot / load_snapshots ─────────────────────────────────────────

def test_parse_separates_wayback_header_from_paragraphs():
    raw = "[Wayback snapshot: 2026]\n[URL: https://web.archive.org/x]\n\nfirst\n\nsecond"
    paragraphs, header = parse_snapshot(raw)
    assert paragraphs == ["first", "second"]
    assert header == "[Wayback snapshot: 2026] [URL: https://web.archive.org/x]"


def test_parse_without_header():
    assert parse_snapshot("only paragraph") == (["only paragraph"], None)


def test_parse_applies_the_scraper_cleaning_rules():
    paragraphs, _ = parse_snapshot("Menu: a b\n\nreal text\n\nreal text", drop=[r"^Menu:"])
    assert paragraphs == ["real text"]


def test_load_is_sorted_and_ignores_non_snapshot_files(tmp_path):
    write(tmp_path, "meta", "2026-02-01", "later")
    write(tmp_path, "meta", "2026-01-01", "earlier")
    (tmp_path / "meta" / "notes.txt").write_text("not a snapshot")
    (tmp_path / "meta" / "2026-01-01-0905.txt").write_text("a catch, not a snapshot")
    assert [d for d, _, _ in load_snapshots("meta", str(tmp_path))] == ["2026-01-01", "2026-02-01"]


def test_load_skips_empty_snapshots_and_missing_folders(tmp_path):
    write(tmp_path, "meta", "2026-01-01", "  \n\n ")
    assert load_snapshots("meta", str(tmp_path)) == []
    assert load_snapshots("nobody", str(tmp_path)) == []


# ─── find_changes ────────────────────────────────────────────────────────────

SNAPS = [
    ("2026-01-01", ["a", "b"], None),
    ("2026-01-08", ["a", "b"], None),
    ("2026-01-15", ["a", "c"], "[Wayback snapshot: x]"),
    ("2026-01-22", ["a", "c", "d"], None),
]


def test_find_changes_skips_identical_weeks_and_is_newest_first():
    changes = find_changes(SNAPS)
    assert [(c["from"], c["to"]) for c in changes] == [
        ("2026-01-15", "2026-01-22"),
        ("2026-01-08", "2026-01-15"),
    ]


def test_find_changes_carries_the_newer_snapshots_wayback_header():
    assert find_changes(SNAPS)[1]["wayback"] == "[Wayback snapshot: x]"


def test_find_changes_with_fewer_than_two_snapshots():
    assert find_changes([]) == []
    assert find_changes(SNAPS[:1]) == []


def test_diff_paragraphs_reports_insert_delete_replace():
    assert diff_paragraphs(["a"], ["a", "b"])[-1] == ("insert", [], ["b"])
    assert diff_paragraphs(["a", "b"], ["a"])[-1] == ("delete", ["b"], [])
    assert diff_paragraphs(["a", "b"], ["a", "c"])[-1] == ("replace", ["b"], ["c"])


# ─── rendering ───────────────────────────────────────────────────────────────

def test_word_diff_marks_only_the_changed_words():
    old, new = word_diff("using our free models", "using our contributor tier models")
    assert old == "using our <del>free</del> models"
    assert new == "using our <ins>contributor tier</ins> models"


def test_word_diff_escapes_html():
    old, new = word_diff("a <b> c", "a <i> c")
    assert "<b>" not in old and "&lt;b&gt;" in old
    assert "<i>" not in new and "&lt;i&gt;" in new


def test_render_ops_escapes_policy_text():
    out = render_ops([("insert", [], ["<script>alert(1)</script>"])])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_ops_uses_word_diff_for_a_reworded_paragraph():
    out = render_ops([("replace", ["we use licensed data from providers"], ["we use information from providers"])])
    assert "<del>licensed data</del>" in out and "<ins>information</ins>" in out


def test_render_ops_shows_unrelated_paragraphs_whole():
    out = render_ops([("replace", ["alpha beta gamma"], ["one two three four"])])
    assert "<del>" not in out and "<ins>" not in out


def test_render_ops_only_shows_gaps_between_changes():
    ops = [("equal", ["a"], ["a"]), ("insert", [], ["b"]), ("equal", ["c", "d"], ["c", "d"]),
           ("insert", [], ["e"]), ("equal", ["f"], ["f"])]
    out = render_ops(ops)
    assert out.count('class="gap"') == 1
    assert "[ 2 unchanged paragraphs ]" in out


def test_render_institution_with_no_changes_says_so():
    out = render_institution("google", SNAPS[:2], [])
    assert "2 snapshots since 2026-01-01" in out
    assert "0 changes" in out
    assert "no change in wording" in out


def test_render_institution_pluralises_and_anchors_each_change():
    out = render_institution("meta", SNAPS, find_changes(SNAPS)[:1])
    assert "1 change " in out and "1 changes" not in out
    assert 'id="meta-2026-01-22"' in out


def test_build_handles_an_empty_archive(tmp_path):
    page = build(base=str(tmp_path), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert page.count("no snapshots yet") == len(diff.LABELS)
    assert "generated 2026-01-01 00:00 UTC" in page


# ─── against the archive ─────────────────────────────────────────────────────

def changed_dates(name):
    return [c["to"] for c in find_changes(load_snapshots(name))]


def test_archive_meta_changes_are_the_three_real_rewrites():
    assert [d for d in changed_dates("meta") if d <= "2026-11-13"] == [
        "2026-09-14", "2026-07-13", "2026-06-01",
    ]


def test_archive_microsoft_changes():
    assert [d for d in changed_dates("microsoft") if d <= "2026-11-13"] == [
        "2026-11-09", "2026-08-10", "2026-05-25", "2026-04-13",
    ]


def test_archive_google_and_openai_have_no_wording_changes_through_nov_2026():
    assert [d for d in changed_dates("google") if d <= "2026-11-13"] == []
    assert [d for d in changed_dates("openai") if d <= "2026-11-13"] == []


def test_archive_page_builds_and_contains_a_known_rewording():
    page = build()
    assert "<del>free</del>" in page
    assert "<ins>contributor tier</ins>" in page


# ─── main ────────────────────────────────────────────────────────────────────

def test_main_writes_the_page_and_copies_fonts_next_to_it(tmp_path):
    assets = tmp_path / "assets" / "fonts"
    assets.mkdir(parents=True)
    (assets / "Font.woff2").write_bytes(b"font")
    out = tmp_path / "site" / "index.html"

    diff.main(out=str(out), assets=str(tmp_path / "assets"))

    assert "<title>corporate aesthetics</title>" in out.read_text(encoding="utf-8")
    assert (tmp_path / "site" / "assets" / "fonts" / "Font.woff2").read_bytes() == b"font"


def test_every_font_the_page_references_is_in_the_repo():
    import re
    from pathlib import Path
    root = Path(__file__).parent.parent
    for url in re.findall(r"url\('([^']+)'\)", diff.CSS):
        assert (root / url).is_file(), url


# ─── snapshot formats ────────────────────────────────────────────────────────

V2 = "[format: 2]\n[source: https://example.test/p]\n\n# Heading\n\n- device names\n\n- device names\n\nMenu: a b"


def test_format_2_snapshots_are_not_cleaned_or_deduplicated():
    paragraphs, header = parse_snapshot(V2, drop=[r"^Menu:"])
    assert paragraphs == ["# Heading", "- device names", "- device names", "Menu: a b"]
    assert header == "[format: 2] [source: https://example.test/p]"


def test_snapshot_format_detection():
    assert diff.snapshot_format(None) == 1
    assert diff.snapshot_format("[Wayback snapshot: 2026] [URL: x]") == 1
    assert diff.snapshot_format("[format: 2] [source: x]") == 2


def test_format_2_wayback_header_is_still_shown_without_the_format_lines():
    snaps = [
        ("2026-12-01", ["a"], "[format: 2] [source: u] [Wayback snapshot: 1] [URL: w1]"),
        ("2026-12-08", ["b"], "[format: 2] [source: u] [Wayback snapshot: 2] [URL: w2]"),
    ]
    assert find_changes(snaps)[0]["wayback"] == "[Wayback snapshot: 2] [URL: w2]"


FORMAT_SWITCH = [
    ("2026-11-09", ["a long paragraph"], None),
    ("2026-11-16", ["# Heading", "a long paragraph", "- short item"], "[format: 2] [source: u]"),
    ("2026-11-23", ["# Heading", "a long paragraph", "- short item, reworded"], "[format: 2] [source: u]"),
]


def test_scraper_change_is_flagged_and_not_diffed():
    newest, switch = find_changes(FORMAT_SWITCH)
    assert switch["format_change"] is True and switch["ops"] == []
    assert "format_change" not in newest and newest["ops"]


def test_scraper_change_is_not_counted_or_shown_as_a_policy_change():
    out = render_institution("meta", FORMAT_SWITCH, find_changes(FORMAT_SWITCH))
    assert "3 snapshots since 2026-11-09 &middot; 1 change " in out
    assert "the scraper changed how it reads this page" in out
    assert out.count('class="change"') == 1


def test_only_a_scraper_change_still_reads_as_no_change_in_wording():
    out = render_institution("meta", FORMAT_SWITCH[:2], find_changes(FORMAT_SWITCH[:2]))
    assert "0 changes" in out and "no change in wording" in out
