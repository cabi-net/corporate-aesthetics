# corporate aesthetics (fka palimpsest)
 
An automated archive that watches the language of institutional AI and data privacy disclosures over time, and makes their changes visible (diff viewer to be implemented).
 
---
## what it watches
 
| institution | page | method |
|---|---|---|
| Google | [How Google Assistant works with your data](https://support.google.com/assistant/answer/11091015?hl=en) | Playwright |
| Meta | [How Meta uses information for generative AI models](https://privacycenter.instagram.com/privacy/genai/) | Playwright |
| Microsoft | [Data, privacy, and security for Models sold by Azure](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy) | Playwright |
| OpenAI | [Data Processing Addendum](https://openai.com/policies/data-processing-addendum/) | Wayback Machine |

---

## how it works

A GitHub Actions workflow runs every Monday at 9am UTC. It scrapes each target page, extracts the meaningful text, and commits a dated snapshot to this repository.

OpenAI's policy pages actively block automated access. Their snapshots are retrieved via the Internet Archive instead — a layer of mediation that is itself part of the record.

Before anything is saved, each scrape is cleaned and checked:

- **cleaning** — side navigation, sign-in banners and "related articles" lists are dropped, list items captured more than once are collapsed, and invisible whitespace is normalised. Without this, Meta's page appeared to change almost every week because one nav link kept appearing and disappearing.
- **validation** — if the text contains a bot-wall phrase or is far shorter than the page should be, nothing is written and the workflow run fails. For OpenAI, the scraper walks back through recent Wayback captures until it finds one that is actually the policy.

Snapshots taken before November 2026 are stored as originally scraped, noise included. They are left untouched; `clean_chunks()` in `scrape.py` can be applied when reading them.

If a snapshot file for the current date already exists when the script runs — for instance, if it is triggered more than once on the same day — the new output is saved to `snapshots/catches/{institution}/YYYY-MM-DD-HHMM.txt` instead of overwriting the canonical snapshot.

---

## structure

```
snapshots/
├── google/
│     YYYY-MM-DD.txt
├── meta/
│     YYYY-MM-DD.txt
├── microsoft/
│     YYYY-MM-DD.txt
├── openai/
│     YYYY-MM-DD.txt
├── catches/     second runs on the same day
└── rejected/    captures that turned out to be block pages or errors
```

---
 
## technical
 
Built with Python, Playwright, BeautifulSoup, and GitHub Actions. There is no database - the repository history is the data structure.

Each target is one entry in `TARGETS` in `scrape.py` (url, tags to collect, boilerplate patterns, minimum length). Adding a page to watch means adding an entry.

```bash
pip install -r requirements-dev.txt
playwright install chromium   # only needed to run the scraper itself
pytest                        # 42 tests, no network
python scrape.py
```

The tests cover extraction, cleaning, validation, the Wayback fallback and file saving. Several run against the archive itself: they assert that the recorded nav noise disappears after cleaning and that the real rewrites (Meta, 13 July 2026; Microsoft, 25 May 2026) still show up as changes.

---

## status
 
Began archiving: March 2026  
Snapshot frequency: weekly  
Diff viewer: forthcoming

---

## a note on infrastructure

This project runs on GitHub, which is owned by Microsoft. Microsoft is one of the four companies whose rhetorical behavior this archive documents. This is not a contradiction to resolve or hide; it is part of the record.
