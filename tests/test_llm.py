"""Offline build_llm tests (SPEC §8 config surface; zero network — constructing
a ChatOpenAI never calls the endpoint, so `make test` stays keyless)."""

from text2sql.config import Settings
from text2sql.llm import build_llm


def _settings(**overrides) -> Settings:
    return Settings(zhipuai_api_key="test-key", **overrides)


def test_default_timeout_is_300s():
    assert Settings().llm_timeout_s == 300.0


def test_build_llm_carries_configured_timeout():
    assert build_llm(_settings()).request_timeout == 300.0


def test_build_llm_reflects_custom_timeout():
    assert build_llm(_settings(llm_timeout_s=1.5)).request_timeout == 1.5
