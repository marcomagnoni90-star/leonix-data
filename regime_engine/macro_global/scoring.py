"""Causal z-scores. A change after date t cannot move the score at t."""

from __future__ import annotations

import pandas as pd

WINDOW = 120
MIN_OBS = 60
WINSOR = 3.0
# Real neutral rate, in percent. Fixed before either sample window.
# One percentage point of stance (real rate minus this neutral) is one score unit.
REAL_NEUTRAL = 0.5
STANCE_SCALE = 1.0
# How long the last print stays usable, in months, by the series frequency.
# Daily and weekly series are already month-end, so the limit is short.
STALE_LIMIT_MONTHS = {"daily": 2, "weekly": 2, "monthly": 4, "quarterly": 7}


def publish(series: pd.Series, lag_months: int) -> pd.Series:
    """Move each print forward by its publication lag, onto month-ends."""
    clean = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if clean.empty:
        return clean
    monthly = clean.resample("ME").last().dropna()
    if lag_months:
        monthly.index = (monthly.index.to_period("M") + int(lag_months)).to_timestamp("M")
    else:
        monthly.index = monthly.index.to_period("M").to_timestamp("M")
    return monthly[~monthly.index.duplicated(keep="last")]


def transform(series: pd.Series, kind: str) -> pd.Series:
    if kind == "level":
        return series
    if kind == "yoy":
        return series.pct_change(12)
    if kind == "diff6":
        return series.diff(6)
    raise ValueError(kind)


def rolling_z(series: pd.Series, window: int = WINDOW, min_obs: int = MIN_OBS) -> pd.Series:
    """Z-score against the trailing window, including the current point only."""
    values = pd.to_numeric(series, errors="coerce")
    mean = values.rolling(window, min_periods=min_obs).mean()
    std = values.rolling(window, min_periods=min_obs).std(ddof=1)
    z = (values - mean) / std
    z = z.where(std > 0)
    return z.clip(-WINSOR, WINSOR)


def stale_limit(frequency: str) -> int:
    return STALE_LIMIT_MONTHS.get(frequency, STALE_LIMIT_MONTHS["monthly"])


def hold_last(series: pd.Series, limit: int) -> pd.Series:
    """Carry the last print forward for at most ``limit`` months.

    Internal gaps and the stretch after the last print are filled only from
    earlier observations. A print dated after month t cannot appear at t.
    """
    values = pd.to_numeric(series, errors="coerce").sort_index()
    known = values.dropna()
    if known.empty or limit <= 0:
        return known
    last = known.index.max()
    end = (last.to_period("M") + int(limit)).to_timestamp("M")
    start = values.index.min()
    full = values.reindex(pd.date_range(start, max(pd.Timestamp(values.index.max()), end), freq="ME"))
    return full.ffill(limit=int(limit))


def _regular_months(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").sort_index()
    if values.dropna().empty:
        return values.dropna()
    start = values.dropna().index.min()
    end = values.dropna().index.max()
    return values.reindex(pd.date_range(start, end, freq="ME"))


def variable_components(series: pd.Series, kind: str, sign: int, hold_months: int = 0) -> pd.DataFrame:
    """Level, both z-scores and the signed score. Same formula as ``variable_score``."""
    level = transform(_regular_months(series), kind)
    if hold_months:
        level = hold_last(level, hold_months)
    level_z = rolling_z(level)
    change_z = rolling_z(level.diff(6))
    score = sign * pd.concat([level_z, change_z], axis=1).mean(axis=1, skipna=True)
    return pd.DataFrame({"level": level, "z_level": level_z, "z_change": change_z, "score": score})


def fixed_scale_components(level: pd.Series, sign: int, scale: float = STANCE_SCALE) -> pd.DataFrame:
    """Score a percentage-point gap on a fixed scale, not against its own history."""
    values = pd.to_numeric(level, errors="coerce")
    z_level = (values / scale).clip(-WINSOR, WINSOR)
    z_change = (values.diff(6) / scale).clip(-WINSOR, WINSOR)
    score = sign * pd.concat([z_level, z_change], axis=1).mean(axis=1, skipna=True)
    return pd.DataFrame({"level": values, "z_level": z_level, "z_change": z_change, "score": score})


def is_policy_rate(series_id: str) -> bool:
    """Official policy rate, not a yield spread."""
    if series_id.endswith("_SLOPE") or series_id == "USA_S2":
        return False
    return series_id.endswith("_RATE") or series_id == "EZ_DFR"


def policy_stance_components(rate: pd.Series, inflation_pp: pd.Series | None, sign: int) -> pd.DataFrame:
    """Stance = policy rate − inflation − a fixed 0.5% real neutral.

    The result is in percentage points and is divided by ``STANCE_SCALE``.
    It is not a z-score against the last decade, so a nominal rate of 2.5%
    is not called tight merely because earlier years were negative. The
    near-zero-variance rule is not used: the scale is fixed.
    """
    rate = pd.to_numeric(rate, errors="coerce")
    if inflation_pp is None:
        empty = pd.Series(index=rate.index, dtype=float)
        frame = fixed_scale_components(empty, sign)
        frame["rule"] = "no_inflation"
        return frame
    inflation = pd.to_numeric(inflation_pp, errors="coerce").reindex(rate.index)
    stance = rate - inflation - REAL_NEUTRAL
    frame = fixed_scale_components(stance, sign, STANCE_SCALE)
    frame["rule"] = "real_neutral_0.5"
    return frame


def variable_score(series: pd.Series, kind: str, sign: int) -> pd.Series:
    """Average of the level z-score and the six-month-change z-score.

    The level uses ``kind``. The change is a six-month difference of that
    transformed series, then a trailing z-score. Both are signed so that a
    positive number always means "more of this pillar".
    """
    return variable_components(series, kind, sign)["score"]


def signed_mean(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted sum divided by the sum of absolute weights of the columns present.

    Negative weights subtract. A missing column is dropped rather than treated as zero.
    """
    if frame.empty:
        return pd.Series(dtype=float)
    numer = pd.Series(0.0, index=frame.index)
    denom = pd.Series(0.0, index=frame.index)
    for column, weight in weights.items():
        if column not in frame.columns:
            continue
        values = frame[column]
        present = values.notna()
        numer = numer.add(values.fillna(0.0) * weight, fill_value=0.0)
        denom = denom.add(present.astype(float) * abs(weight), fill_value=0.0)
    return numer.div(denom.where(denom > 0))


def weighted_mean(frame: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Row-wise mean of the columns that are present. Missing weights are dropped."""
    if frame.empty:
        return pd.Series(dtype=float)
    usable = [column for column in frame.columns if column in weights]
    if not usable:
        return pd.Series(index=frame.index, dtype=float)
    values = frame[usable]
    weight = pd.Series({column: weights[column] for column in usable})
    mask = values.notna()
    weighted = values.mul(weight, axis=1)
    denom = mask.mul(weight, axis=1).sum(axis=1)
    numer = weighted.sum(axis=1, min_count=1)
    out = numer / denom.where(denom > 0)
    return pd.to_numeric(out, errors="coerce")
