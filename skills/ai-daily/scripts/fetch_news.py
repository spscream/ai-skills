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
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.error
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
        # A Russian-language source, since half the keyword list is Russian.
        # Chosen by measurement over a general newspaper feed: within the
        # window the collector actually reads, the newspaper produced no AI
        # items at all and its keyword hits were mostly false (military drones
        # caught on беспилотник), while this one produces real market news.
        # It is a firehose, hence the larger window below.
        {"name": "CNews", "url": "https://www.cnews.ru/inc/rss/news.xml", "max_items": 40},
    ],
    # How many entries to read from a feed. A topic feed emits few and all on
    # subject, so ten is plenty. On a firehose the window itself is the limit,
    # not the quality of the source: CNews hits grow with it almost linearly --
    # 2 at ten entries, 4 at twenty, 5 at thirty, 8 at fifty.
    "max_items": 10,
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
        "image gen", "imagegen", "дипфейк", "беспилотник",
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
    # Сколько кандидатов уходит на ранжирование. Кандидат бесплатен — краула на
    # этом шаге ещё нет, — поэтому окно щедрое: пусть ранжировщик видит весь
    # суточный объём лент, а не верхушку по свежести.
    "max_pool": 60,
    # Потолок на источник в итоговой выдаче, {имя ленты: сколько}. Сортировка по
    # свежести сама по себе даёт перевес той ленте, чьё утро совпало с часом
    # запуска: 03.09.2026 CNews занял 6 слотов из 14, потому что сводка выходит
    # в 10:30 МСК — у русской ленты утренняя пачка уже вышла, у американских
    # ещё ночь. Пустой словарь — потолка нет.
    "max_per_source": {},
    # Внешний ранжировщик: команда, которая читает кандидатов из stdin и
    # печатает их номера в порядке интересности. Пусто — порядок по свежести.
    "rank_cmd": None,
    "rank_timeout": 120,
    "tavily_api_key_env": "TAVILY_API_KEY",
    # Запасные крауллеры в порядке очереди. Ни один из трёх не берёт всё:
    # Tavily справляется и с TechCrunch, и с cnews; Exa силён на англоязычном
    # и проходит Cloudflare, но спотыкается на русских лентах; Firecrawl
    # наоборот берёт cnews и пасует перед капчей. Отсюда и цепочка.
    "exa_api_key_env": "EXA_API_KEY",
    "firecrawl_api_key_env": "FIRECRAWL_API_KEY",
    # Откуда дочитывать ключи, если их нет в окружении.
    "env_file": "/opt/data/.env",
}


def warn(msg):
    """Отказ среды обязан быть слышен.

    Молча вернуть пустой результат нельзя: «сегодня новостей нет» и «на машине
    нет feedparser» выглядят на stdout одинаково, и вторая причина ищется потом
    часами. Стдаут занят материалом для редактора, поэтому предупреждения идут
    в stderr.
    """
    print(f"fetch_news: {msg}", file=sys.stderr)


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


def fetch_rss(url, cfg, limit=None):
    limit = limit or cfg.get("max_items", 10)
    if not feedparser:
        warn("feedparser не установлен — ленты не читаются вообще (pip install feedparser)")
        return []
    try:
        d = feedparser.parse(url)
    except Exception as e:
        warn(f"лента {url} не прочитана: {e}")
        return []
    out = []
    for e in d.entries[:limit]:
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


def normalize_spaces(raw: str) -> str:
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def clean_article(raw: str) -> str:
    """Strip navigation, footers and page furniture from a crawled article.

    Conservative on purpose: it cuts only at markers that cannot appear inside
    the text of an article, because a wrong cut loses real content silently.
    """
    if not raw:
        return ""
    # 1) Жёсткие обрезки ТОЛЬКО по однозначным футер-маркерам страницы.
    for marker in ["## BibTeX formatted citation", "## BibTeX", "## Bibliographic and Citation",
                   "## Submission history", "## Demos", "Recommenders and Search Tools",
                   "arXivLabs is a framework", "Loading the next article", "Most Popular",
                   "Related Stories", "Sign up for our newsletter", "Terms of Use", "Back to top"]:
        idx = raw.find(marker)
        if idx != -1:
            raw = raw[:idx]
    # 1b) у TechCrunch контент начинается после метки "In Brief" — сдвигаем туда старт, если она есть
    brief = raw.find("In Brief")
    if brief != -1:
        raw = raw[brief:]
    # 1c) GitHub: навигация в начале, контент это README
    gh = raw.find("README")
    if "github.com" in raw and gh != -1:
        raw = raw[gh:]
    # 2) Обрезаем слишком длинные (для страниц, где футер не найден)
    raw = raw[:6000]
    # 3) Схлопываем пробелы/пустые строки, убираем строки-разделители и навигацию
    lines = []
    for l in raw.splitlines():
        s = l.strip()
        if not s:
            continue
        # служебные GitHub/JS строки
        if s in ("You signed in with another tab or window. Reload to refresh your session.",
                 "You signed out in another tab or window. Reload to refresh your session.",
                 "You switched accounts on another tab or window. Reload to refresh your session.",
                 "Dismiss alert", "{{ message }}", "Appearance settings", "Sign in", "Sign up"):
            continue
        if re.fullmatch(r"[\|\-–_=·*#\s]{8,}", s):
            continue
        # логотип/картинки-ссылки в начале (markdown ![]) — выкидываем
        if s.startswith("[!["):
            continue
        # строки-навигация/логотипы: много markdown-ссылок при малом числе букв
        links = len(re.findall(r"\]\(|\!\[", s))
        letters = len(re.findall(r"[A-Za-zА-Яа-я]", s))
        if links >= 2 and letters < links * 6:
            continue
        # одиночные категории/теги — ссылки-якоря внутри
        if re.fullmatch(r"\[\]\([^)]+\)|\[[A-Za-z &]+\]\(/[^)]*\)", s):
            continue
        lines.append(s)
    return normalize_spaces("\n".join(lines))


def crawl_arxiv_abs(url: str, max_chars: int = 3000) -> str:
    """Pull the abstract straight from an arXiv HTML page.

    The extract API returns the whole page furniture for arXiv and little of
    the abstract, so this reads the one block that matters.
    """
    try:
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        m = re.search(r'<blockquote class="abstract[^"]*">(.*?)</blockquote>', html, re.S | re.I)
        if not m:
            return ""
        import html as _html
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        text = _html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        text = text.lstrip("Abstract:").strip()
        return text[:max_chars]
    except Exception:
        return ""


class CrawlerRefused(Exception):
    """Провайдер краула отказал целиком, а не по конкретной странице.

    Разница принципиальная. «Страница не отдалась» — частный случай: платный
    сайт, битая ссылка, таймаут; следующий URL у того же провайдера пройдёт.
    «Провайдер отказал» — кончились кредиты, протух ключ, упёрлись в лимит
    частоты; следующие тринадцать запросов туда же обречены, и делать их
    незачем.

    До 03.09.2026 этой разницы не было: любой отказ ловился одним `except` и
    превращался в пустую строку. 02.09 у Tavily кончился баланс на середине
    цикла, девять статей приехали без содержания, редактор их молча выбросил,
    а пост вышел вчетверо короче обычного — и ни в коде, ни в выводе не было
    ничего, что отличало бы это от честного «сегодня новостей мало».
    """


# Коды, по которым отказ считается отказом провайдера, а не страницы.
# 432 — фирменный «кредиты кончились» у Tavily, 402 — общепринятый Payment
# Required, 401/403 — ключ, 429 — частота, 5xx — авария на их стороне.
API_REFUSAL_CODES = {401, 402, 403, 429, 432}

# Сколько страниц подряд должно не отдаться, чтобы счесть это отказом
# провайдера, даже если кодов выше он не присылал. Прикрывает случай, когда
# сервис отвечает 200 с пустотой или просто перестаёт отвечать: три подряд —
# это уже не совпадение, а тенденция.
CONSECUTIVE_FAILURES_AS_REFUSAL = 3

# Приметы страницы-заглушки антибота. Firecrawl отдаёт такую с кодом 200 и
# полем success=true — по коду возврата её не отличить, только по тексту.
# Проверено на TechCrunch: 48 килобайт «Checking your Browser…» вместо статьи.
CHALLENGE_MARKERS = (
    "checking your browser", "verifying you are human", "enable javascript and cookies",
    "challenges.cloudflare.com", "just a moment...", "ddos protection by",
)

# Ниже этого объёма текст бесполезен редактору: столько занимает одна
# навигация, оставшаяся после чистки. Такой результат считаем неудачей
# страницы и пробуем следующего провайдера.
MIN_USEFUL_CHARS = 200


def _usable(text: str) -> bool:
    """Похоже ли это на статью, а не на заглушку антибота или на огрызок."""
    if len(text) < MIN_USEFUL_CHARS:
        return False
    head = text[:2000].lower()
    return not any(m in head for m in CHALLENGE_MARKERS)


def _http_json(url, payload, headers, timeout=40):
    """POST JSON, вернуть разобранный ответ. HTTP-код отказа — в CrawlerRefused."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=dict(headers, **{"Content-Type": "application/json"}))
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    except urllib.error.HTTPError as e:
        if e.code in API_REFUSAL_CODES or e.code >= 500:
            raise CrawlerRefused(f"HTTP {e.code}") from e
        raise


def crawl_tavily(url, cfg, max_chars):
    key = api_key(cfg["tavily_api_key_env"], cfg)
    if not key:
        raise CrawlerRefused(f"нет ключа {cfg['tavily_api_key_env']}")
    # arXiv is handled separately: the extract API brings back the page
    # furniture and barely any of the abstract.
    if "arxiv.org" in url and "/abs/" in url:
        return crawl_arxiv_abs(url, max_chars)
    d = _http_json("https://api.tavily.com/extract",
                   {"api_key": key, "urls": [url]}, {})
    res = (d.get("results") or [{}])[0]
    raw = (res.get("raw_content") or "") if res else ""
    return clean_article(raw)[:max_chars]


def crawl_exa(url, cfg, max_chars):
    """Запасной краул №1. Берёт из индекса, при промахе крауллит вживую.

    Выбран первым запасным по замеру 03.09.2026: единственный из трёх, кто
    отдаёт статьи TechCrunch — их Tavily берёт, а Firecrawl упирается в
    капчу Cloudflare. Русские источники ему не даются (cnews отвечает ему
    500), поэтому в одиночку он основной заменой быть не может.
    """
    key = api_key(cfg.get("exa_api_key_env", "EXA_API_KEY"), cfg)
    if not key:
        raise CrawlerRefused("нет ключа EXA_API_KEY")
    d = _http_json("https://api.exa.ai/contents",
                   {"urls": [url], "text": True, "livecrawl": "fallback"},
                   {"x-api-key": key}, timeout=60)
    res = (d.get("results") or [{}])[0]
    return clean_article((res.get("text") or "") if res else "")[:max_chars]


def crawl_firecrawl(url, cfg, max_chars):
    """Запасной краул №2. Единственный, кто берёт cnews; на CF-сайтах пасует."""
    key = api_key(cfg.get("firecrawl_api_key_env", "FIRECRAWL_API_KEY"), cfg)
    if not key:
        raise CrawlerRefused("нет ключа FIRECRAWL_API_KEY")
    d = _http_json("https://api.firecrawl.dev/v2/scrape",
                   {"url": url, "formats": ["markdown"], "onlyMainContent": True},
                   {"Authorization": f"Bearer {key}"}, timeout=60)
    md = (d.get("data") or {}).get("markdown") or ""
    return clean_article(md)[:max_chars]


# Порядок обхода. Именами, а не ссылками на функции: ссылки замёрзли бы на
# момент импорта, и подмена `crawl_tavily` в тесте молча не сработала бы —
# цепочка продолжала бы звать настоящую и ходить в сеть.
CRAWLERS = ("tavily", "exa", "firecrawl")


def _crawler(name):
    return globals()[f"crawl_{name}"]


class CrawlChain:
    """Цепочка провайдеров краула с запоминанием отказов.

    Отказавший провайдер выбывает до конца прогона: повторять к нему запросы
    по каждому следующему URL — значит тратить минуты на гарантированные
    ошибки. Отчёт о том, кто сколько отдал и кто отвалился, уходит в stderr:
    обёртка ищет там маркер и шлёт владельцу личное сообщение.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.dead = {}                      # имя -> причина отказа
        self.served = {}                    # имя -> сколько страниц отдал
        self.streak = {}                    # имя -> неудач подряд
        self.empty = 0                      # страниц без содержания

    def fetch(self, url, max_chars):
        """Текст статьи и признак того, что провайдеры вообще отвечали.

        Возвращает (text, reached). reached=False означает, что живых
        провайдеров не осталось и статью даже не пытались взять — такую
        нельзя ни отдавать редактору, ни помечать прочитанной.
        """
        alive = [n for n in CRAWLERS if n not in self.dead]
        if not alive:
            return "", False
        # Ответил ли хоть кто-то по существу этой страницы. Отказ на уровне
        # сервиса ответом не считается: на первой же статье прогона все трое
        # могут выбыть разом, и без этого флага она уходила бы в «прочитанные»
        # пустой — ровно та потеря, ради которой всё и затевалось.
        answered = False
        for name in alive:
            try:
                text = _crawler(name)(url, self.cfg, max_chars)
            except CrawlerRefused as e:
                self.dead[name] = str(e)
                warn(f"краул {name} отказал целиком ({e}) — выбывает до конца прогона")
                continue
            except Exception as e:
                answered = True
                self.streak[name] = self.streak.get(name, 0) + 1
                warn(f"краул {name}: {url} не выполнен ({e})")
                if self.streak[name] >= CONSECUTIVE_FAILURES_AS_REFUSAL:
                    self.dead[name] = f"{CONSECUTIVE_FAILURES_AS_REFUSAL} ошибки подряд"
                    warn(f"краул {name} выбывает: {self.dead[name]}")
                continue
            answered = True
            if _usable(text):
                self.streak[name] = 0
                self.served[name] = self.served.get(name, 0) + 1
                return text, True
            warn(f"краул {name}: {url} вернул непригодное ({len(text)} симв.)")
        if not answered:
            return "", False
        # Живые провайдеры попробовали и не смогли — это беда страницы, а не
        # сервиса. Статью отдаём без содержания и помечаем прочитанной: иначе
        # платный сайт возвращался бы в выдачу каждый день.
        self.empty += 1
        return "", True

    def report(self):
        served = ", ".join(f"{n}={c}" for n, c in self.served.items()) or "никто"
        line = f"CRAWL-REPORT отдали: {served}; без содержания: {self.empty}"
        if self.dead:
            line += "; ОТКАЗАЛИ: " + ", ".join(f"{n} ({r})" for n, r in self.dead.items())
        warn(line)
        if self.dead:
            # Маркер для обёртки: по нему post_common шлёт владельцу личку.
            warn("CRAWL-DEGRADED " + "; ".join(
                f"{n}: {r}" for n, r in self.dead.items()))


def rank_pool(pool, cfg):
    """Переставить кандидатов внешним ранжировщиком. Вернуть новый порядок.

    Свежесть — не мера интересности. Сортировка по времени публикации поднимает
    наверх любой только что вышедший пресс-релиз и оставляет за бортом релиз
    модели суточной давности; на ней же держался перевес одной ленты над
    остальными. Ранжировщик решает именно эту задачу и ничего больше: он не
    отбирает окончательно, он лишь меняет порядок.

    Почему отдельная команда, а не код здесь: сборщик обязан оставаться
    standalone и работать без ключей и без сети к LLM — иначе его нельзя гонять
    в тестах, а сам скилл перестаёт быть переносимым. Всё, что знает про
    OpenRouter, лежит в обвязке машины и подставляется через cfg["rank_cmd"].

    Протокол. На stdin построчно: "номер<TAB>заголовок<TAB>источник<TAB>дата<TAB>url",
    номера с нуля. Обратно — номера в порядке интересности, по одному в строке.
    Вернуть меньше, чем дали, можно и нужно: это и есть отбор.

    Отобранное встаёт впереди, остальное идёт следом в прежнем порядке по
    свежести. Хвост не выбрасывается намеренно: квота на источник срежет часть
    отобранного, и добирать до max_results будет уже неоткуда.

    Любой сбой — нет команды, ненулевой код, таймаут, пустой ответ — это
    порядок по свежести и предупреждение в stderr. Сводка выходит каждый день,
    и упавший ранжировщик не имеет права стоить дня без поста.
    """
    cmd = cfg.get("rank_cmd")
    if not cmd or len(pool) < 2:
        return pool
    if isinstance(cmd, str):
        cmd = shlex.split(cmd)
    payload = "\n".join(
        "\t".join([str(i), str(t).replace("\t", " "), src, date_s, url])
        for i, (t, src, date_s, url) in enumerate(pool)
    )
    try:
        p = subprocess.run(list(cmd), input=payload, capture_output=True,
                           text=True, timeout=cfg.get("rank_timeout", 120))
    except (OSError, subprocess.SubprocessError) as e:
        warn(f"RANK-DEGRADED ранжировщик не отработал ({e}) — порядок по свежести")
        return pool
    if p.returncode != 0:
        # Только здесь и печатаем чужой stderr — при отказе он единственная
        # улика. На успешном прогоне не дублируем: команда сама отвечает за
        # свою диагностику, а её пересказ ушёл бы в тот же лог вторым экземпляром.
        # Хвост по строкам, а не по символам: обрезка по символам рвёт слова
        # пополам, и в логе оказывается «ь ИИ; [CNews] …».
        tail = "\n".join((p.stderr or "").strip().splitlines()[-12:])
        warn(f"RANK-DEGRADED ранжировщик exit={p.returncode} — порядок по свежести"
             + (f"\n{tail}" if tail else ""))
        return pool

    picked, taken = [], set()
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line.isdigit():
            continue
        i = int(line)
        if 0 <= i < len(pool) and i not in taken:
            taken.add(i)
            picked.append(pool[i])
    if not picked:
        warn("RANK-DEGRADED ранжировщик не вернул ни одного номера — порядок по свежести")
        return pool
    warn(f"RANK-REPORT отобрано {len(picked)} из {len(pool)}")
    return picked + [it for i, it in enumerate(pool) if i not in taken]


def apply_source_quota(items, cfg):
    """Обрезать список по потолку на источник, сохраняя порядок.

    Квота строгая: лишнее выбрасывается, а не сдвигается в хвост. Мягкий
    вариант — «пустить сверх квоты, если добрать больше нечем» — в тихий день
    возвращает ровно ту картину, ради которой квота и заводилась.

    Выброшенное не теряется навсегда: оно не печатается, а печать и есть
    единственное, что помечает прочитанным, — завтра эти новости вернутся.
    """
    caps = cfg.get("max_per_source") or {}
    if not caps:
        return items
    kept, used, dropped = [], {}, {}
    for it in items:
        src = it[1]
        cap = caps.get(src)
        if cap is not None and used.get(src, 0) >= cap:
            dropped[src] = dropped.get(src, 0) + 1
            continue
        used[src] = used.get(src, 0) + 1
        kept.append(it)
    if dropped:
        # «Из очереди», а не «из поста»: квота накладывается на весь
        # отранжированный список, и часть срезанного всё равно не дошла бы до
        # max_results. Число здесь — верхняя оценка, а не потери в посте.
        warn("квота на источник срезала из очереди: "
             + ", ".join(f"{k} -{v}" for k, v in sorted(dropped.items())))
    return kept


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
        for title, link, pub, cats in fetch_rss(
                f.get("url", ""), cfg, f.get("max_items")):
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

    # Три шага между «что нашлось» и «что печатаем», и порядок между ними
    # существенный. Ранжировщик должен видеть весь пул, иначе он выбирает
    # лучшее из верхушки по свежести — то есть решает не ту задачу. Квота
    # накладывается на уже отранжированное: она срезает лишнее у ленты, а не
    # мешает ранжировщику это лишнее увидеть.
    pool = rank_pool(out[:cfg["max_pool"]], cfg)
    pool = apply_source_quota(pool, cfg)

    # Обрезать ДО записи, а не после. Отметить прочитанным можно только то, что
    # уходит читателю: помеченный, но не напечатанный элемент не попадёт уже
    # ни в один прогон — он исчезает молча.
    lines = pool[:cfg["max_results"]]

    # Краул ДО печати и до записи в seen, хотя это и стоит отложенного вывода.
    # Порядок здесь несёт инвариант: напечатано == помечено прочитанным ==
    # отдано редактору. Пока краул шёл после записи, отказ провайдера на
    # середине цикла сжигал остаток выдачи навсегда — статьи были помечены
    # прочитанными ещё до того, как выяснялось, что содержания не будет
    # (потеря 02.09.2026, девять новостей). Теперь статья, до которой краул не
    # дошёл, не печатается и не помечается: назавтра она вернётся.
    chain, delivered, contents = CrawlChain(cfg), [], []
    if args.crawl:
        for item in lines[:cfg["max_crawl"]]:
            text, reached = chain.fetch(item[3], cfg["crawl_max_chars"])
            if not reached:
                break
            delivered.append(item)
            contents.append((item[0], text))
        # Хвост сверх max_crawl идёт редактору без содержания и при живом
        # краулере: его никто и не собирался краулить.
        if len(delivered) == cfg["max_crawl"]:
            delivered.extend(lines[cfg["max_crawl"]:])
        chain.report()
    else:
        delivered = lines

    if not delivered:
        # Ни одной статьи с содержанием — печатать нечего. Молчаливый выход
        # здесь честнее пустых блоков: обёртка увидит отсутствие материала и
        # не станет публиковать, а seen не тронут, и завтра всё вернётся.
        print("(нет свежих новых новостей за окно)")
        return

    for title, _source, _date_s, _url in delivered:
        remember(cur, title, dry_run=args.dry_run, now_ts=now_add)
    if not args.dry_run:
        c.commit()

    print("СТАТЬИ (заголовок | источник | дата | url):")
    for title, source, date_s, url in delivered:
        print(f"{title} | {source} | {date_s} | {url}")
    if contents:
        print("\nСТАТЬИ-СОДЕРЖАНИЕ (заголовок === текст):")
        for title, text in contents:
            print(f"[{title}]\n{text}\n[КОНЕЦ]\n")


if __name__ == "__main__":
    main()