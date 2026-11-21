#!/usr/bin/env python3
"""Refresh the mobile snapshot through the last closed US session.

Market prices come from the Yahoo chart API (adjusted close). No key.
FRED is used only when FRED_API_KEY is set in the environment. The key is
never printed and never written into the snapshot. Without it, monthly
macro series stay marked missing instead of being invented.

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


def classify_today(as_of: str, prices: dict[str, list[dict]]) -> dict:
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
    clarity = reading_clarity(observable["observations"], observable["regime"])
    drivers = driver_lights(observable)
    for driver in drivers:
        driver["asOf"] = as_of if driver["key"] in {"crescita", "tassi", "stress"} else None
        driver["frequency"] = "giornaliero" if driver["asOf"] else None
    if any(item["key"] == "inflazione" and "non ancora" in item["text"] for item in drivers):
        for item in drivers:
            if item["key"] in {"inflazione", "liquidita"}:
                item["text"] = (
                    "Ultimo dato ufficiale non disponibile in questo aggiornamento: "
                    "la pipeline non ha una chiave FRED. Nessun valore è stato stimato."
                )
                item["asOf"] = None
                item["frequency"] = "mensile"
    for item in drivers:
        if item["key"] == "credito":
            item["text"] = (
                item["text"] + " Senza il file ufficiale, questo aggiornamento non pubblica uno spread ICE."
                if "non osservabile" in item["text"]
                else item["text"]
            )
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

    regime = classify_today(as_of, prices)
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
    PUBLIC_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_SNAPSHOT.write_text(json.dumps(snapshot), encoding="utf-8")
    DATA_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    DATA_SNAPSHOT.write_text(json.dumps(snapshot), encoding="utf-8")
    print(f"pubblicato {PUBLIC_SNAPSHOT}")
    print(f"pubblicato {DATA_SNAPSHOT}")


if __name__ == "__main__":
    main()
