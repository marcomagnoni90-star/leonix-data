from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import MOMENTUM_WINDOWS


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.to_datetime(out.index)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def _pct(series: pd.Series, periods: int) -> pd.Series:
    return (
        pd.to_numeric(series, errors="coerce")
        .pct_change(periods=periods, fill_method=None)
        .replace([np.inf, -np.inf], np.nan)
    )


def _trend(series: pd.Series, periods: int) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    ema = s.ewm(span=periods, adjust=False, min_periods=max(5, periods // 2)).mean()
    return (s / ema - 1.0).replace([np.inf, -np.inf], np.nan)


def _vol(series: pd.Series, periods: int = 21) -> pd.Series:
    r = pd.to_numeric(series, errors="coerce").pct_change(fill_method=None)
    return r.rolling(periods, min_periods=max(5, periods // 2)).std() * np.sqrt(252)


def _downside_vol(series: pd.Series, periods: int = 21) -> pd.Series:
    r = pd.to_numeric(series, errors="coerce").pct_change(fill_method=None)
    downside = r.where(r < 0)
    return downside.rolling(periods, min_periods=max(5, periods // 2)).std() * np.sqrt(252)


def _drawdown(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return s / s.cummax() - 1.0


def build_all_signals(
    asset_prices: pd.DataFrame,
    macro_prices: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    asset_prices = _clean(asset_prices)
    macro_prices = _clean(macro_prices)

    asset_rows = {}

    for col in asset_prices.columns:
        s = asset_prices[col]
        clean_s = pd.to_numeric(s, errors="coerce").dropna()

        if clean_s.empty:
            continue

        asset_rows[col] = {
            "price": float(clean_s.iloc[-1]),
            "momentum_1M": float(_pct(s, MOMENTUM_WINDOWS["1M"]).iloc[-1]),
            "momentum_3M": float(_pct(s, MOMENTUM_WINDOWS["3M"]).iloc[-1]),
            "momentum_6M": float(_pct(s, MOMENTUM_WINDOWS["6M"]).iloc[-1]),
            "trend_20d": float(_trend(s, 20).iloc[-1]),
            "vol_1M": float(_vol(s, 21).iloc[-1]),
            "downside_vol_1M": float(_downside_vol(s, 21).iloc[-1]),
            "drawdown": float(_drawdown(s).iloc[-1]),
        }

    asset_signal_df = pd.DataFrame(asset_rows).T
    asset_signal_df = asset_signal_df.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    macro_rows = {}

    for col in macro_prices.columns:
        s = macro_prices[col]
        clean_s = pd.to_numeric(s, errors="coerce").dropna()

        if clean_s.empty:
            continue

        macro_rows[col] = {
            "level": float(clean_s.iloc[-1]),
            "return_1M": float(_pct(s, 21).iloc[-1]),
            "return_3M": float(_pct(s, 63).iloc[-1]),
            "trend_20d": float(_trend(s, 20).iloc[-1]),
            "trend_60d": float(_trend(s, 60).iloc[-1]),
            "vol_1M": float(_vol(s, 21).iloc[-1]),
        }

    macro_signal_df = pd.DataFrame(macro_rows).T
    macro_signal_df = macro_signal_df.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    history_frames = []

    asset_ret_1d = asset_prices.pct_change(fill_method=None)
    asset_ret_1d = asset_ret_1d.rename(columns=lambda c: f"{c}_ret_1d")
    history_frames.append(asset_ret_1d)

    for col in asset_prices.columns:
        s = asset_prices[col]
        history_frames.append(
            pd.DataFrame(
                {
                    f"{col}_mom_1m": _pct(s, 21),
                    f"{col}_mom_3m": _pct(s, 63),
                    f"{col}_mom_6m": _pct(s, 126),
                    f"{col}_vol_1m": _vol(s, 21),
                    f"{col}_dd": _drawdown(s),
                },
                index=asset_prices.index,
            )
        )

    for col in macro_prices.columns:
        s = macro_prices[col]
        history_frames.append(
            pd.DataFrame(
                {
                    f"{col}_trend_20d": _trend(s, 20),
                    f"{col}_ret_1m": _pct(s, 21),
                    f"{col}_ret_3m": _pct(s, 63),
                },
                index=macro_prices.index,
            )
        )

    history = pd.concat(history_frames, axis=1)
    history = history.sort_index()
    history = history.replace([np.inf, -np.inf], np.nan)

    return asset_signal_df, macro_signal_df, history.copy()


def build_liquidity_signal_table(liquidity_df: pd.DataFrame) -> pd.DataFrame:
    if liquidity_df is None or liquidity_df.empty:
        return pd.DataFrame()

    rows = {}

    for col in liquidity_df.columns:
        s = pd.to_numeric(liquidity_df[col], errors="coerce")
        clean_s = s.dropna()

        if clean_s.empty:
            continue

        rows[col] = {
            "level": float(clean_s.iloc[-1]),
            "trend_12w": float(s.pct_change(60, fill_method=None).iloc[-1]),
            "yoy_change": float(s.pct_change(252, fill_method=None).iloc[-1]),
        }

    out = pd.DataFrame(rows).T
    return out.replace([np.inf, -np.inf], np.nan).fillna(0.0)
