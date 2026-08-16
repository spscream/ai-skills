# ai-daily

Fetch AI-industry news from RSS feeds; keep only AI-relevant items (keyword filter);
dedup against previously-sent headlines (exact + RapidFuzz); crawl short content for context.

Output (stdout) — two blocks the LLM consumes:
```
СТАТЬИ (заголовок | источник | дата | url):
Title | TechCrunch AI | 16.08 | https://...

СТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):
[Title]
<short text>
[КОНЕЦ]
```

## Usage

As a Claude Code skill: ask the agent to run the daily digest. The skill script
collects fresh items; the LLM formats the final post.

Direct:
```bash
python scripts/fetch_news.py [--config config.yaml]
```

## Config

Everything is configurable via `config.yaml` (or the defaults at the top of the
script). Sample:

```yaml
feeds:
  - name: TechCrunch AI
    url: https://techcrunch.com/category/artificial-intelligence/feed/
full_ai_feeds: ["TechCrunch AI", "HN LLM"]      # feeds assumed fully AI-relevant
ai_keywords: ["ии", "ai", "llm", "gpt", "claude", "openai", "deepseek", ...]
db_path: ./seen_ai_news.db                       # SQLite "seen" store
max_hours: 48                                    # freshness window
crawl_max_chars: 1600
max_crawl: 14
tavily_api_key_env: TAVILY_API_KEY               # optional, for /extract content
```

Requires: `python -m pip install feedparser requests rapidfuzz pyyaml`