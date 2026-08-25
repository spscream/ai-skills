#!/usr/bin/env python3
"""
ai-daily — fetch fresh AI news from RSS, filter, dedup, output two blocks.

Standalone / stateless. Reads config.yaml (or defaults), writes to SQLite "seen"
so a repeat run returns only genuinely new items. Stdout is structured for the
LLM editor to assemble the final post.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

try:
    import feedparser
except Exception:  # pragma: no cover
    feedparser = None

# The skill folder is self-contained: dedup.py sits next to this script, so copying
# `skills/ai-daily/` alone gives a working skill. The source of that file is
# `shared/dedup.py` in the repository, and a test keeps the two byte-identical.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from dedup import is_dup, title_hash  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; content-collector/1.0)"}

DEFAULT_CONFIG = {
    "feeds": [
        {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        {"name": "BBC Tech", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
        {"name": "HN LLM", "url": "https://hnrss.org/newest?q=LLM"},
    ],
    "full_ai_feeds": {"TechCrunch AI", "HN LLM"},
    "ai_keywords": [
        "ии", "искусственн", "нейросет", "нейросеч", "ai", "llm", "модел", "гпт", "gpt",
        "claude", "chatgpt", "антропик", "openai", "deepseek", "алгоритм", "машинн",
        "машинное обучение", "агент", "робот", "robotics", "генеративн", "image gen",
        "дипфейк", "беспилотник",
    ],
    "db_path": "./seen_ai_news.db",
    "max_hours": 48,
    "crawl_max_chars": 1600,
    "max_crawl": 14,
    "max_results": 40,
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


def is_ai(title, source, cfg):
    if source in cfg["full_ai_feeds"]:
        return True
    low = " " + str(title).lower().strip() + " "
    for k in cfg["ai_keywords"]:
        if k.lower() in low:
            return True
    return False


def fetch_rss(url, cfg):
    if not feedparser:
        return []
    try:
        d = feedparser.parse(url)
    except Exception:
        return []
    out = []
    for e in d.entries[:25]:
        out.append((e.get("title", "").strip(), e.link or "", e.get("published", "") or ""))
    return out


def parse_ts(pubdate):
    if not pubdate:
        return 0
    try:
        from email.utils import parsedate_to_datetime
        return int(parsedate_to_datetime(pubdate).timestamp())
    except Exception:
        return 0


def crawl_tavily(url, cfg, max_chars):
    key = os.environ.get(cfg["tavily_api_key_env"], "")
    if not key:
        return ""
    try:
        import yaml
    except Exception:
        return ""
    payload = json.dumps({"api_key": key, "urls": [url]}).encode()
    req = urllib.request.Request("https://api.tavily.com/extract", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        res = (d.get("results") or [{}])[0]
        raw = (res.get("raw_content") or "") if res else ""
        raw = re.sub(r"\s+", " ", raw).strip()[:max_chars]
        return raw
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("AI_DAILY_CONFIG", "config.yaml"))
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

    now = int(time.time())
    cutoff = now - cfg["max_hours"] * 3600
    collected = []
    for f in cfg["feeds"]:
        for title, link, pub in fetch_rss(f.get("url", ""), cfg):
            if not title or not is_ai(title, f.get("name", ""), cfg):
                continue
            collected.append((title, f.get("name", ""), parse_ts(pub), link))

    collected.sort(key=lambda x: x[2], reverse=True)
    fresh = [x for x in collected if x[2] >= cutoff]

    out, now_add = [], int(time.time())
    new_known, new_titles = set(known), list(seen_titles)
    for title, source, ts, url in fresh:
        h = title_hash(title)
        if h in new_known or is_dup(title, new_titles):
            continue
        new_known.add(h)
        new_titles.append(title)
        date_s = time.strftime("%d.%m", time.gmtime(ts)) if ts else "?"
        out.append(f"{title} | {source} | {date_s} | {url}")
        cur.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)", (now_add, h, title))
    c.commit()

    if not out:
        print("(нет свежих новых новостей за окно)")
        return

    lines = out[:cfg["max_results"]]
    print("СТАТЬИ (заголовок | источник | дата | url):")
    for l in lines:
        print(l)
    if args.crawl:
        print("\nСТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):")
        for l in lines[:cfg["max_crawl"]]:
            title = l.split(" | ")[0]
            url = l.split(" | ")[-1]
            print(f"[{title}]\n{crawl_tavily(url, cfg, cfg['crawl_max_chars'])}\n[КОНЕЦ]\n")


if __name__ == "__main__":
    main()