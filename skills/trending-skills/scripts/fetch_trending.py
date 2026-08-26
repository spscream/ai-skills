#!/usr/bin/env python3
"""
trending-skills — fetch fresh (7-day) Claude Code / Cursor skills, agents, rules,
MCP servers via Tavily; dedup; output two blocks for the LLM editor.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

# The skill folder is self-contained: dedup.py sits next to this script, so copying
# `skills/trending-skills/` alone gives a working skill. The source of that file is
# `shared/dedup.py` in the repository, and a test keeps the two byte-identical.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from dedup import is_dup, remember, title_hash  # noqa: E402

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
    # Порог по звёздам для находок с GitHub.
    #
    # Запрос ищет репозитории, созданные за последние `days`, поэтому набрать
    # звёзды они физически не успевают — и выдача забивается личными
    # шаблонами на ноль звёзд, вытесняя кураторские находки поиска.
    # Замер одной недели: 48 уникальных, медиана 7, минимум 0. Порог 10 стоит
    # выше медианы, убирает все нулёвки и единицы, но оставляет 20 кандидатов
    # — вдвое больше, чем нужно сводке.
    #
    # Ставить сильно выше опасно: в тихую неделю новых репозиториев со
    # звёздами может не быть вовсе, и секция опустеет. Ноль отключает порог.
    "github_min_stars": 10,
    "gh_token_env": "GH_TOKEN",        # optional, поднимает лимит 60->5000 req/h
    "db_path": "./seen_skills.db",
    "days": 7,
    "max_results": 24,
    "crawl_max_chars": 350,
    "max_crawl": 6,
    "tavily_api_key_env": "TAVILY_API_KEY",
    "firecrawl_api_key_env": "FIRECRAWL_API_KEY",
    "exa_api_key_env": "EXA_API_KEY",
    # Порядок запасных backend. Первый доступный берётся, когда основной
    # исчерпан или ничего не вернул.
    "search_fallbacks": ["firecrawl", "exa"],
    # Куда копить собственный расход Exa. Своей ручки остатка у Exa нет
    # (все /usage, /balance, /account отвечают 404), но каждый ответ несёт
    # costDollars — значит считать можно самим.
    "exa_spend_file": "/opt/data/state/exa_spend.json",
    # Ниже скольких кредитов Tavily перестаём его трогать и уходим на запасной
    # backend. Проверяется один раз за прогон, до первого запроса.
    #
    # Смысл в том, чтобы не расходовать остаток на поиск: extract у дневного
    # сборщика тратит из той же квоты, а новости нужнее, чем находки. Ноль
    # отключает проверку.
    #
    # Про величину: прогон делает столько запросов, сколько записей в queries
    # (сейчас девять), и происходит раз в неделю — порядка сорока в месяц.
    # Сто двадцать это запас месяца на три, то есть порог не про экономию, а
    # про то, чтобы поиск не добил остаток, нужный дневному extract.
    "tavily_min_credits": 120,
}


def warn(msg):
    """Отказ среды обязан быть слышен: stdout занят материалом, поэтому stderr."""
    print(f"fetch_trending: {msg}", file=sys.stderr)


def api_key(name, cfg):
    """Ключ из окружения, иначе из dotenv-файла.

    Окружения одного мало. Под cron hermes подгружает ~/.hermes/.env сам, и
    ключ виден; при запуске руками — нет, и сборщик молча уходил работать без
    поиска, отдавая заметно меньше находок без единого слова об этом. Отличить
    такой прогон от честного «сегодня пусто» было нечем.
    """
    val = os.environ.get(name, "")
    if val:
        return val
    path = cfg.get("env_file") or "/opt/data/.env"
    try:
        for line in open(path, encoding="utf-8"):
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


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


def tavily_credits_left(cfg):
    """Сколько кредитов Tavily осталось. None, если узнать не удалось.

    Отдельный дешёвый запрос перед прогоном: узнать остаток заранее лучше, чем
    выяснять его отказом на середине, когда часть запросов уже потрачена.
    """
    key = api_key(cfg["tavily_api_key_env"], cfg)
    if not key:
        return None
    req = urllib.request.Request("https://api.tavily.com/usage",
                                 headers={"Authorization": "Bearer " + key})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    except Exception as e:
        warn(f"остаток Tavily не проверить: {str(e)[:60]}")
        return None
    a = d.get("account") or {}
    lim, used = a.get("plan_limit"), a.get("plan_usage")
    if not isinstance(lim, int) or not isinstance(used, int):
        return None
    return max(0, lim - used)


def firecrawl_search(query, domain, days, cfg):
    """Запасной поиск. Возвращает те же тройки, что и tavily_search.

    Домен и свежесть выражаются иначе, чем у Tavily: у Firecrawl нет полей
    include_domains и days, зато понимаются оператор site: в самом запросе и
    tbs — тот же синтаксис ограничения по времени, что у поиска Google.
    """
    key = api_key(cfg.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY"), cfg)
    if not key:
        warn("нет ключа Firecrawl — запасной поиск недоступен")
        return []
    q = f"{query} site:{domain}" if domain else query
    # Дни в ближайшую корзину Google: неделя, месяц, год.
    tbs = "qdr:w" if days <= 7 else ("qdr:m" if days <= 31 else "qdr:y")
    body = {"query": q, "limit": 8, "tbs": tbs}
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v1/search", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
    except Exception as e:
        warn(f"запрос Firecrawl {q!r} не выполнен: {str(e)[:60]}")
        return []
    return [(r.get("title") or "", r.get("url") or "", r.get("description") or "")
            for r in (d.get("data") or [])]


def _note_exa_spend(cfg, cost):
    """Прибавить стоимость запроса к месячному счётчику."""
    path = cfg.get("exa_spend_file")
    if not path or not cost:
        return
    month = time.strftime("%Y-%m")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[month] = round(float(data.get(month, 0)) + float(cost), 6)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        warn(f"счётчик расхода Exa не записан: {e}")


def exa_search(query, domain, days, cfg):
    """Поиск через Exa. Те же тройки, что и у остальных backend.

    Домен задаётся полем includeDomains, свежесть — startPublishedDate:
    у Exa нет ни оператора site:, ни счётчика дней.
    """
    key = api_key(cfg.get("exa_api_key_env", "EXA_API_KEY"), cfg)
    if not key:
        warn("нет ключа Exa")
        return []
    body = {"query": query, "numResults": 8, "type": "auto",
            "contents": {"highlights": True}}
    if domain:
        body["includeDomains"] = [domain]
    if days:
        body["startPublishedDate"] = time.strftime(
            "%Y-%m-%dT00:00:00.000Z", time.gmtime(time.time() - days * 86400))
    req = urllib.request.Request(
        "https://api.exa.ai/search", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-api-key": key})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
    except Exception as e:
        warn(f"запрос Exa {query!r} не выполнен: {str(e)[:60]}")
        return []
    _note_exa_spend(cfg, (d.get("costDollars") or {}).get("total"))
    out = []
    for r in d.get("results") or []:
        hl = r.get("highlights") or []
        out.append((r.get("title") or "", r.get("url") or "",
                    " ".join(hl)[:400] if hl else ""))
    return out


BACKENDS = {"tavily": lambda *a: tavily_search(*a),
            "firecrawl": lambda *a: firecrawl_search(*a),
            "exa": lambda *a: exa_search(*a)}


def search(query, domain, days, cfg, backend):
    """Поиск выбранным backend с откатом на другой при пустом ответе.

    Пустой ответ — не всегда исчерпание квоты, но отличить одно от другого по
    ответу нельзя, а пустой прогон стоит недели без сводки. Поэтому вторая
    попытка делается всегда, и она бесплатна, когда первый backend вернул
    результаты.
    """
    order = [backend] + [b for b in (cfg.get("search_fallbacks") or [])
                         if b != backend]
    for i, name in enumerate(order):
        fn = BACKENDS.get(name)
        if fn is None:
            continue
        res = fn(query, domain, days, cfg)
        if res:
            if i:
                warn(f"запрос {query!r}: помог запасной backend {name}")
            return res
    return []


def tavily_search(query, domain, days, cfg):
    key = api_key(cfg["tavily_api_key_env"], cfg)
    if not key:
        warn(f"нет ключа в {cfg['tavily_api_key_env']} — поиск Tavily пропущен")
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
    except Exception as e:
        warn(f"запрос Tavily {query!r} не выполнен: {e}")
        return []


def github_search(topic, days, cfg):
    """GitHub API: свежие репозитории по теме (created within `days`)."""
    token = api_key(cfg.get("gh_token_env", "GH_TOKEN"), cfg)
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
        min_stars = int(cfg.get("github_min_stars", 0) or 0)
        skipped = 0
        for i in d.get("items", []):
            if i.get("stargazers_count", 0) < min_stars:
                skipped += 1
                continue
            title = f"{i.get('full_name','')}: {(i.get('description') or '').strip()[:90]}"
            out.append((title, i.get("html_url") or "", ""))
        if skipped:
            warn(f"тема {topic!r}: отсеяно {skipped} репозиториев ниже {min_stars} звёзд")
        return out
    except Exception as e:
        warn(f"поиск GitHub по теме {topic!r} не выполнен: {e}")
        return []


def crawl_tavily(url, cfg):
    key = api_key(cfg["tavily_api_key_env"], cfg)
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

    # Backend выбирается один раз за прогон, до первого запроса.
    left = tavily_credits_left(cfg)
    min_credits = int(cfg.get("tavily_min_credits", 0) or 0)
    backend = "tavily"
    if left is not None and min_credits and left < min_credits:
        backend = "firecrawl"
        warn(f"у Tavily осталось {left} кредитов (< {min_credits}) — ищу через Firecrawl")
    elif left is not None:
        warn(f"Tavily: осталось {left} кредитов, ищу через него")

    seen_urls, collected = set(), []
    for q in cfg["queries"]:
        for title, url, content in search(q.get("q"), q.get("domain"), cfg["days"], cfg, backend):
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

    if not out:
        print("(нет свежих новых навыков/агентов за окно)")
        return

    # Обрезать ДО записи, а не после. Отметить прочитанным можно только то, что
    # уходит читателю: помеченный, но не напечатанный элемент не попадёт уже
    # ни в один прогон — он исчезает молча. Здесь это особенно заметно: девять
    # запросов по восемь результатов плюс пять тем по десять дают до 122
    # кандидатов при max_results 24.
    out = out[:cfg["max_results"]]
    for title, _url in out:
        remember(cur, title, dry_run=args.dry_run, now_ts=now_add)
    if not args.dry_run:
        c.commit()

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