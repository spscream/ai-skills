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
python scripts/fetch_news.py [--config config.yaml] [--dry-run] [--no-crawl]
```

`--dry-run` prints what a real run would emit but marks nothing as seen, so you
can look at the output without consuming the items.

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
ai_acronyms: ["ии", "ai", "llm", "gpt"]          # matched as a whole word only
ai_keywords: ["искусственн", "модел", "claude", "openai", ...]   # matched at a word start
db_path: ./seen_ai_news.db                       # SQLite "seen" store (dedup)
max_hours: 48                                    # freshness window
crawl_max_chars: 1600
max_crawl: 14
tavily_api_key_env: TAVILY_API_KEY               # optional, for /extract content
```

Requires: `python -m pip install feedparser requests rapidfuzz pyyaml`

## What the editor must do with this output

The keyword filter in the collector is deliberately generous: it is better to
carry one item too many than to drop real news, because an item it rejects is
never seen again. So the list you receive can contain something off topic, and
dropping it is your job, not the collector's.

- Judge by meaning, not by the words in the headline.
- Drop anything that is not about AI. Say nothing about it — do not apologise
  for a short digest and do not list what you removed.
- A short post beats a post with a stray item in it. Fewer items than you were
  aiming for is a normal outcome.
- These belong in the digest even when the headline never says "AI": robotics,
  autonomous vehicles and drones, chips and hardware for training or inference,
  datacenters built for AI, funding rounds and valuations of AI companies,
  regulation and court cases about AI.

The last point matters. Told only to drop what is off topic, a model starts
cutting anything whose headline lacks the word "AI" — a robotics funding round,
for instance. Naming the profile of the channel is what keeps it in.

## Output format (what the LLM turns into a post)

```
СТАТЬИ (заголовок | источник | дата | url):
Title | TechCrunch AI | 16.08 | https://...

СТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):
[Title]
<short text>
[КОНЕЦ]
```