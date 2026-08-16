# trending-skills

Fetch fresh (last 7 days) trending **skills / agents / rules / MCP** for Claude Code and
Cursor via Tavily search; dedup against previously-sent items (exact + RapidFuzz);
crawl a few for short content.

Output (stdout) — two blocks for the LLM:
```
СТАТЬИ (заголовок | источник | url):
Title | github.com | https://...

СТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):
[Title]
<short text>
[КОНЕЦ]
```

## Usage

As a Claude Code skill, ask the agent for the weekly trending-skills digest.

Direct:
```bash
python scripts/fetch_trending.py [--config config.yaml]
```

## Config

Sample `config.yaml`:

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

# Второй источник: GitHub API (свежие репозитории по темам за `days`)
github_topics:
  - claude-code-skills
  - claude-code
  - cursor-rules
  - agents-md
  - mcp-server
github_max: 10
gh_token_env: GH_TOKEN       # optional: raises API limit 60->5000 req/h
```

Requires: `python -m pip install requests rapidfuzz pyyaml`