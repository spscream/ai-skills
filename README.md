# ai-skills

A personal collection of **stateless skills for Claude Code** (and any agent with a tool loop): each skill fetches fresh, deduplicated content and hands the assembly to the LLM.

The pattern is "stateless collector script + LLM editor", with **fuzzy dedup built into the scripts** via [RapidFuzz](https://pypi.org/project/rapidfuzz/) — near-duplicate headlines / repeated items between runs are suppressed before they ever reach the model.

New skills get added as folders under [`skills/`](skills/), each bundling its own `SKILL.md` + `scripts/`.

## Skills

| Skill | What it fetches | Source |
|-------|-----------------|--------|
| [`ai-daily`](skills/ai-daily) | AI industry news, filtered to AI-relevant items | RSS feeds |
| [`trending-skills`](skills/trending-skills) | Fresh (last 7 days) Claude Code / Cursor skills, agents, rules, MCP servers | Tavily search + GitHub API |

## Installation

```bash
pip install -r requirements.txt
```

Copy one skill folder (e.g. `skills/ai-daily`) into your agent's skills directory. Each skill bundles its own `SKILL.md` and `scripts/`.

## Adding a new skill

1. Create a folder `skills/<name>/` with a `SKILL.md` (description, usage, config) and a `scripts/` collector.
2. If it needs fuzzy dedup, reuse [`shared/dedup.py`](shared/dedup.py).
3. Commit and push — the repo is built to grow as a collection.

## Design philosophy

```
┌─────────────────────────────────────────────┐
 │ Agent (LLM) layer                           │
 │ (scheduled runs, persistence, formatting)   │
 └─────────────────────────────────────────────┘
                  ↓
 ┌─────────────────────────────────────────────┐
 │ Stateless collector scripts                 │
 │  ai-daily ⇄ RSS        trending-skills      │
 │  (filter + RapidFuzz dedup, print metadata) │
 └─────────────────────────────────────────────┘
                  ↓
 ┌─────────────────────────────────────────────┐
 │ External sources (RSS, web, GitHub API)     │
 └─────────────────────────────────────────────┘
```

Each script is **stateless and idempotent**: it reads a small SQLite "seen" DB, marks what it printed, and on the next run returns only genuinely new items. Stdout is structured as two blocks (`СТАТЬИ` metadata + `СОДЕРЖАНИЕ` content) that the LLM consumes to write the final post.

## Why dedup lives in the script

A collector that only does exact-hash dedup lets near-duplicates through (same incident with reworded titles). These scripts use RapidFuzz `partial_ratio` (threshold ~65) **plus** exact hash, with a token-overlap fallback when RapidFuzz is unavailable — so rewrites like "OpenAI launched GPT-5" vs "OpenAI releases GPT-5, all details" collapse into one item.

## Config

All paths / sources are configurable via a small `config.yaml` (or env vars) at the top of each script — no hardcoded machine paths.

## License

[MIT](LICENSE)