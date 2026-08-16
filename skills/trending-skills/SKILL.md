---
name: trending-skills
description: Fetch fresh Claude Code / Cursor skills, agents, rules, MCP by topic with dedup.
license: MIT
metadata:
  author: Alexander Malaev
  version: "0.1.0"
---

# trending-skills

Fetches fresh (last 7 days) trending **skills / agents / rules / MCP** for Claude Code
and Cursor via Tavily search plus the GitHub API, dedups against previously-sent items,
and outputs fresh items as a structured two-block digest for the LLM to format.

## When to Use

Use when you need a weekly roundup of what's trending in the Claude Code / Cursor
skill ecosystem: new skills, agents, rule files (`.cursorrules` / `AGENTS.md`),
or MCP servers.

## How to Run

```bash
python scripts/fetch_trending.py [--config config.yaml]
```

The script prints two blocks to stdout:

```
СТАТЬИ (заголовок | источник | url):            <-- fresh items
СТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):         <-- short crawled content per item
```

The agent (LLM) reads these blocks and formats the final human-readable post.
Dedup is built in: a repeat run returns only genuinely new items, safe to call weekly.

## Config

Everything is configurable via `config.yaml` (or the defaults at the top of the
script). Sample:

```yaml
queries:
  - {"q": "claude code skills trending", "domain": "github.com"}
  - {"q": "cursor rules file trending github", "domain": "github.com"}
  - {"q": "claude code MCP server new", "domain": null}
db_path: ./seen_skills.db
days: 7
max_results: 24
crawl_max_chars: 350
max_crawl: 6
tavily_api_key_env: TAVILY_API_KEY

# Second source: GitHub API (fresh repos by topic within `days`)
github_topics:
  - claude-code-skills
  - claude-code
  - cursor-rules
  - agents-md
  - mcp-server
github_max: 10
gh_token_env: GH_TOKEN      # optional: raises API limit 60 -> 5000 req/h
```

Requires: `python -m pip install requests rapidfuzz pyyaml`

## Output format (what the LLM turns into a post)

```
СТАТЬИ (заголовок | источник | url):
Title | github.com | https://...

СТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):
[Title]
<short text>
[КОНЕЦ]
```