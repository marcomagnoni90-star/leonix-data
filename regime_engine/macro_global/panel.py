"""Build the monthly, point-in-time panel and the latest reading."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from config.macro_global import HEALTH_WEIGHTS, PRICE_CAP
from regime_engine.macro_global.allocate import apply_region_tilt, apply_risk_off, base_weights
from regime_engine.macro_global.catalog import SPECS, YAHOO_EQUITY, SeriesSpec
from regime_engine.macro_global.classify import REGIME_LABELS, classify_regime, regime_text
from regime_engine.macro_global.fetch import fetch_fred, fetch_world_bank_gdp, fetch_yahoo_monthly
from regime_engine.macro_global.official import LAST_NOTES, load_official
from regime_engine.macro_global.scoring import (
    fixed_scale_components,
    hold_last,
    is_policy_rate,
    policy_stance_components,
    publish,
    rolling_z,
    signed_mean,
    stale_limit,
    transform,
    variable_components,
    weighted_mean,
)

# Core inflation when the region has it, otherwise headline CPI.
POLICY_INFLATION = {
    "USA": ("USA_CORE", "USA_CPI"),
    "EZ": ("EZ_CPI",),
    "UK": ("UK_CPI",),
    "CH": ("CH_CPI",),
    "JP": ("JP_CPI",),
    "CN": ("CN_CPI",),
}

STALE_MONTHS = 18


def _closed_month(now: datetime | None = None) -> pd.Timestamp:
    """Last month that has already ended. A print dated in the future is not used."""
    now = now or datetime.now(timezone.utc)
    today = pd.Timestamp(now.date())
    month_end = today.to_period("M").to_timestamp("M")
    if today < month_end:
        return (today.to_period("M") - 1).to_timestamp("M")
    return month_end


def _asof_month(now: datetime | None = None) -> pd.Timestamp:
    now = now or datetime.now(timezone.utc)
    return pd.Timestamp(now.date()).to_period("M").to_timestamp("M")


def load_raw(refresh: bool = False) -> tuple[dict[str, pd.Series], list[dict]]:
    """Fetch official series, then let FRED replace a series when it answers.

    A stale series stays in the history so the backtest can use it up to its
    last print. It simply stops contributing after that month.
    """
    raw: dict[str, pd.Series] = load_official(refresh=refresh)
    report: list[dict] = []
    cutoff = _asof_month() - pd.DateOffset(months=STALE_MONTHS)
    for spec in SPECS:
        if spec.fred_id:
            fred = fetch_fred(spec.fred_id, refresh=refresh)
            current = raw.get(spec.series_id)
            def _month(stamp):
                return pd.Timestamp(stamp).to_period("M")
            fred_is_newer = fred is not None and not fred.empty and (
                current is None or current.empty or _month(fred.index.max()) >= _month(current.index.max())
            )
            if fred_is_newer:
                raw[spec.series_id] = fred
                source = f"FRED {spec.fred_id}"
            else:
                source = LAST_NOTES.get(spec.series_id, spec.source)
        else:
            source = LAST_NOTES.get(spec.series_id, spec.source)
        series = raw.get(spec.series_id)
        if series is None or series.empty:
            report.append({
                "id": spec.series_id,
                "region": spec.region,
                "pillar": spec.pillar,
                "name": spec.name,
                "source": source,
                "license": spec.license,
                "frequency": spec.frequency,
                "lag_months": spec.lag_months,
                "status": "missing",
                "last": None,
            })
            continue
        last = series.index.max()
        status = "stale" if last < cutoff else "ok"
        report.append({
            "id": spec.series_id,
            "region": spec.region,
            "pillar": spec.pillar,
            "name": spec.name,
            "source": source,
            "license": spec.license,
            "frequency": spec.frequency,
            "lag_months": spec.lag_months,
            "status": status,
            "last": last.date().isoformat(),
            "start": series.index.min().date().isoformat(),
            "n": int(series.shape[0]),
        })
    yahoo: dict[str, pd.Series] = {}
    for region, ticker in YAHOO_EQUITY.items():
        series = fetch_yahoo_monthly(ticker, refresh=refresh)
        if series is None or series.empty:
            report.append({"id": ticker, "region": region, "pillar": "market", "status": "missing", "last": None, "license": "Yahoo chart, internal backtest only"})
            continue
        yahoo[region] = series
        report.append({
            "id": ticker,
            "region": region,
            "pillar": "market",
            "name": f"{ticker} adjusted close",
            "source": "Yahoo Finance chart API",
            "license": "Not a commercial market-data licence. Internal backtest only.",
            "frequency": "monthly",
            "lag_months": 0,
            "status": "ok",
            "last": series.index.max().date().isoformat(),
            "start": series.index.min().date().isoformat(),
            "n": int(series.shape[0]),
        })
    return {"fred": raw, "yahoo": yahoo, "report": report}  # type: ignore[return-value]


def inflation_percent(bundle: dict) -> dict[str, pd.Series]:
    """Year-over-year inflation in percent, carried forward within the staleness limit."""
    by_id = {spec.series_id: spec for spec in SPECS}
    out: dict[str, pd.Series] = {}
    for region, candidates in POLICY_INFLATION.items():
        for series_id in candidates:
            spec = by_id.get(series_id)
            series = bundle["fred"].get(series_id)
            if spec is None or series is None:
                continue
            published = publish(series, spec.lag_months)
            level = transform(published, spec.transform)
            if spec.transform == "yoy":
                level = level * 100.0
            out[region] = hold_last(level, stale_limit(spec.frequency))
            break
    return out


def series_components(spec: SeriesSpec, series: pd.Series, inflation_pp: pd.Series | None = None) -> pd.DataFrame:
    """Score one series the same way the panel does."""
    published = publish(series, spec.lag_months)
    limit = stale_limit(spec.frequency)
    if is_policy_rate(spec.series_id):
        frame = policy_stance_components(hold_last(published, limit), inflation_pp, spec.sign)
    elif spec.series_id == "USA_INV":
        frame = fixed_scale_components(hold_last(published, limit), spec.sign, 1.0)
    else:
        frame = variable_components(published, spec.transform, spec.sign, hold_months=limit)
    frame["raw"] = hold_last(published, limit)
    return frame


def _components(spec: SeriesSpec, series: pd.Series, inflation_pp: pd.Series | None = None) -> tuple[pd.Series, pd.Series]:
    frame = series_components(spec, series, inflation_pp)
    level = pd.to_numeric(frame["level"], errors="coerce")
    impulse = spec.sign * rolling_z(level.diff(6))
    return frame["score"], impulse


def build_scores(bundle: dict) -> dict[str, pd.DataFrame]:
    raw: dict[str, pd.Series] = bundle["fred"]
    frames: dict[str, list[pd.Series]] = {}
    impulses: dict[str, list[pd.Series]] = {}
    inflation = inflation_percent(bundle)
    for spec in SPECS:
        series = raw.get(spec.series_id)
        if series is None:
            continue
        score, impulse = _components(spec, series, inflation.get(spec.region))
        score.name = spec.series_id
        frames.setdefault(f"{spec.region}|{spec.pillar}", []).append(score)
        if spec.pillar == "growth":
            impulse.name = spec.series_id
            impulses.setdefault(spec.region, []).append(impulse)
    for region, price in bundle["yahoo"].items():
        monthly = price.resample("ME").last().dropna()
        monthly.index = monthly.index.to_period("M").to_timestamp("M")
        momentum = rolling_z(monthly.pct_change(12))
        peak = monthly.rolling(12, min_periods=6).max()
        drawdown = monthly / peak - 1.0
        stress = -rolling_z(drawdown)
        momentum.name = f"{region}_MOM"
        stress.name = f"{region}_DD"
        frames.setdefault(f"{region}|growth", []).append(momentum)
        frames.setdefault(f"{region}|stress", []).append(stress)
    pillar_frames = {}
    for key, columns in frames.items():
        pillar_frames[key] = pd.concat(columns, axis=1)
    impulse_frames = {region: pd.concat(columns, axis=1) for region, columns in impulses.items()}
    return {"pillars": pillar_frames, "impulse": impulse_frames}


def _pillar_series(frame: pd.DataFrame) -> pd.Series:
    """Equal-weight mean, with market prices capped at PRICE_CAP of the pillar.

    If prices would be the whole pillar, the score is PRICE_CAP times the
    price mean. The missing macro share is not filled in.
    """
    price_cols = [column for column in frame.columns if str(column).endswith("_MOM") or str(column).endswith("_DD")]
    macro_cols = [column for column in frame.columns if column not in price_cols]
    ones = {column: 1.0 for column in frame.columns}
    equal = weighted_mean(frame, ones)
    macro_n = frame[macro_cols].notna().sum(axis=1) if macro_cols else pd.Series(0, index=frame.index)
    price_n = frame[price_cols].notna().sum(axis=1) if price_cols else pd.Series(0, index=frame.index)
    macro = weighted_mean(frame[macro_cols], {column: 1.0 for column in macro_cols}) if macro_cols else None
    price = weighted_mean(frame[price_cols], {column: 1.0 for column in price_cols}) if price_cols else None
    total_n = macro_n + price_n
    price_share = price_n / total_n.where(total_n > 0)
    use_cap = (macro_n > 0) & (price_n > 0) & (price_share > PRICE_CAP)
    only_price = (macro_n == 0) & (price_n > 0)
    out = equal
    if macro is not None and price is not None:
        capped = (1.0 - PRICE_CAP) * macro + PRICE_CAP * price
        out = out.where(~use_cap, capped)
    if price is not None:
        out = out.where(~only_price, price * PRICE_CAP)
    return out


def assemble(bundle: dict, through: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per month with global regime, regional health and weights."""
    pieces = build_scores(bundle)
    regions = ("USA", "EZ", "UK", "CH", "JP", "CN", "EM", "GLOBAL")
    pillars = ("growth", "inflation", "policy", "liquidity", "stress")
    pillar_scores: dict[str, pd.Series] = {}
    for region in regions:
        for pillar in pillars:
            frame = pieces["pillars"].get(f"{region}|{pillar}")
            if frame is None:
                continue
            pillar_scores[f"{region}_{pillar}"] = _pillar_series(frame)
    impulse = {region: _pillar_series(frame) for region, frame in pieces["impulse"].items()}
    health = {}
    for region in regions:
        columns = {}
        for pillar, weight in HEALTH_WEIGHTS.items():
            series = pillar_scores.get(f"{region}_{pillar}")
            if series is not None:
                columns[pillar] = series
        if not columns:
            continue
        frame = pd.concat(columns, axis=1)
        health[region] = signed_mean(frame, {pillar: HEALTH_WEIGHTS[pillar] for pillar in columns})
    health_frame = pd.concat(health, axis=1) if health else pd.DataFrame()
    gdp = gdp_weights(bundle.get("gdp") or {})
    if not health_frame.empty and gdp:
        present = [region for region in gdp if region in health_frame.columns and region != "GLOBAL"]
        if present:
            weights = pd.Series({region: gdp[region] for region in present})
            weights = weights / weights.sum()
            global_health = health_frame[present].mul(weights, axis=1).sum(axis=1, min_count=1)
            health_frame = health_frame.copy()
            health_frame["GLOBAL_WEIGHTED"] = global_health
    index = health_frame.index if not health_frame.empty else pd.DatetimeIndex([])
    rows = []
    for date in index:
        limit = _closed_month() if through is None else pd.Timestamp(through)
        if date < pd.Timestamp("2005-01-31") or date > limit:
            continue
        growth = _blend(_at(pillar_scores, "GLOBAL_growth", date), _mix(pillar_scores, "growth", date, gdp))
        inflation = _blend(_at(pillar_scores, "GLOBAL_inflation", date), _mix(pillar_scores, "inflation", date, gdp))
        stress = _blend(_at(pillar_scores, "GLOBAL_stress", date), _mix(pillar_scores, "stress", date, gdp))
        liquidity = _blend(_at(pillar_scores, "GLOBAL_liquidity", date), _mix(pillar_scores, "liquidity", date, gdp))
        imp = _blend(_at(impulse, "GLOBAL", date), _mix_impulse(impulse, date, gdp))
        code = classify_regime(growth, inflation, stress, liquidity, imp)
        region_scores = {region: _at_frame(health_frame, region, date) for region in ("USA", "EZ", "UK", "CH", "JP", "CN", "EM")}
        region_regimes = {}
        for region in region_scores:
            region_regimes[region] = classify_regime(
                _at(pillar_scores, f"{region}_growth", date),
                _at(pillar_scores, f"{region}_inflation", date),
                _at(pillar_scores, f"{region}_stress", date),
                _at(pillar_scores, f"{region}_liquidity", date),
                _at(impulse, region, date),
            )
        weights = apply_region_tilt(base_weights(code), region_scores)
        weights = apply_risk_off(weights, region_regimes, gdp)
        row = {
            "date": date,
            "regime": code,
            "growth": growth,
            "inflation": inflation,
            "policy": _mix(pillar_scores, "policy", date, gdp),
            "liquidity": liquidity,
            "stress": stress,
            "impulse": imp,
        }
        for region, score in region_scores.items():
            row[f"health_{region}"] = score
            row[f"regime_{region}"] = region_regimes[region]
            for pillar in pillars:
                row[f"{region}_{pillar}"] = _at(pillar_scores, f"{region}_{pillar}", date)
        row.update(weights)
        rows.append(row)
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def _at(mapping: dict, key: str, date: pd.Timestamp) -> float | None:
    series = mapping.get(key)
    if series is None or date not in series.index or pd.isna(series.loc[date]):
        return None
    return float(series.loc[date])


def _at_frame(frame: pd.DataFrame, column: str, date: pd.Timestamp) -> float | None:
    if column not in frame.columns or date not in frame.index or pd.isna(frame.at[date, column]):
        return None
    return float(frame.at[date, column])


def _blend(direct: float | None, mix: float | None, direct_weight: float = 0.15) -> float | None:
    """GDP-weighted regions, plus a fixed slice for the global block when both exist."""
    if direct is None:
        return mix
    if mix is None:
        return direct
    return (1.0 - direct_weight) * mix + direct_weight * direct


def _mix(pillar_scores: dict[str, pd.Series], pillar: str, date: pd.Timestamp, gdp: dict[str, float]) -> float | None:
    values = []
    weights = []
    for region, weight in gdp.items():
        value = _at(pillar_scores, f"{region}_{pillar}", date)
        if value is None:
            continue
        values.append(value)
        weights.append(weight)
    if not values:
        return None
    total = sum(weights)
    return float(sum(value * weight for value, weight in zip(values, weights)) / total)


def _mix_impulse(impulse: dict[str, pd.Series], date: pd.Timestamp, gdp: dict[str, float]) -> float | None:
    values = []
    weights = []
    for region, weight in gdp.items():
        series = impulse.get(region)
        if series is None or date not in series.index or pd.isna(series.loc[date]):
            continue
        values.append(float(series.loc[date]))
        weights.append(weight)
    if not values:
        return None
    total = sum(weights)
    return float(sum(value * weight for value, weight in zip(values, weights)) / total)


REGION_KEYS = {"USA", "EZ", "UK", "CH", "JP", "CN", "EM"}


def gdp_weights(raw: dict[str, float]) -> dict[str, float]:
    """Map World Bank codes onto the model regions.

    A dict that is already keyed by region is returned unchanged apart from
    normalising. Calling this twice does not drop the euro area, the UK or
    the emerging-market basket.

    EM is the sum of Brazil, India, Indonesia, Mexico, Turkey and South Africa.
    It is the basket used in the model, not the whole emerging-market world.
    No residual is invented when a country is missing.
    """
    positive = {str(code): float(value) for code, value in raw.items() if value and float(value) > 0}
    if positive and set(positive).issubset(REGION_KEYS):
        total = sum(positive.values())
        return {code: value / total for code, value in positive.items()}
    rename = {
        "US": "USA", "USA": "USA",
        "XC": "EZ", "EMU": "EZ",
        "GB": "UK", "GBR": "UK",
        "CH": "CH", "CHE": "CH",
        "JP": "JP", "JPN": "JP",
        "CN": "CN", "CHN": "CN",
        "BR": "EM", "BRA": "EM",
        "IN": "EM", "IND": "EM",
        "ID": "EM", "IDN": "EM",
        "MX": "EM", "MEX": "EM",
        "TR": "EM", "TUR": "EM",
        "ZA": "EM", "ZAF": "EM",
    }
    mapped: dict[str, float] = {}
    for code, value in raw.items():
        region = rename.get(code)
        if region and value > 0:
            mapped[region] = mapped.get(region, 0.0) + float(value)
    if not mapped:
        return {}
    total = sum(mapped.values())
    return {region: value / total for region, value in mapped.items()}


def latest_reading(refresh: bool = False) -> dict:
    bundle = load_raw(refresh=refresh)
    gdp_raw = fetch_world_bank_gdp()
    bundle["gdp"] = gdp_weights(gdp_raw)
    panel = assemble(bundle)
    if panel.empty:
        return {"available": False, "reason": "empty panel", "series": bundle["report"], "schema": 1}
    row = panel.iloc[-1]
    code = row["regime"] if isinstance(row["regime"], str) else None
    regions = {}
    for region in ("USA", "EZ", "UK", "CH", "JP", "CN", "EM"):
        code_region = row.get(f"regime_{region}")
        regions[region] = {
            "regime": None if code_region is None or (isinstance(code_region, float) and pd.isna(code_region)) else str(code_region),
            "label": REGIME_LABELS.get(str(code_region), None) if isinstance(code_region, str) else None,
            "health": _none(row.get(f"health_{region}")),
            "growth": _none(row.get(f"{region}_growth")),
            "inflation": _none(row.get(f"{region}_inflation")),
            "policy": _none(row.get(f"{region}_policy")),
            "liquidity": _none(row.get(f"{region}_liquidity")),
            "stress": _none(row.get(f"{region}_stress")),
        }
    from config.macro_global import ALLOCATION
    tickers = set()
    for book in ALLOCATION.values():
        tickers.update(book)
    weights = {asset: float(row[asset]) for asset in tickers if asset in row.index and pd.notna(row[asset])}
    return {
        "available": True,
        "schema": 1,
        "asOf": pd.Timestamp(row.name).date().isoformat(),
        "regime": {
            "code": code,
            "label": REGIME_LABELS.get(code or "", code),
            "plain": regime_text(code),
        },
        "pillars": {
            "growth": _none(row.get("growth")),
            "inflation": _none(row.get("inflation")),
            "policy": _none(row.get("policy")),
            "liquidity": _none(row.get("liquidity")),
            "stress": _none(row.get("stress")),
            "impulse": _none(row.get("impulse")),
        },
        "regions": regions,
        "weights": weights,
        "gdpWeights": bundle["gdp"],
        "series": bundle["report"],
    }


def _none(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
