"""Offline FRED cache.

Point-in-time mode indexes each vintage by the day it was first published.
A revised ``*_latest.csv`` value is never copied onto an earlier date.
Daily series without an ALFRED file are lagged by one business day.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import FRED_CACHE_DIR


def read_fred_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, na_values=["."])
    if "date" not in frame.columns:
        raise ValueError(f"{path} is missing a date column.")
    frame["date"] = pd.to_datetime(frame["date"])
    if "first_release_date" in frame.columns:
        frame["first_release_date"] = pd.to_datetime(frame["first_release_date"])
    return frame


def _value_column(frame: pd.DataFrame, series_id: str) -> str:
    if series_id in frame.columns:
        return series_id
    reserved = {"date", "first_release_date"}
    candidates = [column for column in frame.columns if column not in reserved]
    if not candidates:
        raise ValueError(f"No value column for {series_id}.")
    return candidates[-1]


def _series_from_frame(frame: pd.DataFrame, series_id: str, index_column: str) -> pd.Series:
    values = pd.to_numeric(frame[_value_column(frame, series_id)], errors="coerce")
    values.index = pd.DatetimeIndex(frame[index_column])
    values = values.groupby(level=0).last().sort_index()
    return values.dropna()


def load_point_in_time_series(series_id: str, cache_dir: Path | None = None) -> tuple[pd.Series, str]:
    """Return ``(values indexed by the first day they were knowable, source)``."""
    cache_dir = Path(cache_dir) if cache_dir is not None else FRED_CACHE_DIR
    first_release = cache_dir / f"{series_id}_first_release.csv"
    latest = cache_dir / f"{series_id}_latest.csv"

    if first_release.is_file():
        frame = read_fred_csv(first_release)
        series = _series_from_frame(frame, series_id, "first_release_date")
        return series, "first_release"

    if latest.is_file():
        frame = read_fred_csv(latest)
        series = _series_from_frame(frame, series_id, "date")
        # No vintage file: the print dated T is treated as known on the next business day.
        series.index = pd.DatetimeIndex(series.index) + pd.offsets.BDay(1)
        series = series.groupby(level=0).last().sort_index()
        return series, "latest_plus_1_business_day"

    return pd.Series(dtype=float), "missing"


def load_revised_series(series_id: str, cache_dir: Path | None = None) -> pd.Series:
    """Latest vintage indexed by the observation date. This is not point-in-time."""
    cache_dir = Path(cache_dir) if cache_dir is not None else FRED_CACHE_DIR
    latest = cache_dir / f"{series_id}_latest.csv"
    if not latest.is_file():
        return pd.Series(dtype=float)
    frame = read_fred_csv(latest)
    return _series_from_frame(frame, series_id, "date")


def load_cached_fred_frame(
    series_map: dict[str, str],
    cache_dir: Path | None = None,
    *,
    point_in_time: bool,
) -> tuple[pd.DataFrame, list[dict]]:
    cache_dir = Path(cache_dir) if cache_dir is not None else FRED_CACHE_DIR
    frames: list[pd.Series] = []
    coverage: list[dict] = []
    for alias, series_id in series_map.items():
        if point_in_time:
            series, source = load_point_in_time_series(series_id, cache_dir)
        else:
            series = load_revised_series(series_id, cache_dir)
            source = "latest_revised" if not series.empty else "missing"
        if series.empty:
            coverage.append({"alias": alias, "series_id": series_id, "source": "missing", "start": None, "end": None, "rows": 0})
            continue
        series.name = alias
        frames.append(series)
        coverage.append(
            {
                "alias": alias,
                "series_id": series_id,
                "source": source,
                "start": str(series.index.min().date()),
                "end": str(series.index.max().date()),
                "rows": int(series.shape[0]),
            }
        )
    if not frames:
        return pd.DataFrame(), coverage
    out = pd.concat(frames, axis=1).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.attrs["point_in_time"] = point_in_time
    out.attrs["coverage"] = coverage
    return out, coverage


def credit_spread_proxy(prices: pd.DataFrame) -> pd.DataFrame:
    """Positive spread-like levels from ETF prices.

    HY uses the 63-day log underperformance of HYG versus TLT.
    IG uses LQD versus TLT. The centre is a typical OAS level in percent
    so that a 20-day percent change has the same order of magnitude as a
    move in the ICE series. The rolling window uses only past prices.
    """
    out = pd.DataFrame(index=prices.index)

    def _level(credit: str, hedge: str, centre: float, scale: float) -> pd.Series | None:
        if credit not in prices.columns or hedge not in prices.columns:
            return None
        credit_px = pd.to_numeric(prices[credit], errors="coerce")
        hedge_px = pd.to_numeric(prices[hedge], errors="coerce")
        excess = (np.log(hedge_px).diff() - np.log(credit_px).diff()).rolling(63, min_periods=21).sum()
        return (centre + scale * excess).clip(lower=0.30)

    hy = _level("HYG", "TLT", centre=4.5, scale=25.0)
    ig = _level("LQD", "TLT", centre=1.4, scale=12.0)
    if hy is not None:
        out["HY_SPREAD"] = hy
    if ig is not None:
        out["IG_SPREAD"] = ig
    return out


def splice_official_spreads(liquidity: pd.DataFrame, prices: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Use the price proxy until the first official print, then the official series.

    The official sample is not used to rescale the earlier proxy, so a date
    before the ICE history starts cannot see the 2023 level.
    """
    proxy = credit_spread_proxy(prices)
    out = liquidity.copy() if liquidity is not None else pd.DataFrame()
    proxy_index = proxy.dropna(how="all").index
    if len(proxy_index):
        base_index = out.index.union(proxy_index).sort_values() if len(out.index) else proxy_index
        out = out.reindex(base_index)
    info: dict = {}
    for column in ("HY_SPREAD", "IG_SPREAD"):
        if column not in proxy.columns:
            info[column] = {"status": "proxy_unavailable"}
            continue
        official = out[column].dropna() if column in out.columns else pd.Series(dtype=float)
        proxy_series = proxy[column]
        if official.empty:
            out[column] = proxy_series
            info[column] = {"status": "proxy_only"}
            continue
        first = pd.Timestamp(official.index.min())
        combined_index = proxy_series.dropna().index.union(official.index).sort_values()
        combined = proxy_series.reindex(combined_index)
        official_forward = official.reindex(combined_index).ffill()
        combined.loc[combined.index >= first] = official_forward.loc[combined.index >= first]
        out[column] = combined
        proxy_on_join = proxy_series.reindex([first], method="ffill")
        proxy_value = float(proxy_on_join.iloc[0]) if len(proxy_on_join) and pd.notna(proxy_on_join.iloc[0]) else float("nan")
        official_value = float(official.iloc[0])
        overlap = pd.concat(
            [proxy_series.rename("proxy"), official.rename("official")],
            axis=1,
        ).dropna()
        correlation = float(overlap["proxy"].corr(overlap["official"])) if len(overlap) > 20 else float("nan")
        info[column] = {
            "status": "spliced",
            "official_from": str(first.date()),
            "proxy_level_at_join": None if np.isnan(proxy_value) else round(proxy_value, 3),
            "official_level_at_join": round(official_value, 3),
            "overlap_correlation": None if np.isnan(correlation) else round(correlation, 3),
            "overlap_rows": int(len(overlap)),
        }
    if liquidity is not None:
        out.attrs.update(liquidity.attrs)
    out.attrs["credit_spread_proxy"] = info
    return out, info
