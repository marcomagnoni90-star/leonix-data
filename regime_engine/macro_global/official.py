"""Official-source downloads used when the public FRED CSV does not answer.

Every function returns a series or nothing. Missing files stay missing.
"""

from __future__ import annotations

import json

import pandas as pd

from regime_engine.macro_global.catalog import EM_OECD, OECD_VARS, REGION_OECD
from regime_engine.macro_global.fetch import CACHE, _curl

def _month_end(values: pd.Series) -> pd.Series:
    clean = pd.to_numeric(values, errors="coerce").dropna().sort_index()
    clean = clean[~clean.index.duplicated(keep="last")]
    if clean.empty:
        return clean
    monthly = clean.resample("ME").last().dropna()
    monthly.index = monthly.index.to_period("M").to_timestamp("M")
    return monthly[~monthly.index.duplicated(keep="last")]


def _read(dest, **kwargs) -> pd.DataFrame | None:
    try:
        return pd.read_csv(dest, **kwargs)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError, ValueError):
        return None


def _oecd_frame(refresh: bool) -> pd.DataFrame | None:
    dest = CACHE / "oecd_kei.csv"
    areas = list(REGION_OECD.values()) + list(EM_OECD) + ["KOR", "OECD", "G20"]
    key = "+".join(dict.fromkeys(areas))
    url = (
        "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_KEI@DF_KEI,4.0/"
        f"{key}.M......?startPeriod=1995-01&format=csvfile"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=180):
            return None
    frame = _read(dest)
    if frame is None or "TIME_PERIOD" not in frame.columns:
        return None
    frame["TIME_PERIOD"] = pd.to_datetime(frame["TIME_PERIOD"], errors="coerce")
    return frame.dropna(subset=["TIME_PERIOD"])


def _pick(frame: pd.DataFrame, area: str, measure: str, unit: str, activity: str, adjustment: str, transformation: str) -> pd.Series | None:
    mask = (
        (frame["REF_AREA"] == area)
        & (frame["MEASURE"] == measure)
        & (frame["UNIT_MEASURE"] == unit)
        & (frame["ACTIVITY"] == activity)
        & (frame["ADJUSTMENT"] == adjustment)
        & (frame["TRANSFORMATION"] == transformation)
    )
    rows = frame.loc[mask, ["TIME_PERIOD", "OBS_VALUE"]]
    if rows.empty:
        return None
    series = pd.Series(pd.to_numeric(rows["OBS_VALUE"], errors="coerce").to_numpy(), index=pd.DatetimeIndex(rows["TIME_PERIOD"]))
    return _month_end(series)


def _from_oecd(frame: pd.DataFrame) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for region, area in REGION_OECD.items():
        for suffix, measure, unit, activity, adjustment, transformation, *_rest in OECD_VARS:
            if region == "EZ" and suffix == "RATE":
                continue
            series = _pick(frame, area, measure, unit, activity, adjustment, transformation)
            if series is None and suffix == "RS":
                series = _pick(frame, area, "TOVM", "IX", "G47", "Y", "_Z")
            if series is not None:
                out[f"{region}_{suffix}"] = series
        long = _pick(frame, area, "IRLT", "PA", "_Z", "_Z", "_Z")
        short = _pick(frame, area, "IR3TIB", "PA", "_Z", "_Z", "_Z")
        if long is not None and short is not None:
            slope = long.sub(short).dropna()
            if not slope.empty:
                out[f"{region}_SLOPE"] = slope
        exports = _pick(frame, area, "EX", "USD", "_T", "Y", "_Z")
        if region == "CN" and exports is not None:
            out["CN_EX"] = exports
    for area in EM_OECD:
        ip = _pick(frame, area, "PRVM", "IX", "C", "Y", "_Z")
        un = _pick(frame, area, "UNEMP", "PT_LF", "_T", "Y", "_Z")
        cpi = _pick(frame, area, "CP", "IX", "_Z", "_Z", "_Z")
        ex = _pick(frame, area, "EX", "USD", "_T", "Y", "_Z")
        if ip is not None:
            out[f"EM_{area}_IP"] = ip
        if un is not None:
            out[f"EM_{area}_UN"] = un
        if cpi is not None:
            out[f"EM_{area}_CPI"] = cpi
        if ex is not None:
            out[f"EM_{area}_EX"] = ex
    cli = _pick(frame, "OECD", "LI", "IX", "_T", "AA", "_Z")
    g20 = _pick(frame, "G20", "LI", "IX", "_T", "AA", "_Z")
    kor_ip = _pick(frame, "KOR", "PRVM", "IX", "C", "Y", "_Z")
    kor_ex = _pick(frame, "KOR", "EX", "USD", "_T", "Y", "_Z")
    if cli is not None:
        out["GL_CLI"] = cli
    if g20 is not None:
        out["GL_CLI_G20"] = g20
    if kor_ip is not None:
        out["GL_KOR_IP"] = kor_ip
    if kor_ex is not None:
        out["GL_KOR_EX"] = kor_ex
    return out


def _eurostat_unemployment(geo: str, refresh: bool) -> pd.Series | None:
    dest = CACHE / f"eurostat_un_{geo}.json"
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/une_rt_m"
        f"?format=JSON&lang=EN&geo={geo}&s_adj=SA&age=TOTAL&sex=T&unit=PC_ACT&sinceTimePeriod=1995-01"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=60):
            return None
    try:
        payload = json.loads(dest.read_text(encoding="utf-8"))
        times = list(payload["dimension"]["time"]["category"]["index"])
        size = payload["size"]
        values = payload.get("value") or {}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if not values or not times:
        return None
    rows = []
    for key, value in values.items():
        index = int(key)
        coords = []
        for length in reversed(size):
            coords.append(index % length)
            index //= length
        coords.reverse()
        if coords[-1] >= len(times):
            continue
        rows.append((pd.Timestamp(times[coords[-1]]), float(value)))
    if not rows:
        return None
    series = pd.Series({date: value for date, value in rows})
    return _month_end(series)


def _japan_cpi(refresh: bool) -> pd.Series | None:
    dest = CACHE / "oecd_jpn_cpi.csv"
    url = (
        "https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_G20_PRICES@DF_G20_PRICES,1.0/"
        "JPN.M......?startPeriod=1995-01&format=csvfile"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=90):
            return None
    frame = _read(dest)
    if frame is None or "OBS_VALUE" not in frame.columns:
        return None
    mask = (frame["MEASURE"] == "CPI") & (frame["UNIT_MEASURE"] == "IX")
    rows = frame.loc[mask]
    if rows.empty:
        return None
    index = pd.to_datetime(rows["TIME_PERIOD"], errors="coerce")
    series = pd.Series(pd.to_numeric(rows["OBS_VALUE"], errors="coerce").to_numpy(), index=index)
    return _month_end(series)


def _bls(series_id: str, refresh: bool) -> pd.Series | None:
    dest = CACHE / f"bls_{series_id}.json"
    if refresh or not dest.is_file():
        rows = []
        for start in (1996, 2006, 2016, 2017):
            end = start + 9
            payload = json.dumps({"seriesid": [series_id], "startyear": str(start), "endyear": str(end)}).encode()
            tmp = CACHE / f"bls_{series_id}_{start}.json"
            # POST via curl
            import subprocess
            result = subprocess.run(
                [
                    "curl", "--http2", "--ipv4", "-fsSL", "--max-time", "40",
                    "-A", "LeonixResearch/1.0 (macro research)",
                    "-H", "Content-Type: application/json",
                    "-o", str(tmp),
                    "-d", payload.decode(),
                    "https://api.bls.gov/publicAPI/v2/timeseries/data/",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0 or not tmp.is_file():
                continue
            try:
                body = json.loads(tmp.read_text(encoding="utf-8"))
                for item in body["Results"]["series"][0]["data"]:
                    if not str(item.get("period", "")).startswith("M") or item.get("period") == "M13":
                        continue
                    rows.append({"date": f"{item['year']}-{item['period'][1:]}-01", "value": item.get("value")})
            except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue
            tmp.unlink(missing_ok=True)
        if not rows:
            return None
        pd.DataFrame(rows).drop_duplicates("date").to_json(dest, orient="records")
    try:
        frame = pd.read_json(dest)
    except (OSError, ValueError):
        return None
    if frame.empty:
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    series = pd.Series(frame["value"].to_numpy(), index=pd.DatetimeIndex(frame["date"]))
    return _month_end(series)


def _g17(refresh: bool) -> pd.Series | None:
    dest = CACHE / "g17_ip_sa.txt"
    url = "https://www.federalreserve.gov/releases/g17/Current/ipdisk/ip_sa.txt"
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=60):
            return None
    try:
        text = dest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rows = []
    for line in text.splitlines():
        if '"B50001"' not in line or "Total" in line:
            continue
        parts = line.replace('"', "").split()
        if len(parts) < 3:
            continue
        year = parts[1]
        for month, raw in enumerate(parts[2:14], start=1):
            try:
                rows.append((pd.Timestamp(year=int(year), month=month, day=1), float(raw)))
            except ValueError:
                continue
    if not rows:
        return None
    series = pd.Series({date: value for date, value in rows})
    return _month_end(series)


def _h15_slopes(refresh: bool) -> dict[str, pd.Series]:
    dest = CACHE / "h15_treasuries.csv"
    url = (
        "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H15"
        "&series=bf17364827e38702b42a58cf8eaa3f78&filetype=csv&label=include&layout=seriescolumn"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=60):
            return {}
    frame = _read(dest, header=None)
    if frame is None or frame.shape[0] < 8:
        return {}
    codes = [str(value).strip() for value in frame.iloc[5].tolist()]
    data = frame.iloc[6:].copy()
    data.columns = codes
    data = data.rename(columns={codes[0]: "date"})
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    out = {}
    for name, left, right in (
        ("USA_S2", "RIFLGFCY10_N.B", "RIFLGFCY02_N.B"),
        ("USA_SLOPE", "RIFLGFCY10_N.B", "RIFLGFCM03_N.B"),
    ):
        if left not in data.columns or right not in data.columns:
            continue
        spread = pd.to_numeric(data[left], errors="coerce") - pd.to_numeric(data[right], errors="coerce")
        series = _month_end(pd.Series(spread.to_numpy(), index=pd.DatetimeIndex(data["date"])))
        if series is not None and not series.empty:
            out[name] = series
    slope = out.get("USA_SLOPE")
    if slope is not None:
        # Positive only when the 3-month yield is above the 10-year yield.
        out["USA_INV"] = (-slope).clip(lower=0.0)
    return out


def _h41(refresh: bool) -> pd.Series | None:
    dest = CACHE / "h41_assets.csv"
    url = (
        "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H41"
        "&series=17398fbf71bc6a47df150bceebdea2bc&filetype=csv&label=include&layout=seriescolumn"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=60):
            return None
    frame = _read(dest, header=None)
    if frame is None or frame.shape[0] < 8:
        return None
    codes = [str(value).strip() for value in frame.iloc[5].tolist()]
    data = frame.iloc[6:].copy()
    data.columns = codes
    date_col = codes[0]
    value_col = "RESPPA_N.WW" if "RESPPA_N.WW" in data.columns else codes[1]
    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    series = pd.Series(pd.to_numeric(data[value_col], errors="coerce").to_numpy(), index=pd.DatetimeIndex(data[date_col]))
    return _month_end(series)


def _h6_m2(refresh: bool) -> pd.Series | None:
    dest = CACHE / "h6_money.csv"
    url = (
        "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H6"
        "&series=798e2796917702a5f8423426ba7e6b42&filetype=csv&label=include&layout=seriescolumn"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=60):
            return None
    frame = _read(dest, header=None)
    if frame is None or frame.shape[0] < 8:
        return None
    descriptions = [str(value) for value in frame.iloc[0].tolist()]
    chosen = None
    for index, text in enumerate(descriptions):
        low = text.lower()
        if "m2" in low and "seasonally adjusted" in low and "not seasonally" not in low:
            chosen = index
            break
    if chosen is None:
        return None
    codes = [str(value).strip() for value in frame.iloc[5].tolist()]
    data = frame.iloc[6:].copy()
    data.columns = codes
    date_col = codes[0]
    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    series = pd.Series(pd.to_numeric(data.iloc[:, chosen], errors="coerce").to_numpy(), index=pd.DatetimeIndex(data[date_col]))
    return _month_end(series)


def _nfci(refresh: bool) -> pd.Series | None:
    dest = CACHE / "nfci.csv"
    url = "https://www.chicagofed.org/-/media/publications/nfci/nfci-data-series-csv.csv"
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=40):
            return None
    frame = _read(dest)
    if frame is None or "NFCI" not in frame.columns:
        return None
    date_col = frame.columns[0]
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
    series = pd.Series(pd.to_numeric(frame["NFCI"], errors="coerce").to_numpy(), index=pd.DatetimeIndex(frame[date_col]))
    return _month_end(series)


def _bis_dollar(refresh: bool) -> pd.Series | None:
    dest = CACHE / "bis_eer_us.csv"
    url = "https://stats.bis.org/api/v1/data/WS_EER/M.R.B.US?startPeriod=1995&format=csv"
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=40):
            return None
    frame = _read(dest)
    if frame is None or "OBS_VALUE" not in frame.columns:
        return None
    index = pd.to_datetime(frame["TIME_PERIOD"], errors="coerce")
    series = pd.Series(pd.to_numeric(frame["OBS_VALUE"], errors="coerce").to_numpy(), index=index)
    return _month_end(series)


def _ecb(path: str, dest_name: str, refresh: bool) -> pd.Series | None:
    dest = CACHE / dest_name
    url = f"https://data-api.ecb.europa.eu/service/data/{path}&startPeriod=1999-01"
    if "format=" not in path:
        url = f"https://data-api.ecb.europa.eu/service/data/{path}?format=csvdata&startPeriod=1999-01"
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=90):
            return None
    frame = _read(dest)
    if frame is None or "OBS_VALUE" not in frame.columns:
        return None
    index = pd.to_datetime(frame["TIME_PERIOD"], errors="coerce")
    series = pd.Series(pd.to_numeric(frame["OBS_VALUE"], errors="coerce").to_numpy(), index=index)
    return _month_end(series)


def _treasury_breakeven(refresh: bool) -> pd.Series | None:
    frames = []
    for kind, name in (
        ("daily_treasury_yield_curve", "nominal"),
        ("daily_treasury_real_yield_curve", "real"),
    ):
        pieces = []
        for year in range(2003, 2027):
            dest = CACHE / f"treasury_{name}_{year}.csv"
            url = (
                "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                f"daily-treasury-rates.csv/{year}/all?type={kind}&field_tdr_date_value={year}&page&_format=csv"
            )
            if refresh or not dest.is_file():
                if not _curl(url, dest, timeout=40):
                    continue
            frame = _read(dest)
            if frame is None or frame.empty:
                continue
            pieces.append(frame)
        if not pieces:
            return None
        frames.append(pd.concat(pieces, ignore_index=True))
    nominal, real = frames
    def _column(frame: pd.DataFrame, name: str) -> str | None:
        for column in frame.columns:
            if str(column).strip().lower() == name.lower():
                return str(column)
        return None

    nominal_col = _column(nominal, "10 yr")
    real_col = _column(real, "10 yr")
    date_col_n = _column(nominal, "date")
    date_col_r = _column(real, "date")
    if not nominal_col or not real_col or not date_col_n or not date_col_r:
        return None
    nominal[date_col_n] = pd.to_datetime(nominal[date_col_n], errors="coerce")
    real[date_col_r] = pd.to_datetime(real[date_col_r], errors="coerce")
    left = nominal.set_index(date_col_n)[nominal_col]
    right = real.set_index(date_col_r)[real_col]
    spread = pd.to_numeric(left, errors="coerce").sub(pd.to_numeric(right, errors="coerce"))
    return _month_end(spread)


def _fx_usd(refresh: bool) -> dict[str, pd.Series]:
    """ECB quotes per EUR, crossed into a USD pair."""
    dest = CACHE / "ecb_exr.csv"
    url = (
        "https://data-api.ecb.europa.eu/service/data/EXR/"
        "M.USD+JPY+CHF+CNY+GBP.EUR.SP00.A?format=csvdata&startPeriod=1999-01"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=90):
            return {}
    frame = _read(dest)
    if frame is None or "CURRENCY" not in frame.columns:
        return {}
    frame["TIME_PERIOD"] = pd.to_datetime(frame["TIME_PERIOD"], errors="coerce")
    wide = {}
    for currency, rows in frame.groupby("CURRENCY"):
        series = pd.Series(
            pd.to_numeric(rows["OBS_VALUE"], errors="coerce").to_numpy(),
            index=pd.DatetimeIndex(rows["TIME_PERIOD"]),
        )
        wide[str(currency)] = _month_end(series)
    usd = wide.get("USD")
    if usd is None or usd.empty:
        return {}
    out = {"EZ_EURUSD": usd}
    # Foreign currency per EUR divided by USD per EUR = foreign currency per USD.
    for currency, series_id, invert in (
        ("GBP", "UK_FX", True),
        ("CHF", "CH_FX", False),
        ("JPY", "JP_FX", False),
        ("CNY", "CN_FX", False),
    ):
        other = wide.get(currency)
        if other is None:
            continue
        pair = other.div(usd)
        if invert:
            pair = 1.0 / pair
        pair = pair.replace([float("inf"), float("-inf")], pd.NA).dropna()
        if not pair.empty:
            out[series_id] = pair
    return out


def _chain_index_from_yoy(yoy_percent: pd.Series) -> pd.Series:
    """Level index whose 12-month change equals the published year-on-year rate.

    The first year is a flat base of 100. From the thirteenth month the
    model's existing year-on-year transform reproduces the official rate.
    """
    rates = pd.to_numeric(yoy_percent, errors="coerce").dropna().sort_index()
    rates = rates[~rates.index.duplicated(keep="last")]
    if rates.empty:
        return rates
    rates.index = rates.index.to_period("M").to_timestamp("M")
    rates = rates[~rates.index.duplicated(keep="last")]
    level: dict[pd.Timestamp, float] = {}
    dates = list(rates.index)
    for i, stamp in enumerate(dates):
        if i < 12:
            level[stamp] = 100.0
            continue
        base = level.get(dates[i - 12])
        if base is None:
            continue
        level[stamp] = base * (1.0 + float(rates.loc[stamp]) / 100.0)
    return pd.Series(level, dtype=float).sort_index()


def _eurostat_hicp_yoy(refresh: bool) -> pd.Series | None:
    """Euro area HICP annual rate, item TOTAL, from prc_hicp_minr."""
    dest = CACHE / "eurostat_hicp_minr_total_ea.csv"
    url = (
        "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/"
        "prc_hicp_minr/M.RCH_A.TOTAL.EA?format=SDMX-CSV&startPeriod=1996-01"
    )
    if refresh or not dest.is_file():
        if not _curl(url, dest, timeout=60):
            return None
    frame = _read(dest)
    if frame is None or "OBS_VALUE" not in frame.columns or "TIME_PERIOD" not in frame.columns:
        return None
    frame = frame.copy()
    frame["TIME_PERIOD"] = pd.to_datetime(frame["TIME_PERIOD"], errors="coerce")
    frame["OBS_VALUE"] = pd.to_numeric(frame["OBS_VALUE"], errors="coerce")
    frame = frame.dropna(subset=["TIME_PERIOD", "OBS_VALUE"])
    series = pd.Series(frame["OBS_VALUE"].to_numpy(), index=pd.DatetimeIndex(frame["TIME_PERIOD"]))
    return _month_end(series)


def _bfs_lik_index(refresh: bool) -> pd.Series | None:
    """Swiss CPI total, LIK, base December 2025 = 100."""
    import re
    import xml.etree.ElementTree as ET
    import zipfile
    from datetime import datetime, timedelta

    dest = CACHE / "bfs_lik_index.csv"
    if not refresh and dest.is_file():
        frame = _read(dest)
        if frame is not None and frame.shape[1] >= 2:
            frame.columns = ["date", "value"]
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
            frame = frame.dropna()
            if not frame.empty:
                series = pd.Series(frame["value"].to_numpy(), index=pd.DatetimeIndex(frame["date"]))
                return _month_end(series)
    page_url = "https://www.bfs.admin.ch/bfs/de/home/statistiken/preise/landesindex-konsumentenpreise.html"
    import subprocess
    page_dest = CACHE / "bfs_lik_page.html"
    if not _curl(page_url, page_dest, timeout=40):
        return None
    page = page_dest.read_text(encoding="utf-8", errors="replace")
    ids: list[str] = []
    for asset_id in re.findall(r"dam/assets/(\d+)/master", page) + re.findall(r"/asset/(\d+)", page):
        if asset_id not in ids:
            ids.append(asset_id)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for asset_id in ids:
        blob_path = CACHE / f"bfs_{asset_id}.xlsx"
        url = f"https://dam-api.bfs.admin.ch/hub/api/dam/assets/{asset_id}/master"
        if not _curl(url, blob_path, timeout=60):
            continue
        try:
            archive = zipfile.ZipFile(blob_path)
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            blob_path.unlink(missing_ok=True)
            continue
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        target = None
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            if sheet.attrib.get("name") == "INDEX_m":
                rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                target = targets.get(rel_id)
                break
        if not target:
            blob_path.unlink(missing_ok=True)
            continue
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared.findall("m:si", ns):
                strings.append("".join(node.text or "" for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
        path = target if target.startswith("xl/") else "xl/" + target
        sheet = ET.fromstring(archive.read(path))
        rows = []
        for row in sheet.findall("m:sheetData/m:row", ns):
            values = []
            for cell in row.findall("m:c", ns):
                node = cell.find("m:v", ns)
                if node is None or node.text is None:
                    values.append(None)
                    continue
                if cell.attrib.get("t") == "s":
                    values.append(strings[int(node.text)])
                else:
                    values.append(node.text)
            rows.append(values)
        blob_path.unlink(missing_ok=True)
        if len(rows) < 5:
            continue
        title = " ".join(str(cell) for cell in rows[2] if cell)
        if "Dezember 2025" not in title and "dicembre 2025" not in title.lower():
            continue
        total = next((row for row in rows if row and row[0] == "100_100"), None)
        if total is None:
            continue
        points = []
        for header, value in zip(rows[3], total):
            if header is None or value is None:
                continue
            try:
                serial = float(header)
                level = float(value)
            except (TypeError, ValueError):
                continue
            stamp = datetime(1899, 12, 30) + timedelta(days=serial)
            points.append((pd.Timestamp(stamp), level))
        if len(points) < 13:
            continue
        series = pd.Series({stamp: level for stamp, level in points})
        series = _month_end(series)
        dest.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": series.index, "value": series.to_numpy()}).to_csv(dest, index=False)
        return series
    return None


LAST_NOTES: dict[str, str] = {}


def load_official(refresh: bool = False) -> dict[str, pd.Series]:
    """Every series this environment can build without a FRED key."""
    LAST_NOTES.clear()
    out: dict[str, pd.Series] = {}
    frame = _oecd_frame(refresh)
    if frame is not None:
        out.update(_from_oecd(frame))
    japan = _japan_cpi(refresh)
    if japan is not None and (out.get("JP_CPI") is None or japan.index.max() > out["JP_CPI"].index.max()):
        out["JP_CPI"] = japan
        LAST_NOTES["JP_CPI"] = "OECD G20 consumer prices"
    euro_yoy = _eurostat_hicp_yoy(refresh)
    if euro_yoy is not None and len(euro_yoy) >= 13:
        euro_index = _chain_index_from_yoy(euro_yoy)
        current = out.get("EZ_CPI")
        if current is None or current.empty or euro_index.index.max() > current.index.max():
            out["EZ_CPI"] = euro_index
            LAST_NOTES["EZ_CPI"] = "Eurostat prc_hicp_minr TOTAL EA, index chained so the model yoy matches the official rate"
    swiss = _bfs_lik_index(refresh)
    if swiss is not None and len(swiss) >= 13:
        current = out.get("CH_CPI")
        if current is None or current.empty or swiss.index.max() > current.index.max():
            out["CH_CPI"] = swiss
            LAST_NOTES["CH_CPI"] = "BFS LIK total, base December 2025"
    ez_un = _eurostat_unemployment("EA21", refresh)
    if ez_un is not None:
        out["EZ_UN"] = ez_un
        LAST_NOTES["EZ_UN"] = "Eurostat une_rt_m, euro area EA21, seasonally adjusted"
    ch_un = _eurostat_unemployment("CH", refresh)
    if ch_un is not None:
        out["CH_UN"] = ch_un
        LAST_NOTES["CH_UN"] = "Eurostat une_rt_m, Switzerland"
    g17 = _g17(refresh)
    if g17 is not None:
        out["USA_IP"] = g17
        LAST_NOTES["USA_IP"] = "Federal Reserve G.17 total index"
    for series_id, bls_id in (
        ("USA_PAY", "CES0000000001"),
        ("USA_UN", "LNS14000000"),
        ("USA_CPI", "CUSR0000SA0"),
        ("USA_CORE", "CUSR0000SA0L1E"),
        ("USA_PPI", "WPU00000000"),
    ):
        series = _bls(bls_id, refresh)
        if series is not None:
            out[series_id] = series
            LAST_NOTES[series_id] = f"BLS {bls_id}"
    m2 = _h6_m2(refresh)
    if m2 is not None:
        out["USA_M2"] = m2
        LAST_NOTES["USA_M2"] = "Federal Reserve H.6 M2, seasonally adjusted"
    assets = _h41(refresh)
    if assets is not None:
        out["USA_WALCL"] = assets
        LAST_NOTES["USA_WALCL"] = "Federal Reserve H.4.1 total assets"
    slopes = _h15_slopes(refresh)
    out.update(slopes)
    if "USA_S2" in slopes:
        LAST_NOTES["USA_S2"] = "Federal Reserve H.15 10-year minus 2-year"
    if "USA_SLOPE" in slopes:
        LAST_NOTES["USA_SLOPE"] = "Federal Reserve H.15 10-year minus 3-month"
    nfci = _nfci(refresh)
    if nfci is not None:
        out["USA_NFCI"] = nfci
    dollar = _bis_dollar(refresh)
    if dollar is not None:
        out["GL_DOLLAR"] = dollar
    dfr = _ecb("FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata", "ecb_dfr.csv", refresh)
    if dfr is not None:
        out["EZ_DFR"] = dfr
    ciss = _ecb("CISS/D.U2.Z0Z.4F.EC.SS_CIN.IDX?format=csvdata", "ecb_ciss_cin.csv", refresh)
    if ciss is not None and ciss.median() < 2:
        out["EZ_CISS"] = ciss
    out.update(_fx_usd(refresh))
    breakeven = _treasury_breakeven(refresh)
    if breakeven is not None:
        out["USA_BE"] = breakeven
    return {key: series for key, series in out.items() if series is not None and not series.empty}
