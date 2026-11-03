"""Web datasheet lookup tests (parts not in the catalog).

Network is mocked, so these are deterministic and offline. They verify ranking,
the `exact` flag (normalized part string must appear in url/title), the disabled
and too-short guards, and that this path never fabricates a part number.
"""
from __future__ import annotations

import pytest

from server import web_datasheet as wd
from server import config


FAKE_RESULTS = [
    {"title": "Random blog about timers", "snippet": "", "url": "https://example.com/blog"},
    {"title": "STM32F407 datasheet", "snippet": "ST product PDF",
     "url": "https://www.st.com/resource/en/datasheet/stm32f407vg.pdf"},
    {"title": "STM32F407VG - alldatasheet", "snippet": "",
     "url": "https://www.alldatasheet.com/view.jsp?Searchword=STM32F407VG"},
    {"title": "stm32f407 pinout forum", "snippet": "", "url": "https://forum.example.org/t/123"},
]


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH_ENABLED", True)


def test_exact_pdf_ranked_first(monkeypatch):
    monkeypatch.setattr(wd, "_raw_search", lambda q, n: FAKE_RESULTS)
    out = wd.search_datasheet("STM32F407VG")
    assert out["available"] is True
    top = out["results"][0]
    assert top["url"].endswith(".pdf")
    assert top["exact"] is True            # exact part string present in the URL
    assert top["domain"].endswith("st.com")


def test_exact_flag_false_when_part_absent(monkeypatch):
    monkeypatch.setattr(wd, "_raw_search", lambda q, n: [
        {"title": "some timer datasheet", "snippet": "", "url": "https://x.com/misc.pdf"}])
    out = wd.search_datasheet("STM32F407VG")
    assert out["results"][0]["exact"] is False
    assert out["exact_count"] == 0


def test_disabled_returns_unavailable(monkeypatch):
    monkeypatch.setattr(config, "WEB_SEARCH_ENABLED", False)
    out = wd.search_datasheet("NE555P")
    assert out["available"] is False and "disabled" in out["reason"]


def test_too_short_is_guarded(monkeypatch):
    monkeypatch.setattr(wd, "_raw_search", lambda q, n: FAKE_RESULTS)
    out = wd.search_datasheet("55")
    assert out["available"] is False


def test_no_results(monkeypatch):
    monkeypatch.setattr(wd, "_raw_search", lambda q, n: [])
    out = wd.search_datasheet("ZZZ9999NONEXISTENT")
    assert out["available"] is False


def test_query_uses_exact_part(monkeypatch):
    captured = {}
    def fake(q, n):
        captured["q"] = q
        return FAKE_RESULTS
    monkeypatch.setattr(wd, "_raw_search", fake)
    wd.search_datasheet("STM32F407VG")
    assert "STM32F407VG" in captured["q"] and "datasheet" in captured["q"]
