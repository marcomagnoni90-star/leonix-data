"""Daily total-return panel for the portfolio analysis.

SPY, the model and the 60/40 book are the Run 3 equity curve, so a 100% SPY
exercise uses the same daily returns as the published benchmark.

TLT, TIP and GLD are Yahoo Finance adjusted closes (split and dividend
adjusted). No API key. A missing print stays null.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
CURVE = ROOT / "results_v3" / "equity_curves.csv"
REGIMES = ROOT / "results_v3" / "regimes_monthly.csv"
METRICS = ROOT / "results_v3" / "period_metrics.csv"
CACHE = Path(__file__).resolve().parents[1] / "src" / "data" / "adjusted_closes.json"

TICKERS = ("TLT", "TIP", "GLD")
NY = ZoneInfo("America/New_York")
COST = 0.001  # 10 bp one-way, same as the backtest
TRADING_DAYS = 252


def _get(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def download_adjusted(ticker: str, period1: int = 1136073600) -> list[dict]:
    period2 = int(time.time()) + 86400
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{urllib.parse.quote(ticker, safe='')}?period1={period1}&period2={period2}&interval=1d&events=div%7Csplit"
    )
    payload = _get(url)
    result = payload["chart"]["result"][0]
    stamps = result["timestamp"]
    closes = result["indicators"]["adjclose"][0]["adjclose"]
    rows = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp, NY).date().isoformat()
        rows.append({"date": day, "adjclose": float(close)})
    rows.sort(key=lambda row: row["date"])
    # Keep the last print if Yahoo repeats a calendar day.
    dedup: dict[str, dict] = {}
    for row in rows:
        dedup[row["date"]] = row
    return [dedup[key] for key in sorted(dedup)]


def save_adjusted_cache() -> dict[str, list[dict]]:
    series = {}
    for ticker in TICKERS:
        series[ticker] = download_adjusted(ticker)
        print(f"{ticker} {len(series[ticker])} adjusted closes, {series[ticker][0]['date']} → {series[ticker][-1]['date']}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(
        json.dumps(
            {
                "source": "Yahoo Finance chart API, adjusted close",
                "downloadedAt": datetime.now(NY).date().isoformat(),
                "series": series,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return series


def load_adjusted_cache() -> dict[str, list[dict]]:
    if not CACHE.is_file():
        return save_adjusted_cache()
    payload = json.loads(CACHE.read_text(encoding="utf-8"))
    return payload["series"]


def read_curve() -> list[dict]:
    import csv

    rows = []
    with CURVE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            date = (row.get("") or row.get("date") or "")[:10]
            if not date:
                continue

            def num(key: str) -> float | None:
                text = (row.get(key) or "").strip()
                if text == "":
                    return None
                return float(text)

            rows.append(
                {
                    "date": date,
                    "model": num("strategy"),
                    "spy": num("spy"),
                    "balanced": num("balanced_60_40"),
                }
            )
    return rows


def wealth_to_returns(levels: list[float | None]) -> list[float | None]:
    """Invert compound(): the first level is 1 + r0, then each step is a ratio."""
    out: list[float | None] = [None] * len(levels)
    if levels and levels[0] is not None:
        out[0] = levels[0] - 1.0
    for index in range(1, len(levels)):
        previous, current = levels[index - 1], levels[index]
        if previous in (None, 0) or current is None:
            out[index] = None
        else:
            out[index] = current / previous - 1.0
    return out


def price_returns(rows: list[dict]) -> dict[str, float]:
    out = {}
    previous = None
    for row in rows:
        price = row["adjclose"]
        if previous not in (None, 0):
            out[row["date"]] = price / previous - 1.0
        previous = price
    return out


def month_ends(dates: list[str]) -> set[str]:
    last: dict[str, str] = {}
    for date in dates:
        last[date[:7]] = date
    return set(last.values())


def read_regimes() -> dict[str, str]:
    import csv

    out = {}
    if not REGIMES.is_file():
        return out
    with REGIMES.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            out[row["date"][:10]] = row.get("regime") or ""
    return out


def build_daily(series: dict[str, list[dict]]) -> dict:
    curve = read_curve()
    dates = [row["date"] for row in curve]
    ends = month_ends(dates)
    regimes = read_regimes()
    mapped = {name: price_returns(rows) for name, rows in series.items()}
    daily = {
        "dates": dates,
        "model": wealth_to_returns([row["model"] for row in curve]),
        "spy": wealth_to_returns([row["spy"] for row in curve]),
        "balanced": wealth_to_returns([row["balanced"] for row in curve]),
        "rebalance": [date in ends for date in dates],
        "regime": [regimes.get(date) for date in dates],
    }
    coverage = {}
    for name in TICKERS:
        values = [mapped.get(name, {}).get(date) for date in dates]
        daily[name] = values
        present = [date for date, value in zip(dates, values) if value is not None]
        coverage[name] = {
            "start": present[0] if present else None,
            "end": present[-1] if present else None,
            "days": len(present),
        }
    daily["coverage"] = coverage
    return daily


def _clean(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None and math.isfinite(value)]


def cagr(values: list[float]) -> float:
    if not values:
        return float("nan")
    growth = 1.0
    for value in values:
        growth *= 1.0 + value
    years = len(values) / TRADING_DAYS
    if years <= 0 or growth <= 0:
        return float("nan")
    return growth ** (1.0 / years) - 1.0


def volatility(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def sharpe(values: list[float]) -> float:
    if len(values) < 2:
        return float("nan")
    mean = sum(values) / len(values)
    var = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    std = math.sqrt(var)
    if std == 0:
        return float("nan")
    return mean / std * math.sqrt(TRADING_DAYS)


def max_drawdown(values: list[float]) -> float:
    level = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        level *= 1.0 + value
        peak = max(peak, level)
        worst = min(worst, level / peak - 1.0)
    return worst


def slice_period(dates: list[str], values: list[float | None], start: str, end: str) -> list[float]:
    chosen = []
    for date, value in zip(dates, values):
        if start <= date <= end and value is not None and math.isfinite(value):
            chosen.append(value)
    return chosen


def one_way(previous: dict[str, float], target: dict[str, float]) -> float:
    keys = set(previous) | set(target)
    traded = sum(abs(target.get(key, 0.0) - previous.get(key, 0.0)) for key in keys)
    return 0.5 * traded


def simulate(weights: dict[str, float], daily: dict) -> list[float | None]:
    """Month-end rebalance, 10 bp on one-way turnover, cash yield zero.

    The book starts already at the target weights, so a constant 100% SPY
    book has no turnover and matches the published SPY returns.
    """
    names = ["SPY", "TLT", "TIP", "GLD", "CASH"]
    target = {name: float(weights.get(name, 0.0)) for name in names}
    total = sum(target.values())
    if total <= 0:
        return []
    target = {name: value / total for name, value in target.items()}
    held = dict(target)
    dates: list[str] = daily["dates"]
    out: list[float | None] = []
    for index, date in enumerate(dates):
        growth: dict[str, float] = {}
        portfolio_return = 0.0
        blocked = False
        for name, weight in held.items():
            if name == "CASH":
                change = 0.0
            else:
                column = "spy" if name == "SPY" else name
                change = daily[column][index]
                if change is None and weight > 1e-12:
                    blocked = True
                    break
                change = 0.0 if change is None else float(change)
            portfolio_return += weight * change
            growth[name] = weight * (1.0 + change)
        if blocked:
            out.append(None)
            continue
        gross = sum(growth.values())
        drifted = {name: value / gross for name, value in growth.items()} if gross > 0 else held
        cost = 0.0
        if daily["rebalance"][index]:
            cost = one_way(drifted, target) * COST
            held = dict(target)
        else:
            held = drifted
        out.append((1.0 + portfolio_return) * (1.0 - cost) - 1.0)
    return out


def verify_spy(daily: dict, frozen_end: str = "2026-11-05") -> None:
    """100% SPY equals the snapshot's SPY column, and frozen periods still match Run 3."""
    import csv

    simulated = simulate({"SPY": 1.0}, daily)
    worst = 0.0
    for got, published in zip(simulated, daily["spy"]):
        if got is None and published is None:
            continue
        if got is None or published is None or abs(got - published) > 1e-12:
            raise SystemExit("la serie 100% SPY non coincide con la colonna spy")
    published_rows = {}
    with METRICS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["book"] == "spy" and row["end"] <= frozen_end:
                published_rows[row["period"]] = row
    for period, row in published_rows.items():
        window = slice_period(daily["dates"], simulated, row["start"], row["end"])
        checks = {
            "cagr": cagr(window),
            "volatility": volatility(window),
            "sharpe": sharpe(window),
            "max_drawdown": max_drawdown(window),
        }
        for name, value in checks.items():
            gap = abs(value - float(row[name]))
            worst = max(worst, gap)
            if gap > 1e-8:
                raise SystemExit(f"SPY {period} {name} gap {gap}")
    print(f"SPY_CHECK_OK max abs gap {worst:.3e}")
