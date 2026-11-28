"""Download two FRED series for the macro model. The key is never printed.

Writes data/macro_cache/STLFSI4.csv and CP0000EZ19M086NEST.csv when
FRED_API_KEY is set. Without the key the script exits quietly: the public
FRED CSV host does not answer from the usual runner, and Eurostat remains
the euro-area fallback.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

SERIES = ("STLFSI4", "CP0000EZ19M086NEST")
ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "data" / "macro_cache"


def _download(series_id: str, key: str) -> None:
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={urllib.parse.quote(series_id)}&file_type=json&api_key={urllib.parse.quote(key)}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "LeonixData/1.0"})
    with urllib.request.urlopen(request, timeout=40) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = ["observation_date,value"]
    for item in payload.get("observations") or []:
        value = item.get("value")
        if value in (None, "", "."):
            continue
        rows.append(f"{item.get('date','')},{value}")
    if len(rows) < 3:
        print(f"FRED {series_id} vuota")
        return
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / f"{series_id}.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"FRED {series_id} ok n={len(rows) - 1} ultimo={rows[-1].split(',')[0]}")


def main() -> None:
    key = os.environ.get("FRED_API_KEY") or ""
    if not key:
        print("FRED_API_KEY assente: STLFSI4 non scaricato")
        return
    for series_id in SERIES:
        try:
            _download(series_id, key)
        except Exception as exc:
            print(f"FRED {series_id} non disponibile ({type(exc).__name__})")


if __name__ == "__main__":
    main()
