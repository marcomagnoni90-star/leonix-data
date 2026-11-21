"""Offline market snapshot for the retail app. No broker and no order routing."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import FRED_SERIES
from data_engine.data_storage import load_dataframe
from data_engine.fred_cache import load_cached_fred_frame, splice_official_spreads
from regime_engine.illustrative_allocation import illustrative_book
from regime_engine.observable_regime import (
    classify_observable_regime,
    reading_clarity,
)
from signal_engine.signals import build_all_signals

EQUITY = {
    "SPY", "ACWI", "EFA", "EEM", "VGK", "EWJ", "FXI",
    "XLK", "XLF", "XLE", "XLI", "XLU", "XLV", "XLY", "XLP", "XLB", "XLRE",
}
BONDS = {"TLT", "TIP", "LQD", "HYG"}
CASH = {"CASH", "UUP"}

GROUP_ORDER = ["Azionario globale", "Obbligazionario", "Oro", "Liquidità"]

ITALIAN_REGIME = {
    "Panic": "Panico",
    "Crash": "Crash",
    "Stress": "Stress",
    "Deleveraging": "Stretta",
    "Neutral": "Neutrale",
    "Expansion": "Espansione",
    "Bubble Risk": "Rischio bolla",
}

REGIME_PLAIN = {
    "Panic": "Il mercato è in panico: la volatilità è estrema o le perdite dal massimo sono già da crollo.",
    "Crash": "Siamo in un crash: l'azionario ha perso molto dal massimo, o il mese in corso è stato violento.",
    "Stress": "C'è stress, non un crollo. Volatilità alta o una correzione, con il mercato ancora in piedi.",
    "Deleveraging": "La storia di questo periodo è la stretta dei tassi, con l'azionario già in difficoltà.",
    "Neutral": "Il quadro è misto. Non c'è un trend pulito e non c'è nemmeno una crisi conclamata.",
    "Expansion": "Il mercato è in espansione: il trend è positivo e la volatilità è contenuta.",
    "Bubble Risk": "Il rialzo è molto tirato e la volatilità è insolitamente bassa. Si partecipa, ma senza euforia.",
}


def italian_regime(regime: str) -> str:
    return ITALIAN_REGIME.get(regime, regime)


def load_offline_market() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prices = load_dataframe("asset_prices.csv").sort_index()
    macro = load_dataframe("macro_prices.csv").sort_index().reindex(prices.index).ffill()
    liquidity, _coverage = load_cached_fred_frame(FRED_SERIES, point_in_time=True)
    if liquidity is None or liquidity.empty:
        liquidity = pd.DataFrame(index=prices.index)
    else:
        liquidity, _info = splice_official_spreads(liquidity, prices)
        calendar = liquidity.index.union(prices.index).sort_values()
        liquidity = liquidity.sort_index().reindex(calendar).ffill().reindex(prices.index)
    return prices, macro, liquidity


def _slice_to(frame: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    return frame.loc[:asof]


def build_snapshot(prices: pd.DataFrame, macro: pd.DataFrame, liquidity: pd.DataFrame, asof: pd.Timestamp | None = None) -> dict:
    asof = pd.Timestamp(prices.index.max() if asof is None else asof)
    asset_signals, macro_signals, _history = build_all_signals(
        _slice_to(prices, asof),
        _slice_to(macro, asof),
    )
    observable = classify_observable_regime(asset_signals, macro_signals, _slice_to(liquidity, asof))
    clarity = reading_clarity(observable["observations"], observable["regime"])
    regime = observable["regime"]
    risk_state = observable["risk_state"]
    book = illustrative_book(regime, observable["observations"])
    return {
        "asof": asof,
        "regime": regime,
        "regime_label": italian_regime(regime),
        "plain": REGIME_PLAIN.get(regime, ""),
        "risk_state": risk_state,
        "clarity": clarity,
        "observable": observable,
        "allocation": book["tickers"],
        "classes": book["classes"],
        "examples": book["examples"],
        "allocation_key": book["key"],
        "allocation_note": book["note"],
        "drivers": driver_lights(observable),
        "reasons": list(observable.get("reasons") or []) + [book["note"]],
    }


def previous_month_end(index: pd.DatetimeIndex, asof: pd.Timestamp) -> pd.Timestamp | None:
    month_start = pd.Timestamp(asof).to_period("M").to_timestamp()
    prior = index[index < month_start]
    if len(prior) == 0:
        return None
    return pd.Timestamp(prior.max())


def driver_lights(observable: dict) -> list[dict]:
    obs = observable.get("observations") or {}
    vix = float(obs.get("vix") or 0)
    drawdown = float(obs.get("spy_drawdown") or 0)
    spy_3m = float(obs.get("spy_3m") or 0)
    spy_6m = float(obs.get("spy_6m") or 0)
    yield_3m = float(obs.get("yield_3m") or 0)
    hy = obs.get("hy_spread")
    cpi = obs.get("cpi_yoy")
    fed = obs.get("fed_yoy")
    m2 = obs.get("m2_yoy")

    growth = "verde" if spy_6m > 0.05 and spy_3m > 0 else "rosso" if spy_6m < -0.05 else "giallo"
    if cpi is None:
        inflation, inflation_text = "giallo", "Dato sull'inflazione non ancora osservabile."
    elif cpi <= 0.025:
        inflation, inflation_text = "verde", f"Inflazione pubblicata al {cpi:.0%}, vicina a un ritmo contenuto."
    elif cpi <= 0.04:
        inflation, inflation_text = "giallo", f"Inflazione pubblicata al {cpi:.0%}: sopra il 2%, non è una fiammata."
    else:
        inflation, inflation_text = "rosso", f"Inflazione pubblicata al {cpi:.0%}, ancora alta."

    if yield_3m >= 0.15:
        rates, rates_text = "rosso", f"I tassi a 10 anni sono saliti di circa {yield_3m:.0%} in tre mesi."
    elif yield_3m <= -0.10:
        rates, rates_text = "verde", f"I tassi a 10 anni sono scesi di circa {abs(yield_3m):.0%} in tre mesi."
    else:
        rates, rates_text = "giallo", "I tassi a 10 anni non hanno avuto una stretta negli ultimi tre mesi."

    if fed is None and m2 is None:
        liquidity, liquidity_text = "giallo", "Liquidità ufficiale non ancora osservabile."
    elif (fed or 0) < -0.05 or (m2 or 0) < 0:
        liquidity, liquidity_text = "rosso", _liquidity_line(fed, m2)
    elif (fed or 0) > 0.02 and (m2 is None or m2 > 0):
        liquidity, liquidity_text = "verde", _liquidity_line(fed, m2)
    else:
        liquidity, liquidity_text = "giallo", _liquidity_line(fed, m2)

    if hy is None:
        credit, credit_text = "giallo", "Spread high yield non osservabile."
    elif hy >= 6:
        credit, credit_text = "rosso", f"Spread high yield a {hy:.1f} punti: credito sotto stress."
    elif hy >= 4.5:
        credit, credit_text = "giallo", f"Spread high yield a {hy:.1f} punti: un po' teso, non in crisi."
    else:
        credit, credit_text = "verde", f"Spread high yield a {hy:.1f} punti: credito tranquillo."

    if vix >= 32 or drawdown <= -0.20:
        stress, stress_text = "rosso", f"VIX {vix:.0f}, S&P 500 a {drawdown:.0%} dal massimo."
    elif vix >= 22 or drawdown <= -0.08:
        stress, stress_text = "giallo", f"VIX {vix:.0f}, S&P 500 a {drawdown:.0%} dal massimo."
    else:
        stress, stress_text = "verde", f"VIX {vix:.0f}, S&P 500 a {drawdown:.0%} dal massimo."

    growth_text = f"S&P 500 a tre mesi {spy_3m:.0%}, a sei mesi {spy_6m:.0%}."
    return [
        {"key": "crescita", "title": "Crescita", "light": growth, "text": growth_text},
        {"key": "inflazione", "title": "Inflazione", "light": inflation, "text": inflation_text},
        {"key": "tassi", "title": "Tassi e politica monetaria", "light": rates, "text": rates_text},
        {"key": "liquidita", "title": "Liquidità", "light": liquidity, "text": liquidity_text},
        {"key": "credito", "title": "Credito", "light": credit, "text": credit_text},
        {"key": "stress", "title": "Stress di mercato", "light": stress, "text": stress_text},
    ]


def _liquidity_line(fed: float | None, m2: float | None) -> str:
    bits = []
    if fed is not None:
        bits.append(f"bilancio Fed {fed:.0%} anno su anno")
    if m2 is not None:
        bits.append(f"M2 {m2:.0%} anno su anno")
    return "Ultimo dato pubblicato: " + ", ".join(bits) + "."


def asset_group(symbol: str) -> str:
    symbol = str(symbol).upper().strip()
    if symbol in EQUITY:
        return "Azionario globale"
    if symbol in BONDS:
        return "Obbligazionario"
    if symbol == "GLD":
        return "Oro"
    if symbol in CASH:
        return "Liquidità"
    return "Altro"


def group_weights(allocation: pd.DataFrame) -> pd.DataFrame:
    if allocation is None or allocation.empty:
        return pd.DataFrame({"gruppo": GROUP_ORDER, "peso": [0.0, 0.0, 0.0, 0.0]})
    frame = allocation.copy()
    frame["gruppo"] = frame["asset"].map(asset_group)
    grouped = frame.groupby("gruppo", as_index=False)["weight"].sum()
    grouped = grouped.rename(columns={"weight": "peso"})
    base = pd.DataFrame({"gruppo": GROUP_ORDER + ["Altro"]})
    out = base.merge(grouped, on="gruppo", how="left").fillna({"peso": 0.0})
    out = out[out["peso"] > 0.0005]
    return out.reset_index(drop=True)


def parse_holdings(frame: pd.DataFrame) -> pd.DataFrame:
    columns = {str(column).strip().lower(): column for column in frame.columns}
    symbol_col = columns.get("symbol") or columns.get("ticker") or columns.get("asset") or columns.get("isin")
    if symbol_col is None:
        raise ValueError("Serve una colonna symbol, ticker o asset.")
    value_col = columns.get("value") or columns.get("valore") or columns.get("market_value") or columns.get("importo")
    weight_col = columns.get("weight") or columns.get("peso") or columns.get("weight_pct")
    out = pd.DataFrame({"symbol": frame[symbol_col].astype(str).str.upper().str.strip()})
    out = out[out["symbol"].ne("") & out["symbol"].ne("NAN")]
    if weight_col is not None:
        out["weight"] = pd.to_numeric(frame.loc[out.index, weight_col], errors="coerce")
    elif value_col is not None:
        values = pd.to_numeric(frame.loc[out.index, value_col], errors="coerce").fillna(0.0)
        total = float(values.sum())
        if total <= 0:
            raise ValueError("I valori devono essere maggiori di zero.")
        out["weight"] = values / total
    else:
        raise ValueError("Serve una colonna weight/peso oppure value/valore.")
    out["weight"] = pd.to_numeric(out["weight"], errors="coerce").fillna(0.0)
    if out["weight"].max() > 1.5:
        out["weight"] = out["weight"] / 100.0
    out = out[out["weight"] > 0]
    total = float(out["weight"].sum())
    if total <= 0:
        raise ValueError("I pesi devono essere maggiori di zero.")
    out["weight"] = out["weight"] / total
    out["gruppo"] = out["symbol"].map(asset_group)
    return out.reset_index(drop=True)


def compare_groups(current: pd.DataFrame, illustrative: pd.DataFrame) -> pd.DataFrame:
    left = current.groupby("gruppo", as_index=False)["weight"].sum().rename(columns={"weight": "tuo"})
    right = illustrative.copy()
    if "classe" in right.columns:
        right = right.rename(columns={"classe": "gruppo", "peso": "illustrativo"})
    elif "peso" in right.columns:
        right = right.rename(columns={"peso": "illustrativo"})
    elif "weight" in right.columns:
        right = right.rename(columns={"weight": "illustrativo"})
    merged = pd.DataFrame({"gruppo": GROUP_ORDER + ["Altro"]})
    merged = merged.merge(left, on="gruppo", how="left").merge(right[["gruppo", "illustrativo"]], on="gruppo", how="left")
    merged = merged.fillna(0.0)
    merged["scarto"] = merged["illustrativo"] - merged["tuo"]
    return merged[merged[["tuo", "illustrativo"]].sum(axis=1) > 0.001].reset_index(drop=True)


def learning_notes(comparison: pd.DataFrame) -> list[str]:
    """Describe the gap. Do not tell the user to trade."""
    lines = []
    for row in comparison.itertuples(index=False):
        if row.gruppo == "Altro" and float(row.tuo) > 0.001:
            lines.append(
                "Una parte dell'esercizio non rientra nelle quattro classi dell'esempio e resta fuori dal grafico."
            )
            continue
        if abs(float(row.scarto)) < 0.05:
            continue
        lines.append(
            f"Sulla classe {row.gruppo} l'esercizio pesa {float(row.tuo):.0%} e l'allocazione illustrativa "
            f"di questo regime pesa {float(row.illustrativo):.0%}. "
            "È la distanza tra due mix generici. Non è un'indicazione operativa."
        )
    if not lines:
        lines.append(
            "Sulle quattro classi l'esercizio e l'allocazione illustrativa di questo regime distano meno di cinque punti. "
            "Il confronto resta una descrizione."
        )
    return lines


def month_change_text(current: dict, previous: dict | None) -> str:
    if previous is None:
        return "Non c'è un mese precedente nel campione."
    if current["regime"] == previous["regime"]:
        head = f"Rispetto al mese scorso il regime resta {current['regime_label'].lower()}."
    else:
        head = f"Rispetto al mese scorso il regime è passato da {previous['regime_label']} a {current['regime_label']}."
    changed = []
    previous_lights = {item["key"]: item for item in previous["drivers"]}
    for item in current["drivers"]:
        before = previous_lights.get(item["key"])
        if before and before["light"] != item["light"]:
            changed.append(f"{item['title'].lower()} da {before['light']} a {item['light']}")
    if not changed:
        return head + " I semafori dei driver non sono cambiati."
    return head + " È cambiato: " + ", ".join(changed) + "."


def load_history() -> dict[str, pd.DataFrame]:
    folder = ROOT / "outputs" / "evaluation"
    found = {}
    for name in ("equity_curves.csv", "metrics.csv", "rebalances.csv", "regime_validation.csv"):
        path = folder / name
        if path.is_file():
            found[name] = pd.read_csv(path)
    return found
