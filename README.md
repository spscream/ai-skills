# ai-skills

A personal collection of **skills for Claude Code** (and any agent with a tool loop).

The skills differ in kind. Some collect fresh content. Some drive other tools or other models.
What they share is the split: **a stateless script does the mechanical work, and the agent writes
the result**. The script never formats prose, and the agent never scrapes or polls by hand.

Each skill is a folder under [`skills/`](skills/) with its own `SKILL.md` and `scripts/`.
Copy the folder you want into your agent's skills directory. Nothing else in this repo is needed
at runtime: a skill that shares code carries its own copy, and
[`tests/test_standalone_skill.py`](tests/test_standalone_skill.py) runs every skill from a copied
folder to keep that promise true.

## Skills

| Skill | Kind | What it does | Needs |
|-------|------|--------------|-------|
| [`ai-daily`](skills/ai-daily) | collector | AI-industry news from RSS, filtered to AI-relevant items, deduplicated against earlier runs. The default feeds and keywords cover Russian and English; change `feeds` and `ai_keywords` in the config for another language | Python |
| [`trending-skills`](skills/trending-skills) | collector | Claude Code / Cursor skills, agents, rules and MCP servers from the last 7 days | Python, Tavily key, GitHub API |
| [`consensus`](skills/consensus) | panel | Asks one question to models from several vendors in two rounds, and keeps their disagreements | bash, jq, `claude` and `agent` CLIs |

## Installation

Install what the skill you copied needs.

The collectors need Python packages:

```bash
pip install -r requirements.txt
```

The `consensus` panel needs no Python and no API key. It needs `jq`, the `claude` CLI, and the
Cursor CLI (`agent login`). It runs both on their own subscription.

## Adding a new skill

1. Create the folder `skills/<name>/`. Put a `SKILL.md` and a `scripts/` directory in it.
2. Give `SKILL.md` YAML frontmatter with `name` and `description`. The `name` must equal the
   folder name, in lowercase and hyphens. The test suite checks this.
3. Keep the script stateless. Let it print structured material. Let the agent write the prose.
4. Use no machine-specific paths. Take configuration from `config.yaml`, from flags, or from
   environment variables.
5. Run the tests. Then commit and push. The repo is built to grow as a collection.

If the new skill collects content and needs fuzzy dedup, take
[`shared/dedup.py`](shared/dedup.py) — but **copy it into the skill's own `scripts/`** and import
it from there. `shared/` is the source, not a runtime dependency: a skill that imports it from the
repository root stops working the moment its folder is copied out. The test suite compares each
copy with the source byte for byte, so edit `shared/dedup.py` and copy it over the others.

## Content collectors

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

Each collector is **stateless and idempotent**: it reads a small SQLite "seen" database, marks
what it printed, and on the next run returns only genuinely new items. Stdout carries two blocks
(`СТАТЬИ` metadata and `СОДЕРЖАНИЕ` content) that the LLM turns into the final post.

### Why dedup lives in the script

A collector that does exact-hash dedup only lets near-duplicates through: the same incident under
a reworded title. These scripts use RapidFuzz `partial_ratio` (threshold ~65) **plus** the exact
hash, with a token-overlap fallback when RapidFuzz is absent. So "OpenAI launched GPT-5" and
"OpenAI releases GPT-5, all details" collapse into one item.

## Model panel

[`consensus`](skills/consensus) sends one question to models from different vendors. Round one
collects independent answers. Round two shows each model the other answers with the names removed,
and asks it to revise. The script prints the material and stops there; the agent writes the
synthesis and keeps the disagreements, because agreement between models is not proof.

Both harnesses run read-only. Claude runs without `Edit`, `Write` and `Bash`. Cursor runs with
`--mode ask`.

## Tests

```bash
python -m pytest tests/ -v
```

The suite checks the pure functions of the collectors, the dedup helper, and the frontmatter of
every `SKILL.md` against the Agent Skills spec. CI runs it on Python 3.11, 3.12 and 3.13.

## License

[MIT](LICENSE)
