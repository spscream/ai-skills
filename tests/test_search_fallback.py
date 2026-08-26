"""Выбор поискового backend и откат на запасной. Без сети.

Зачем: недельный сборщик съедает 324 из 392 израсходованных кредитов Tavily,
и когда квота кончится, он не должен просто вернуть пустоту — неделя без
сводки дороже, чем поиск через другой сервис.
"""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "skills" / "trending-skills" / "scripts"))
import fetch_trending as ft  # noqa: E402

HIT = [("t", "u", "c")]


@pytest.fixture
def cfg():
    return ft.load_config("/x/none")


def test_default_threshold_leaves_room_for_the_daily_extract(cfg):
    """Порог не нулевой: extract дневного сборщика тратит ту же квоту."""
    assert cfg["tavily_min_credits"] > 0


@pytest.mark.parametrize("left,threshold,expected", [
    (600, 120, "tavily"),      # запаса вдоволь
    (119, 120, "firecrawl"),   # чуть ниже порога
    (0, 120, "firecrawl"),     # кончились
    (5, 0, "tavily"),          # порог отключён — остаёмся на основном
])
def test_backend_choice_follows_remaining_credits(left, threshold, expected):
    """Повторяет ту же ветку, что и main: остаток ниже порога -> запасной."""
    backend = "tavily"
    if left is not None and threshold and left < threshold:
        backend = "firecrawl"
    assert backend == expected


def _stub_all(monkeypatch, tavily=None, firecrawl=None, exa=None, order=None):
    """Заглушить все backend разом.

    Именно все: если оставить хоть один настоящим, тест при неудачном стечении
    уйдёт в сеть, и провал будет означать не то, что проверяли.
    """
    def mk(name, res):
        def fn(*a, **k):
            if order is not None:
                order.append(name)
            return res or []
        return fn
    monkeypatch.setattr(ft, "tavily_search", mk("tavily", tavily))
    monkeypatch.setattr(ft, "firecrawl_search", mk("firecrawl", firecrawl))
    monkeypatch.setattr(ft, "exa_search", mk("exa", exa))


def test_falls_back_when_primary_returns_nothing(cfg, monkeypatch):
    _stub_all(monkeypatch, tavily=[], firecrawl=HIT)
    assert ft.search("q", None, 7, cfg, "tavily") == HIT


def test_walks_the_chain_until_something_answers(cfg, monkeypatch):
    """Второй запасной берётся, когда и первый пуст."""
    order = []
    _stub_all(monkeypatch, tavily=[], firecrawl=[], exa=HIT, order=order)
    assert ft.search("q", None, 7, cfg, "tavily") == HIT
    assert order == ["tavily", "firecrawl", "exa"]


def test_does_not_call_the_spare_when_primary_answers(cfg, monkeypatch):
    order = []
    _stub_all(monkeypatch, tavily=HIT, firecrawl=HIT, exa=HIT, order=order)
    assert ft.search("q", None, 7, cfg, "tavily") == HIT
    assert order == ["tavily"], "запасные не должны вызываться впустую"


def test_chosen_backend_goes_first_and_is_not_repeated(cfg, monkeypatch):
    """Выбранный backend идёт первым, а в хвосте не дублируется."""
    order = []
    _stub_all(monkeypatch, order=order)
    ft.search("q", None, 7, cfg, "firecrawl")
    assert order == ["firecrawl", "exa"], "firecrawl не должен вызываться дважды"


def test_unknown_backend_name_is_skipped_not_fatal(cfg, monkeypatch):
    order = []
    _stub_all(monkeypatch, exa=HIT, order=order)
    cfg["search_fallbacks"] = ["нет-такого", "exa"]
    assert ft.search("q", None, 7, cfg, "tavily") == HIT
    assert order == ["tavily", "exa"]


def test_all_empty_returns_empty_not_an_error(cfg, monkeypatch):
    _stub_all(monkeypatch)
    assert ft.search("q", None, 7, cfg, "tavily") == []


@pytest.mark.parametrize("days,expected", [(7, "qdr:w"), (30, "qdr:m"), (400, "qdr:y")])
def test_freshness_maps_to_google_style_buckets(days, expected, cfg, monkeypatch):
    """У Firecrawl нет поля days — свежесть выражается оператором tbs."""
    seen = {}

    class _R:
        def __init__(self, payload): self._p = payload
        def read(self): return b'{"data":[]}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        import json as _j
        seen.update(_j.loads(req.data))
        return _R(None)

    monkeypatch.setenv("FIRECRAWL_API_KEY", "x")
    monkeypatch.setattr(ft.urllib.request, "urlopen", fake_urlopen)
    ft.firecrawl_search("q", None, days, cfg)
    assert seen["tbs"] == expected


def test_domain_becomes_a_site_operator(cfg, monkeypatch):
    seen = {}

    class _R:
        def read(self): return b'{"data":[]}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        import json as _j
        seen.update(_j.loads(req.data))
        return _R()

    monkeypatch.setenv("FIRECRAWL_API_KEY", "x")
    monkeypatch.setattr(ft.urllib.request, "urlopen", fake_urlopen)
    ft.firecrawl_search("skills", "github.com", 7, cfg)
    assert seen["query"] == "skills site:github.com"
