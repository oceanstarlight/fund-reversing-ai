"""Streamlit app smoke test with AppTest (headless, offline)."""

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.test_trace_gleif import CHILD, _http  # noqa: E402
from tracer import orchestrator  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")


@pytest.fixture
def offline_tracer(monkeypatch):
    graph = orchestrator.Tracer(_http()).trace(CHILD)
    monkeypatch.setattr(orchestrator.Tracer, "trace", lambda self, q: graph)
    return graph


def test_app_renders_and_traces(offline_tracer):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("지원 범위" in i.value for i in at.info)
    next(b for b in at.button if b.label == "A 카카오").click().run()
    assert not at.exception
    assert any("소유 그래프" in h.value for h in at.subheader)
    assert len(at.metric) == 4
