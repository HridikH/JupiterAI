"""Tool layer — all free / local, no LLM-provider APIs.

- web_search: SearXNG (self-hosted, JSON API) with DuckDuckGo HTML-endpoint
  fallback when SearXNG isn't running. No keys, no cost.
- get_weather: Open-Meteo (geocoding + current conditions). No key.
- get_stock: Stooq CSV endpoint. No key, no library. (yfinance works too but
  drags in pandas — dead weight at this RAM tier.)

Latency-tier note: weather and stock results are verbalized by TEMPLATE, not
by any model — a structured API result doesn't need a 7B model to read a
number aloud. web_search results DO go to the main model for synthesis,
because summarizing ranked snippets is a real language task. This split is
deliberate: it keeps "what's the weather" at router+HTTP cost (~0.5s total)
while keeping search answers smart.
"""
from __future__ import annotations

import json
import re

import requests

from . import config

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh) JARVIS-local/1.0"}


# ---- implementations ---------------------------------------------------------
def web_search(query: str, max_results: int = 4) -> list[dict]:
    try:
        r = requests.get(
            f"{config.SEARXNG_URL}/search",
            params={"q": query, "format": "json"},
            timeout=config.TOOL_TIMEOUT_S, headers=HEADERS,
        )
        r.raise_for_status()
        results = r.json().get("results", [])[:max_results]
        if results:
            return [{"title": x.get("title", ""), "snippet": x.get("content", ""),
                     "url": x.get("url", "")} for x in results]
    except Exception:
        pass  # SearXNG down -> DDG fallback
    from bs4 import BeautifulSoup
    r = requests.post(
        "https://html.duckduckgo.com/html/", data={"q": query},
        timeout=config.TOOL_TIMEOUT_S, headers=HEADERS,
    )
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result")[:max_results]:
        a = res.select_one(".result__a")
        sn = res.select_one(".result__snippet")
        if a:
            out.append({"title": a.get_text(" ", strip=True),
                        "snippet": sn.get_text(" ", strip=True) if sn else "",
                        "url": a.get("href", "")})
    return out


def get_weather(location: str = "") -> dict:
    location = location or config.DEFAULT_LOCATION
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": location, "count": 1}, timeout=config.TOOL_TIMEOUT_S,
    ).json()
    if not geo.get("results"):
        return {"error": f"couldn't find '{location}'"}
    g = geo["results"][0]
    wx = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": g["latitude"], "longitude": g["longitude"],
            "current": "temperature_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
            "timezone": "auto", "forecast_days": 1,
        }, timeout=config.TOOL_TIMEOUT_S,
    ).json()
    cur, day = wx.get("current", {}), wx.get("daily", {})
    return {
        "location": g["name"],
        "temp_f": cur.get("temperature_2m"),
        "feels_like_f": cur.get("apparent_temperature"),
        "condition": _wmo(cur.get("weather_code")),
        "wind_mph": cur.get("wind_speed_10m"),
        "high_f": (day.get("temperature_2m_max") or [None])[0],
        "low_f": (day.get("temperature_2m_min") or [None])[0],
        "rain_chance_pct": (day.get("precipitation_probability_max") or [None])[0],
    }


def _stock_stooq(ticker: str) -> dict:
    sym = ticker.lower().strip()
    if "." not in sym:
        sym += ".us"
    r = requests.get(
        "https://stooq.com/q/l/",
        params={"s": sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        timeout=config.TOOL_TIMEOUT_S, headers=HEADERS,
    )
    lines = r.text.strip().splitlines()
    if len(lines) < 2 or "N/D" in lines[1]:
        return {"error": f"no quote for '{ticker}'"}
    row = dict(zip(lines[0].split(","), lines[1].split(",")))
    close, open_ = float(row["Close"]), float(row["Open"])
    return {
        "ticker": row["Symbol"].upper().removesuffix(".US"),
        "price": close,
        "change_pct": round((close - open_) / open_ * 100, 2),
        "date": row["Date"],
    }


def _stock_yahoo(ticker: str) -> dict:
    """Fallback: Yahoo v8 chart API — free, no key, needs a browser-ish UA."""
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper().strip()}",
        params={"interval": "1d", "range": "1d"},
        timeout=config.TOOL_TIMEOUT_S,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta["regularMarketPrice"]
    prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
    return {
        "ticker": meta["symbol"],
        "price": price,
        "change_pct": round((price - prev) / prev * 100, 2) if prev else 0.0,
        "date": "",
    }


def get_stock(ticker: str) -> dict:
    try:
        quote = _stock_stooq(ticker)
        if "error" not in quote:
            return quote
    except Exception:
        pass  # Stooq down/rate-limited -> Yahoo fallback
    try:
        return _stock_yahoo(ticker)
    except Exception:
        return {"error": f"no quote for '{ticker}'"}


# ---- template verbalization (no LLM) ------------------------------------------
def speak_weather(w: dict) -> str:
    if "error" in w:
        return f"Sorry — {w['error']}."
    s = f"{w['location']}: {round(w['temp_f'])} degrees and {w['condition']}, high of {round(w['high_f'])}."
    if (w.get("rain_chance_pct") or 0) >= 30:
        s += f" {w['rain_chance_pct']} percent chance of rain."
    return s


def speak_stock(s: dict) -> str:
    if "error" in s:
        return f"Sorry — {s['error']}."
    direction = "up" if s["change_pct"] >= 0 else "down"
    return f"{s['ticker']} is at {s['price']:.2f}, {direction} {abs(s['change_pct'])} percent today."


# ---- Qwen tool-calling plumbing ------------------------------------------------
# Schemas passed to tokenizer.apply_chat_template(tools=...) — Qwen2.5 renders
# these Hermes-style and emits <tool_call>{...}</tool_call> blocks.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for current or unknown information.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "Current weather and today's forecast for a location.",
        "parameters": {"type": "object", "properties": {
            "location": {"type": "string"}}, "required": ["location"]}}},
    {"type": "function", "function": {
        "name": "get_stock",
        "description": "Latest stock quote for a ticker symbol.",
        "parameters": {"type": "object", "properties": {
            "ticker": {"type": "string"}}, "required": ["ticker"]}}},
]

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_FUNCS = {"web_search": web_search, "get_weather": get_weather, "get_stock": get_stock}


def parse_tool_calls(text: str) -> list[dict]:
    calls = []
    for m in _TOOL_CALL_RE.finditer(text):
        try:
            calls.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    return calls


def execute(name: str, args: dict):
    fn = _FUNCS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**args)
    except Exception as e:  # tools must never crash the voice loop
        return {"error": f"{name} failed: {e}"}


def _wmo(code) -> str:
    table = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
             45: "foggy", 48: "foggy", 51: "drizzling", 53: "drizzling",
             55: "drizzling", 61: "raining lightly", 63: "raining",
             65: "raining heavily", 71: "snowing lightly", 73: "snowing",
             75: "snowing heavily", 80: "showers", 81: "showers",
             82: "heavy showers", 95: "thunderstorms", 96: "thunderstorms",
             99: "thunderstorms"}
    return table.get(code, "unknown conditions")
