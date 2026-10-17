"""Offline defaults: no LLM, no HTTP cache, no real keys."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACER_DISABLE_LLM", "1")
    for k in ("DART_API_KEY", "SEC_USER_AGENT", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("tracer.config.HTTP_CACHE", False)
    monkeypatch.setattr("tracer.config.LLM_CACHE", False)
    monkeypatch.setattr("tracer.config.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("tracer.config.CH_INDEX_PATH", tmp_path / "ch" / "missing.sqlite")
