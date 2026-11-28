"""Download public series. No API key is read, printed, or stored.

FRED's public CSV is the default. If FRED_API_KEY is present in the
environment the official API is tried first and the CSV remains the
fallback. The key itself is never logged.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
from pathlib import Path

import pandas as pd

CACHE = Path(__file__).resolve().parents[2] / "data" / "macro_cache"
_FRED_CSV_DOWN = False


def _curl(url: str, dest: Path, timeout: int = 90) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "curl", "--http2", "--ipv4", "--compressed", "-fsSL",
            "--retry", "2", "--retry-delay", "1",
            "--max-time", str(timeout),
            "-A", "LeonixResearch/1.0 (macro research)",
            "-o", str(dest),
            url,
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def fetch_fred(series_id: str, refresh: bool = False) -> pd.Series | None:
    """Observations from the FRED API when a key is set, else the public CSV.

    The key is read from the environment and put only in the request. It is
    never logged. If the public CSV host does not answer, later calls skip it.
    """
    global _FRED_CSV_DOWN
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{series_id}.csv"
    if refresh or not dest.is_file():
        key = os.environ.get("FRED_API_KEY")
        ok = False
        if key:
            url = (
                "https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={urllib.parse.quote(series_id)}&file_type=json&api_key={urllib.parse.quote(key)}"
            )
            raw = CACHE / f"{series_id}.json"
            ok = _curl(url, raw, timeout=40)
            if ok:
                try:
                    payload = json.loads(raw.read_text(encoding="utf-8"))
                    rows = ["observation_date,value"]
                    for item in payload.get("observations") or []:
                        rows.append(f"{item.get('date','')},{item.get('value','')}")
                    dest.write_text("\n".join(rows) + "\n", encoding="utf-8")
                except (OSError, json.JSONDecodeError):
                    ok = False
            raw.unlink(missing_ok=True)
        if not ok and not (dest.is_file() and dest.stat().st_size > 0):
            if _FRED_CSV_DOWN:
                return None
            url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={urllib.parse.quote(series_id)}"
            if not _curl(url, dest, timeout=20):
                _FRED_CSV_DOWN = True
                return None
    try:
        frame = pd.read_csv(dest)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return None
    if frame.shape[1] < 2:
        return None
    frame.columns = ["date", "value"]
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"]).sort_values("date")
    if frame.empty:
        return None
    series = pd.Series(frame["value"].to_numpy(), index=pd.DatetimeIndex(frame["date"]), name=series_id)
    return series[~series.index.duplicated(keep="last")]


def fetch_yahoo_monthly(ticker: str, refresh: bool = False) -> pd.Series | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    safe = ticker.replace("^", "_").replace("=", "_")
    dest = CACHE / f"yahoo_{safe}.json"
    if refresh or not dest.is_file():
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(ticker)}?period1=0&period2=2000000000&interval=1mo"
        )
        if not _curl(url, dest):
            return None
    try:
        payload = json.loads(dest.read_text(encoding="utf-8"))
        result = payload["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["adjclose"][0]["adjclose"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, IndexError):
        return None
    rows = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue
        rows.append((pd.to_datetime(stamp, unit="s", utc=True).tz_convert(None), float(close)))
    if not rows:
        return None
    series = pd.Series({date: value for date, value in rows}, name=ticker).sort_index()
    return series[~series.index.duplicated(keep="last")]


def fetch_world_bank_gdp() -> dict[str, float]:
    """Latest nominal GDP in current USD. Empty dict if the API does not answer."""
    codes = "US;XC;GB;CH;JP;CN;BR;IN;ID;MX;TR;ZA;1W"
    url = (
        "https://api.worldbank.org/v2/country/"
        f"{codes}/indicator/NY.GDP.MKTP.CD?format=json&mrv=1&per_page=200"
    )
    dest = CACHE / "world_bank_gdp.json"
    if not _curl(url, dest):
        return {}
    try:
        payload = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    out: dict[str, float] = {}
    for row in rows or []:
        value = row.get("value")
        if value is None:
            continue
        code = row.get("countryiso3code") or (row.get("country") or {}).get("id")
        if code:
            out[str(code)] = float(value)
    return out
