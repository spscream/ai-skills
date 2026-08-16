#!/usr/bin/env python3
"""
trending-skills — fetch fresh (7-day) Claude Code / Cursor skills, agents, rules,
MCP servers via Tavily; dedup; output two blocks for the LLM editor.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

# allow running from anywhere: make repo-root `shared/` importable
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, _ROOT)
from shared import dedup  # noqa: E402
from shared.dedup import is_dup, title_hash  # noqa: E402

DEFAULT_CONFIG = {
    "queries": [
        {"q": "claude code skills trending", "domain": "github.com"},
        {"q": "awesome claude code skills repository", "domain": "github.com"},
        {"q": "claude skills new release", "domain": None},
        {"q": "cursor rules file trending github", "domain": "github.com"},
        {"q": "AGENTS.md template examples", "domain": None},
        {"q": "claude code MCP server new", "domain": "github.com"},
        {"q": "MCP server release this week", "domain": None},
        {"q": "claude code agent workflow roundup", "domain": None},
        {"q": "cursor agent feature new", "domain": None},
    ],
    # GitHub API search: свежие репозитории по этим темам (created within `days`)
    "github_topics": [
        "claude-code-skills",
        "claude-code",
        "cursor-rules",
        "agents-md",
        "mcp-server",
    ],
    "github_max": 10,
    "gh_token_env": "GH_TOKEN",        # optional, поднимает лимит 60->5000 req/h
    "db_path": "./seen_skills.db",
    "days": 7,
    "max_results": 24,
    "crawl_max_chars": 350,
    "max_crawl": 6,
    "tavily_api_key_env": "TAVILY_API_KEY",
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.isfile(path):
        try:
            import yaml
            cfg.update(yaml.safe_load(open(path)) or {})
        except Exception:
            pass
    return cfg


def tavily_search(query, domain, days, cfg):
    key = os.environ.get(cfg["tavily_api_key_env"], "")
    if not key:
        return []
    body = {"api_key": key, "query": query, "search_depth": "basic",
            "max_results": 8, "days": days}
    if domain:
        body["include_domains"] = [domain]
    req = urllib.request.Request("https://api.tavily.com/search", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        return [(r.get("title") or "", r.get("url") or "", r.get("content") or "")
                for r in d.get("results", [])]
    except Exception:
        return []


def github_search(topic, days, cfg):
    """GitHub API: свежие репозитории по теме (created within `days`)."""
    token = os.environ.get(cfg.get("gh_token_env", "GH_TOKEN"), "")
    since = (int(time.time()) - days * 86400)
    # GitHub search wants ISO date
    since_d = time.strftime("%Y-%m-%d", time.gmtime(since))
    q = f"topic:{topic} created:>{since_d}"
    url = ("https://api.github.com/search/repositories?q=" + urllib.parse.quote(q)
           + "&sort=stars&order=desc&per_page=" + str(cfg.get("github_max", 10)))
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ai-skills"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        out = []
        for i in d.get("items", []):
            title = f"{i.get('full_name','')}: {(i.get('description') or '').strip()[:90]}"
            out.append((title, i.get("html_url") or "", ""))
        return out
    except Exception:
        return []


def crawl_tavily(url, cfg):
    key = os.environ.get(cfg["tavily_api_key_env"], "")
    if not key:
        return ""
    payload = json.dumps({"api_key": key, "urls": [url]}).encode()
    req = urllib.request.Request("https://api.tavily.com/extract", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        res = (d.get("results") or [{}])[0]
        raw = (res.get("raw_content") or "") if res else ""
        raw = re.sub(r"\s+", " ", raw).strip()[:cfg["crawl_max_chars"]]
        return raw
    except Exception:
        return ""


def source_of(url):
    m = re.search(r"https?://([^/]+)", url or "")
    return m.group(1) if m else url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("TRENDING_CONFIG", "config.yaml"))
    ap.add_argument("--no-crawl", dest="crawl", action="store_false", default=True)
    args = ap.parse_args()
    cfg = load_config(args.config)

    db_dir = os.path.dirname(os.path.abspath(cfg["db_path"]))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    c = sqlite3.connect(cfg["db_path"])
    c.execute("CREATE TABLE IF NOT EXISTS seen (added_ts INTEGER, h TEXT PRIMARY KEY, title TEXT)")
    cur = c.cursor()
    cur.execute("SELECT h, title FROM seen")
    rows = cur.fetchall()
    known = {r[0] for r in rows}
    seen_titles = [t for _, t in rows if t]

    seen_urls, collected = set(), []
    for q in cfg["queries"]:
        for title, url, content in tavily_search(q.get("q"), q.get("domain"), cfg["days"], cfg):
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            collected.append((title, url))
    # GitHub API: свежие репозитории по темам
    for topic in cfg.get("github_topics", []):
        for title, url, content in github_search(topic, cfg["days"], cfg):
            if not title or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            collected.append((title, url))

    out, now_add = [], int(time.time())
    new_known, new_titles = set(known), list(seen_titles)
    for title, url in collected:
        h = title_hash(title)
        if h in new_known or is_dup(title, new_titles):
            continue
        new_known.add(h)
        new_titles.append(title)
        out.append((title, url))
        cur.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)", (now_add, h, title))
    c.commit()

    if not out:
        print("(нет свежих новых навыков/агентов за окно)")
        return

    out = out[:cfg["max_results"]]
    print("СТАТЬИ (заголовок | источник | url):")
    for title, url in out:
        print(f"{title} | {source_of(url)} | {url}")
    if args.crawl:
        print("\nСТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):")
        for title, url in out[:cfg["max_crawl"]]:
            content = crawl_tavily(url, cfg) or ""
            print(f"[{title}]\n{content}\n[КОНЕЦ]\n")


if __name__ == "__main__":
    main()