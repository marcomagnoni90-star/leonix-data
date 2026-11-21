"""Aggregated news tone from titles only.

Full article text is never stored. GDELT 2.0 is tried first for a tone
timeline. If it does not answer, official RSS feeds supply headline titles
and links, scored with the small lexicon in this file. That lexicon is a
transparent list of finance words, not a copy of the Loughran-McDonald file.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

POSITIVE = {
    "growth", "expand", "expansion", "gain", "gains", "rise", "rises", "rising", "rally",
    "stable", "stability", "ease", "easing", "cool", "cooling", "strong", "strength",
    "surplus", "upgrade", "recovery", "crescita", "rialzo", "stabile", "ripresa",
}
NEGATIVE = {
    "recession", "inflation", "war", "crisis", "crash", "default", "downgrade", "layoff",
    "unemployment", "hike", "tightening", "sanction", "invasion", "slowdown", "bear",
    "recessione", "inflazione", "guerra", "crisi", "rialzo dei tassi", "stagnazione",
}

FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
]


def _get(url: str, timeout: int = 25) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "LeonixResearch/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def score_title(title: str) -> int:
    text = title.lower()
    score = 0
    for word in POSITIVE:
        if word in text:
            score += 1
    for word in NEGATIVE:
        if word in text:
            score -= 1
    return score


def gdelt_tone() -> dict | None:
    query = "inflation OR recession OR \"federal reserve\" OR \"interest rates\" OR credit OR war"
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?"
        + urllib.parse.urlencode({
            "query": query,
            "mode": "timelinetone",
            "format": "json",
            "timespan": "3m",
        })
    )
    try:
        payload = json.loads(_get(url).decode("utf-8", errors="replace"))
    except Exception:
        return None
    timeline = payload.get("timeline") or payload.get("timeline") or []
    # GDELT returns {"timeline":[{"data":[{"date","value"}]}]} or a list.
    points = []
    if isinstance(timeline, list):
        for series in timeline:
            for row in series.get("data", []) if isinstance(series, dict) else []:
                if "value" in row:
                    points.append({"date": row.get("date"), "value": row.get("value")})
    if len(points) < 2:
        return None
    last = points[-1]
    month_ago = points[max(0, len(points) - 5)]
    return {
        "source": "GDELT 2.0 DOC timelinetone",
        "score": last.get("value"),
        "previous": month_ago.get("value"),
        "points": points[-8:],
    }


def rss_headlines(limit: int = 8) -> list[dict]:
    rows = []
    for source, url in FEEDS:
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link:
                continue
            rows.append({
                "title": title,
                "url": link,
                "source": source,
                "date": (item.findtext("pubDate") or "").strip(),
                "score": score_title(title),
            })
    return rows[:limit]


def sentiment_snapshot() -> dict:
    """Scores and titles. No article bodies."""
    tone = gdelt_tone()
    headlines = rss_headlines(5)
    lexicon_score = None
    if headlines:
        lexicon_score = sum(item["score"] for item in headlines) / len(headlines)
    score = tone["score"] if tone and tone.get("score") is not None else lexicon_score
    previous = tone.get("previous") if tone else None
    trend = None
    if score is not None and previous is not None:
        if score > previous + 0.2:
            trend = "in miglioramento"
        elif score < previous - 0.2:
            trend = "in peggioramento"
        else:
            trend = "stabile"
    return {
        "asOf": datetime.now(timezone.utc).date().isoformat(),
        "score": score,
        "previous": previous,
        "trend": trend,
        "weightCap": 0.05,
        "appliedToAllocation": False,
        "note": (
            "Punteggio informativo sui titoli raccolti. GDELT non ha restituito una serie storica in questa corsa, quindi il tilt non è stato misurato e non entra nei pesi."
            if not (tone and tone.get("score") is not None)
            else "Tono medio GDELT sulle query macro. Il peso massimo previsto è 5 punti di azionario. In questa corsa non è stato acceso, perché manca un confronto fuori campione del tilt."
        ),
        "gdelt": tone is not None,
        "headlines": [{key: item[key] for key in ("title", "url", "source", "date")} for item in headlines],
        "lexiconScore": lexicon_score,
    }
