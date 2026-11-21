#!/usr/bin/env python3
"""Build the offline snapshot bundled in the mobile app.

Reads the Run 3 artifacts already in the repo. Does not download anything
and does not invent a missing series: a price that is not on disk is null.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results_v3"
OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "snapshot.json"
PRICES = ROOT / "data" / "asset_prices.csv"

PERIODS = (
    "full",
    "out_of_sample",
    "in_sample",
    "gfc",
    "covid_2020",
    "rates_2022",
)


def md5(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.md5(path.read_bytes()).hexdigest()


def num(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_snapshot_md(path: Path, missing: list[str]) -> dict | None:
    if not path.is_file():
        missing.append("results_v3/current_snapshot.md")
        return None
    text = path.read_text(encoding="utf-8")
    as_of = None
    match = re.search(r"Situazione al (\d{4}-\d{2}-\d{2})", text)
    if match:
        as_of = match.group(1)
    regime_line = re.search(r"Regime: \*\*(.+?)\*\* \((.+?)\)\.", text)
    plain = None
    if regime_line:
        after = text.split(regime_line.group(0), 1)[1].strip()
        plain = after.split("\n\n", 1)[0].strip() or None
    clarity = re.search(
        r"Chiarezza della lettura: (\w+) (\d+)/100\. (.+)",
        text,
    )
    map_key = re.search(r"Mappa illustrativa: \*\*(.+?)\*\*\.", text)
    map_note = None
    if "Mappa illustrativa:" in text and "## Classi" in text:
        map_note = text.split("Mappa illustrativa:", 1)[1].split("## Classi", 1)[0]
        map_note = re.sub(r"\*\*.+?\*\*\.", "", map_note).strip() or None
    classes = []
    for name, weight in re.findall(r"^- ([^:\n]+): (\d+(?:[.,]\d+)?)%\s*$", text, re.M):
        if name.startswith("SPY") or name.startswith("TLT") or name.startswith("TIP") or name.startswith("GLD") or name.startswith("CASH"):
            continue
        classes.append({"name": name.strip(), "weight": float(weight.replace(",", ".")) / 100.0})
    examples = []
    for ticker, weight, role in re.findall(
        r"^- ([A-Z]+): (\d+(?:[.,]\d+)?)% — (.+)$",
        text,
        re.M,
    ):
        examples.append(
            {
                "ticker": ticker,
                "weight": float(weight.replace(",", ".")) / 100.0,
                "role": role.strip(),
            }
        )
    drivers = []
    for title, light, body in re.findall(r"^- (.+?) \((\w+)\): (.+)$", text, re.M):
        drivers.append({"title": title.strip(), "light": light.strip(), "text": body.strip()})
    if not regime_line:
        missing.append("regime nel current_snapshot.md")
    return {
        "asOf": as_of,
        "code": regime_line.group(2) if regime_line else None,
        "label": regime_line.group(1) if regime_line else None,
        "plain": plain,
        "clarityLabel": clarity.group(1) if clarity else None,
        "clarityScore": int(clarity.group(2)) if clarity else None,
        "clarityDetail": clarity.group(3).strip() if clarity else None,
        "mapKey": map_key.group(1) if map_key else None,
        "mapNote": map_note,
        "classes": classes,
        "examples": examples,
        "drivers": drivers,
    }


def read_metrics(path: Path, missing: list[str]) -> list[dict]:
    if not path.is_file():
        missing.append("results_v3/period_metrics.csv")
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "period": row["period"],
                    "label": row["period_label"],
                    "book": row["book"],
                    "start": row["start"],
                    "end": row["end"],
                    "cagr": num(row["cagr"]),
                    "volatility": num(row["volatility"]),
                    "sharpe": num(row["sharpe"]),
                    "maxDrawdown": num(row["max_drawdown"]),
                }
            )
    return rows


def month_end_curves(path: Path, missing: list[str]) -> list[dict]:
    if not path.is_file():
        missing.append("results_v3/equity_curves.csv")
        return []
    last: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_date = row.get("") or row.get("date")
            if not raw_date:
                continue
            model = num(row.get("strategy"))
            if model is None:
                continue
            key = raw_date[:7]
            last[key] = {
                "date": raw_date[:10],
                "model": model,
                "spy": num(row.get("spy")),
                "balanced": num(row.get("balanced_60_40")),
            }
    return [last[key] for key in sorted(last)]


def read_regimes(path: Path, missing: list[str]) -> list[dict]:
    if not path.is_file():
        missing.append("results_v3/regimes_monthly.csv")
        return []
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "date": row["date"][:10],
                    "regime": row.get("regime") or "",
                    "allocationKey": row.get("allocation_key") or "",
                    "equity": num(row.get("equity")),
                }
            )
    return rows


def month_end_prices(path: Path) -> dict[str, dict]:
    """Last price of each month for the example tickers, when the file exists."""
    wanted = ("SPY", "TLT", "TIP", "GLD")
    buckets: dict[str, dict[str, dict]] = {name: {} for name in wanted}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        date_key = reader.fieldnames[0] if reader.fieldnames else "date"
        for row in reader:
            raw = row.get(date_key) or ""
            if len(raw) < 7:
                continue
            key = raw[:7]
            for name in wanted:
                value = num(row.get(name))
                if value is not None and value > 0:
                    buckets[name][key] = {"date": raw[:10], "value": value}
    out = {}
    for name, points in buckets.items():
        levels = [points[key] for key in sorted(points)]
        out[name] = (
            {"source": "data/asset_prices.csv", "levels": levels} if levels else None
        )
    return out


def spy_from_curve(monthly: list[dict]) -> dict | None:
    levels = [
        {"date": row["date"], "value": row["spy"]}
        for row in monthly
        if row.get("spy") is not None
    ]
    if not levels:
        return None
    return {"source": "results_v3/equity_curves.csv colonna spy", "levels": levels}


def month_end_from_adjusted(rows: list[dict]) -> dict | None:
    last: dict[str, dict] = {}
    for row in rows:
        last[row["date"][:7]] = {"date": row["date"], "value": row["adjclose"]}
    levels = [last[key] for key in sorted(last)]
    if not levels:
        return None
    return {"source": "Yahoo Finance adjusted close", "levels": levels}


def main() -> None:
    from history_panel import build_daily, load_adjusted_cache, verify_spy

    missing: list[str] = []
    regime = parse_snapshot_md(RESULTS / "current_snapshot.md", missing)
    periods = read_metrics(RESULTS / "period_metrics.csv", missing)
    equity = month_end_curves(RESULTS / "equity_curves.csv", missing)
    regimes = read_regimes(RESULTS / "regimes_monthly.csv", missing)
    adjusted = load_adjusted_cache()
    daily = build_daily(adjusted)
    overlay_path = OUT.parent / "overlay.json"
    overlay = json.loads(overlay_path.read_text(encoding="utf-8")) if overlay_path.is_file() else None
    if overlay and overlay.get("append"):
        for row in overlay["append"]:
            daily["dates"].append(row["date"])
            daily["spy"].append(row["spy"])
            daily["model"].append(row["model"])
            daily["balanced"].append(row["balanced"])
            daily["TLT"].append(row["TLT"])
            daily["TIP"].append(row["TIP"])
            daily["GLD"].append(row["GLD"])
            daily["rebalance"].append(bool(row["rebalance"]))
            daily["regime"].append(row.get("regime"))
    verify_spy(daily)
    prices = {name: None for name in ("SPY", "TLT", "TIP", "GLD")}
    prices["SPY"] = spy_from_curve(equity)
    for name in ("TLT", "TIP", "GLD"):
        prices[name] = month_end_from_adjusted(adjusted.get(name, []))
        if prices[name] is None:
            missing.append(f"serie {name}")
    if prices["SPY"] is None:
        missing.append("serie SPY")
    if overlay and overlay.get("regime"):
        regime = overlay["regime"]
        if regime.get("asOf"):
            regime = {**regime, "asOf": overlay["asOf"]}

    from history_panel import cagr, max_drawdown, sharpe, slice_period, volatility

    if overlay and overlay.get("asOf"):
        for row in periods:
            if row["period"] in {"full", "out_of_sample", "post_2023"} and row["end"] < overlay["asOf"]:
                row["end"] = overlay["asOf"]
    books = {"strategy": daily["model"], "spy": daily["spy"], "balanced_60_40": daily["balanced"]}
    for row in periods:
        series = books.get(row["book"])
        if series is None:
            continue
        window = slice_period(daily["dates"], series, row["start"], row["end"])
        if len(window) < 2:
            continue
        row["cagr"] = cagr(window)
        row["volatility"] = volatility(window)
        row["sharpe"] = sharpe(window)
        row["maxDrawdown"] = max_drawdown(window)

    snapshot = {
        "meta": {
            "run": "3",
            "asOf": (overlay or {}).get("asOf") or (regime["asOf"] if regime else None),
            "session": (overlay or {}).get("session") or "chiusura",
            "equityCurveMd5": md5(RESULTS / "equity_curves.csv"),
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updatedAt": (overlay or {}).get("updatedAt") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "missing": missing,
        },
        "sentiment": (overlay or {}).get("sentiment"),
        "regime": regime,
        "periods": periods,
        "focusPeriods": list(PERIODS),
        "equityMonthly": equity,
        "regimesMonthly": regimes,
        "prices": prices,
        "daily": {
            "dates": daily["dates"],
            "model": daily["model"],
            "spy": daily["spy"],
            "balanced": daily["balanced"],
            "TLT": daily["TLT"],
            "TIP": daily["TIP"],
            "GLD": daily["GLD"],
            "rebalance": daily["rebalance"],
            "regime": daily["regime"],
            "coverage": daily["coverage"],
        },
        "notes": {
            "cash": "La liquidità rende zero, come nella simulazione della Run 3.",
            "costs": (
                "L'analisi dell'esercizio ribilancia a fine mese, all'ultimo giorno di borsa del mese, "
                "e applica 10 punti base one-way sul turnover. Si parte già investiti: un esercizio "
                "100% SPY non paga un costo iniziale e coincide con il benchmark SPY della Run 3."
            ),
            "prices": (
                "SPY, il modello e il 60/40 sono le curve giornaliere della Run 3. "
                "TLT, TIP e GLD sono gli adjusted close di Yahoo Finance, allineati a quel calendario."
            ),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    strategy_full = next(
        (row["cagr"] for row in periods if row["period"] == "full" and row["book"] == "strategy"),
        None,
    )
    print(f"scritto {OUT}")
    print(f"mesi curva {len(equity)} regimi {len(regimes)} periodi {len(periods)}")
    print(f"strategy full CAGR {strategy_full}")
    print(f"mancanti: {', '.join(missing) if missing else 'nessuno'}")
    if strategy_full is None:
        raise SystemExit("period_metrics senza il CAGR full del modello")


if __name__ == "__main__":
    main()
