from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a local .env file without overriding the process environment."""
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_load_dotenv_file(PROJECT_ROOT / ".env")
DATA_DIR = PROJECT_ROOT / "data"
FRED_CACHE_DIR = Path(os.getenv("FRED_CACHE_DIR", str(DATA_DIR / "fred")))
OUTPUT_DIR = PROJECT_ROOT / "outputs"

START_DATE = os.getenv("MACRO_COPILOT_START_DATE", "2006-01-01")

ASSETS = [
    "SPY",
    "ACWI",
    "TLT",
    "GLD",
    "DBC",
    "EFA",
    "EEM",
    "VGK",
    "EWJ",
    "FXI",
    "HYG",
    "LQD",
    "TIP",
    "UUP",
    "XLK",
    "XLF",
    "XLE",
    "XLI",
    "XLU",
    "XLV",
    "XLY",
    "XLP",
    "XLB",
    "XLRE",
]

BENCHMARK = "SPY"

MACRO_TICKERS = {
    "VIX": "^VIX",
    "VVIX": "^VVIX",

    "DXY": "DX-Y.NYB",
    "UUP": "UUP",
    "FXE": "FXE",
    "FXY": "FXY",
    "CYB": "CYB",

    "US10Y": "^TNX",
    "US30Y": "^TYX",
    "TLT": "TLT",
    "TIP": "TIP",

    "US_EQUITY": "SPY",
    "WORLD_EQUITY": "ACWI",
    "EUROPE_EQUITY": "VGK",
    "JAPAN_EQUITY": "EWJ",
    "EM_EQUITY": "EEM",
    "CHINA_EQUITY": "FXI",

    "HY_CREDIT": "HYG",
    "IG_CREDIT": "LQD",

    "GOLD": "GLD",
    "COMMODITIES": "DBC",
    "OIL": "USO",

    "TECH": "XLK",
    "FINANCIALS": "XLF",
    "ENERGY": "XLE",
    "INDUSTRIALS": "XLI",
    "UTILITIES": "XLU",
    "HEALTHCARE": "XLV",
    "CONSUMER_DISC": "XLY",
    "CONSUMER_STAPLES": "XLP",
    "MATERIALS": "XLB",
    "REAL_ESTATE": "XLRE",
}

FRED_SERIES = {
    "FED_BALANCE_SHEET": "WALCL",
    "M2": "M2SL",

    "US_CPI": "CPIAUCSL",
    "US_CORE_CPI": "CPILFESL",
    "US_PPI": "PPIACO",
    "US_BREAKEVEN_10Y": "T10YIE",

    "FED_FUNDS": "FEDFUNDS",
    "US_2Y": "DGS2",
    "US_10Y": "DGS10",
    "US_30Y": "DGS30",

    "UNEMPLOYMENT": "UNRATE",
    "INDUSTRIAL_PRODUCTION": "INDPRO",
    "RETAIL_SALES": "RSAFS",

    "HY_SPREAD": "BAMLH0A0HYM2",
    "IG_SPREAD": "BAMLC0A0CM",

    "NFCI": "NFCI",
}

MOMENTUM_WINDOWS = {
    "1M": 21,
    "3M": 63,
    "6M": 126,
}

ASSET_UNIVERSE = ASSETS + ["CASH"]

REGIME_ORDER = [
    "Panic",
    "Crash",
    "Stress",
    "Deleveraging",
    "Neutral",
    "Expansion",
    "Bubble Risk",
]

REGIME_SEVERITY = {
    "Expansion": 0,
    "Neutral": 1,
    "Bubble Risk": 2,
    "Deleveraging": 3,
    "Stress": 4,
    "Crash": 5,
    "Panic": 6,
}

REGIME_COMPATIBILITY = {
    "Expansion": {"SPY": 0.32, "ACWI": 0.26, "TLT": -0.10, "GLD": 0.03, "DBC": 0.08, "CASH": -0.04},
    "Bubble Risk": {"SPY": 0.24, "ACWI": 0.20, "TLT": -0.14, "GLD": 0.04, "DBC": 0.12, "CASH": -0.03},
    "Neutral": {"SPY": 0.14, "ACWI": 0.12, "TLT": 0.03, "GLD": 0.03, "DBC": 0.02, "CASH": 0.00},
    "Deleveraging": {"SPY": -0.06, "ACWI": -0.06, "TLT": 0.08, "GLD": 0.06, "DBC": -0.02, "CASH": 0.04},
    "Stress": {"SPY": -0.14, "ACWI": -0.14, "TLT": 0.14, "GLD": 0.10, "DBC": -0.04, "CASH": 0.08},
    "Crash": {"SPY": -0.24, "ACWI": -0.24, "TLT": 0.18, "GLD": 0.14, "DBC": -0.08, "CASH": 0.12},
    "Panic": {"SPY": -0.34, "ACWI": -0.34, "TLT": 0.16, "GLD": 0.16, "DBC": -0.12, "CASH": 0.18},
}

REGIME_CONSTRAINTS = {
    "Expansion": {"top_n": 3, "max_weight": 0.52, "cash_floor": 0.00, "equity_floor": 0.55},
    "Bubble Risk": {"top_n": 4, "max_weight": 0.42, "cash_floor": 0.00, "equity_floor": 0.45},
    "Neutral": {"top_n": 4, "max_weight": 0.40, "cash_floor": 0.00, "equity_floor": 0.35},
    "Deleveraging": {"top_n": 4, "max_weight": 0.38, "cash_floor": 0.02, "equity_floor": 0.20},
    "Stress": {"top_n": 4, "max_weight": 0.40, "cash_floor": 0.04, "equity_floor": 0.10},
    "Crash": {"top_n": 4, "max_weight": 0.42, "cash_floor": 0.08, "equity_floor": 0.00},
    "Panic": {"top_n": 4, "max_weight": 0.45, "cash_floor": 0.12, "equity_floor": 0.00},
}

IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "11"))

IBKR_ASSET_MAP = {
    asset: {"symbol": asset, "sec_type": "STK", "exchange": "SMART", "currency": "USD"}
    for asset in ASSETS
}

def get_fred_api_key(required: bool = False) -> str | None:
    api_key = os.getenv("FRED_API_KEY")
    if required and not api_key:
        raise EnvironmentError("FRED_API_KEY is not set.")
    return api_key
