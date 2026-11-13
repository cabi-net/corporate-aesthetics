"""
corporate aesthetics — diff viewer.

Reads every snapshot in snapshots/, cleans it with the same rules the
scraper uses (so nav noise recorded in older snapshots doesn't show up as
a change), and writes a static page listing each time a policy's text
actually changed, newest first.

    python diff.py            # writes site/index.html
"""

import difflib
import html
import os
import re
from datetime import datetime, timezone

from scrape import SNAPSHOT_DIR, TARGETS, clean_chunks, normalize

LABELS = {"google": "Google", "meta": "Meta", "microsoft": "Microsoft", "openai": "OpenAI"}

DATE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.txt$")


# ─── loading ─────────────────────────────────────────────────────────────────

def parse_snapshot(raw, drop=()):
    """Split a snapshot file into (paragraphs, wayback_header_or_None)."""
    paragraphs = [normalize(p) for p in raw.split("\n\n")]
    paragraphs = [p for p in paragraphs if p]
    header = None
    if paragraphs and paragraphs[0].startswith("[Wayback snapshot:"):
        header = paragraphs.pop(0)
    return clean_chunks(paragraphs, drop), header


def load_snapshots(name, base=SNAPSHOT_DIR):
    """[(date, paragraphs, wayback_header)] for one institution, oldest first."""
    folder = os.path.join(base, name)
    if not os.path.isdir(folder):
        return []
    drop = TARGETS.get(name, {}).get("drop", ())
    entries = []
    for filename in sorted(os.listdir(folder)):
        if not DATE_FILE.match(filename):
            continue
        with open(os.path.join(folder, filename), encoding="utf-8") as f:
            paragraphs, header = parse_snapshot(f.read(), drop)
        if paragraphs:
            entries.append((filename[:-4], paragraphs, header))
    return entries


# ─── diffing ─────────────────────────────────────────────────────────────────

def diff_paragraphs(old, new):
    """Paragraph-level opcodes: [(tag, old_paragraphs, new_paragraphs)]."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [(tag, old[i1:i2], new[j1:j2]) for tag, i1, i2, j1, j2 in matcher.get_opcodes()]


def find_changes(snapshots):
    """Every consecutive pair of snapshots whose text differs, newest first."""
    changes = []
    for (date_old, old, _), (date_new, new, header) in zip(snapshots, snapshots[1:]):
        if old != new:
            changes.append({
                "from": date_old,
                "to": date_new,
                "ops": diff_paragraphs(old, new),
                "wayback": header,
            })
    return changes[::-1]


def word_diff(old, new):
    """Mark the words that differ between two versions of one paragraph.
    Returns (old_html, new_html)."""
    a, b = old.split(), new.split()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out_old, out_new = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        left = html.escape(" ".join(a[i1:i2]))
        right = html.escape(" ".join(b[j1:j2]))
        if tag == "equal":
            out_old.append(left)
            out_new.append(right)
        else:
            if left:
                out_old.append(f"<del>{left}</del>")
            if right:
                out_new.append(f"<ins>{right}</ins>")
    return " ".join(out_old), " ".join(out_new)


def similar(a, b):
    return difflib.SequenceMatcher(None, a.split(), b.split(), autojunk=False).ratio() > 0.5


# ─── rendering ───────────────────────────────────────────────────────────────

def render_ops(ops):
    parts = []
    for i, (tag, old, new) in enumerate(ops):
        if tag == "equal":
            if 0 < i < len(ops) - 1:
                n = len(old)
                parts.append(f'<div class="gap">[ {n} unchanged paragraph{"s" if n != 1 else ""} ]</div>')
            continue

        # a rewritten paragraph is shown word by word; anything else whole
        if tag == "replace" and len(old) == len(new) and all(map(similar, old, new)):
            for o, n in zip(old, new):
                o_html, n_html = word_diff(o, n)
                parts.append(f'<div class="para removed">{o_html}</div>')
                parts.append(f'<div class="para added">{n_html}</div>')
            continue

        parts.extend(f'<div class="para removed">{html.escape(p)}</div>' for p in old)
        parts.extend(f'<div class="para added">{html.escape(p)}</div>' for p in new)
    return "".join(parts)


def render_institution(name, snapshots, changes):
    label = LABELS.get(name, name.title())
    url = TARGETS.get(name, {}).get("url", "")
    if not snapshots:
        summary = "no snapshots yet"
    else:
        n = len(changes)
        summary = (
            f'{len(snapshots)} snapshots since {snapshots[0][0]} &middot; '
            f'{n} change{"s" if n != 1 else ""}'
        )

    periods = []
    for c in changes:
        wayback = f'<p class="wayback">{html.escape(c["wayback"])}</p>' if c["wayback"] else ""
        periods.append(
            f'<article class="change" id="{name}-{c["to"]}">'
            f'<p class="dates"><a href="#{name}-{c["to"]}">{c["from"]} &rarr; {c["to"]}</a></p>'
            f'{wayback}{render_ops(c["ops"])}</article>'
        )
    if snapshots and not changes:
        periods.append('<p class="quiet">no change in wording since archiving began.</p>')

    return (
        f'<section id="{name}"><h2>{label}</h2>'
        f'<p class="summary">{summary} &middot; <a href="{html.escape(url)}">source page</a></p>'
        f'{"".join(periods)}</section>'
    )


CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #f9f7f4; color: #1a1a1a; font-family: Georgia, 'Times New Roman', serif;
       font-size: 1rem; line-height: 1.65; padding: 3rem 1.5rem; }
.wrap { max-width: 860px; margin: 0 auto; }
a { color: inherit; }
header { border-bottom: 1px solid #ddd; margin-bottom: 2rem; padding-bottom: 1.5rem; }
header h1 { font-size: 1.4rem; font-weight: normal; letter-spacing: 0.12em; text-transform: lowercase; }
header .subtitle { color: #666; font-size: 0.9rem; margin-top: 0.3rem; }
header .generated, nav, .dates, .wayback { font-family: 'Courier New', monospace; }
header .generated { color: #767676; font-size: 0.75rem; margin-top: 0.5rem; }
nav { font-size: 0.8rem; margin-bottom: 3rem; display: flex; gap: 1.5rem; flex-wrap: wrap; }
section { margin-bottom: 4rem; }
section h2 { font-size: 0.8rem; font-weight: normal; letter-spacing: 0.18em; text-transform: uppercase; color: #444; }
.summary { color: #666; font-size: 0.85rem; margin-bottom: 1.5rem; }
.change { margin-bottom: 2.5rem; border-top: 1px solid #e8e4df; padding-top: 1rem; }
.dates { font-size: 0.8rem; color: #555; margin-bottom: 0.75rem; }
.dates a { text-decoration: none; }
.dates a:hover { text-decoration: underline; }
.wayback { font-size: 0.72rem; color: #767676; margin-bottom: 0.75rem; overflow-wrap: anywhere; }
.para { padding: 0.55em 1em; margin: 0.2em 0; border-left: 3px solid transparent; font-size: 0.92rem; overflow-wrap: anywhere; }
.removed { background: #fdf0ef; border-left-color: #c0392b; color: #7a2a25; }
.added { background: #f0f7f0; border-left-color: #27ae60; color: #1e5c3a; }
.removed::before { content: "\\2212\\00a0"; }
.added::before { content: "+\\00a0"; }
del { background: #f5c6c1; text-decoration: line-through; }
ins { background: #bfe3c6; text-decoration: none; font-weight: bold; }
.gap, .quiet { color: #767676; font-size: 0.78rem; font-style: italic; padding: 0.5em 1em; }
footer { border-top: 1px solid #ddd; margin-top: 4rem; padding-top: 1.5rem; font-size: 0.78rem; color: #767676; }
"""


def render_page(sections, generated_at):
    nav = "".join(f'<a href="#{name}">{LABELS[name].lower()}</a>' for name in LABELS)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>corporate aesthetics</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
<h1>corporate aesthetics</h1>
<p class="subtitle">how four companies reword their AI and data privacy disclosures</p>
<p class="generated">generated {generated_at}</p>
</header>
<nav>{nav}</nav>
<main>
{"".join(sections)}
</main>
<footer>
each entry is a week in which a page's wording changed, newest first. removed text is red, added text is green.
navigation menus and sign-in banners are filtered out before comparing.
<a href="https://github.com/cabi-net/corporate-aesthetics">the git history is the archive.</a>
</footer>
</div>
</body>
</html>
"""


def build(base=SNAPSHOT_DIR, now=None):
    now = now or datetime.now(timezone.utc)
    sections = []
    for name in LABELS:
        snapshots = load_snapshots(name, base)
        sections.append(render_institution(name, snapshots, find_changes(snapshots)))
    return render_page(sections, now.strftime("%Y-%m-%d %H:%M UTC"))


def main(out="site/index.html"):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    print(f"saved {out}")


if __name__ == "__main__":
    main()
