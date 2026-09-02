"""Цепочка краула и её поведение при отказе провайдера. Без сети.

Зачем: 02.09.2026 у Tavily кончился баланс на середине цикла. Отказ ловился
одним `except` и превращался в пустую строку, статьи к тому моменту уже были
помечены прочитанными — девять новостей исчезли навсегда, а пост вышел
вчетверо короче обычного. Ни в выводе, ни в коде не было ничего, что отличало
бы это от честного «сегодня новостей мало».

Здесь проверяется ровно то, что тогда сломалось: отказ провайдера уводит на
запасного, выбывший больше не опрашивается, а статья, до которой краул не
дошёл, не печатается и не помечается прочитанной.
"""
import io
import contextlib
import sqlite3
import sys
from email.utils import formatdate
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "skills" / "ai-daily" / "scripts"))
import fetch_news as fn  # noqa: E402

ARTICLE = "Достаточно длинный текст статьи. " * 20


@pytest.fixture
def cfg():
    return fn.load_config("/x/none")


def _stub(monkeypatch, **behaviour):
    """Подменить все три крауллера разом.

    Именно все: оставленный настоящим уйдёт в сеть, и провал теста будет
    означать не то, что проверяли. Значение — либо строка, либо исключение,
    либо список по вызовам.
    """
    calls = []

    def mk(name, spec):
        def crawl(url, cfg, max_chars):
            calls.append((name, url))
            value = spec.pop(0) if isinstance(spec, list) else spec
            if isinstance(value, Exception):
                raise value
            return value
        return crawl

    for name in fn.CRAWLERS:
        monkeypatch.setattr(fn, f"crawl_{name}", mk(name, behaviour.get(name, "")))
    return calls


def test_refusal_moves_to_the_next_provider(cfg, monkeypatch):
    calls = _stub(monkeypatch, tavily=fn.CrawlerRefused("HTTP 432"), exa=ARTICLE)
    text, reached = fn.CrawlChain(cfg).fetch("http://example.invalid/1", 500)
    assert reached and text
    assert [c[0] for c in calls] == ["tavily", "exa"]


def test_refused_provider_is_not_asked_again(cfg, monkeypatch):
    """Главная экономия: тринадцать обречённых запросов после первого отказа."""
    calls = _stub(monkeypatch, tavily=fn.CrawlerRefused("HTTP 432"), exa=ARTICLE)
    chain = fn.CrawlChain(cfg)
    for i in range(3):
        chain.fetch(f"http://example.invalid/{i}", 500)
    assert [c[0] for c in calls].count("tavily") == 1


def test_page_failure_does_not_retire_the_provider(cfg, monkeypatch):
    """Битая ссылка — не повод хоронить сервис: следующий URL он возьмёт."""
    calls = _stub(monkeypatch, tavily=["", ARTICLE], exa=ARTICLE)
    chain = fn.CrawlChain(cfg)
    chain.fetch("http://example.invalid/1", 500)
    chain.fetch("http://example.invalid/2", 500)
    assert [c[0] for c in calls].count("tavily") == 2
    assert "tavily" not in chain.dead


def test_all_refused_means_the_article_was_never_attempted(cfg, monkeypatch):
    """reached=False — статью нельзя ни печатать, ни помечать прочитанной.

    Отдельный случай от «страница не отдалась»: там виновата страница, и
    пометить её надо, иначе платный сайт вернётся в выдачу завтра и послезавтра.
    """
    _stub(monkeypatch, **{n: fn.CrawlerRefused("HTTP 401") for n in fn.CRAWLERS})
    text, reached = fn.CrawlChain(cfg).fetch("http://example.invalid/1", 500)
    assert (text, reached) == ("", False)


def test_page_nobody_could_take_is_still_delivered(cfg, monkeypatch):
    _stub(monkeypatch, **{n: "" for n in fn.CRAWLERS})
    text, reached = fn.CrawlChain(cfg).fetch("http://example.invalid/1", 500)
    assert (text, reached) == ("", True)


def test_antibot_stub_is_not_content(cfg, monkeypatch):
    """Firecrawl отдаёт капчу Cloudflare с кодом 200 и success=true.

    По коду возврата её не отличить, только по тексту — иначе редактор получит
    сорок килобайт «Checking your Browser…» вместо статьи.
    """
    calls = _stub(monkeypatch, tavily="Checking your Browser… " * 50, exa=ARTICLE)
    text, _ = fn.CrawlChain(cfg).fetch("http://example.invalid/1", 5000)
    assert text == ARTICLE[:5000]
    assert [c[0] for c in calls] == ["tavily", "exa"]


def test_report_marks_degradation_only_on_refusal(cfg, monkeypatch, capsys):
    """Маркер CRAWL-DEGRADED — триггер личного сообщения владельцу.

    Неудачи отдельных страниц его давать не должны: сообщения приходили бы
    каждый день и перестали бы читаться.
    """
    _stub(monkeypatch, tavily="")
    chain = fn.CrawlChain(cfg)
    chain.fetch("http://example.invalid/1", 500)
    chain.report()
    assert "CRAWL-DEGRADED" not in capsys.readouterr().err

    _stub(monkeypatch, tavily=fn.CrawlerRefused("HTTP 432"), exa=ARTICLE)
    chain = fn.CrawlChain(cfg)
    chain.fetch("http://example.invalid/1", 500)
    chain.report()
    assert "CRAWL-DEGRADED" in capsys.readouterr().err


def _cfg_file(tmp_path, db_path):
    (tmp_path / "c.yaml").write_text(
        f"db_path: {db_path}\n"
        "max_hours: 999999\n"
        "max_results: 5\n"
        "max_crawl: 5\n"
        "feeds:\n"
        "  - name: FAKE\n"
        "    url: http://example.invalid/feed\n"
        'full_ai_feeds: ["FAKE"]\n'
    )
    return str(tmp_path / "c.yaml")


def _run_main(tmp_path, db, monkeypatch, titles):
    stamp = formatdate(localtime=False)
    monkeypatch.setattr(
        fn, "fetch_rss",
        lambda url, cfg, limit=None: [(t, f"http://example.invalid/{i}", stamp, [])
                                      for i, t in enumerate(titles)],
        raising=False,
    )
    monkeypatch.setattr(sys, "argv", ["x", "--config", _cfg_file(tmp_path, db)])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn.main()
    return buf.getvalue()


TITLES = [
    "OpenAI ships a Sora update",
    "Anthropic closes a funding round",
    "Mistral opens a lab in Paris",
    "A robotics firm wins a defence contract",
    "Chip export rules tighten again",
]


def test_midrun_refusal_leaves_the_tail_unseen(tmp_path, monkeypatch):
    """Та самая потеря 02.09.2026, воспроизведённая целиком.

    Краул отдаёт две статьи и умирает. В выдачу должны попасть ровно эти две,
    а остальные три обязаны остаться непрочитанными и вернуться завтра.
    """
    db = tmp_path / "seen.db"
    _stub(monkeypatch,
          tavily=[ARTICLE, ARTICLE] + [fn.CrawlerRefused("HTTP 432")] * 5,
          exa=fn.CrawlerRefused("HTTP 402"),
          firecrawl=fn.CrawlerRefused("HTTP 402"))
    out = _run_main(tmp_path, db, monkeypatch, TITLES)

    assert out.count("[КОНЕЦ]") == 2
    con = sqlite3.connect(str(db))
    seen = {r[0] for r in con.execute("SELECT title FROM seen")}
    con.close()
    assert len(seen) == 2
    assert set(TITLES) - seen == set(TITLES[2:])


def test_healthy_run_marks_everything(tmp_path, monkeypatch):
    """Контроль: без отказов поведение прежнее — всё напечатано и помечено."""
    db = tmp_path / "seen.db"
    _stub(monkeypatch, tavily=ARTICLE)
    out = _run_main(tmp_path, db, monkeypatch, TITLES)

    assert out.count("[КОНЕЦ]") == len(TITLES)
    con = sqlite3.connect(str(db))
    assert con.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == len(TITLES)
    con.close()
