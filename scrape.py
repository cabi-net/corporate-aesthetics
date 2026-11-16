"""
corporate aesthetics — weekly snapshot scraper.

Each target is one entry in TARGETS. A run opens the page in a headless
browser, expands every collapsed section, reads the text of the content
container the way a person would see it (headings, list items and table
rows kept, however short), validates that what came back is really the
policy, and only then writes a dated snapshot. A target that fails
validation writes nothing and makes the run exit non-zero, so the workflow
fails visibly instead of archiving junk.
"""

import asyncio
import os
import re
import sys
from datetime import datetime, timezone

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SNAPSHOT_DIR = "snapshots"
FORMAT = 2  # 1 = tag/length filter (to Nov 2026), 2 = structural extraction

# ─── targets ─────────────────────────────────────────────────────────────────
#
# root          CSS selector for the content container(s); None = whole page
#               minus navigation
# min_chars     a valid snapshot is at least this long
# min_headings  ...and has at least this many headings
# headings      extra selectors to treat as headings (h1–h6 always are)
# drop          regexes for boilerplate lines that survive root scoping
# strip         literal strings removed from inside lines
# wait          ms to wait after load
#
# `drop` is also what diff.py uses to clean format-1 snapshots.

TARGETS = {
    "google": {
        "method": "live",
        "url": "https://support.google.com/assistant/answer/11091015?hl=en",
        "root": ".cc",
        "headings": "a.zippy",  # accordion titles
        "drop": [r"^\d+ of \d+ "],
        "min_chars": 5000,
        "min_headings": 5,
        "wait": 3000,
    },
    "meta": {
        "method": "live",
        "url": "https://privacycenter.instagram.com/privacy/genai/",
        "root": None,  # the page has no content container
        "drop": [
            r"^Privacy Policy What is the Privacy Policy",
            r"^Other policies and articles",
        ],
        "min_chars": 5000,
        "min_headings": 4,
        "wait": 4000,
    },
    "microsoft": {
        "method": "live",
        "url": "https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy",
        "root": "main .content",
        "drop": [
            r"^Access to this page requires authorization",
            r"^(Copy|Expand table|JSON)$",  # code-block and table buttons
        ],
        "min_chars": 12000,
        "min_headings": 8,
        "wait": 4000,
    },
    "openai": {
        "method": "wayback",
        "url": "https://openai.com/policies/data-processing-addendum/",
        "root": "main article",
        "strip": ["(opens in a new window)"],  # screen-reader link labels
        "drop": [
            r"Back to main menu",
            r"\(opens in a new window\)$",  # format-1 snapshots only
            r"^Download PDF$",
            r"^Select language$",
        ],
        "min_chars": 12000,
        "min_headings": 6,
    },
}

# Phrases that only show up when a site has served a bot wall instead of
# the page. Matched case-insensitively against the extracted text.
BLOCK_MARKERS = [
    "false positive designation",
    "verify you are human",
    "verify you are a human",
    "unusual traffic",
    "enable javascript and cookies to continue",
    "access denied",
    "checking your browser",
    "captcha",
]


class ScrapeError(Exception):
    """The page could not be fetched, or what came back is not the policy."""


# ─── in-page extraction ──────────────────────────────────────────────────────
#
# Runs inside the browser. Opens collapsed sections, removes site chrome,
# marks headings with "# " and list items with "- ", then returns the
# rendered text (innerText), which follows reading order and leaves out
# anything the page hides.

EXTRACT_JS = """
async (cfg) => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const CHROME = 'nav, header, footer, [role=navigation], [role=banner], [role=dialog], ' +
    'select, .sr-only, .visually-hidden, script, style, noscript';
  const roots = () => (cfg.root ? [...document.querySelectorAll(cfg.root)] : [document.body]);

  for (let round = 0; round < 3; round++) {
    const closed = roots()
      .flatMap((r) => [...r.querySelectorAll('[aria-expanded="false"]')])
      .filter((el) => !el.closest(CHROME) && el.getClientRects().length);
    if (!closed.length) break;
    for (const el of closed) {
      try { el.click(); } catch (e) {}
      await sleep(cfg.clickDelay ?? 300);
    }
    await sleep(cfg.clickDelay ?? 300);
  }

  const HEADINGS = 'h1, h2, h3, h4, h5, h6, [role=heading]' + (cfg.headings ? ', ' + cfg.headings : '');
  const parts = [];
  for (const root of roots()) {
    root.querySelectorAll(CHROME).forEach((el) => el.remove());
    root.querySelectorAll(HEADINGS).forEach((h) => {
      h.prepend(document.createTextNode('\\n\\n# '));
      h.append(document.createTextNode('\\n\\n'));
    });
    root.querySelectorAll('li').forEach((li) => li.prepend(document.createTextNode('- ')));
    parts.push(root.innerText);
  }
  return parts.join('\\n');
}
"""


# ─── text handling (pure functions) ──────────────────────────────────────────

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_MARKERS = {"#", "-", "•", "·", "◦", "▪"}


def normalize(text):
    """Collapse whitespace and strip invisible characters, so a changed
    non-breaking space doesn't register as a changed policy."""
    text = text.translate(_INVISIBLE).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def text_to_blocks(text, drop=(), strip=()):
    """Turn the page's rendered text into one block per line of content.

    Headings come out as "# text", list items as "- text", table rows as
    "cell | cell". A marker that the page rendered on a line of its own
    (Meta draws its bullets that way) is attached to the text that follows.
    Nothing is filtered by length; only lines with no letters or digits
    at all (stray punctuation) are skipped.
    """
    patterns = [re.compile(p) for p in drop]
    blocks = []
    prefix = ""
    for raw in text.split("\n"):
        for junk in strip:
            raw = raw.replace(junk, "")
        line = normalize(raw.replace("\t", " | "))
        if not line:
            continue
        words = line.split(" ")
        while words and words[0] in _MARKERS:
            prefix = "#" if "#" in (words.pop(0), prefix) else "-"
        line = " ".join(words)
        if not line:
            continue  # the marker stood alone; keep it for the next line
        if not re.search(r"\w", line):
            prefix = ""
            continue
        if not any(p.search(line) for p in patterns):
            blocks.append(f"{prefix} {line}" if prefix else line)
        prefix = ""
    return blocks


def clean_chunks(chunks, drop=()):
    """Clean a format-1 snapshot: remove navigation noise and nested duplicates.

    Only diff.py uses this now, to read snapshots taken before the
    structural extractor.

    - a chunk matching a `drop` pattern is boilerplate; so is any chunk
      whose text sits entirely inside one (the individual nav links)
    - a list is often captured three times: once as a whole
      ("• a • b • c"), then each item with and without its bullet.
      The whole is kept, the repeated items are dropped.
    - exact duplicates are dropped, first occurrence wins
    """
    patterns = [re.compile(p) for p in drop]
    boilerplate = [c for c in chunks if any(p.search(c) for p in patterns)]

    list_items = set()
    for c in chunks:
        if c.count("•") >= 2:
            list_items.update(p.strip() for p in c.split("•") if p.strip())

    seen = set()
    cleaned = []
    for c in chunks:
        if any(c in b for b in boilerplate):
            continue
        if c.count("•") < 2 and c.lstrip("• ").strip() in list_items:
            continue
        if c in seen:
            continue
        seen.add(c)
        cleaned.append(c)
    return cleaned


def validate(name, text, min_chars, min_headings=0):
    """Raise ScrapeError unless `text` plausibly is the policy page."""
    lowered = text.lower()
    for marker in BLOCK_MARKERS:
        if marker in lowered:
            raise ScrapeError(f"{name}: looks like a block page ({marker!r})")
    if len(text) < min_chars:
        raise ScrapeError(
            f"{name}: only {len(text)} chars extracted, expected at least {min_chars}"
        )
    headings = sum(1 for line in text.split("\n") if line.startswith("# "))
    if headings < min_headings:
        raise ScrapeError(
            f"{name}: only {headings} headings found, expected at least {min_headings}"
        )
    return text


def build_snapshot(name, rendered_text, source, extra_header=()):
    """Rendered page text → validated snapshot file content."""
    cfg = TARGETS[name]
    body = "\n\n".join(text_to_blocks(rendered_text, cfg.get("drop", ()), cfg.get("strip", ())))
    validate(name, body, cfg["min_chars"], cfg.get("min_headings", 0))
    header = [f"[format: {FORMAT}]", f"[source: {source}]", *extra_header]
    return "\n".join(header) + "\n\n" + body


# ─── browser ─────────────────────────────────────────────────────────────────

def _extract_args(cfg, click_delay=300):
    return {"root": cfg.get("root"), "headings": cfg.get("headings"), "clickDelay": click_delay}


async def render_live(cfg):
    """Open the live page and return (rendered_text, final_url)."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(user_agent=USER_AGENT, locale="en-US")
            await page.goto(cfg["url"], wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(cfg.get("wait", 2000))
            final_url = page.url  # before expanding: some pages rewrite the hash
            return await page.evaluate(EXTRACT_JS, _extract_args(cfg)), final_url
        finally:
            await browser.close()


_SCRIPT = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)


async def render_html(html, cfg):
    """Render saved HTML offline, with the page's own scripts stripped,
    and return its text. Used for Wayback captures and by the tests."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(offline=True)
            page = await context.new_page()
            await page.set_content(_SCRIPT.sub("", html), wait_until="domcontentloaded")
            return await page.evaluate(EXTRACT_JS, _extract_args(cfg, click_delay=0))
        finally:
            await browser.close()


# ─── wayback ─────────────────────────────────────────────────────────────────

def wayback_captures(url, limit=8, get=requests.get):
    """Newest-first (timestamp, original_url) pairs from the Wayback CDX index."""
    response = get(
        "https://web.archive.org/cdx/search/cdx",
        params={
            "url": url,
            "output": "json",
            "filter": "statuscode:200",
            "limit": f"-{limit}",
        },
        timeout=30,
    )
    if not response.text.strip():
        return []
    rows = response.json()[1:]  # first row is the column header
    return [(row[1], row[2]) for row in reversed(rows)]


async def scrape_wayback(name, get=requests.get):
    """OpenAI blocks automated access, so its page is read through the
    Internet Archive. The newest capture is sometimes itself a block page,
    so walk back through recent captures until one validates."""
    cfg = TARGETS[name]
    try:
        captures = wayback_captures(cfg["url"], get=get)
    except Exception as e:
        raise ScrapeError(f"{name}: Wayback CDX lookup failed ({e})") from e
    if not captures:
        raise ScrapeError(f"{name}: no Wayback captures found")

    last_error = None
    for timestamp, original in captures:
        snapshot_url = f"https://web.archive.org/web/{timestamp}/{original}"
        try:
            html = get(snapshot_url, timeout=30).content.decode("utf-8", "replace")
            rendered = await render_html(html, cfg)
            return build_snapshot(
                name, rendered, original,
                extra_header=(f"[Wayback snapshot: {timestamp}]", f"[URL: {snapshot_url}]"),
            )
        except Exception as e:
            last_error = e

    raise ScrapeError(f"{name}: no usable capture in the last {len(captures)} ({last_error})")


async def scrape(name):
    cfg = TARGETS[name]
    if cfg["method"] == "wayback":
        return await scrape_wayback(name)
    try:
        rendered, final_url = await render_live(cfg)
    except Exception as e:
        raise ScrapeError(f"{name}: fetch failed ({e})") from e
    return build_snapshot(name, rendered, final_url)


# ─── saving ──────────────────────────────────────────────────────────────────

def save_snapshot(name, content, now=None, base=SNAPSHOT_DIR):
    """Write today's snapshot. If one already exists (a second run on the
    same day), save to catches/ instead of overwriting it."""
    now = now or datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    folder = os.path.join(base, name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{date}.txt")

    if os.path.exists(path):
        folder = os.path.join(base, "catches", name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{date}-{now.strftime('%H%M')}.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


async def main():
    failures = []
    for name in TARGETS:
        print(f"scraping {name}...")
        try:
            content = await scrape(name)
        except ScrapeError as e:
            print(f"  FAILED — {e}")
            failures.append(name)
            continue
        print(f"  saved {save_snapshot(name, content)}")

    if failures:
        print(f"\n{len(failures)} target(s) failed: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
