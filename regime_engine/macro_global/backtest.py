"""Monthly backtest of the global macro weights.

The position held in month t uses the regime dated t-1, so the month's own
return is not an input. Cash yields zero. Cost is 10 bp one-way.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config.macro_global import ALLOCATION, ONE_WAY_COST


def month_end_prices(prices: dict[str, pd.Series]) -> pd.DataFrame:
    frames = []
    for ticker, series in prices.items():
        monthly = series.resample("ME").last().dropna()
        monthly.index = monthly.index.to_period("M").to_timestamp("M")
        monthly.name = ticker
        frames.append(monthly)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).sort_index()


def run_backtest(panel: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    tickers = [asset for asset in sorted({key for book in ALLOCATION.values() for key in book}) if asset != "CASH"]
    returns = prices[tickers].pct_change()
    rows = []
    previous = None
    dates = list(panel.index.sort_values())
    for left, right in zip(dates, dates[1:]):
        if right not in returns.index:
            continue
        weights = {asset: float(panel.at[left, asset]) for asset in tickers if asset in panel.columns and pd.notna(panel.at[left, asset])}
        cash = float(panel.at[left, "CASH"]) if "CASH" in panel.columns and pd.notna(panel.at[left, "CASH"]) else 0.0
        missing = [asset for asset, weight in weights.items() if weight > 1e-8 and (asset not in returns.columns or pd.isna(returns.at[right, asset]))]
        if missing:
            continue
        gross = cash * 0.0
        for asset, weight in weights.items():
            gross += weight * float(returns.at[right, asset])
        current = {**weights, "CASH": cash}
        if previous is None:
            turnover = 0.0
        else:
            names = set(previous) | set(current)
            turnover = 0.5 * sum(abs(current.get(name, 0.0) - previous.get(name, 0.0)) for name in names)
        net = gross - turnover * ONE_WAY_COST
        rows.append({"date": right, "net": net, "gross": gross, "turnover": turnover, "regime": panel.at[left, "regime"]})
        previous = current
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def performance(returns: pd.Series) -> dict[str, float | None]:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 3:
        return {"cagr": None, "vol": None, "max_drawdown": None, "sharpe": None, "calmar": None, "months": int(len(clean))}
    wealth = (1.0 + clean).cumprod()
    years = len(clean) / 12.0
    cagr = float(wealth.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 and wealth.iloc[-1] > 0 else None
    vol = float(clean.std(ddof=1) * np.sqrt(12.0))
    peak = wealth.cummax()
    drawdown = wealth / peak - 1.0
    max_dd = float(drawdown.min())
    sharpe = float(clean.mean() / clean.std(ddof=1) * np.sqrt(12.0)) if clean.std(ddof=1) > 0 else None
    calmar = float(cagr / abs(max_dd)) if cagr is not None and max_dd < 0 else None
    return {"cagr": cagr, "vol": vol, "max_drawdown": max_dd, "sharpe": sharpe, "calmar": calmar, "months": int(len(clean))}


def buy_and_hold(prices: pd.Series) -> pd.Series:
    monthly = prices.resample("ME").last().dropna()
    monthly.index = monthly.index.to_period("M").to_timestamp("M")
    return monthly.pct_change()


def fixed_mix(prices: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    returns = prices.pct_change()
    out = pd.Series(0.0, index=returns.index)
    for asset, weight in weights.items():
        if asset == "CASH":
            continue
        out = out.add(returns[asset] * weight, fill_value=0.0)
    return out.dropna()
