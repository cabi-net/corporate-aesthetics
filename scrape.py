"""
corporate aesthetics — weekly snapshot scraper.

Each target is one entry in TARGETS. A run fetches the page, extracts the
text, cleans out navigation noise, validates that what came back is really
the policy (not a block page or an empty shell), and only then writes a
dated snapshot. A target that fails validation writes nothing and makes the
run exit non-zero, so the workflow fails visibly instead of archiving junk.
"""

import asyncio
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SNAPSHOT_DIR = "snapshots"

# ─── targets ─────────────────────────────────────────────────────────────────
#
# tags       elements whose text is collected
# min_len    shortest chunk worth keeping
# root       CSS selector to scope extraction to (falls back to whole page)
# drop       regexes for boilerplate chunks (nav menus, sign-in banners)
# min_chars  a valid snapshot is at least this long
# expand     click collapsed accordions before reading the page
# wait       ms to wait after load

TARGETS = {
    "google": {
        "method": "playwright",
        "url": "https://support.google.com/assistant/answer/11091015?hl=en",
        "tags": ["h2", "h3", "p", "li"],
        "min_len": 40,
        "drop": [r"^\d+ of \d+ "],  # "related articles" list
        "min_chars": 3000,
        "wait": 3000,
    },
    "meta": {
        "method": "playwright",
        "url": "https://privacycenter.instagram.com/privacy/genai/",
        "tags": ["h1", "h2", "h3", "p", "li", "span"],
        "min_len": 60,
        "drop": [
            r"^Privacy Policy What is the Privacy Policy",  # side nav
            r"^Other policies and articles",                # side nav
        ],
        "min_chars": 4000,
        "expand": True,
        "wait": 4000,
    },
    "microsoft": {
        "method": "playwright",
        "url": "https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy",
        "tags": ["h1", "h2", "h3", "p", "li"],
        "min_len": 40,
        "root": "main",
        "drop": [r"^Access to this page requires authorization"],
        "min_chars": 6000,
        "wait": 4000,
    },
    "openai": {
        "method": "wayback",
        "url": "https://openai.com/policies/data-processing-addendum/",
        "tags": ["h1", "h2", "h3", "p", "li"],
        "min_len": 40,
        "min_chars": 6000,
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


# ─── text extraction (pure functions — these are what the tests cover) ───────

_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


def normalize(text):
    """Collapse whitespace and strip invisible characters, so a changed
    non-breaking space doesn't register as a changed policy."""
    text = text.translate(_INVISIBLE).replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def extract_chunks(html, tags, min_len=40, root=None):
    """Return the text of each matching element, in document order."""
    soup = BeautifulSoup(html, "html.parser")
    scope = (soup.select_one(root) if root else None) or soup
    chunks = []
    for tag in scope.find_all(tags):
        text = normalize(tag.get_text(separator=" ", strip=True))
        if len(text) > min_len:
            chunks.append(text)
    return chunks


def clean_chunks(chunks, drop=()):
    """Remove navigation noise and nested duplicates.

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


def validate(name, text, min_chars):
    """Raise ScrapeError unless `text` plausibly is the policy page."""
    lowered = text.lower()
    for marker in BLOCK_MARKERS:
        if marker in lowered:
            raise ScrapeError(f"{name}: looks like a block page ({marker!r})")
    if len(text) < min_chars:
        raise ScrapeError(
            f"{name}: only {len(text)} chars extracted, expected at least {min_chars}"
        )
    return text


def html_to_snapshot(name, html):
    """Full pipeline for one target: extract → clean → validate."""
    cfg = TARGETS[name]
    chunks = extract_chunks(html, cfg["tags"], cfg["min_len"], cfg.get("root"))
    text = "\n\n".join(clean_chunks(chunks, cfg.get("drop", ())))
    return validate(name, text, cfg["min_chars"])


# ─── fetching ────────────────────────────────────────────────────────────────

async def fetch_playwright(cfg):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(user_agent=USER_AGENT)
            await page.goto(cfg["url"], wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(cfg.get("wait", 2000))

            if cfg.get("expand"):
                for button in await page.query_selector_all('[role="button"]'):
                    try:
                        if await button.get_attribute("aria-expanded") == "false":
                            await button.click()
                            await page.wait_for_timeout(500)
                    except Exception:
                        continue  # a button that won't click shouldn't sink the run
                await page.wait_for_timeout(2000)

            return await page.content()
        finally:
            await browser.close()


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


def scrape_wayback(name, get=requests.get):
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
            html = get(snapshot_url, timeout=30).content
            body = html_to_snapshot(name, html)
        except Exception as e:
            last_error = e
            continue
        return f"[Wayback snapshot: {timestamp}]\n[URL: {snapshot_url}]\n\n{body}"

    raise ScrapeError(f"{name}: no usable capture in the last {len(captures)} ({last_error})")


async def scrape(name):
    cfg = TARGETS[name]
    if cfg["method"] == "wayback":
        return scrape_wayback(name)
    try:
        html = await fetch_playwright(cfg)
    except Exception as e:
        raise ScrapeError(f"{name}: fetch failed ({e})") from e
    return html_to_snapshot(name, html)


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
