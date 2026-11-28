"""One daily market narrative for the Leonix snapshot.

The script collects prices, the model reading, news tone, inflation and a
handful of GDELT headlines, then asks Mistral for a JSON story in Italian
and English. Every number and every link in that story must come from the
inputs. If the check fails twice, or there is no API key, the snapshot
keeps a plain template built from the same inputs.

The key is read from MISTRAL_API_KEY and is never printed.
"""

from __future__ import annotations

import html
import io
import json
import os
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

MISTRAL_URL = os.environ.get("MISTRAL_API_BASE", "https://api.mistral.ai/v1/chat/completions")
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")

MARKETS = (
    ("spx", "S&P 500", "%5EGSPC", "pct"),
    ("nasdaq", "Nasdaq", "%5EIXIC", "pct"),
    ("stoxx", "Euro Stoxx 50", "%5ESTOXX50E", "pct"),
    ("dax", "DAX", "%5EGDAXI", "pct"),
    ("ftse", "FTSE 100", "%5EFTSE", "pct"),
    ("smi", "SMI", "%5ESSMI", "pct"),
    ("nikkei", "Nikkei", "%5EN225", "pct"),
    ("shanghai", "Shanghai", "000001.SS", "pct"),
    ("hsi", "Hang Seng", "%5EHSI", "pct"),
    ("us10y", "Tassi USA a 10 anni", "%5ETNX", "yield10"),
    ("eurusd", "Euro/dollaro", "EURUSD=X", "level"),
    ("usdchf", "Dollaro/franco", "USDCHF=X", "level"),
    ("dxy", "Indice dollaro", "DX-Y.NYB", "level"),
    ("gold", "Oro", "GC=F", "level"),
    ("brent", "Brent", "BZ=F", "level"),
    ("vix", "VIX", "%5EVIX", "level"),
)

# Word-boundary patterns. "cpi" must not match the Indian party CPI(M); "brent" must not match Brentwood.
BUCKETS = (
    ("intelligenza artificiale", "artificial intelligence", (r"\bnvidia\b", r"\bartificial intelligence\b", r"\bsemiconductors?\b")),
    ("banche centrali e tassi", "central banks and rates", (r"\bfederal reserve\b", r"\becb\b", r"\binterest rates?\b", r"\brate hike\b", r"\bcentral banks?\b")),
    ("inflazione", "inflation", (r"\binflation\b", r"\bconsumer prices?\b", r"\bhicp\b", r"\bcpi\b(?!\s*\()")),
    ("dazi", "tariffs", (r"\btariffs?\b", r"\bduties\b")),
    ("petrolio", "oil", (r"\bcrude oil\b", r"\boil prices?\b", r"\bbrent crude\b", r"\bbrent\b(?!wood)", r"\bopec\b", r"\bhormuz\b")),
    ("europa", "europe", (r"\beurozone\b", r"\beuro area\b", r"\bstoxx\b", r"\beuropean stocks?\b")),
    ("asia", "asia", (r"\bshanghai\b", r"\bnikkei\b", r"\bhang seng\b", r"\bchina stocks?\b")),
    ("conflitti", "conflict", (r"\bukraine\b", r"\bgaza\b", r"\bhormuz\b")),
)
FINANCE = re.compile(
    r"\b(stocks?|markets?|shares?|investors?|trading|equit(?:y|ies)|bonds?|yields?|"
    r"wall street|nasdaq|s&p|fed\b|ecb|central banks?|inflation|tariffs?|crude|opec|"
    r"oil prices?|oil\b|economy|gdp|dollar|euro|treasury|interest rates?|rate hike)\b",
    re.I,
)
POLITICAL = re.compile(
    r"cpi\s*\(\s*m\s*\)|\bcpim\b|communist party|\bbjp\b|civic poll|\bpolling\b|\bcandidate\b",
    re.I,
)
PREFERRED_HOSTS = (
    "reuters.com", "apnews.com", "cnbc.com", "ft.com", "bloomberg.com", "wsj.com",
    "euronews.com", "aa.com.tr", "marketwatch.com", "investing.com", "finance.yahoo.com",
    "federalreserve.gov", "ecb.europa.eu", "bbc.com", "bbc.co.uk", "nytimes.com",
    "theguardian.com", "economist.com", "barrons.com", "foxbusiness.com",
    "independent.co.uk", "businessinsider.com", "nikkei.com", "finanznachrichten.de",
    "investinglive.com",
)
US_MEASURE = {
    "CPIAUCSL": ("CPI", "CPI"),
    "CPILFESL": ("CPI core", "core CPI"),
    "PCEPI": ("PCE", "PCE"),
    "PCEPILFE": ("PCE core", "core PCE"),
}


def _get(url: str, timeout: int = 25) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "LeonixData/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _closes(ticker: str) -> list[tuple[str, float]]:
    period2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    period1 = period2 - 40 * 86400
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{ticker}?period1={period1}&period2={period2}&interval=1d"
    )
    payload = json.loads(_get(url, timeout=20))
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    block = result[0]
    stamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    rows = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        rows.append((day, float(close)))
    dedup: dict[str, float] = {}
    for day, close in rows:
        dedup[day] = close
    return [(day, dedup[day]) for day in sorted(dedup)]


def _close_on_or_before(rows: list[tuple[str, float]], day: str) -> float | None:
    earlier = [value for stamp, value in rows if stamp <= day]
    if not earlier:
        return None
    return earlier[-1]


def weekly_base(rows: list[tuple[str, float]]) -> float | None:
    """Close seven calendar days before the last print, or the session just before it.

    Five index steps is the previous Friday only when every weekday traded.
    A US holiday such as Thanksgiving removes a bar, so those five steps land
    on Thursday of the week before. Seven calendar days still lands on the
    Friday a week earlier.
    """
    if len(rows) < 2:
        return None
    last_day = datetime.strptime(rows[-1][0], "%Y-%m-%d").date()
    target = (last_day - timedelta(days=7)).isoformat()
    return _close_on_or_before(rows, target)


def _move(rows: list[tuple[str, float]], kind: str) -> dict | None:
    if len(rows) < 2:
        return None
    last_day, last = rows[-1]
    prev = rows[-2][1]
    week = weekly_base(rows)
    if week is None:
        return None
    if kind == "yield10":
        # Yahoo's ^TNX is the yield times ten when the print is above 20.
        if last > 20:
            last, prev, week = last / 10.0, prev / 10.0, week / 10.0
        daily = last - prev
        weekly = last - week
    else:
        daily = None if not prev else (last / prev - 1.0) * 100.0
        weekly = None if not week else (last / week - 1.0) * 100.0
    return {"date": last_day, "level": last, "daily": daily, "weekly": weekly, "kind": kind}


def market_moves() -> list[dict]:
    out = []
    for key, label, ticker, kind in MARKETS:
        try:
            row = _move(_closes(ticker), kind)
        except Exception as exc:
            print(f"prezzo {label} non disponibile ({type(exc).__name__})")
            continue
        if row is None:
            continue
        row["key"] = key
        row["label"] = label
        out.append(row)
        print(f"prezzo {label} {row['date']} livello {row['level']:.4g}")
    return out


def _page_title(extras: str) -> str:
    match = re.search(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", extras, flags=re.I | re.S)
    if not match:
        return ""
    title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return title[:180]


def _gdelt_file(stamp: str) -> list[dict]:
    url = f"https://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip"
    blob = _get(url, timeout=40)
    archive = zipfile.ZipFile(io.BytesIO(blob))
    name = archive.namelist()[0]
    articles = []
    with archive.open(name) as handle:
        for raw in io.TextIOWrapper(handle, encoding="utf-8", errors="replace"):
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            url_field = parts[4]
            if not url_field.startswith("http"):
                continue
            title = _page_title(parts[-1])
            if len(title) < 12:
                continue
            articles.append({"title": title, "url": url_field.split()[0], "source": parts[3][:60]})
    return articles


def _host(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _preferred(url: str) -> bool:
    host = _host(url)
    return any(host == name or host.endswith("." + name) for name in PREFERRED_HOSTS)


def _theme_key(title: str, url: str) -> tuple[str, str] | None:
    # The site name is not a keyword: nikkei.com must not make every story an Asia story.
    path = urllib.parse.urlparse(url).path.replace("-", " ")
    hay = f"{title} {path}"
    if POLITICAL.search(hay) or POLITICAL.search(title):
        return None
    for key, english, patterns in BUCKETS:
        if not any(re.search(pattern, hay, flags=re.I) for pattern in patterns):
            continue
        # A war headline with no market word is not a market theme. The other
        # buckets are already finance words, matched on a word boundary.
        if key == "conflitti" and not FINANCE.search(hay):
            return None
        return key, english
    return None


def gdelt_themes(as_of: str | None) -> list[dict]:
    """Five to eight market themes. A theme without a major-news link is dropped."""
    day = datetime.strptime((as_of or datetime.now(timezone.utc).date().isoformat())[:10], "%Y-%m-%d")
    stamps = []
    for shift in range(0, 5):
        current = day - timedelta(days=shift)
        if current.weekday() >= 5:
            continue
        for hour in ("160000", "120000", "070000"):
            stamps.append(f"{current:%Y%m%d}{hour}")
        if len(stamps) >= 6:
            break
    grouped: dict[str, list[dict]] = {key: [] for key, *_rest in BUCKETS}
    english = {key: label for key, label, _patterns in BUCKETS}
    seen: set[str] = set()
    for stamp in stamps:
        try:
            articles = _gdelt_file(stamp)
        except Exception as exc:
            print(f"GDELT {stamp} non disponibile ({type(exc).__name__})")
            continue
        print(f"GDELT {stamp} titoli {len(articles)}")
        for article in articles:
            if article["url"] in seen or not _preferred(article["url"]):
                continue
            matched = _theme_key(article["title"], article["url"])
            if matched is None:
                continue
            key, _label = matched
            grouped[key].append(article)
            seen.add(article["url"])
    themes = []
    for key, _label, _patterns in BUCKETS:
        items = grouped[key][:2]
        if not items:
            continue
        themes.append({
            "theme": key,
            "theme_en": english[key],
            "links": [{"label": item["title"], "url": item["url"], "title": item["title"]} for item in items],
        })
        if len(themes) >= 8:
            break
    return themes[:8]


def gkg_mean_tone(day: str) -> float | None:
    """Average GDELT GKG V2Tone for one weekday. Same scale as the sentiment card."""
    stamp = day.replace("-", "")[:8] + "154500"
    url = f"https://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip"
    try:
        blob = _get(url, timeout=45)
        archive = zipfile.ZipFile(io.BytesIO(blob))
        text = archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"tono GDELT non disponibile ({type(exc).__name__})")
        return None
    total = 0.0
    count = 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 16 or not parts[15]:
            continue
        try:
            total += float(parts[15].split(",")[0])
        except ValueError:
            continue
        count += 1
    if count == 0:
        return None
    tone = total / count
    print(f"tono GDELT {day} {tone:.3f} n={count}")
    return tone


def news_tone(sentiment: dict, as_of: str | None, fetch: bool) -> float | None:
    """The card's GDELT average. A lexicon fallback is a different scale and is not used."""
    if sentiment.get("gdelt") and sentiment.get("score") is not None:
        return float(sentiment["score"])
    points = sentiment.get("points") or []
    if sentiment.get("gdelt") and points and points[-1].get("value") is not None:
        return float(points[-1]["value"])
    if fetch and as_of:
        return gkg_mean_tone(as_of[:10])
    return None


def _num(value) -> list[float]:
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, str):
        return [float(token.replace(",", ".")) for token in re.findall(r"-?\d+(?:[.,]\d+)?", value)]
    if isinstance(value, dict):
        found = []
        for item in value.values():
            found.extend(_num(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_num(item))
        return found
    return []


def allowed_numbers(inputs: dict) -> list[float]:
    values = _num(inputs)
    for row in (inputs.get("inflation") or {}).values():
        if isinstance(row, dict) and isinstance(row.get("yoy"), (int, float)):
            values.append(float(row["yoy"]) * 100.0)
    return values


def _places(token: str) -> int:
    if "." in token:
        return len(token.split(".")[-1])
    if "," in token:
        return len(token.split(",")[-1])
    return 0


def numbers_outside(text: str, allowed: list[float]) -> list[str]:
    plain = re.sub(r"https?://\S+", " ", text)
    bad = []
    for match in re.finditer(r"-?\d+(?:[.,]\d+)?", plain):
        token = match.group(0)
        number = float(token.replace(",", "."))
        places = _places(token)
        if any(round(value, places) == round(number, places) for value in allowed):
            continue
        bad.append(token)
    return bad


def urls_in(text: str) -> list[str]:
    return [url.rstrip(".,") for url in re.findall(r"https?://[^\s)>\],\"']+", text)]


def validate_narrative(narrative: dict, inputs: dict) -> list[str]:
    """Return a list of problems. Empty means the story is usable."""
    allowed = allowed_numbers(inputs)
    known_urls = set()
    for theme in inputs.get("themes") or []:
        for link in theme.get("links") or []:
            if link.get("url"):
                known_urls.add(link["url"])
    for headline in inputs.get("headlines") or []:
        if headline.get("url"):
            known_urls.add(headline["url"])
    problems = []
    for lang in ("it", "en"):
        doc = narrative.get(lang) or {}
        if not doc.get("title") or not doc.get("cosa_sta_succedendo"):
            problems.append(f"{lang}: manca titolo o racconto")
        sentences = [part for part in re.split(r"(?<=[.!?])\s+", doc.get("cosa_sta_succedendo") or "") if part.strip()]
        if not (4 <= len(sentences) <= 8):
            problems.append(f"{lang}: il racconto ha {len(sentences)} frasi")
        themes = doc.get("temi") or []
        available = len(inputs.get("themes") or [])
        if available >= 5 and not (5 <= len(themes) <= 8):
            problems.append(f"{lang}: {len(themes)} temi")
        if available and not themes:
            problems.append(f"{lang}: nessun tema")
        watch = doc.get("da_tenere_docchio") or []
        if len(watch) != 3:
            problems.append(f"{lang}: {len(watch)} punti da tenere d'occhio")
        blob = json.dumps(doc, ensure_ascii=False)
        for token in numbers_outside(blob, allowed):
            problems.append(f"{lang}: numero {token} non è negli input")
        for url in urls_in(blob):
            if url.rstrip(".,") not in known_urls:
                problems.append(f"{lang}: link non presente negli input")
    advice = re.compile(r"\b(compra|vendi|acquista|buy|sell|should buy|should sell)\b", re.I)
    if advice.search(json.dumps(narrative, ensure_ascii=False)):
        problems.append("il testo contiene un consiglio di acquisto o vendita")
    return problems


def _fmt(value: float | None, places: int, lang: str) -> str | None:
    if value is None:
        return None
    text = f"{value:.{places}f}"
    return text.replace(".", ",") if lang == "it" else text


def _pct(value: float | None, lang: str) -> str:
    if value is None:
        return "dato non ancora pubblicato"
    sign = "+" if value > 0 else ""
    return f"{sign}{_fmt(value, 1, lang)}%"


def _market(inputs: dict, key: str) -> dict | None:
    return next((row for row in inputs.get("markets") or [] if row.get("key") == key), None)


def _theme_docs(inputs: dict, lang: str) -> list[dict]:
    docs = []
    for theme in (inputs.get("themes") or [])[:8]:
        links = [link for link in theme.get("links") or [] if link.get("url") and (link.get("title") or link.get("label"))]
        if not links:
            continue
        name = theme["theme"] if lang == "it" else theme.get("theme_en") or theme["theme"]
        title = links[0].get("title") or links[0].get("label")
        sentence = f"Su {name}, il titolo è «{title}»." if lang == "it" else f"On {name}, the headline is “{title}”."
        docs.append({
            "theme": name[:1].upper() + name[1:],
            "sentence": sentence,
            "links": [{"label": link.get("title") or link.get("label"), "url": link["url"]} for link in links[:2]],
        })
    return docs


def template_narrative(inputs: dict) -> dict:
    """A plain story that only repeats numbers already in the inputs."""
    spx = _market(inputs, "spx")
    stoxx = _market(inputs, "stoxx")
    vix = _market(inputs, "vix")
    us10 = _market(inputs, "us10y")
    eurusd = _market(inputs, "eurusd")
    brent = _market(inputs, "brent")
    gold = _market(inputs, "gold")
    regime = inputs.get("regime") or {}
    pillars = inputs.get("pillars") or {}
    inflation = inputs.get("inflation") or {}
    sentiment = inputs.get("sentiment") or {}
    behavior = inputs.get("behavior") or {}
    as_of = (inputs.get("asOf") or "")[:10]
    months_it = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
    months_en = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    day = inputs.get("day")
    month = inputs.get("month") or 0
    year = inputs.get("year")
    when_it = f"{day} {months_it[month]} {year}" if day and year and 1 <= month <= 12 else "oggi"
    when_en = f"{day} {months_en[month]} {year}" if day and year and 1 <= month <= 12 else "today"

    def line(lang: str) -> str:
        bits = []
        if spx:
            bits.append(
                f"S&P 500 {_pct(spx.get('weekly'), lang)} nella settimana e {_pct(spx.get('daily'), lang)} nell'ultima seduta"
                if lang == "it"
                else f"the S&P 500 is {_pct(spx.get('weekly'), lang)} on the week and {_pct(spx.get('daily'), lang)} on the last session"
            )
        if stoxx:
            bits.append(
                f"Euro Stoxx 50 {_pct(stoxx.get('weekly'), lang)} nella settimana"
                if lang == "it"
                else f"the Euro Stoxx 50 is {_pct(stoxx.get('weekly'), lang)} on the week"
            )
        if vix and vix.get("level") is not None:
            bits.append(
                f"il VIX è a {_fmt(vix['level'], 1, lang)}"
                if lang == "it"
                else f"the VIX is at {_fmt(vix['level'], 1, lang)}"
            )
        if us10 and us10.get("level") is not None:
            bits.append(
                f"il Treasury a 10 anni è al {_fmt(us10['level'], 2, lang)}%"
                if lang == "it"
                else f"the 10-year Treasury yield is {_fmt(us10['level'], 2, lang)}%"
            )
        return ", ".join(bits)

    def _when(row: dict, lang: str) -> str:
        stamp = (row.get("asOf") or "")[:10]
        if len(stamp) < 7:
            return ""
        month_no = int(stamp[5:7])
        year_no = stamp[:4]
        if not 1 <= month_no <= 12:
            return ""
        name = months_it[month_no] if lang == "it" else months_en[month_no]
        return f"{name} {year_no}"

    def inflation_line(lang: str) -> str:
        parts = []
        for key, label_it, label_en in (("EZ", "eurozona", "euro area"), ("CH", "Svizzera", "Switzerland"), ("US", "Stati Uniti", "United States")):
            row = inflation.get(key) or {}
            label = label_it if lang == "it" else label_en
            if row.get("yoy") is None:
                parts.append(f"{label}: dato non ancora pubblicato" if lang == "it" else f"{label}: figure not published yet")
                continue
            figure = _fmt(row["yoy"] * 100.0, 1, lang)
            when = _when(row, lang)
            if key == "US":
                measure = US_MEASURE.get(row.get("series") or "", ("prezzi al consumo", "consumer prices"))
                measure_name = measure[0] if lang == "it" else measure[1]
                dated = f" di {when}" if when and lang == "it" else (f" for {when}" if when else "")
                parts.append(f"{label}, {measure_name}{dated}, {figure}%")
            else:
                parts.append(f"{label} {figure}%")
        return ", ".join(parts)

    def score_line(lang: str) -> str:
        names = (
            ("growth", "crescita", "growth"),
            ("inflation", "inflazione", "inflation"),
            ("policy", "stance reale", "real-rate stance"),
            ("stress", "stress", "stress"),
        )
        bits = []
        for key, it, en in names:
            if pillars.get(key) is None:
                continue
            bits.append(f"{it if lang == 'it' else en} {_fmt(pillars[key], 2, lang)}")
        return ", ".join(bits)

    themes = _theme_docs(inputs, "it")
    themes_en = _theme_docs(inputs, "en")
    markets_it = []
    markets_en = []
    for row in inputs.get("markets") or []:
        if row.get("kind") == "pct":
            markets_it.append({"name": row["label"], "text": f"{_pct(row.get('weekly'), 'it')} nella settimana ({_pct(row.get('daily'), 'it')} nell'ultima seduta)"})
            markets_en.append({"name": row["label"], "text": f"{_pct(row.get('weekly'), 'en')} on the week ({_pct(row.get('daily'), 'en')} last session)"})
        elif row.get("kind") == "yield10":
            markets_it.append({"name": row["label"], "text": f"{_fmt(row.get('level'), 2, 'it')}% ({_fmt(row.get('weekly'), 2, 'it')} punti nella settimana)"})
            markets_en.append({"name": row["label"], "text": f"{_fmt(row.get('level'), 2, 'en')}% ({_fmt(row.get('weekly'), 2, 'en')} points on the week)"})
        else:
            markets_it.append({"name": row["label"], "text": f"{_fmt(row.get('level'), 2 if row['key'] in {'eurusd', 'usdchf'} else 1, 'it')} ({_pct(row.get('weekly'), 'it')} nella settimana)"})
            markets_en.append({"name": row["label"], "text": f"{_fmt(row.get('level'), 2 if row['key'] in {'eurusd', 'usdchf'} else 1, 'en')} ({_pct(row.get('weekly'), 'en')} on the week)"})

    label = regime.get("label") or "Neutrale"
    plain = ((regime.get("plain") or "").split(". ")[0]).strip()
    if plain and not plain.endswith("."):
        plain += "."
    sent = sentiment.get("score")
    mood = behavior.get("value")
    it_story = " ".join(part for part in (
        f"Alla chiusura del {when_it} i prezzi che osserviamo dicono questo: {line('it')}." if line("it") else "",
        f"Il modello legge «{label}». {plain}".strip(),
        f"I punteggi mensili, non le percentuali dei prezzi, sono {score_line('it')}." if score_line("it") else "",
        f"L'inflazione ufficiale pubblicata è {inflation_line('it')}.",
        f"Il tono delle notizie è {_fmt(sent, 2, 'it')}." if sent is not None else "",
        f"L'umore degli investitori è {_fmt(mood, 2, 'it')}." if mood is not None else "",
        f"Euro/dollaro {_fmt(eurusd['level'], 2, 'it')}, oro {_fmt(gold['level'], 1, 'it')}, Brent {_fmt(brent['level'], 1, 'it')}." if eurusd and gold and brent else "",
    ) if part)
    en_story = " ".join(part for part in (
        f"At the close of {when_en} the prices we follow say this: {line('en')}." if line("en") else "",
        f"The model reads “{label}”. {plain}".strip(),
        f"The monthly scores, which are not price percentages, are {score_line('en')}." if score_line("en") else "",
        f"The latest official inflation is {inflation_line('en')}.",
        f"The news tone is {_fmt(sent, 2, 'en')}." if sent is not None else "",
        f"Investor mood is {_fmt(mood, 2, 'en')}." if mood is not None else "",
        f"Euro/dollar {_fmt(eurusd['level'], 2, 'en')}, gold {_fmt(gold['level'], 1, 'en')}, Brent {_fmt(brent['level'], 1, 'en')}." if eurusd and gold and brent else "",
    ) if part)
    watch_it = [
        {"text": "La prossima lettura mensile del modello arriva a fine mese. Fino ad allora i punteggi restano quelli dell'ultimo mese chiuso."},
        {"text": "Inflazione: si usa solo il dato ufficiale già pubblicato, senza stimare il mese in corso."},
        {"text": "Il tono delle notizie descrive i titoli del giorno. Non entra nei pesi del portafoglio."},
    ]
    watch_en = [
        {"text": "The next monthly model reading comes at month-end. Until then the scores stay those of the last closed month."},
        {"text": "Inflation uses only the official print already published. The current month is not estimated."},
        {"text": "The news tone describes the day's headlines. It does not change the portfolio weights."},
    ]
    week = spx.get("weekly") if spx else None
    if lang_up := (week is not None and week >= 0):
        title_it = "Settimana in rialzo, con il modello ancora fermo sulla lettura mensile"
        title_en = "An up week, while the model stays on its monthly reading"
    else:
        title_it = "Settimana in calo, con il modello ancora fermo sulla lettura mensile"
        title_en = "A down week, while the model stays on its monthly reading"
    if week is None:
        title_it = "Chiusura del giorno, con il modello ancora fermo sulla lettura mensile"
        title_en = "The day's close, with the model still on its monthly reading"
    _ = lang_up
    return {
        "source": "template",
        "it": {
            "title": title_it,
            "cosa_sta_succedendo": it_story,
            "temi": themes,
            "mercati": markets_it,
            "da_tenere_docchio": watch_it,
        },
        "en": {
            "title": title_en,
            "cosa_sta_succedendo": en_story,
            "temi": themes_en,
            "mercati": markets_en,
            "da_tenere_docchio": watch_en,
        },
    }


def collect_inputs(snapshot: dict, with_network: bool = True) -> dict:
    regime = snapshot.get("regime") or {}
    macro = snapshot.get("macroGlobal") or {}
    sentiment = snapshot.get("sentiment") or {}
    as_of = (snapshot.get("meta") or {}).get("asOf")
    inputs = {
        "asOf": as_of,
        "regime": {"label": regime.get("label"), "code": regime.get("code"), "plain": regime.get("plain")},
        "pillars": macro.get("pillars") or {},
        "inflation": {
            key: {
                "yoy": (row or {}).get("yoy"),
                "asOf": (row or {}).get("asOf"),
                "series": (row or {}).get("series"),
            }
            for key, row in (snapshot.get("inflation") or {}).items()
            if isinstance(row, dict)
        },
        "sentiment": {"score": news_tone(sentiment, as_of, fetch=with_network), "gdelt": True},
        "behavior": snapshot.get("behavior") or {},
        "headlines": [{"title": item.get("title"), "url": item.get("url"), "source": item.get("source")} for item in (sentiment.get("headlines") or [])[:8]],
        "markets": [],
        "themes": [],
    }
    if as_of and len(as_of) >= 10:
        inputs["day"] = int(as_of[8:10])
        inputs["month"] = int(as_of[5:7])
        inputs["year"] = int(as_of[0:4])
    if with_network:
        inputs["markets"] = market_moves()
        inputs["themes"] = gdelt_themes(as_of)
    if not inputs["themes"]:
        inputs["themes"] = [{
            "theme": "notizie del giorno",
            "theme_en": "today's news",
            "links": [{"label": item.get("source") or "fonte", "url": item["url"], "title": item.get("title")} for item in inputs["headlines"][:2] if item.get("url")],
        }]
    return inputs


def _prompt(inputs: dict) -> str:
    payload = json.dumps(inputs, ensure_ascii=False)
    return (
        "Scrivi una narrativa didattica dei mercati per un'app. "
        "Rispondi solo con JSON con le chiavi it e en. "
        "Ogni lingua ha: title (stringa), cosa_sta_succedendo (4-6 frasi), "
        "temi (5-8 oggetti con theme, sentence, links), mercati (lista di name e text), "
        "da_tenere_docchio (esattamente 3 oggetti con text). "
        "links ha label e url. "
        "Italiano semplice e inglese semplice. Nessun consiglio di acquisto o vendita, nessun 'compra' o 'vendi'. "
        "Ogni numero del testo deve essere un numero già presente nel JSON di input, arrotondato. "
        "Ogni url deve essere uno degli url di input. Non inventare date, percentuali o link. "
        "Il punteggio di inflazione del modello non è la percentuale dei prezzi al consumo.\n"
        f"INPUT:\n{payload}"
    )


def call_mistral(inputs: dict) -> dict | None:
    key = os.environ.get("MISTRAL_API_KEY") or ""
    if not key:
        print("Mistral non chiamato: chiave assente")
        return None
    body = json.dumps({
        "model": MISTRAL_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Sei un redattore didattico. Non dai consigli di investimento."},
            {"role": "user", "content": _prompt(inputs)},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        MISTRAL_URL,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "LeonixData/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"Mistral non disponibile ({type(exc).__name__})")
        return None
    content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        print("Mistral ha risposto senza JSON")
        return None
    parsed["source"] = "mistral"
    return parsed


def build_narrative(snapshot: dict, with_network: bool = True) -> dict:
    inputs = collect_inputs(snapshot, with_network=with_network)
    narrative = None
    if os.environ.get("MISTRAL_API_KEY"):
        for attempt in (1, 2):
            narrative = call_mistral(inputs)
            if narrative is None:
                break
            problems = validate_narrative(narrative, inputs)
            if not problems:
                print(f"narrativa Mistral accettata al tentativo {attempt}")
                break
            print(f"narrativa Mistral rifiutata al tentativo {attempt}: {len(problems)} controlli")
            narrative = None
    if narrative is None:
        narrative = template_narrative(inputs)
        problems = validate_narrative(narrative, inputs)
        if problems:
            print(f"template con {len(problems)} controlli, si pubblica comunque il testo a modello")
        narrative["source"] = "template"
    narrative["generatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    narrative["inputsAsOf"] = inputs.get("asOf")
    return narrative


def attach_narrative(snapshot: dict) -> dict:
    snapshot["narrative"] = build_narrative(snapshot, with_network=True)
    print(f"narrativa {snapshot['narrative'].get('source')}")
    return snapshot


def validate_sample(sample_text: str, inputs: dict) -> list[str]:
    """Check an approved sample as if it were the Italian story."""
    body = sample_text.split("# Note per Marco")[0]
    title = ""
    match = re.search(r"## Titolo\s+\*\*(.+?)\*\*", body)
    if match:
        title = match.group(1).strip()
    story_match = re.search(r"## Cosa sta succedendo\s+(.+?)\n## ", body, flags=re.S)
    story = story_match.group(1).strip() if story_match else body
    urls = urls_in(body)
    fake = {
        "it": {
            "title": title or "Esempio",
            "cosa_sta_succedendo": story,
            "temi": [{"theme": "t", "sentence": "s", "links": [{"label": "l", "url": url}]} for url in urls[:6]] or [{"theme": "t", "sentence": "s", "links": []}] * 5,
            "mercati": [],
            "da_tenere_docchio": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        },
        "en": {
            "title": title or "Sample",
            "cosa_sta_succedendo": "Sentence one. Sentence two. Sentence three. Sentence four.",
            "temi": [{"theme": "t", "sentence": "s", "links": []}] * 5,
            "mercati": [],
            "da_tenere_docchio": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
        },
    }
    # The English side of the sample is not separate. Check the Italian body only.
    problems = []
    allowed = allowed_numbers(inputs)
    known = set()
    for theme in inputs.get("themes") or []:
        for link in theme.get("links") or []:
            if link.get("url"):
                known.add(link["url"])
    for token in numbers_outside(title + "\n" + story, allowed):
        problems.append(f"numero {token} non è negli input")
    for url in urls_in(title + "\n" + story):
        if url.rstrip(".,)") not in known:
            problems.append("link non presente negli input")
    _ = fake
    return problems
