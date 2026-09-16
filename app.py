import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

st.set_page_config(page_title="Bond & Macro Dashboard", page_icon="📊", layout="wide")

# ==============================
# Secrets
# ==============================
ECOS_API_KEY = st.secrets.get("ECOS_API_KEY", "")
EIA_API_KEY = st.secrets.get("EIA_API_KEY", "")
BLS_API_KEY = st.secrets.get("BLS_API_KEY", "")

# ==============================
# 공통 함수
# ==============================
def empty_df():
    return pd.DataFrame(columns=["date", "value"])

def get_latest_change(df, value_type):
    if df.empty:
        return None, None, None
    df = df.dropna().sort_values("date").reset_index(drop=True)
    if len(df) == 0:
        return None, None, None
    current = float(df.iloc[-1]["value"])
    current_date = df.iloc[-1]["date"]
    if len(df) < 2:
        return current_date, current, None
    previous = float(df.iloc[-2]["value"])
    if value_type in ["rate", "policy"]:
        change = (current - previous) * 100
    elif value_type == "pp":
        change = current - previous
    elif value_type == "nfp":
        change = current - previous
    elif value_type == "yoy":
        target = current_date - pd.DateOffset(months=12)
        past = df[df["date"] <= target]
        change = (current / float(past.iloc[-1]["value"]) - 1) * 100 if not past.empty and float(past.iloc[-1]["value"]) != 0 else None
    else:
        change = (current / previous - 1) * 100 if previous != 0 else None
    return current_date, current, change

def add_row(rows, country, name, df, freq, value_type):
    if df is None or df.empty:
        return
    date, value, change = get_latest_change(df, value_type)
    if date is None:
        return
    rows.append({"구분": country, "지표": name, "기준일": date, "현재": value, "변화값": change, "빈도": freq, "type": value_type})

# ==============================
# Treasury
# Treasury 공식 XML feed: 일별 미국 국채 금리
# ==============================
@st.cache_data(ttl=600)
def get_treasury_curve():
    year = datetime.now(ZoneInfo("Asia/Seoul")).year
    url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        rows = []
        for entry in root.iter():
            tag = entry.tag.split("}")[-1]
            if tag == "entry":
                record = {}
                for child in entry.iter():
                    key = child.tag.split("}")[-1]
                    if child.text and key not in record:
                        record[key] = child.text.strip()
                date = record.get("NEW_DATE")
                if not date:
                    continue
                row = {"date": pd.to_datetime(date, errors="coerce")}
                for key in ["BC_2YEAR", "BC_5YEAR", "BC_10YEAR", "BC_30YEAR"]:
                    row[key] = pd.to_numeric(record.get(key), errors="coerce")
                rows.append(row)
        df = pd.DataFrame(rows)
        if df.empty:
            return {}
        df = df.dropna(subset=["date"]).sort_values("date")
        out = {}
        for col, name in [("BC_2YEAR", "미국 2Y"), ("BC_5YEAR", "미국 5Y"), ("BC_10YEAR", "미국 10Y"), ("BC_30YEAR", "미국 30Y")]:
            if col in df:
                out[name] = df[["date", col]].rename(columns={col: "value"}).dropna()
        return out
    except Exception:
        return {}

@st.cache_data(ttl=600)
def get_treasury_real_curve():
    year = datetime.now(ZoneInfo("Asia/Seoul")).year
    url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value={year}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        rows = []
        for entry in root.iter():
            if entry.tag.split("}")[-1] != "entry":
                continue
            record = {}
            for child in entry.iter():
                key = child.tag.split("}")[-1]
                if child.text and key not in record:
                    record[key] = child.text.strip()
            date = record.get("NEW_DATE")
            if not date:
                continue
            rows.append({"date": pd.to_datetime(date, errors="coerce"), "real5": pd.to_numeric(record.get("TC_5YEAR"), errors="coerce"), "real10": pd.to_numeric(record.get("TC_10YEAR"), errors="coerce")})
        df = pd.DataFrame(rows).dropna(subset=["date"]).sort_values("date")
        return {"미국 실질 5Y": df[["date", "real5"]].rename(columns={"real5": "value"}).dropna(), "미국 실질 10Y": df[["date", "real10"]].rename(columns={"real10": "value"}).dropna()}
    except Exception:
        return {}

# ==============================
# BLS
# CPI / Core CPI / 실업률 / NFP
# ==============================
@st.cache_data(ttl=1800)
def get_bls(series_ids):
    end_year = datetime.now(ZoneInfo("Asia/Seoul")).year
    start_year = end_year - 5
    payload = {"seriesid": series_ids, "startyear": str(start_year), "endyear": str(end_year)}
    if BLS_API_KEY:
        payload["registrationkey"] = BLS_API_KEY
    try:
        r = requests.post("https://api.bls.gov/publicAPI/v2/timeseries/data/", json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        result = {}
        for series in data.get("Results", {}).get("series", []):
            rows = []
            for x in series.get("data", []):
                period = x.get("period", "")
                if not period.startswith("M"):
                    continue
                date = pd.to_datetime(f"{x['year']}-{period[1:]}-01", errors="coerce")
                value = pd.to_numeric(x.get("value"), errors="coerce")
                rows.append({"date": date, "value": value})
            result[series["seriesID"]] = pd.DataFrame(rows).dropna().sort_values("date")
        return result
    except Exception:
        return {}

# ==============================
# EIA - WTI
# ==============================
@st.cache_data(ttl=1800)
def get_wti():
    if not EIA_API_KEY:
        return empty_df()
    url = f"https://api.eia.gov/v2/petroleum/pri/spt/data/?api_key={EIA_API_KEY}&frequency=daily&data[0]=value&facets[series][]=RWTC&sort[0][column]=period&sort[0][direction]=desc&length=100"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.json().get("response", {}).get("data", [])
        rows = [{"date": pd.to_datetime(x.get("period"), errors="coerce"), "value": pd.to_numeric(x.get("value"), errors="coerce")} for x in data]
        return pd.DataFrame(rows).dropna().sort_values("date")
    except Exception:
        return empty_df()

# ==============================
# New York Fed - EFFR
# ==============================
@st.cache_data(ttl=1800)
def get_effr():
    urls = [
        "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate=2020-01-01&type=rate",
        "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate=2020-01-01&type=rate"
    ]
    try:
        r = requests.get(urls[1], timeout=20)
        r.raise_for_status()
        data = r.json()
        rows = []
        for x in data.get("refRates", data.get("data", [])):
            date = pd.to_datetime(x.get("effectiveDate", x.get("date")), errors="coerce")
            value = pd.to_numeric(x.get("percentRate", x.get("rate")), errors="coerce")
            if pd.notna(date) and pd.notna(value):
                rows.append({"date": date, "value": value})
        return pd.DataFrame(rows).dropna().sort_values("date")
    except Exception:
        return empty_df()

# ==============================
# ECOS
# ==============================
@st.cache_data(ttl=1800)
def get_ecos(stat, item, cycle):
    if not ECOS_API_KEY:
        return empty_df()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    start, end = ("20200101", now.strftime("%Y%m%d")) if cycle == "D" else ("202001", now.strftime("%Y%m"))
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/{stat}/{cycle}/{start}/{end}"
    if item:
        url += f"/{item}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        rows = r.json().get("StatisticSearch", {}).get("row", [])
        if not rows:
            return empty_df()
        df = pd.DataFrame(rows)
        fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
        df["date"] = pd.to_datetime(df["TIME"], format=fmt, errors="coerce")
        df["value"] = pd.to_numeric(df["DATA_VALUE"], errors="coerce")
        return df[["date", "value"]].dropna().sort_values("date")
    except Exception:
        return empty_df()

# ==============================
# ECOS 지표 설정
# ==============================
ecos_series = {
    "한국 국고채 3Y": {"stat": "817Y002", "item": "010200000", "cycle": "D", "freq": "일간", "type": "rate"},
    "한국 국고채 10Y": {"stat": "817Y002", "item": "010210000", "cycle": "D", "freq": "일간", "type": "rate"},
    "한국 회사채 AA- 3Y": {"stat": "817Y002", "item": "010310000", "cycle": "D", "freq": "일간", "type": "rate"},
    "USD/KRW": {"stat": "731Y003", "item": "0000003", "cycle": "D", "freq": "일간", "type": "pct"},
    "한국 기준금리": {"stat": "722Y001", "item": "0101000", "cycle": "D", "freq": "정책금리", "type": "policy"},
    "한국 CPI": {"stat": "901Y009", "item": "0", "cycle": "M", "freq": "월간", "type": "yoy"},
    "한국 산업생산": {"stat": "901Y033", "item": None, "cycle": "M", "freq": "월간", "type": "yoy"}
}

@st.cache_data(ttl=3600)
def find_ecos_item(stat, keywords):
    if not ECOS_API_KEY:
        return None
    url = f"https://ecos.bok.or.kr/api/StatisticItemList/{ECOS_API_KEY}/json/kr/1/10000/{stat}"
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        rows = r.json().get("StatisticItemList", {}).get("row", [])
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        name_cols = [c for c in df.columns if c.startswith("ITEM_NAME")]
        for _, row in df.iterrows():
            text = " ".join(str(row[c]) for c in name_cols if pd.notna(row[c]))
            if all(k in text for k in keywords):
                return row.get("ITEM_CODE1")
    except Exception:
        return None
    return None

ecos_series["한국 산업생산"]["item"] = find_ecos_item("901Y033", ["전산업생산지수"])

# ==============================
# 데이터 로딩
# ==============================
with st.spinner("미국·한국 채권 및 거시경제 데이터를 불러오는 중입니다..."):
    treasury = get_treasury_curve()
    treasury_real = get_treasury_real_curve()
    bls = get_bls(["CUSR0000SA0", "CUSR0000SA0L1E", "LNS14000000", "CES0000000001"])
    wti = get_wti()
    effr = get_effr()
    ecos = {name: get_ecos(info["stat"], info["item"], info["cycle"]) for name, info in ecos_series.items()}

# ==============================
# 결과 구성
# ==============================
rows = []
for name in ["미국 2Y", "미국 5Y", "미국 10Y", "미국 30Y"]:
    add_row(rows, "미국", name, treasury.get(name, empty_df()), "일간", "rate")

if not treasury.get("미국 2Y", empty_df()).empty and not treasury.get("미국 10Y", empty_df()).empty:
    curve = pd.merge(treasury["미국 2Y"], treasury["미국 10Y"], on="date", suffixes=("_2Y", "_10Y"))
    curve["value"] = curve["value_10Y"] - curve["value_2Y"]
    add_row(rows, "미국", "미국 10Y-2Y", curve[["date", "value"]], "일간", "rate")

if not treasury.get("미국 5Y", empty_df()).empty and not treasury_real.get("미국 실질 5Y", empty_df()).empty:
    be5 = pd.merge(treasury["미국 5Y"], treasury_real["미국 실질 5Y"], on="date", suffixes=("_nominal", "_real"))
    be5["value"] = be5["value_nominal"] - be5["value_real"]
    add_row(rows, "미국", "미국 5Y 기대인플레이션", be5[["date", "value"]], "일간", "rate")

if not treasury.get("미국 10Y", empty_df()).empty and not treasury_real.get("미국 실질 10Y", empty_df()).empty:
    be10 = pd.merge(treasury["미국 10Y"], treasury_real["미국 실질 10Y"], on="date", suffixes=("_nominal", "_real"))
    be10["value"] = be10["value_nominal"] - be10["value_real"]
    add_row(rows, "미국", "미국 10Y 기대인플레이션", be10[["date", "value"]], "일간", "rate")

add_row(rows, "미국", "미국 실질 10Y", treasury_real.get("미국 실질 10Y", empty_df()), "일간", "rate")
add_row(rows, "미국", "EFFR", effr, "정책금리", "policy")
add_row(rows, "미국", "WTI", wti, "일간", "pct")

# BLS
for sid, name, value_type in [
    ("CUSR0000SA0", "미국 CPI", "yoy"),
    ("CUSR0000SA0L1E", "미국 Core CPI", "yoy"),
    ("LNS14000000", "미국 실업률", "pp"),
    ("CES0000000001", "미국 NFP", "nfp")
]:
    add_row(rows, "미국", name, bls.get(sid, empty_df()), "월간", value_type)

# 한국
for name, info in ecos_series.items():
    add_row(rows, "한국", name, ecos.get(name, empty_df()), info["freq"], info["type"])

if not ecos.get("한국 국고채 3Y", empty_df()).empty and not ecos.get("한국 국고채 10Y", empty_df()).empty:
    curve = pd.merge(ecos["한국 국고채 3Y"], ecos["한국 국고채 10Y"], on="date", suffixes=("_3Y", "_10Y"))
    curve["value"] = curve["value_10Y"] - curve["value_3Y"]
    add_row(rows, "한국", "한국 10Y-3Y", curve[["date", "value"]], "일간", "rate")

# ==============================
# 표시
# ==============================
result = pd.DataFrame(rows)

def format_change(change, value_type):
    if pd.isna(change):
        return "-"
    sign = "+" if change > 0 else ""
    icon = "🔴" if change > 0 else "🔵" if change < 0 else "⚪"
    if value_type in ["rate", "policy"]:
        return f"{icon} {sign}{change:.1f}bp"
    if value_type == "pp":
        return f"{icon} {sign}{change:.1f}%p"
    if value_type == "nfp":
        return f"{icon} {sign}{change:.0f}K"
    return f"{icon} {sign}{change:.1f}%"

if result.empty:
    st.error("데이터를 불러오지 못했습니다. Streamlit Secrets의 API 키와 각 API 상태를 확인해주세요.")
else:
    result["변화"] = [format_change(c, t) for c, t in zip(result["변화값"], result["type"])]
    result["기준일"] = pd.to_datetime(result["기준일"]).dt.strftime("%Y-%m-%d")
    result["현재"] = result["현재"].apply(lambda x: f"{x:,.2f}")
    st.title("Bond & Macro Dashboard")
    st.caption("한국시간 오전 9시 기준으로 확인하기 위한 미국·한국 채권시장 및 주요 거시경제 지표")
    latest = result["기준일"].max()
    st.info(f"표시되는 기준일은 각 데이터 제공기관의 **최신 발표일**입니다. 오늘 날짜와 다를 수 있습니다. 전체 데이터 조회 기준: {latest}")
    for freq in ["일간", "월간", "정책금리"]:
        temp = result[result["빈도"] == freq]
        if temp.empty:
            continue
        st.subheader(f"📊 {freq} 지표")
        st.dataframe(temp[["구분", "지표", "기준일", "현재", "변화"]].reset_index(drop=True), use_container_width=True, hide_index=True)
    st.caption("출처: U.S. Treasury · U.S. Bureau of Labor Statistics · U.S. Energy Information Administration · Federal Reserve Bank of New York · Bank of Korea ECOS")
