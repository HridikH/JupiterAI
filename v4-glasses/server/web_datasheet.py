"""Web datasheet lookup for parts NOT in the local catalog.

The offline guarantee covers CACHED catalog parts. When the OCR reads a marking
that the deterministic resolver cannot confidently match to the local catalog,
this module searches the web for that EXACT part number's datasheet PDF.

HARD RULES (so this does not weaken the safety invariant):
  * This is a RETRIEVAL aid, not identification. It never produces or confirms a
    part number; it takes a string (the OCR read, or a user-typed/confirmed part
    number) and returns candidate datasheet links.
  * It is never called inside the identification decision. The resolver still
    accepts/offers candidates ONLY from the curated local catalog.
  * It only runs on demand (a UI/voice action), never silently in the hot path.
  * A result is marked `exact` only when the normalized part string appears in the
    result URL or title, so "plausible" web hits are not presented as the answer.

Search uses a local SearXNG instance if configured, else the DuckDuckGo HTML
endpoint (no API key), mirroring the parent repo's web_search.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import requests

from . import config
from .logging_setup import get

log = get("web_datasheet")

_HEADERS = {"User-Agent": "Mozilla/5.0 (glasses-datasheet-lookup)"}

# Manufacturer / distributor domains whose PDFs we trust more (ranking only).
_PREFERRED_DOMAINS = (
    "ti.com", "analog.com", "nxp.com", "st.com", "microchip.com", "onsemi.com",
    "infineon.com", "diodes.com", "renesas.com", "rohm.com", "vishay.com",
    "toshiba.semicon-storage.com", "mouser.com", "digikey.com", "alldatasheet.com",
)


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _raw_search(query: str, max_results: int) -> list[dict]:
    # Try SearXNG JSON first (if someone runs one locally), else DuckDuckGo HTML.
    try:
        r = requests.get(f"{config.SEARXNG_URL}/search",
                         params={"q": query, "format": "json"},
                         timeout=config.WEB_SEARCH_TIMEOUT_S, headers=_HEADERS)
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            return [{"title": x.get("title", ""), "snippet": x.get("content", ""),
                     "url": x.get("url", "")} for x in results[:max_results * 3]]
    except Exception:
        pass
    try:
        from bs4 import BeautifulSoup
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                          timeout=config.WEB_SEARCH_TIMEOUT_S, headers=_HEADERS)
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for res in soup.select(".result")[:max_results * 3]:
            a = res.select_one(".result__a")
            sn = res.select_one(".result__snippet")
            if a:
                out.append({"title": a.get_text(" ", strip=True),
                            "snippet": sn.get_text(" ", strip=True) if sn else "",
                            "url": a.get("href", "")})
        return out
    except Exception as e:
        log.warning(f"web search failed: {e}")
        return []


def _rank(results: list[dict], part: str, max_results: int) -> list[dict]:
    npart = _normalize(part)
    scored = []
    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        host = urlparse(url).netloc.lower()
        is_pdf = url.lower().split("?")[0].endswith(".pdf")
        npart_in = bool(npart) and (npart in _normalize(url) or npart in _normalize(title))
        pref = any(host.endswith(d) for d in _PREFERRED_DOMAINS)
        score = 0
        if is_pdf:
            score += 3
        if npart_in:
            score += 4                 # exact part string present -> strong
        if pref:
            score += 2
        if "datasheet" in (title + " " + url).lower():
            score += 1
        scored.append((score, {
            "title": title, "url": url, "snippet": r.get("snippet", ""),
            "is_pdf": is_pdf, "domain": host,
            "exact": npart_in,          # True only if the exact part string matches
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    # de-dup by url
    seen, out = set(), []
    for _, item in scored:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        out.append(item)
        if len(out) >= max_results:
            break
    return out


def search_datasheet(part: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web for `part`'s datasheet. Returns a structured result.

    {available, part, query, results:[{title,url,snippet,is_pdf,domain,exact}],
     note}. `available` is False when web search is disabled or nothing is found.
    """
    part = (part or "").strip()
    if not config.WEB_SEARCH_ENABLED:
        return {"available": False, "part": part, "reason": "web search disabled"}
    if len(_normalize(part)) < 3:
        return {"available": False, "part": part, "reason": "part string too short to search"}

    query = f"{part} datasheet filetype:pdf"
    raw = _raw_search(query, max_results)
    if not raw:  # retry without the filetype filter (some engines ignore it)
        raw = _raw_search(f"{part} datasheet", max_results)
    ranked = _rank(raw, part, max_results)
    log.info(f"web datasheet search '{part}': {len(ranked)} results")
    return {
        "available": bool(ranked),
        "part": part,
        "query": query,
        "results": ranked,
        "exact_count": sum(1 for r in ranked if r["exact"]),
        "note": "Web results are unverified — confirm the manufacturer and revision "
                "before trusting any value. Not part of identification.",
    }
