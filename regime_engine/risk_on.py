"""Risk-on overlay for a healthy equity uptrend.

The thresholds below are round market conventions, written down before the
replay. They were not searched on returns.

A healthy uptrend is: price above its 200-day average, positive 6-month and
12-month momentum, contained volatility, drawdown short of a correction, and
credit not widening. In that state the illustrative map may lift equity up to
90 percent. A real deterioration (stress-level VIX, a deeper drawdown, a
negative 6-month trend, or credit stress) drops the latch and the defensive
map is used in full. Between those two, hysteresis keeps the last risk-on
floor so one quiet month does not cut equity back to the neutral map.
"""

from __future__ import annotations

import pandas as pd

MA_DAYS = 200
MOM_6M_DAYS = 126
MOM_12M_DAYS = 252
VIX_CONTAINED = 22.0
VIX_DETERIORATION = 25.0
DRAWDOWN_HEALTHY = -0.08
DRAWDOWN_DETERIORATION = -0.12
HY_CRISIS = 6.0
HY_WIDEN = 0.50
EQUITY_CEILING = 0.90
EQUITY_FLOOR_LATCHED = 0.80
SENTIMENT_WEIGHT_CAP = 0.05


def _f(obs: dict, key: str) -> float | None:
    value = obs.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def trend_observations(spy: pd.Series | None) -> dict:
    """Point-in-time trend facts from a close series. Missing history stays None."""
    if spy is None or len(spy) == 0:
        return {"above_ma200": None, "spy_6m": None, "spy_12m": None, "spy_drawdown": None}
    series = pd.to_numeric(spy, errors="coerce").dropna()
    if series.empty:
        return {"above_ma200": None, "spy_6m": None, "spy_12m": None, "spy_drawdown": None}
    last = float(series.iloc[-1])
    above = None
    if len(series) >= MA_DAYS:
        above = bool(last > float(series.iloc[-MA_DAYS:].mean()))
    def momentum(days: int) -> float | None:
        if len(series) <= days:
            return None
        base = float(series.iloc[-1 - days])
        if base == 0:
            return None
        return last / base - 1.0
    peak = float(series.max())
    drawdown = last / peak - 1.0 if peak else None
    return {
        "above_ma200": above,
        "spy_6m": momentum(MOM_6M_DAYS),
        "spy_12m": momentum(MOM_12M_DAYS),
        "spy_drawdown": drawdown,
    }


def healthy_uptrend(obs: dict) -> bool:
    if obs.get("above_ma200") is not True:
        return False
    spy_6m = _f(obs, "spy_6m")
    spy_12m = _f(obs, "spy_12m")
    if spy_6m is None or spy_12m is None or spy_6m <= 0.0 or spy_12m <= 0.0:
        return False
    vix = _f(obs, "vix")
    if vix is None or vix >= VIX_CONTAINED:
        return False
    drawdown = _f(obs, "spy_drawdown")
    if drawdown is None or drawdown <= DRAWDOWN_HEALTHY:
        return False
    hy = _f(obs, "hy_spread")
    if hy is not None and hy >= HY_CRISIS:
        return False
    hy_change = _f(obs, "hy_change_3m")
    if hy_change is not None and hy_change > 0.0:
        return False
    return True


def deteriorated(obs: dict) -> bool:
    vix = _f(obs, "vix")
    if vix is not None and vix >= VIX_DETERIORATION:
        return True
    drawdown = _f(obs, "spy_drawdown")
    if drawdown is not None and drawdown <= DRAWDOWN_DETERIORATION:
        return True
    spy_6m = _f(obs, "spy_6m")
    if spy_6m is not None and spy_6m < 0.0:
        return True
    hy = _f(obs, "hy_spread")
    if hy is not None and hy >= HY_CRISIS:
        return True
    hy_change = _f(obs, "hy_change_3m")
    if hy_change is not None and hy_change > HY_WIDEN:
        return True
    return False


def equity_of(weights: pd.Series) -> float:
    total = 0.0
    for asset, weight in weights.items():
        if str(asset).upper() in {"SPY", "ACWI"}:
            total += float(weight)
    return total


def _retarget(weights: pd.Series, equity: float) -> pd.Series:
    current = equity_of(weights)
    if current <= 1e-8:
        out = weights.copy()
        out["SPY"] = equity
        rest = max(0.0, 1.0 - equity)
        others = [key for key in out.index if str(key).upper() not in {"SPY", "ACWI"}]
        if others and rest > 0:
            share = rest / len(others)
            for key in others:
                out[key] = share
        elif rest > 0:
            out["CASH"] = float(out.get("CASH", 0.0)) + rest
        total = float(out.sum())
        return out / total if total else out
    scale_rest = (1.0 - equity) / (1.0 - current) if current < 1.0 else 0.0
    out = {}
    for asset, weight in weights.items():
        if str(asset).upper() in {"SPY", "ACWI"}:
            out[asset] = equity * (float(weight) / current)
        else:
            out[asset] = float(weight) * scale_rest
    series = pd.Series(out, dtype=float)
    series = series[series > 1e-8]
    total = float(series.sum())
    return series / total if total else series


def risk_on_equity(base_equity: float, obs: dict, latched: bool) -> tuple[float, bool, str]:
    """Return ``(equity target, latch afterwards, reason)``."""
    if deteriorated(obs):
        return base_equity, False, "deterioration"
    if healthy_uptrend(obs):
        return min(EQUITY_CEILING, max(base_equity, EQUITY_CEILING)), True, "risk-on"
    if latched:
        return min(EQUITY_CEILING, max(base_equity, EQUITY_FLOOR_LATCHED)), True, "hysteresis"
    return base_equity, False, "base"


def apply_risk_on(weights: pd.Series, obs: dict, latched: bool = False) -> tuple[pd.Series, bool, str]:
    target, latch, reason = risk_on_equity(equity_of(weights), obs, latched)
    if reason == "base" or abs(target - equity_of(weights)) < 1e-8:
        return weights, latch, reason
    return _retarget(weights, target), latch, reason


def sentiment_tilt(equity: float, tone: float | None, enabled: bool) -> tuple[float, float]:
    """Capped ±5 point tilt. Disabled unless a replay showed it adds value."""
    if not enabled or tone is None:
        return equity, 0.0
    tilt = max(-SENTIMENT_WEIGHT_CAP, min(SENTIMENT_WEIGHT_CAP, float(tone) / 10.0 * SENTIMENT_WEIGHT_CAP))
    return min(EQUITY_CEILING, max(0.0, equity + tilt)), tilt
