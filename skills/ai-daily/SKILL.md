---
name: ai-daily
description: Fetch fresh AI news from RSS with dedup; emit structured digest for the editor.
license: MIT
metadata:
  author: Alexander Malaev
  version: "0.1.0"
---

# ai-daily

Fetches AI-industry news from RSS feeds, keeps only AI-relevant items (keyword
filter), dedups against previously-sent headlines (exact + RapidFuzz), and
outputs fresh items as a structured two-block digest for the LLM to format.

## When to Use

Use when you need a fresh digest of AI news: daily briefing, channel/feed post,
or a roundup of what happened in the last 24–48 hours.

## How to Run

```bash
python scripts/fetch_news.py [--config config.yaml]
```

The script prints two blocks to stdout:

```
СТАТЬИ (заголовок | источник | дата | url):     <-- fresh items
СТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):        <-- short crawled content per item
```

The agent (LLM) reads these blocks and formats the final human-readable post.
Dedup is built in: a repeat run returns only genuinely new items, so it is safe
to call every day.

## Config

Everything is configurable via `config.yaml` (or the defaults at the top of the
script). Sample:

```yaml
feeds:
  - name: TechCrunch AI
    url: https://techcrunch.com/category/artificial-intelligence/feed/
full_ai_feeds: ["TechCrunch AI", "HN LLM"]      # feeds assumed fully AI-relevant
ai_keywords: ["ии", "ai", "llm", "gpt", "claude", "openai", "deepseek", ...]
db_path: ./seen_ai_news.db                       # SQLite "seen" store (dedup)
max_hours: 48                                    # freshness window
crawl_max_chars: 1600
max_crawl: 14
tavily_api_key_env: TAVILY_API_KEY               # optional, for /extract content
```

Requires: `python -m pip install feedparser requests rapidfuzz pyyaml`

## Output format (what the LLM turns into a post)

```
СТАТЬИ (заголовок | источник | дата | url):
Title | TechCrunch AI | 16.08 | https://...

СТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):
[Title]
<short text>
[КОНЕЦ]
```