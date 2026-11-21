"""Regime an informed observer can name without a fitted score.

Thresholds are round market conventions, chosen so that a deep equity
drawdown with a volatility spike is a crash and a rising 10-year yield with
a negative equity trend is tightening. They were not searched on returns.
The 2007-2016 window is only a sanity check that 2008 is a crash and the
mid-2010s are not.
"""

from __future__ import annotations

import pandas as pd

REGIME_ORDER = [
    "Panic",
    "Crash",
    "Stress",
    "Deleveraging",
    "Neutral",
    "Expansion",
    "Bubble Risk",
]

# Market conventions, not fitted coefficients.
VIX_PANIC = 40.0
VIX_CRASH = 32.0
VIX_STRESS = 25.0
VIX_CALM = 22.0
DRAWDOWN_PANIC = -0.30
DRAWDOWN_CRASH = -0.20
DRAWDOWN_STRESS = -0.08
SPY_1M_CRASH = -0.12
SPY_1M_STRESS = -0.06
YIELD_3M_TIGHTENING = 0.15
HY_SPREAD_GENUINE = 6.0
TREND_EQUITY_FLOOR = 0.55


def _lookup(frame: pd.DataFrame | None, row: str, column: str) -> float | None:
    if frame is None or frame.empty or row not in frame.index or column not in frame.columns:
        return None
    value = pd.to_numeric(pd.Series([frame.loc[row, column]]), errors="coerce").iloc[0]
    if pd.isna(value):
        return None
    return float(value)


def _change(frame: pd.DataFrame | None, column: str, periods: int) -> float | None:
    """Percent change over `periods` observations.

    Twelve or fifty-two steps are one year on a monthly or weekly print.
    After a daily forward-fill those same steps are only a few weeks, so a
    dense series uses the level one calendar year earlier.
    """
    if frame is None or frame.empty or column not in frame.columns:
        return None
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return None
    previous = _previous_level(series, periods)
    if previous is None or previous == 0:
        return None
    return float(series.iloc[-1] / previous - 1.0)


def _previous_level(series: pd.Series, periods: int) -> float | None:
    index = series.index
    if isinstance(index, pd.DatetimeIndex) and len(series) >= 2:
        gaps = index.to_series().diff().dt.days.dropna()
        median_gap = float(gaps.median()) if not gaps.empty else 999.0
        if median_gap <= 10:
            prior = series.loc[: index[-1] - pd.DateOffset(years=1)]
            if prior.empty:
                return None
            return float(prior.iloc[-1])
    if len(series) <= periods:
        return None
    return float(series.iloc[-1 - periods])


def _last_value(frame: pd.DataFrame | None, column: str) -> float | None:
    if frame is None or frame.empty or column not in frame.columns:
        return None
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])


def observations_from_signals(
    asset_signal_df: pd.DataFrame | None,
    macro_signal_df: pd.DataFrame | None,
    liquidity_df: pd.DataFrame | None = None,
) -> dict[str, float | None]:
    return {
        "spy_1m": _lookup(asset_signal_df, "SPY", "momentum_1M"),
        "spy_3m": _lookup(asset_signal_df, "SPY", "momentum_3M"),
        "spy_6m": _lookup(asset_signal_df, "SPY", "momentum_6M"),
        "spy_drawdown": _lookup(asset_signal_df, "SPY", "drawdown"),
        "vix": _lookup(macro_signal_df, "VIX", "level"),
        "yield_3m": _lookup(macro_signal_df, "US10Y", "return_3M"),
        "hy_spread": _last_value(liquidity_df, "HY_SPREAD"),
        "cpi_yoy": _change(liquidity_df, "US_CPI", 12),
        "m2_yoy": _change(liquidity_df, "M2", 12),
        "fed_yoy": _change(liquidity_df, "FED_BALANCE_SHEET", 52),
    }


def _num(obs: dict, key: str, default: float = 0.0) -> float:
    value = obs.get(key)
    if value is None:
        return default
    return float(value)


def genuine_credit_or_financial_stress(obs: dict) -> bool:
    """Stress severe enough to switch off the bull-market equity floor."""
    vix = _num(obs, "vix", 0.0)
    drawdown = _num(obs, "spy_drawdown", 0.0)
    hy = obs.get("hy_spread")
    wide_credit = hy is not None and float(hy) >= HY_SPREAD_GENUINE
    return bool(vix >= VIX_CRASH or drawdown <= DRAWDOWN_CRASH or wide_credit)


def bull_trend(obs: dict) -> bool:
    return _num(obs, "spy_6m") > 0.0 and _num(obs, "spy_3m") > 0.0 and _num(obs, "spy_drawdown") > DRAWDOWN_STRESS


def classify_from_observations(obs: dict[str, float | None]) -> dict:
    vix = _num(obs, "vix")
    drawdown = _num(obs, "spy_drawdown")
    spy_1m = _num(obs, "spy_1m")
    spy_3m = _num(obs, "spy_3m")
    spy_6m = _num(obs, "spy_6m")
    yield_3m = _num(obs, "yield_3m")
    hy = obs.get("hy_spread")

    reasons: list[str] = []
    if vix >= VIX_PANIC or drawdown <= DRAWDOWN_PANIC:
        regime = "Panic"
        if vix >= VIX_PANIC:
            reasons.append(f"Il VIX è a {vix:.0f}, un livello da panico di mercato.")
        if drawdown <= DRAWDOWN_PANIC:
            reasons.append(f"Lo S&P 500 è a {drawdown:.0%} dal massimo, un crollo già ampio.")
    elif drawdown <= DRAWDOWN_CRASH or spy_1m <= SPY_1M_CRASH or (drawdown <= -0.12 and vix >= VIX_CRASH):
        regime = "Crash"
        if drawdown <= DRAWDOWN_CRASH:
            reasons.append(f"Lo S&P 500 ha perso {abs(drawdown):.0%} dal massimo.")
        if spy_1m <= SPY_1M_CRASH:
            reasons.append(f"Nell'ultimo mese l'azionario USA ha perso {abs(spy_1m):.0%}.")
        if vix >= VIX_CRASH:
            reasons.append(f"Il VIX è elevato ({vix:.0f}).")
    elif yield_3m >= YIELD_3M_TIGHTENING and spy_6m < 0.0:
        regime = "Deleveraging"
        reasons.append(
            f"Il rendimento del Treasury a 10 anni è salito di circa {yield_3m:.0%} in tre mesi, "
            "mentre l'azionario a sei mesi è negativo: è una stretta, non un crollo di liquidità."
        )
    elif vix >= VIX_STRESS or drawdown <= DRAWDOWN_STRESS or spy_1m <= SPY_1M_STRESS:
        regime = "Stress"
        if vix >= VIX_STRESS:
            reasons.append(f"Il VIX è a {vix:.0f}, sopra una soglia di stress ordinaria.")
        if drawdown <= DRAWDOWN_STRESS:
            reasons.append(f"Il mercato è a {drawdown:.0%} dal massimo, una correzione non ancora un crash.")
        if spy_1m <= SPY_1M_STRESS:
            reasons.append(f"L'ultimo mese dell'S&P 500 è {spy_1m:.0%}.")
    elif spy_6m >= 0.20 and spy_3m > 0.05 and vix <= 15 and drawdown > -0.05:
        regime = "Bubble Risk"
        reasons.append(
            f"L'azionario sale da sei mesi ({spy_6m:.0%}) con il VIX molto basso ({vix:.0f}): "
            "è un mercato teso, non una fase neutra."
        )
    elif spy_6m > 0.0 and spy_3m > 0.0 and vix < VIX_CALM and drawdown > DRAWDOWN_STRESS:
        regime = "Expansion"
        reasons.append(
            f"L'S&P 500 è positivo a tre mesi ({spy_3m:.0%}) e a sei mesi ({spy_6m:.0%}), "
            f"il VIX è calmo ({vix:.0f}) e il drawdown è contenuto ({drawdown:.0%})."
        )
    else:
        regime = "Neutral"
        reasons.append(
            "Non c'è né un trend rialzista pulito né una dislocazione da stress: "
            "crescita, volatilità e drawdown non raccontano la stessa storia."
        )

    if hy is not None and regime in {"Crash", "Panic", "Stress", "Deleveraging"}:
        reasons.append(f"Lo spread high yield osservabile è intorno a {float(hy):.1f} punti.")

    credit_stress = genuine_credit_or_financial_stress(obs)
    trend = bull_trend(obs)
    equity_floor = None
    if trend and regime not in {"Crash", "Panic"} and not credit_stress:
        equity_floor = TREND_EQUITY_FLOOR
        reasons.append(
            "Il trend a 3 e 6 mesi è ancora positivo e non c'è uno stress di credito o di volatilità "
            f"da crisi, quindi l'azionario non scende sotto {TREND_EQUITY_FLOOR:.0%}."
        )
    elif regime in {"Crash", "Panic"}:
        reasons.append("In un crash il filtro di trend è spento: la priorità è non inseguire il rialzo.")
    elif credit_stress and trend:
        reasons.append("Il trend è positivo, ma credito o volatilità sono in zona di crisi: il filtro rialzista resta spento.")

    probability = _probability(regime)
    risk_state = {
        "Panic": "panic",
        "Crash": "crash",
        "Stress": "stress",
        "Deleveraging": "stress",
        "Neutral": "normal",
        "Expansion": "normal",
        "Bubble Risk": "normal",
    }[regime]
    equity_cap = {
        "Panic": 0.15,
        "Crash": 0.35,
        "Stress": 0.60,
        "Deleveraging": 0.65,
        "Neutral": 0.85,
        "Expansion": 1.00,
        "Bubble Risk": 0.90,
    }[regime]
    cash_floor = {"Panic": 0.20, "Crash": 0.12, "Stress": 0.05, "Deleveraging": 0.04}.get(regime, 0.0)

    drivers = {
        "crescita": _growth_text(spy_3m, spy_6m),
        "inflazione": _inflation_text(obs.get("cpi_yoy")),
        "tassi": _rates_text(yield_3m),
        "liquidita": _liquidity_text(obs.get("fed_yoy"), obs.get("m2_yoy")),
        "credito": _credit_text(hy),
        "stress_mercato": _stress_text(vix, drawdown),
    }

    return {
        "regime": regime,
        "probability": float(probability.loc[regime]),
        "probability_distribution": probability,
        "reasons": reasons,
        "drivers": drivers,
        "risk_state": risk_state,
        "equity_cap": equity_cap,
        "cash_floor": cash_floor,
        "equity_floor": equity_floor,
        "bull_trend": trend,
        "genuine_stress": credit_stress,
        "observations": {
            "vix": vix,
            "spy_drawdown": drawdown,
            "spy_1m": spy_1m,
            "spy_3m": spy_3m,
            "spy_6m": spy_6m,
            "yield_3m": yield_3m,
            "hy_spread": None if hy is None else float(hy),
            "cpi_yoy": obs.get("cpi_yoy"),
            "m2_yoy": obs.get("m2_yoy"),
            "fed_yoy": obs.get("fed_yoy"),
        },
    }


def _probability(regime: str) -> pd.Series:
    """A coarse distribution: the chosen label leads, a neighbour is the alternative."""
    neighbours = {
        "Panic": "Crash",
        "Crash": "Stress",
        "Stress": "Neutral",
        "Deleveraging": "Stress",
        "Neutral": "Expansion",
        "Expansion": "Neutral",
        "Bubble Risk": "Expansion",
    }
    scores = pd.Series(0.04, index=REGIME_ORDER, dtype=float)
    scores.loc[regime] = 0.62
    scores.loc[neighbours[regime]] = 0.18
    return scores / float(scores.sum())


def _growth_text(spy_3m: float, spy_6m: float) -> str:
    if spy_6m > 0.05 and spy_3m > 0:
        return f"La crescita di mercato è solida: S&P 500 a tre mesi {spy_3m:.0%}, a sei mesi {spy_6m:.0%}."
    if spy_6m < -0.05:
        return f"La crescita di mercato è debole: S&P 500 a sei mesi {spy_6m:.0%}."
    return f"La crescita di mercato è mista: tre mesi {spy_3m:.0%}, sei mesi {spy_6m:.0%}."


def _rates_text(yield_3m: float) -> str:
    if yield_3m >= YIELD_3M_TIGHTENING:
        return f"I tassi stanno stringendo: il Treasury a 10 anni è salito di circa {yield_3m:.0%} in tre mesi."
    if yield_3m <= -0.10:
        return f"I tassi si stanno allentando: il Treasury a 10 anni è sceso di circa {abs(yield_3m):.0%} in tre mesi."
    return "I tassi a dieci anni non hanno avuto una variazione da stretta negli ultimi tre mesi."


def _inflation_text(cpi_yoy: float | None) -> str:
    if cpi_yoy is None:
        return "L'inflazione non è nel pannello di questo mese: la serie non è ancora osservabile."
    return f"I prezzi al consumo, sull'ultimo dato pubblicato, sono a {cpi_yoy:.0%} anno su anno."


def _liquidity_text(fed_yoy: float | None, m2_yoy: float | None) -> str:
    parts = []
    if fed_yoy is not None:
        parts.append(f"il bilancio della Fed è a {fed_yoy:.0%} anno su anno")
    if m2_yoy is not None:
        parts.append(f"M2 è a {m2_yoy:.0%} anno su anno")
    if not parts:
        return "Liquidità e bilancio della Fed non sono ancora osservabili in questo pannello."
    return "Sull'ultimo dato pubblicato, " + " e ".join(parts) + "."


def _credit_text(hy: float | None) -> str:
    if hy is None:
        return "Lo spread high yield ufficiale non è ancora osservabile: non lo inventiamo."
    if hy >= HY_SPREAD_GENUINE:
        return f"Il credito è sotto stress: lo spread high yield è a {hy:.1f} punti."
    return f"Il credito non è in zona di crisi: lo spread high yield è a {hy:.1f} punti."


def _stress_text(vix: float, drawdown: float) -> str:
    return f"Stress di mercato: VIX {vix:.0f}, S&P 500 a {drawdown:.0%} dal massimo."


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def reading_clarity(observations: dict, regime: str) -> dict:
    """How far the readings sit from the neighbouring regime, on a 0–100 scale.

    This is not a probability that the label is correct and not a forecast.
    A high score means the inputs are comfortably past the threshold. A low
    score means a small move in VIX, drawdown or the three-month trend would
    flip the label.
    """
    vix = _num(observations, "vix")
    drawdown = _num(observations, "spy_drawdown")
    spy_1m = _num(observations, "spy_1m")
    spy_3m = _num(observations, "spy_3m")
    spy_6m = _num(observations, "spy_6m")
    yield_3m = _num(observations, "yield_3m")

    if regime == "Panic":
        parts = []
        if vix >= VIX_PANIC:
            parts.append(_clip01((vix - VIX_PANIC) / 15))
        if drawdown <= DRAWDOWN_PANIC:
            parts.append(_clip01((DRAWDOWN_PANIC - drawdown) / 0.10))
    elif regime == "Crash":
        parts = []
        if drawdown <= DRAWDOWN_CRASH:
            parts.append(_clip01((DRAWDOWN_CRASH - drawdown) / 0.08))
        if spy_1m <= SPY_1M_CRASH:
            parts.append(_clip01((SPY_1M_CRASH - spy_1m) / 0.06))
        if vix >= VIX_CRASH:
            parts.append(_clip01((vix - VIX_CRASH) / 8))
    elif regime == "Deleveraging":
        parts = [
            _clip01((yield_3m - YIELD_3M_TIGHTENING) / 0.10),
            _clip01((-spy_6m) / 0.06),
        ]
    elif regime == "Stress":
        parts = []
        if vix >= VIX_STRESS:
            parts.append(_clip01((vix - VIX_STRESS) / 8))
        if drawdown <= DRAWDOWN_STRESS:
            parts.append(_clip01((DRAWDOWN_STRESS - drawdown) / 0.06))
        if spy_1m <= SPY_1M_STRESS:
            parts.append(_clip01((SPY_1M_STRESS - spy_1m) / 0.04))
    elif regime == "Bubble Risk":
        parts = [_clip01((spy_6m - 0.20) / 0.08), _clip01((15 - vix) / 5)]
    elif regime == "Expansion":
        parts = [
            _clip01(spy_6m / 0.06),
            _clip01(spy_3m / 0.04),
            _clip01((VIX_CALM - vix) / 6),
            _clip01((drawdown - DRAWDOWN_STRESS) / 0.05),
        ]
    else:
        parts = [
            _clip01(abs(spy_3m) / 0.03),
            _clip01(abs(VIX_STRESS - vix) / 6),
            _clip01((drawdown - DRAWDOWN_STRESS) / 0.04),
        ]

    score = int(round(100 * (sum(parts) / len(parts)))) if parts else 40
    score = max(0, min(100, score))
    if score < 40:
        label = "Incerta"
        detail = "I numeri sono vicini alla soglia. Una piccola variazione di VIX, drawdown o trend a tre mesi cambierebbe l'etichetta."
    elif score < 70:
        label = "Moderata"
        detail = "La lettura è abbastanza stabile, ma non è distante dal regime vicino."
    else:
        label = "Chiara"
        detail = "I numeri sono ben oltre la soglia che separa questo regime da quello vicino."
    return {
        "score": score,
        "label": label,
        "detail": detail,
        "name": "Chiarezza della lettura",
    }


def classify_observable_regime(
    asset_signal_df: pd.DataFrame | None,
    macro_signal_df: pd.DataFrame | None,
    liquidity_df: pd.DataFrame | None = None,
) -> dict:
    return classify_from_observations(
        observations_from_signals(asset_signal_df, macro_signal_df, liquidity_df)
    )
