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

# --- is_ai must match whole words, not substrings -----------------------

@pytest.mark.parametrize("headline", [
    "Ukraine peace talks continue",        # ai inside Ukr-ai-ne
    "Email marketing tips for 2026",       # ai inside Em-ai-l
    "Supply chain disruption in Asia",     # ai inside ch-ai-n
    "Kremlin said nothing new",            # ai inside s-ai-d
    "Air travel demand rebounds",          # ai at the head of Air
    "Aid convoy reaches the border",       # ai at the head of Aid
    "Ремоделирование зданий подорожало",   # модел inside ре-модел-ирование
    "Football transfer news today",
])
def test_is_ai_rejects_substring_lookalikes(headline):
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai(headline, "BBC Tech", cfg) is False


@pytest.mark.parametrize("headline", [
    "New LLM model released",
    "OpenAI ships an update",
    "GPT-5 tops the benchmark",
    "Российские банки внедряют нейросети",
    "Модель научили считать",
    "ИИ в медицине: первые итоги",
])
def test_is_ai_accepts_real_mentions(headline):
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai(headline, "BBC Tech", cfg) is True


def test_is_ai_honours_custom_keywords(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("ai_acronyms: []\nai_keywords: ['квантов']\n")
    cfg = fetch_news.load_config(str(p))
    assert fetch_news.is_ai("Квантовый компьютер собран", "BBC Tech", cfg) is True
    assert fetch_news.is_ai("New LLM model released", "BBC Tech", cfg) is False


# --- vendors and model names ------------------------------------------

@pytest.mark.parametrize("headline", [
    "Meta launches Llama 4",
    "Cohere raises a round",
    "Gemini 3 tops the benchmarks",
    "OpenAI updates Sora",
    "Grok gets a voice mode",
    "Nvidia ships new GPUs",
    "Mistral releases open weights",
    "Hugging Face hits 2M models",
    "Perplexity launches search",
    "ElevenLabs clones voices",
    "Stable Diffusion 4 released",
    "Qwen tops the leaderboard",
    "xAI announces funding",
    "Databricks buys a startup",
    "Groq speeds up inference",
    "GigaChat обновили",
    "Кандинский рисует лучше",
    "Инференс подешевел вдвое",
])
def test_is_ai_knows_the_vendors_and_models(headline):
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai(headline, "BBC Tech", cfg) is True


@pytest.mark.parametrize("headline", [
    # A vendor name that is also the start of an ordinary word must not leak.
    "Metal prices climb in Asia",
    "Metadata standard published",
    "Coherent light source built",
    "Policy coherence review published",
    # These are why flux and runway are deliberately absent from the lists:
    # matching them as whole words would still fire here.
    "Runway repairs at Heathrow",
    "Magnetic flux measured in the lab",
])
def test_is_ai_does_not_leak_on_vendor_lookalikes(headline):
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai(headline, "BBC Tech", cfg) is False


# --- the English half of the vocabulary --------------------------------

@pytest.mark.parametrize("headline", [
    "Robot horse steals the spotlight",     # "robotics" alone used to miss this
    "Robots on the factory floor",
    "Machine learning cuts drug trial time",
    "Generative video tool launches",
    "Chatbot passes the bar exam",
    "Deepfake scam hits a bank",
    "Inference costs fall by half",
    "Neural network spots tumours",
    "Self-driving taxis expand to Austin",
    "Driverless trucks hit the highway",
    "Autonomous vehicle rules tighten",
    "A new foundation model for biology",
    "Large language model tops the leaderboard",
    "Drone delivery starts in Texas",
])
def test_is_ai_covers_the_concepts_in_english_too(headline):
    """The list held these concepts in Russian only, and brand names in English.

    BBC Tech is an English feed with no categories, so the keyword filter is the
    only thing standing in front of it -- and half the vocabulary did not apply.
    """
    cfg = fetch_news.load_config("/x/none")
    assert fetch_news.is_ai(headline, "BBC Tech", cfg) is True
