#!/usr/bin/env python3
"""
ai-daily — fetch fresh AI news from RSS, filter, dedup, output two blocks.

Standalone / stateless. Reads config.yaml (or defaults), writes to SQLite "seen"
so a repeat run returns only genuinely new items. Stdout is structured for the
LLM editor to assemble the final post.
"""
import argparse
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
from dedup import is_dup, remember, title_hash  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; content-collector/1.0)"}

DEFAULT_CONFIG = {
    "feeds": [
        {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        {"name": "BBC Tech", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml"},
        {"name": "HN LLM", "url": "https://hnrss.org/newest?q=LLM"},
    ],
    "full_ai_feeds": {"TechCrunch AI", "HN LLM"},
    # Акронимы и имена, совпадающие ТОЛЬКО как отдельное слово. Без правой
    # границы "ai" ловит "aim", "aid", "air"; без левой — "Ukraine", "email",
    # "chain", "said". Сюда же те названия, что являются началом обычных слов:
    # "cohere" -> "coherent", "coherence". Голое "meta" сюда не годится:
    # у Meta много новостей про соцсети и VR, и они не про ИИ — берём "meta ai".
    "ai_acronyms": [
        "ии", "ai", "llm", "vlm", "agi", "gpt", "гпт",
        "cohere", "xai", "grok", "gemini", "sora", "llama",
    ],
    # Основы: правая граница свободна, чтобы ловить словоформы ("модел" ->
    # "модель", "модели"), но левая обязательна — иначе "ремоделирование".
    "ai_keywords": [
        # общая лексика
        "искусственн", "нейросет", "нейросеч", "нейронк", "модел",
        "алгоритм", "машинн", "машинное обучение", "генеративн", "инференс",
        "агент", "робот", "чат-бот", "чатбот",
        # Понятия по-английски. Их не было вовсе: список знал термины только
        # по-русски, а по-английски одни бренды — при том что BBC Tech
        # англоязычная и категорий в ней нет, то есть фильтр по словам
        # для неё единственный. "robot" вместо "robotics": основа ловит
        # и robots, и robotic, а "Robot horse" мимо "robotics" проходил.
        "robot", "machine learning", "deep learning", "neural net",
        "generative", "inference", "chatbot", "deepfake", "drone",
        "self-driving", "driverless", "autonomous vehicle",
        "large language model", "foundation model",
        "image gen", "дипфейк", "беспилотник",
        # компании и лаборатории
        "openai", "anthropic", "антропик", "deepmind", "mistral", "midjourney",
        "nvidia", "huggingface", "hugging face", "perplexity", "elevenlabs",
        "stability ai", "databricks", "cerebras", "sambanova", "groq",
        "moonshot", "scale ai", "character.ai", "runwayml", "meta ai",
        # модели и продукты
        "chatgpt", "claude", "deepseek", "mixtral", "qwen", "copilot",
        "stable diffusion", "gigachat", "гигачат", "яндексгпт", "кандинск",
    ],
    # Правила по <category>, применяются ДО поиска по словам: категория
    # дешевле и снимает целые пласты чужой темы прежде, чем ключ успеет
    # ошибиться. Ключи правила — имена лент из feeds.
    #
    # deny_top    — выкинуть, если верхний уровень категории в списке.
    #               Верхний уровень: "Политика / Армия" -> "политика".
    # require_any — оставить, только если есть хоть одна из категорий.
    #               Запись вообще без категорий не наказывается: лента могла
    #               перестать их отдавать, и молча потерять источник целиком
    #               хуже, чем пропустить лишнее.
    "feed_category_rules": {
        "TechCrunch AI": {"require_any": ["ai", "artificial intelligence"]},
        "Ведомости": {"deny_top": [
            "политика", "общество", "стиль жизни", "недвижимость", "карьера", "спорт",
        ]},
    },
    "db_path": "./seen_ai_news.db",
    "max_hours": 48,
    "crawl_max_chars": 1600,
    "max_crawl": 14,
    "max_results": 40,
    "tavily_api_key_env": "TAVILY_API_KEY",
}


def warn(msg):
    """Отказ среды обязан быть слышен.

    Молча вернуть пустой результат нельзя: «сегодня новостей нет» и «на машине
    нет feedparser» выглядят на stdout одинаково, и вторая причина ищется потом
    часами. Стдаут занят материалом для редактора, поэтому предупреждения идут
    в stderr.
    """
    print(f"fetch_news: {msg}", file=sys.stderr)


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.isfile(path):
        try:
            import yaml
            cfg.update(yaml.safe_load(open(path)) or {})
        except Exception as e:
            # Раньше здесь был pass: опечатка в YAML откатывала конфиг к
            # умолчаниям целиком, включая db_path, и база дедупа незаметно
            # расщеплялась надвое.
            warn(f"конфиг {path} не прочитан ({e}), работаю на умолчаниях")
    return cfg


# Буква или цифра любого из двух алфавитов. Граница слова \b здесь не годится:
# она считает границей стык латиницы и кириллицы, так что "ai" в "айти" прошло бы.
_W = r"[0-9a-zA-Zа-яёА-ЯЁ]"


def _compile_matcher(cfg):
    """Собрать один регэксп из акронимов и основ. Кэшируется в cfg."""
    acronyms = [re.escape(str(k).lower()) for k in cfg.get("ai_acronyms", []) if str(k).strip()]
    stems = [re.escape(str(k).lower()) for k in cfg.get("ai_keywords", []) if str(k).strip()]
    parts = []
    if acronyms:  # слово целиком
        parts.append(f"(?<!{_W})(?:{'|'.join(acronyms)})(?!{_W})")
    if stems:     # начало слова, окончание свободно
        parts.append(f"(?<!{_W})(?:{'|'.join(stems)})")
    return re.compile("|".join(parts)) if parts else None


def is_ai(title, source, cfg):
    if source in cfg["full_ai_feeds"]:
        return True
    rx = cfg.get("_matcher")
    if rx is None:
        rx = cfg["_matcher"] = _compile_matcher(cfg)
    if rx is None:
        return False
    return bool(rx.search(str(title).lower()))


def category_ok(source, cats, cfg):
    """Пропускает ли запись правило по категориям своей ленты.

    Замерено на общей ленте Ведомостей: из пяти срабатываний фильтра по словам
    четыре были ложными — «ПВО сбили 39 беспилотников» цеплялось за
    «беспилотник», «образование перейдет на новую модель» за «модел». Обе
    категории к теме отношения не имеют, и отсечь их по категории дешевле и
    надёжнее, чем чинить словарь.
    """
    rule = (cfg.get("feed_category_rules") or {}).get(source)
    if not rule:
        return True
    low = [str(x).strip().lower() for x in (cats or []) if str(x).strip()]
    deny = rule.get("deny_top")
    if deny:
        tops = {x.split(" / ")[0].strip() for x in low}
        if tops & {str(d).strip().lower() for d in deny}:
            return False
    need = rule.get("require_any")
    if need and low:
        if not (set(low) & {str(n).strip().lower() for n in need}):
            return False
    return True


def fetch_rss(url, cfg):
    if not feedparser:
        warn("feedparser не установлен — ленты не читаются вообще (pip install feedparser)")
        return []
    try:
        d = feedparser.parse(url)
    except Exception as e:
        warn(f"лента {url} не прочитана: {e}")
        return []
    out = []
    for e in d.entries[:25]:
        cats = [t.get("term") or "" for t in (e.get("tags") or [])]
        out.append((e.get("title", "").strip(), e.link or "",
                    e.get("published", "") or "", cats))
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
    ap.add_argument(
        "--dry-run", action="store_true",
        help="inspect output without marking items as seen (nothing is written)",
    )
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
        name = f.get("name", "")
        for title, link, pub, cats in fetch_rss(f.get("url", ""), cfg):
            if not title or not category_ok(name, cats, cfg):
                continue
            if not is_ai(title, name, cfg):
                continue
            collected.append((title, name, parse_ts(pub), link))

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
        out.append((title, source, date_s, url))

    if not out:
        print("(нет свежих новых новостей за окно)")
        return

    # Обрезать ДО записи, а не после. Отметить прочитанным можно только то, что
    # уходит читателю: помеченный, но не напечатанный элемент не попадёт уже
    # ни в один прогон — он исчезает молча.
    lines = out[:cfg["max_results"]]
    for title, _source, _date_s, _url in lines:
        remember(cur, title, dry_run=args.dry_run, now_ts=now_add)
    if not args.dry_run:
        c.commit()

    print("СТАТЬИ (заголовок | источник | дата | url):")
    for title, source, date_s, url in lines:
        print(f"{title} | {source} | {date_s} | {url}")
    if args.crawl:
        print("\nСТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):")
        for title, _source, _date_s, url in lines[:cfg["max_crawl"]]:
            print(f"[{title}]\n{crawl_tavily(url, cfg, cfg['crawl_max_chars'])}\n[КОНЕЦ]\n")


if __name__ == "__main__":
    main()