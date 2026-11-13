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
import shutil
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
/* palette, type and components adapted from the sick genesis design system (pale mint mode) */
@font-face { font-family: 'Karrik'; src: url('assets/fonts/Karrik-Regular.woff2') format('woff2'); font-display: swap; }
@font-face { font-family: 'Steps Mono'; src: url('assets/fonts/Steps-Mono.otf') format('opentype'); font-display: swap; }
@font-face { font-family: 'Steps Mono Thin'; src: url('assets/fonts/Steps-Mono-Thin.otf') format('opentype'); font-display: swap; }
:root {
  --sg-black: #000000; --sg-white: #ffffff;
  --sg-cyan: #9fedfc; --sg-blue: #2c73dd;
  --sg-magenta: #f35aea; --sg-pink-pale: #f59cf8; --sg-pink-deep: #df2a85;
  --sg-violet: #9d4eac; --sg-mint-pale: #d7fefb;

  --color-bg: var(--sg-mint-pale);
  --color-surface: var(--sg-white);
  --color-text: var(--sg-black);
  --color-text-body: #1a1a1a;
  --color-text-muted: #444;
  --color-secondary: var(--sg-blue);
  --color-removed: var(--sg-pink-deep);
  --color-removed-mark: var(--sg-pink-pale);
  --color-added: var(--sg-blue);
  --color-added-mark: var(--sg-cyan);
  --color-rule: rgba(0, 0, 0, 0.15);

  --font-body: 'Karrik', Georgia, serif;
  --font-ui: 'Steps Mono', 'Courier New', monospace;
  --font-label: 'Steps Mono Thin', 'Courier New', monospace;

  --text-xs: 11px; --text-sm: 13px; --text-base: 15px; --text-md: 18px;
  --text-lg: 24px; --text-xl: 32px; --text-2xl: 56px;
  --space-xs: 4px; --space-sm: 8px; --space-md: 16px; --space-lg: 24px; --space-xl: 32px;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--color-bg); color: var(--color-text-body); font-family: var(--font-body);
       font-size: var(--text-base); line-height: 1.6; padding: 64px 48px; }
.wrap { max-width: 900px; margin: 0 auto; }
a { color: inherit; }
a:focus-visible { outline: 2px solid var(--sg-blue); outline-offset: 2px; }

header { margin-bottom: var(--space-xl); }
header h1 { font-size: clamp(36px, 9vw, var(--text-2xl)); font-weight: 700; line-height: 1.1;
            color: var(--color-text); text-transform: lowercase; margin-bottom: var(--space-md); }
header .subtitle { font-size: var(--text-md); max-width: 60ch; }
header .generated, .summary, .dates, .wayback, .gap, .quiet, footer {
  font-family: var(--font-ui); font-size: var(--text-xs); letter-spacing: 0.08em; text-transform: uppercase; }
header .generated { color: var(--color-text-muted); margin-top: var(--space-md); }

nav { display: flex; flex-wrap: wrap; gap: var(--space-sm); margin-bottom: 56px; }
nav a { font-family: var(--font-ui); font-size: var(--text-xs); letter-spacing: 0.15em; text-transform: uppercase;
        text-decoration: none; color: var(--color-secondary); border: 1px solid var(--color-secondary); padding: 3px 10px; }
nav a:hover { background: var(--color-secondary); color: var(--sg-white); }

section { padding-top: 48px; margin-bottom: 48px; border-top: 1px solid var(--color-rule); }
section h2 { font-size: var(--text-xl); font-weight: 700; color: var(--color-text); margin-bottom: var(--space-xs); }
.summary { color: var(--color-text-muted); margin-bottom: var(--space-lg); }

.change { background: var(--color-surface); border: 1px solid rgba(0, 0, 0, 0.1); padding: 20px; margin-bottom: var(--space-lg); }
.dates { color: #666; margin-bottom: var(--space-md); }
.dates a { text-decoration: none; }
.dates a:hover { text-decoration: underline; }
.wayback { color: #666; text-transform: none; letter-spacing: 0; margin-bottom: var(--space-md); overflow-wrap: anywhere; }

.para { padding: 10px 14px; margin: var(--space-xs) 0; border-left: 3px solid transparent;
        font-size: 14px; line-height: 1.5; overflow-wrap: anywhere; }
.removed { border-left-color: var(--color-removed); background: rgba(245, 156, 248, 0.16); }
.added { border-left-color: var(--color-added); background: rgba(159, 237, 252, 0.28); }
.removed::before { content: "\\2212\\00a0"; color: var(--color-removed); font-family: var(--font-ui); }
.added::before { content: "+\\00a0"; color: var(--color-added); font-family: var(--font-ui); }
del { background: var(--color-removed-mark); text-decoration: line-through; text-decoration-thickness: 1px; }
ins { background: var(--color-added-mark); text-decoration: none; font-weight: 700; }
.gap, .quiet { font-family: var(--font-label); color: var(--color-text-muted); padding: var(--space-sm) 14px; }

footer { border-top: 1px solid var(--color-rule); padding-top: var(--space-lg); color: var(--color-text-muted); line-height: 1.8; }

@media (max-width: 600px) { body { padding: 32px 16px; } .change { padding: 14px; } }
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


def main(out="site/index.html", assets="assets"):
    site = os.path.dirname(out)
    os.makedirs(site, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(build())
    if os.path.isdir(assets):  # fonts
        shutil.copytree(assets, os.path.join(site, "assets"), dirs_exist_ok=True)
    print(f"saved {out}")


if __name__ == "__main__":
    main()
