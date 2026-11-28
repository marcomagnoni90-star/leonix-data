#!/usr/bin/env python3
"""Refresh the mobile snapshot through the last closed US session.

Market prices come from the Yahoo chart API (adjusted close). No key.
FRED is used when FRED_API_KEY is set, and the public CSV is the fallback.
The key is never printed and never written into the snapshot. If FRED does
not answer, the global model uses the official sources in regime_engine
(OECD, BLS, ECB, BIS, Treasury, World Bank). Missing series stay missing.

The Run 3 equity curve is kept through its last date and extended with the
new sessions, so a 100% SPY book still matches the SPY column of this snapshot.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

NY = ZoneInfo("America/New_York")
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from history_panel import CACHE, download_adjusted  # noqa: E402

PUBLIC_SNAPSHOT = ROOT / "mobile" / "public" / "snapshot.json"
OVERLAY = ROOT / "mobile" / "src" / "data" / "overlay.json"
# File letto dall'app. Sovrascrivibile con LEONIX_SNAPSHOT_OUT (percorso relativo alla root del repo).
DATA_SNAPSHOT = ROOT / os.environ.get("LEONIX_SNAPSHOT_OUT", "data/snapshot.json")


def last_closed_session(now: datetime | None = None) -> str:
    """Last weekday whose 16:00 America/New_York cash close is already over."""
    now = now or datetime.now(NY)
    day = now.date()
    if now.time() < time(16, 15):
        day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


def download_symbol(symbol: str) -> list[dict]:
    return download_adjusted(symbol)


def try_fred_latest(series_id: str) -> list[tuple[str, float]]:
    """Public FRED graph, no key. Returns [] if the site does not answer."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    rows = []
    for line in raw.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2 or parts[1] in {"", "."}:
            continue
        try:
            rows.append((parts[0], float(parts[1])))
        except ValueError:
            continue
    return rows


def _missing_phrase(text: str) -> bool:
    lowered = (text or "").lower()
    needles = ("non ancora", "non osservabile", "non disponibile", "non è stato stimato", "non inventiamo", "chiave fred", "pipeline")
    return any(needle in lowered for needle in needles)


def _fred_observations(series_id: str) -> list[tuple[str, float]]:
    """FRED observations. The key stays in the request and is never printed."""
    key = os.environ.get("FRED_API_KEY") or ""
    if key:
        url = (
            "https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={urllib.parse.quote(series_id)}&file_type=json&api_key={urllib.parse.quote(key)}"
        )
        request = urllib.request.Request(url, headers={"User-Agent": "LeonixData/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = []
            for item in payload.get("observations") or []:
                raw = item.get("value")
                if raw in (None, "", "."):
                    continue
                rows.append((str(item.get("date")), float(raw)))
            if rows:
                print(f"FRED {series_id} ok n={len(rows)} ultimo={rows[-1][0]}")
                return rows
        except Exception as exc:
            print(f"FRED {series_id} non disponibile ({type(exc).__name__})")
    rows = try_fred_latest(series_id)
    if rows:
        print(f"FRED csv {series_id} ok n={len(rows)} ultimo={rows[-1][0]}")
    return rows


def _index_yoy(rows: list[tuple[str, float]]) -> dict | None:
    if len(rows) < 13:
        return None
    last_date, last_value = rows[-1]
    year = int(last_date[:4]) - 1
    month = last_date[5:7]
    prior = next((value for date, value in rows if date[:7] == f"{year}-{month}"), None)
    if not prior:
        prior = rows[-13][1]
    if not prior:
        return None
    return {"asOf": last_date[:10], "index": last_value, "yoy": last_value / prior - 1.0}


def _eurostat_table(url: str) -> tuple[str, float] | None:
    request = urllib.request.Request(url, headers={"User-Agent": "LeonixData/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"Eurostat non disponibile ({type(exc).__name__})")
        return None
    last = None
    for line in raw.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 8 or not parts[6] or not parts[7]:
            continue
        try:
            last = (parts[6], float(parts[7]))
        except ValueError:
            continue
    return last


def _eurostat_hicp() -> dict | None:
    """Latest euro-area HICP annual rate.

    prc_hicp_manr (CP00, EA20) stopped at December 2025. The flash and the
    later months are in prc_hicp_minr, item TOTAL, geo EA.
    """
    tables = (
        (
            "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/"
            "prc_hicp_minr/M.RCH_A.TOTAL.EA?format=SDMX-CSV&startPeriod=2019-01",
            "Eurostat HICP prc_hicp_minr TOTAL EA",
        ),
        (
            "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/"
            "prc_hicp_manr/M.RCH_A.CP00.EA?format=SDMX-CSV&startPeriod=2019-01",
            "Eurostat HICP prc_hicp_manr CP00 EA",
        ),
    )
    best: dict | None = None
    for url, source in tables:
        last = _eurostat_table(url)
        if last is None:
            continue
        period, value = last
        print(f"Eurostat {source} ultimo={period} {value}")
        row = {"asOf": f"{period}-01", "yoy": value / 100.0, "source": source, "series": "CP0000EZ19M086NEST"}
        if best is None or row["asOf"] > best["asOf"]:
            best = row
    return best


def _xlsx_sheet_rows(blob: bytes, sheet_name: str) -> list[list] | None:
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError):
        return None
    targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
    target = None
    for sheet in workbook.findall("m:sheets/m:sheet", ns):
        if sheet.attrib.get("name") == sheet_name:
            rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = targets.get(rel_id)
            break
    if not target:
        return None
    strings = []
    if "xl/sharedStrings.xml" in archive.namelist():
        shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in shared.findall("m:si", ns):
            strings.append("".join(node.text or "" for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    path = target if target.startswith("xl/") else "xl/" + target
    sheet = ET.fromstring(archive.read(path))
    table = []
    for row in sheet.findall("m:sheetData/m:row", ns):
        values = []
        for cell in row.findall("m:c", ns):
            node = cell.find("m:v", ns)
            if node is None or node.text is None:
                values.append(None)
                continue
            if cell.attrib.get("t") == "s":
                values.append(strings[int(node.text)])
            else:
                values.append(node.text)
        table.append(values)
    return table


def _swiss_lik() -> dict | None:
    """BFS LIK, base December 2025 = 100. The cube id stays stable when the file number changes."""
    page_url = "https://www.bfs.admin.ch/bfs/de/home/statistiken/preise/landesindex-konsumentenpreise.html"
    request = urllib.request.Request(page_url, headers={"User-Agent": "LeonixData/1.0"})
    try:
        page = urllib.request.urlopen(request, timeout=40).read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"BFS pagina non disponibile ({type(exc).__name__})")
        return None
    import re
    ids = []
    for asset_id in re.findall(r"dam/assets/(\d+)/master", page) + re.findall(r"/asset/(\d+)", page):
        if asset_id not in ids:
            ids.append(asset_id)
    from datetime import datetime, timedelta
    for asset_id in ids:
        url = f"https://dam-api.bfs.admin.ch/hub/api/dam/assets/{asset_id}/master"
        try:
            blob = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "LeonixData/1.0"}), timeout=60).read()
        except Exception:
            continue
        index_rows = _xlsx_sheet_rows(blob, "INDEX_m")
        change_rows = _xlsx_sheet_rows(blob, "VAR_m-12")
        if not index_rows or not change_rows:
            continue
        title = " ".join(str(cell) for cell in index_rows[2] if cell)
        if "Dezember 2025" not in title and "dicembre 2025" not in title.lower():
            continue
        headers = change_rows[3]
        total = next((row for row in change_rows if row and row[0] == "100_100"), None)
        if total is None:
            continue
        latest = None
        for header, value in zip(headers, total):
            if value is None or header is None:
                continue
            try:
                serial = float(header)
                yoy = float(value)
            except (TypeError, ValueError):
                continue
            latest = (datetime(1899, 12, 30) + timedelta(days=serial), yoy)
        if latest is None:
            continue
        stamp, yoy = latest
        print(f"BFS LIK ok ultimo={stamp.date().isoformat()} yoy={yoy}")
        return {
            "asOf": stamp.date().isoformat(),
            "yoy": yoy / 100.0,
            "source": "BFS LIK, base dicembre 2025",
            "series": "LIK2025",
        }
    print("BFS LIK non trovato")
    return None


def fetch_inflation() -> dict:
    us_rows = _fred_observations("CPIAUCSL")
    ez_rows = _fred_observations("CP0000EZ19M086NEST")
    us = _index_yoy(us_rows) if us_rows else None
    ez = _index_yoy(ez_rows) if ez_rows else None
    if us:
        us["series"] = "CPIAUCSL"
        us["source"] = "FRED"
    if ez:
        ez["series"] = "CP0000EZ19M086NEST"
        ez["source"] = "FRED"
    eurostat = _eurostat_hicp()
    if eurostat and (ez is None or (eurostat.get("asOf") or "") > (ez.get("asOf") or "")):
        ez = eurostat
        ez["series"] = "CP0000EZ19M086NEST"
    ch = _swiss_lik()
    return {"US": us, "EZ": ez, "CH": ch}


def _apply_official_inflation(drivers: list[dict], inflation: dict | None) -> None:
    item = next((row for row in drivers if row.get("key") == "inflazione"), None)
    if item is None:
        return
    labels = (("US", "Stati Uniti"), ("EZ", "Eurozona"), ("CH", "Svizzera"))
    parts = []
    any_value = False
    latest = None
    for key, label in labels:
        row = (inflation or {}).get(key) or {}
        yoy = row.get("yoy") if isinstance(row, dict) else None
        if yoy is None:
            parts.append(f"{label}: dato non ancora pubblicato")
            continue
        any_value = True
        latest = row.get("asOf") or latest
        suffix = " (LIK, base dicembre 2025)" if key == "CH" else ""
        parts.append(f"{label} {yoy * 100:.1f}".replace(".", ",") + f"%{suffix}")
    item["frequency"] = "mensile"
    if not any_value:
        item["text"] = "Dato non ancora pubblicato."
        item["asOf"] = None
        item["light"] = "giallo"
        return
    item["text"] = "Prezzi al consumo, ultimo dato pubblicato: " + ", ".join(parts) + "."
    item["asOf"] = latest
    us_yoy = ((inflation or {}).get("US") or {}).get("yoy")
    if us_yoy is None:
        item["light"] = "giallo"
    elif us_yoy <= 0.025:
        item["light"] = "verde"
    elif us_yoy <= 0.04:
        item["light"] = "giallo"
    else:
        item["light"] = "rosso"


def classify_today(as_of: str, prices: dict[str, list[dict]], inflation: dict | None = None) -> dict:
    import pandas as pd

    from regime_engine.illustrative_allocation import illustrative_book
    from regime_engine.observable_regime import classify_observable_regime, reading_clarity
    from signal_engine.signals import build_all_signals
    from web_app.engine import REGIME_PLAIN, driver_lights, italian_regime

    def frame(symbol: str) -> pd.Series:
        rows = [row for row in prices[symbol] if row["date"] <= as_of]
        index = pd.to_datetime([row["date"] for row in rows])
        return pd.Series([row["adjclose"] for row in rows], index=index, name=symbol)

    assets = pd.DataFrame({"SPY": frame("SPY")})
    macro = pd.DataFrame({"VIX": frame("^VIX"), "US10Y": frame("^TNX")})
    asset_signals, macro_signals, _history = build_all_signals(assets, macro)
    observable = classify_observable_regime(asset_signals, macro_signals, None)
    from regime_engine.risk_on import apply_risk_on, trend_observations

    observations = dict(observable["observations"])
    observations.update(trend_observations(frame("SPY")))
    book = illustrative_book(observable["regime"], observations)
    lifted, _latched, reason = apply_risk_on(book["examples"].set_index("asset")["weight"], observations, latched=False)
    if reason != "base":
        book["note"] = (
            book["note"]
            + " Overlay risk-on, regole fissate a priori: sopra la media a 200 sedute, momentum a 6 e 12 mesi positivo, "
            "VIX sotto 22, drawdown meno ampio di una correzione e credito non in allargamento. "
            "Il peso azionario può salire fino al 90%. Un deterioramento (VIX da 25, drawdown da -12%, momentum a sei mesi negativo o spread da crisi) spegne l'overlay."
        )
        book["key"] = f"{book['key']}+risk-on"
        book["examples"]["weight"] = book["examples"]["asset"].map(lambda asset: float(lifted.get(asset, 0.0)))
        equity = float(lifted.get("SPY", 0.0))
        bonds = float(lifted.get("TLT", 0.0)) + float(lifted.get("TIP", 0.0))
        gold = float(lifted.get("GLD", 0.0))
        cash = float(lifted.get("CASH", 0.0))
        book["classes"] = pd.DataFrame({
            "classe": ["Azionario globale", "Obbligazionario", "Oro", "Liquidità"],
            "peso": [equity, bonds, gold, cash],
        })
        book["classes"] = book["classes"][book["classes"]["peso"] > 1e-8]
    if inflation and (inflation.get("US") or {}).get("yoy") is not None:
        observable["observations"]["cpi_yoy"] = inflation["US"]["yoy"]
    clarity = reading_clarity(observable["observations"], observable["regime"])
    drivers = driver_lights(observable)
    for driver in drivers:
        driver["asOf"] = as_of if driver["key"] in {"crescita", "tassi", "stress"} else None
        driver["frequency"] = "giornaliero" if driver["asOf"] else None
    _apply_official_inflation(drivers, inflation)
    for item in drivers:
        if item["key"] == "liquidita" and _missing_phrase(item["text"]):
            item["text"] = "Dato non ancora pubblicato."
            item["asOf"] = None
            item["frequency"] = "mensile"
        if item["key"] == "credito" and _missing_phrase(item["text"]):
            item["text"] = "Dato non ancora pubblicato."
            item["asOf"] = None
    regime = observable["regime"]
    return {
        "asOf": as_of,
        "session": "chiusura",
        "code": regime,
        "label": italian_regime(regime),
        "plain": REGIME_PLAIN.get(regime),
        "clarityLabel": clarity["label"],
        "clarityScore": clarity["score"],
        "clarityDetail": clarity["detail"],
        "mapKey": book["key"],
        "mapNote": book["note"],
        "classes": [
            {"name": row.classe, "weight": float(row.peso)} for row in book["classes"].itertuples(index=False)
        ],
        "examples": [
            {"ticker": row.asset, "weight": float(row.weight), "role": row.ruolo}
            for row in book["examples"].itertuples(index=False)
        ],
        "drivers": drivers,
        "riskOn": reason,
        "inflation": inflation or {},
    }


def returns_after(rows: list[dict], as_of: str, after: str) -> dict[str, float]:
    ordered = [row for row in rows if row["date"] <= as_of]
    out = {}
    previous = None
    for row in ordered:
        if previous is not None and row["date"] > after:
            out[row["date"]] = row["adjclose"] / previous - 1.0
        previous = row["adjclose"]
    return out


def main() -> None:
    as_of = last_closed_session()
    print(f"chiusura utile {as_of}")
    symbols = ["SPY", "TLT", "TIP", "GLD", "^VIX", "^TNX"]
    prices = {symbol: download_adjusted(symbol) for symbol in symbols}
    for symbol, rows in prices.items():
        last = rows[-1]["date"] if rows else "vuoto"
        print(f"{symbol} ultimo {last} n={len(rows)}")

    # Cache only the portfolio ETFs, clipped to the closed session.
    cache_series = {}
    for ticker in ("TLT", "TIP", "GLD"):
        cache_series[ticker] = [row for row in prices[ticker] if row["date"] <= as_of]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps(
            {
                "source": "Yahoo Finance chart API, adjusted close",
                "downloadedAt": datetime.now(NY).date().isoformat(),
                "series": cache_series,
            }
        ),
        encoding="utf-8",
    )

    inflation = fetch_inflation()
    regime = classify_today(as_of, prices, inflation)
    print(f"regime {regime['code']} mappa {regime['mapKey']} chiarezza {regime['clarityScore']} risk {regime.get('riskOn')}")
    from data_engine.news_sentiment import sentiment_snapshot

    sentiment = sentiment_snapshot()
    print(f"sentiment score {sentiment.get('score')} gdelt {sentiment.get('gdelt')} titoli {len(sentiment.get('headlines') or [])}")

    import csv

    curve_path = ROOT / "results_v3" / "equity_curves.csv"
    with curve_path.open(newline="", encoding="utf-8") as handle:
        curve_rows = list(csv.DictReader(handle))
    last_date = (curve_rows[-1].get("") or curve_rows[-1].get("date"))[:10]
    spy_ret = returns_after(prices["SPY"], as_of, last_date)
    tlt_ret = returns_after(prices["TLT"], as_of, last_date)
    tip_ret = returns_after(prices["TIP"], as_of, last_date)
    gld_ret = returns_after(prices["GLD"], as_of, last_date)
    extra_dates = sorted(spy_ret)
    print(f"curva fino a {last_date}, giorni nuovi {len(extra_dates)}")

    # Last published model weights, held until the next month-end.
    regimes_path = ROOT / "results_v3" / "regimes_monthly.csv"
    with regimes_path.open(newline="", encoding="utf-8") as handle:
        last_regime = list(csv.DictReader(handle))[-1]

    def weight(key: str) -> float:
        text = (last_regime.get(key) or "").strip()
        return float(text) if text else 0.0

    held = {
        "SPY": weight("w_SPY"),
        "TLT": weight("w_TLT"),
        "TIP": weight("w_TIP"),
        "GLD": weight("w_GLD"),
        "CASH": weight("w_CASH"),
    }
    bal_held = {"SPY": 0.6, "TLT": 0.4, "CASH": 0.0}
    append = []
    for date in extra_dates:
        changes = {
            "SPY": spy_ret[date],
            "TLT": tlt_ret.get(date),
            "TIP": tip_ret.get(date),
            "GLD": gld_ret.get(date),
        }
        if any(changes[name] is None and held.get(name, 0) > 1e-8 for name in ("TLT", "TIP", "GLD")):
            continue

        def portfolio_return(weights: dict[str, float]) -> float:
            total = 0.0
            for name, share in weights.items():
                if name == "CASH":
                    continue
                total += share * float(changes[name])
            return total

        append.append(
            {
                "date": date,
                "spy": spy_ret[date],
                "model": portfolio_return(held),
                "balanced": portfolio_return(bal_held),
                "TLT": changes["TLT"],
                "TIP": changes["TIP"],
                "GLD": changes["GLD"],
                "rebalance": False,
                "regime": None,
            }
        )
    overlay = {
        "asOf": as_of,
        "session": "chiusura",
        "updatedAt": datetime.now(NY).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "regime": regime,
        "sentiment": sentiment,
        "append": append,
    }
    OVERLAY.write_text(json.dumps(overlay), encoding="utf-8")
    print(f"overlay {len(append)} giorni")

    # Rebuild the snapshot with the overlay hook in export_snapshot.main
    from export_snapshot import main as export_main

    export_main()
    snapshot = json.loads((ROOT / "mobile" / "src" / "data" / "snapshot.json").read_text(encoding="utf-8"))
    snapshot["inflation"] = inflation
    if snapshot.get("regime"):
        for driver in snapshot["regime"].get("drivers") or []:
            if driver.get("key") == "inflazione":
                official = next((row for row in regime["drivers"] if row.get("key") == "inflazione"), None)
                if official:
                    driver.update(official)
    snapshot = _with_macro_global(snapshot)
    try:
        from daily_narrative import attach_narrative

        snapshot = attach_narrative(snapshot)
    except Exception as exc:
        print(f"narrativa non generata ({type(exc).__name__})")
    PUBLIC_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_SNAPSHOT.write_text(json.dumps(snapshot), encoding="utf-8")
    DATA_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT.write_text(json.dumps(snapshot), encoding="utf-8")
    bundled = ROOT / "mobile" / "src" / "data" / "snapshot.json"
    if bundled.is_file():
        bundled.write_text(json.dumps(snapshot), encoding="utf-8")
    print(f"pubblicato {PUBLIC_SNAPSHOT}")
    print(f"pubblicato {DATA_SNAPSHOT}")
    print(f"macroGlobal {snapshot.get('macroGlobal', {}).get('available')}")


def _with_macro_global(snapshot: dict) -> dict:
    """Attach the global reading. A network failure leaves the previous regime."""
    try:
        from regime_engine.macro_global.panel import latest_reading

        reading = latest_reading(refresh=False)
    except Exception as exc:
        snapshot["macroGlobal"] = {"available": False, "schema": 1, "reason": type(exc).__name__}
        return snapshot
    snapshot["macroGlobal"] = reading
    if reading.get("available") and reading.get("regime"):
        regime = dict(snapshot.get("regime") or {})
        regime["code"] = reading["regime"].get("code")
        regime["label"] = reading["regime"].get("label")
        regime["plain"] = reading["regime"].get("plain")
        pillars = reading.get("pillars") or {}
        regime["drivers"] = [
            {
                "title": name,
                "key": key,
                "light": "rosso" if (pillars.get(key) or 0) > 0.6 and key in {"inflation", "stress", "policy"} else "verde" if (pillars.get(key) or 0) > 0.2 and key == "growth" else "giallo",
                "text": f"Punteggio {pillars.get(key):.2f}." if pillars.get(key) is not None else "Dato mancante.",
            }
            for key, name in (
                ("growth", "Crescita"),
                ("inflation", "Inflazione"),
                ("policy", "Politica monetaria"),
                ("liquidity", "Liquidità"),
                ("stress", "Stress"),
            )
        ]
        weights = reading.get("weights") or {}
        if weights:
            buckets = {
                "Azionario globale": 0.0,
                "Obbligazionario": 0.0,
                "Oro": 0.0,
                "Materie prime": 0.0,
                "Liquidità": 0.0,
            }
            for name, weight in weights.items():
                if name in {"SPY", "EFA", "VGK", "EWJ", "FXI", "EEM"}:
                    buckets["Azionario globale"] += float(weight)
                elif name in {"TLT", "TIP", "LQD"}:
                    buckets["Obbligazionario"] += float(weight)
                elif name == "GLD":
                    buckets["Oro"] += float(weight)
                elif name == "DBC":
                    buckets["Materie prime"] += float(weight)
                elif name == "CASH":
                    buckets["Liquidità"] += float(weight)
            regime["classes"] = [{"name": name, "weight": weight} for name, weight in buckets.items() if weight > 1e-6]
        snapshot["regime"] = regime
    return snapshot


if __name__ == "__main__":
    main()
