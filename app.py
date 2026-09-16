import streamlit as st
import pandas as pd
import requests
import xml.etree.ElementTree as ET
import re
import html
from datetime import datetime
from zoneinfo import ZoneInfo

st.set_page_config(page_title="Bond & Macro Dashboard", page_icon="📊", layout="wide")

# ==============================
# 시간대 / 상단 컨트롤
# ==============================
KOREA_TZ = ZoneInfo("Asia/Seoul")
NEW_YORK_TZ = ZoneInfo("America/New_York")

# 한국과 미국(뉴욕) 현재 시각 및 시차
now_utc = datetime.now(ZoneInfo("UTC"))
now_korea = now_utc.astimezone(KOREA_TZ)
now_new_york = now_utc.astimezone(NEW_YORK_TZ)
ny_tz_name = now_new_york.tzname() or "ET"

time_diff_hours = (
    now_korea.utcoffset().total_seconds() - now_new_york.utcoffset().total_seconds()
) / 3600

if "last_updated_krt" not in st.session_state:
    st.session_state["last_updated_krt"] = None

st.title("Bond & Macro Dashboard")
st.caption("한국시간 오전 9시 기준으로 확인하기 위한 미국·한국 채권시장 및 주요 거시경제 지표")

time_col, refresh_col = st.columns([4.5, 1.3])
with time_col:
    diff_text = f"{time_diff_hours:g}시간"
    st.markdown(
        f"**🇰🇷 한국 KST** {now_korea.strftime('%Y-%m-%d %H:%M:%S')}  "
        f"｜ **🇺🇸 미국 뉴욕 {ny_tz_name}** {now_new_york.strftime('%Y-%m-%d %H:%M:%S')}  "
        f"｜ **시차** 한국이 뉴욕보다 {diff_text} 빠름"
    )
with refresh_col:
    refresh_clicked = st.button("🔄 최신 데이터 수집", use_container_width=True, type="primary")

# 버튼을 누르면 TTL과 무관하게 모든 캐시를 비워 각 API에서 다시 수집
if refresh_clicked:
    st.cache_data.clear()

# ==============================
# Secrets
# ==============================
ECOS_API_KEY = st.secrets.get("ECOS_API_KEY", "")
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
    rows.append({
        "구분": country,
        "지표": name,
        "기준일": date,
        "현재": value,
        "현재표시": None,
        "변화값": change,
        "빈도": freq,
        "type": value_type
    })

# ==============================
# Treasury
# Treasury 공식 XML feed: 일별 미국 국채 금리
# ==============================
@st.cache_data(ttl=600)
def get_treasury_curve():
    year = datetime.now(KOREA_TZ).year
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
    year = datetime.now(KOREA_TZ).year
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
    end_year = datetime.now(KOREA_TZ).year
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
# Market WTI Futures - Yahoo Finance
# CL=F: NYMEX WTI Crude Oil Futures
# EIA 현물가격보다 업데이트가 빠른 시장가격을 사용합니다.
# ==============================
@st.cache_data(ttl=300)
def get_wti():
    url = "https://query1.finance.yahoo.com/v8/finance/chart/CL%3DF"
    params = {
        "range": "1mo",
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits"
    }
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    try:
        r = requests.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        payload = r.json()

        result = payload.get("chart", {}).get("result", [])
        if not result:
            return empty_df()

        chart = result[0]
        timestamps = chart.get("timestamp", [])
        quote = chart.get("indicators", {}).get("quote", [{}])[0]
        closes = quote.get("close", [])

        rows = []
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue

            # Yahoo timestamp를 미국 뉴욕시간의 거래일로 변환
            dt_et = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC")).astimezone(NEW_YORK_TZ)
            rows.append({
                "date": pd.Timestamp(dt_et.date()),
                "value": pd.to_numeric(close, errors="coerce")
            })

        if not rows:
            return empty_df()

        return (
            pd.DataFrame(rows)
            .dropna()
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
        )

    except Exception:
        return empty_df()

# ==============================
# 정책금리 - Federal Reserve
# FOMC 공식 statement에서
# 1) 발표일
# 2) Federal Funds Target Range
# 를 직접 읽습니다.
# ==============================
def strip_html(raw_html):
    text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_fraction_rate(token):
    """
    Fed 문서의 3-1/2, 3-3/4 같은 표기를 float로 변환합니다.
    """
    token = (
        token.strip()
        .replace("‑", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )

    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)

    m = re.fullmatch(r"(\d+)-(\d+)/(\d+)", token)
    if m:
        whole, num, den = map(int, m.groups())
        return whole + num / den

    m = re.fullmatch(r"(\d+)/(\d+)", token)
    if m:
        num, den = map(int, m.groups())
        return num / den

    return None


def extract_fed_target_range(statement_html):
    text = strip_html(statement_html)
    normalized = (
        text.replace("‑", "-")
            .replace("–", "-")
            .replace("—", "-")
            .replace("−", "-")
    )

    rate_token = r"\d+(?:\.\d+)?(?:-\d+/\d+)?"

    # FOMC statement의 해당 문장을 우선 탐색
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    for sentence in sentences:
        if "target range for the federal funds rate" not in sentence.lower():
            continue

        matches = re.findall(
            rf"({rate_token})\s+to\s+({rate_token})\s+percent",
            sentence,
            flags=re.I
        )
        if matches:
            low_token, high_token = matches[-1]
            low = parse_fraction_rate(low_token)
            high = parse_fraction_rate(high_token)
            if low is not None and high is not None:
                return low, high

    # 문장 분리에서 놓친 경우 전체 본문에서 한 번 더 탐색
    matches = re.findall(
        rf"target range for the federal funds rate.*?"
        rf"({rate_token})\s+to\s+({rate_token})\s+percent",
        normalized,
        flags=re.I | re.S
    )
    if matches:
        low_token, high_token = matches[-1]
        low = parse_fraction_rate(low_token)
        high = parse_fraction_rate(high_token)
        if low is not None and high is not None:
            return low, high

    return None


@st.cache_data(ttl=600)
def get_fed_policy_rate():
    """
    Fed 공식 FOMC press release 페이지에서 최근 두 번의
    FOMC statement를 읽어 최신 target range와 직전 회의 대비 변화를 반환합니다.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    today_et = datetime.now(NEW_YORK_TZ).date()

    statement_links = []

    # 연초에도 직전 회의를 찾을 수 있도록 현재연도 + 전년도 확인
    for year in [today_et.year, today_et.year - 1]:
        list_url = f"https://www.federalreserve.gov/newsevents/pressreleases/{year}-press-fomc.htm"

        try:
            r = requests.get(list_url, headers=headers, timeout=20)
            r.raise_for_status()
        except Exception:
            continue

        # FOMC statement 링크만 추출
        anchor_pattern = re.compile(
            r"<a[^>]+href=[\"']([^\"']*monetary(\d{8})a\.htm)[\"'][^>]*>(.*?)</a>",
            flags=re.I | re.S
        )

        for href, yyyymmdd, anchor_text in anchor_pattern.findall(r.text):
            title = strip_html(anchor_text).lower()
            if "fomc statement" not in title:
                continue

            release_date = datetime.strptime(yyyymmdd, "%Y%m%d").date()

            # 미래 회의 링크가 혹시 페이지에 있어도 제외
            if release_date > today_et:
                continue

            if href.startswith("http"):
                url = href
            else:
                url = "https://www.federalreserve.gov" + (
                    href if href.startswith("/") else "/" + href
                )

            statement_links.append((release_date, url))

    # 중복 제거 후 최신순
    statement_links = sorted(
        set(statement_links),
        key=lambda x: x[0],
        reverse=True
    )

    parsed = []
    for release_date, url in statement_links[:4]:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            target = extract_fed_target_range(r.text)
            if target:
                parsed.append({
                    "date": pd.Timestamp(release_date),
                    "low": target[0],
                    "high": target[1],
                    "url": url
                })
        except Exception:
            continue

        if len(parsed) >= 2:
            break

    if not parsed:
        return None

    current = parsed[0]
    current_mid = (current["low"] + current["high"]) / 2

    change = None
    if len(parsed) >= 2:
        previous_mid = (parsed[1]["low"] + parsed[1]["high"]) / 2
        change = current_mid - previous_mid

    current["change"] = change
    return current


# ==============================
# 정책금리 - Bank of Korea
# 한국은행 홈페이지의 최신 '통화정책방향'에서
# 발표일과 한국은행 기준금리를 직접 읽습니다.
# ==============================
@st.cache_data(ttl=600)
def get_bok_policy_rate():
    url = "https://www.bok.or.kr/portal/main/main.do"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        r.encoding = "utf-8"

        page_text = strip_html(r.text)

        # 최신 통화정책방향 발표일
        date_match = re.search(
            r"통화정책방향\s*\(\s*(\d{4})[.]\s*(\d{1,2})[.]\s*(\d{1,2})\s*\)",
            page_text
        )
        if not date_match:
            return None

        y, m, d = map(int, date_match.groups())
        release_date = pd.Timestamp(year=y, month=m, day=d)

        # 홈페이지에 표시되는 현재 한국은행 기준금리
        rate_match = re.search(
            r"한국은행\s*기준금리\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            page_text
        )

        # 홈페이지 레이아웃에 따라 공백이 전혀 없을 경우까지 대비
        if not rate_match:
            rate_match = re.search(
                r"한국은행기준금리\s*([0-9]+(?:\.[0-9]+)?)\s*%",
                page_text
            )

        if not rate_match:
            return None

        current_rate = float(rate_match.group(1))

        # 최신 통화정책방향 문구에서 인상/인하/동결 여부를 읽어 변화폭 계산
        # 예) "기준금리를 2.75%에서 3.00%로 ..."
        change = None

        changed_match = re.search(
            r"기준금리를\s*(?:현재의\s*)?"
            r"([0-9]+(?:\.[0-9]+)?)\s*%\s*"
            r"(?:수준에서|에서)\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*%\s*로",
            page_text
        )

        if changed_match:
            old_rate = float(changed_match.group(1))
            new_rate = float(changed_match.group(2))
            # 홈페이지의 현재 기준금리와 동일한 최신 문구일 때만 사용
            if abs(new_rate - current_rate) < 1e-9:
                change = new_rate - old_rate

        # 동결 문구 처리
        if change is None:
            hold_patterns = [
                r"기준금리를\s*(?:현재의\s*)?"
                r"([0-9]+(?:\.[0-9]+)?)\s*%\s*수준에서\s*유지",
                r"기준금리를\s*(?:현재의\s*)?"
                r"([0-9]+(?:\.[0-9]+)?)\s*%\s*로\s*유지",
            ]

            for pattern in hold_patterns:
                hold_match = re.search(pattern, page_text)
                if hold_match:
                    held_rate = float(hold_match.group(1))
                    if abs(held_rate - current_rate) < 1e-9:
                        change = 0.0
                        break

        return {
            "date": release_date,
            "rate": current_rate,
            "change": change,
            "url": url
        }

    except Exception:
        return None

# ==============================
# ECOS
# ==============================
@st.cache_data(ttl=1800)
def get_ecos(stat, item, cycle):
    if not ECOS_API_KEY:
        return empty_df()
    now = datetime.now(KOREA_TZ)
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

# ==============================
# 데이터 로딩
# ==============================
# 최초 접속과 '최신 데이터 수집' 버튼 클릭 시,
# 현재 어떤 데이터를 불러오는 중인지 화면에 표시합니다.
loading_title = "최신 데이터를 다시 수집하는 중입니다..." if refresh_clicked else "데이터를 불러오는 중입니다..."

loading_status = st.status(
    f"📡 {loading_title}",
    expanded=True,
    state="running"
)

loading_message = st.empty()
loading_progress = st.progress(0)

# 1개 ECOS 항목코드 조회 + 미국 데이터 5종 + 한국은행 정책금리 1종 + 한국 ECOS 지표
total_steps = 1 + 5 + 1 + len(ecos_series)
current_step = 0

def show_loading(message):
    global current_step
    current_step += 1
    pct = int(current_step / total_steps * 100)
    loading_message.info(f"⏳ {message}")
    loading_progress.progress(pct, text=f"{current_step}/{total_steps} · {message}")
    loading_status.write(f"⏳ {message}")

# 한국 산업생산 항목코드 확인
show_loading("한국은행 ECOS · 산업생산 지표 항목 확인 중")
ecos_series["한국 산업생산"]["item"] = find_ecos_item("901Y033", ["전산업생산지수"])

# 미국 데이터
show_loading("U.S. Treasury · 미국 명목 국채금리(2Y·5Y·10Y·30Y) 로딩 중")
treasury = get_treasury_curve()

show_loading("U.S. Treasury · 미국 실질금리(5Y·10Y) 로딩 중")
treasury_real = get_treasury_real_curve()

show_loading("U.S. BLS · CPI·Core CPI·실업률·NFP 로딩 중")
bls = get_bls(["CUSR0000SA0", "CUSR0000SA0L1E", "LNS14000000", "CES0000000001"])

show_loading("시장데이터 · WTI 선물가격(CL=F) 로딩 중")
wti = get_wti()

show_loading("Federal Reserve · FOMC 기준금리 발표일·Target Range 로딩 중")
fed_policy = get_fed_policy_rate()

show_loading("한국은행 · 최신 기준금리 발표일·기준금리 로딩 중")
bok_policy = get_bok_policy_rate()

# 한국 데이터는 지표별로 현재 로딩 항목을 표시
ecos = {}
for name, info in ecos_series.items():
    show_loading(f"한국은행 ECOS · {name} 로딩 중")
    ecos[name] = get_ecos(info["stat"], info["item"], info["cycle"])

loading_message.success("✅ 모든 데이터를 불러왔습니다.")
loading_progress.progress(100, text="데이터 로딩 완료")
loading_status.update(
    label="✅ 미국·한국 채권 및 거시경제 데이터 로딩 완료",
    state="complete",
    expanded=False
)

# 수동 새로고침이 끝난 시각을 한국시간으로 저장
if refresh_clicked:
    st.session_state["last_updated_krt"] = datetime.now(KOREA_TZ).strftime("%H:%M:%S")

if st.session_state["last_updated_krt"]:
    st.success(f"✅ KRT {st.session_state['last_updated_krt']}에 업데이트됐습니다.")

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
add_row(rows, "미국", "WTI 선물 (CL=F)", wti, "일간", "pct")

# 연준 정책금리: FOMC가 발표하는 Federal Funds Target Range
if fed_policy:
    fed_mid = (fed_policy["low"] + fed_policy["high"]) / 2
    rows.append({
        "구분": "미국",
        "지표": "연준 기준금리 (Fed Funds Target Range)",
        "기준일": fed_policy["date"],
        "현재": fed_mid,
        "현재표시": f'{fed_policy["low"]:.2f}–{fed_policy["high"]:.2f}%',
        "변화값": fed_policy["change"],
        "빈도": "정책금리",
        "type": "policy"
    })

# BLS
for sid, name, value_type in [
    ("CUSR0000SA0", "미국 CPI", "yoy"),
    ("CUSR0000SA0L1E", "미국 Core CPI", "yoy"),
    ("LNS14000000", "미국 실업률", "pp"),
    ("CES0000000001", "미국 NFP", "nfp")
]:
    add_row(rows, "미국", name, bls.get(sid, empty_df()), "월간", value_type)

# 한국은행 정책금리
if bok_policy:
    rows.append({
        "구분": "한국",
        "지표": "한국은행 기준금리",
        "기준일": bok_policy["date"],
        "현재": bok_policy["rate"],
        "현재표시": f'{bok_policy["rate"]:.2f}%',
        "변화값": bok_policy["change"],
        "빈도": "정책금리",
        "type": "policy"
    })

# 한국 시장·거시 지표
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
    if value_type in ["rate", "policy"]:
        return f"{sign}{change:.1f}bp"
    if value_type == "pp":
        return f"{sign}{change:.1f}%p"
    if value_type == "nfp":
        return f"{sign}{change:.0f}K"
    return f"{sign}{change:.1f}%"

if result.empty:
    st.error("데이터를 불러오지 못했습니다. Streamlit Secrets의 API 키와 각 API 상태를 확인해주세요.")
else:
    result["변화"] = [format_change(c, t) for c, t in zip(result["변화값"], result["type"])]

    # 일반 지표는 숫자로, 정책금리는 Fed range / BOK rate 형식으로 표시
    result["현재"] = result.apply(
        lambda row: row["현재표시"]
        if isinstance(row.get("현재표시"), str) and row["현재표시"]
        else f'{float(row["현재"]):,.2f}',
        axis=1
    )

    # 기준일 표시 규칙
    # - 일간: 실제 관측일 YYYY-MM-DD
    # - 월간: 해당 지표가 의미하는 기준월 YYYY-MM
    # - 정책금리: 실제 정책결정 발표일 YYYY-MM-DD 발표
    result["기준일"] = pd.to_datetime(result["기준일"])

    st.info(
        "기준일은 **일간 지표의 실제 관측일**, **월간 지표의 기준월**, "
        "**정책금리의 실제 발표일**을 의미합니다. "
        "예: 미국 CPI `2026-08`은 2026년 8월분 CPI이고, "
        "정책금리 `2026-07-29 발표`는 해당 날짜의 정책결정 발표를 의미합니다. "
        "WTI는 **시장 WTI 선물(CL=F)** 가격을 사용합니다."
    )

    for freq in ["일간", "월간", "정책금리"]:
        temp = result[result["빈도"] == freq].copy()
        if temp.empty:
            continue

        if freq == "월간":
            temp["기준일"] = temp["기준일"].dt.strftime("%Y-%m")
        elif freq == "정책금리":
            temp["기준일"] = temp["기준일"].dt.strftime("%Y-%m-%d") + " 발표"
        else:
            temp["기준일"] = temp["기준일"].dt.strftime("%Y-%m-%d")

        st.subheader(f"📊 {freq} 지표")

        # 표시용 데이터프레임
        display_cols = ["구분", "지표", "기준일", "현재", "변화"]
        display_df = temp[display_cols + ["변화값"]].reset_index(drop=True)

        # 변화값에 따라 변화 열의 글자색/굵기 적용
        # 상승: 빨간색 bold / 하락: 파란색 bold / 변화 없음: 검정색 bold
        def style_change_column(col):
            styles = []
            for idx in col.index:
                change = display_df.loc[idx, "변화값"]

                if pd.isna(change):
                    styles.append("font-weight: 700; color: #000000;")
                elif change > 0:
                    styles.append("font-weight: 700; color: #d62728;")
                elif change < 0:
                    styles.append("font-weight: 700; color: #1f77b4;")
                else:
                    styles.append("font-weight: 700; color: #000000;")

            return styles

        styled_df = (
            display_df[display_cols]
            .style
            .apply(style_change_column, subset=["변화"])
        )

        st.dataframe(
            styled_df,
            use_container_width=True,
            hide_index=True
        )

    st.caption(
        "출처: U.S. Treasury · U.S. Bureau of Labor Statistics · "
        "Yahoo Finance (WTI Futures, CL=F) · Federal Reserve Board (FOMC) · "
        "Bank of Korea · Bank of Korea ECOS"
    )
