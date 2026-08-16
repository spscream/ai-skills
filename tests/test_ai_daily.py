"""Unit tests for ai-daily pure functions (load_config, is_ai). No network."""
import sys
from pathlib import Path

import pytest

# path: tests/ -> repo root -> skills/ai-daily/scripts
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "skills" / "ai-daily" / "scripts"))
import fetch_news  # noqa: E402


def test_load_config_defaults(tmp_path):
    cfg = fetch_news.load_config(str(tmp_path / "nonexistent.yaml"))
    assert "feeds" in cfg
    assert cfg["max_hours"] == 48
    assert cfg["tavily_api_key_env"] == "TAVILY_API_KEY"


def test_load_config_merges_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("max_hours: 6\nfeeds: []\n")
    cfg = fetch_news.load_config(str(p))
    assert cfg["max_hours"] == 6          # overridden
    assert cfg["feeds"] == []             # overridden
    assert cfg["tavily_api_key_env"] == "TAVILY_API_KEY"  # default kept


def test_is_ai_full_feed_always_true():
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai("Random non-AI headline", "TechCrunch AI", cfg) is True


def test_is_ai_keyword_match():
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai("New LLM model released", "BBC Tech", cfg) is True
    assert fetch_news.is_ai("Российские банки внедряют нейросети", "Ведомости", cfg) is True


def test_is_ai_non_relevant_false():
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai("Football transfer news today", "BBC Sport", cfg) is False