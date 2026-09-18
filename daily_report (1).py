import html
import os
import re
import smtplib
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.message import EmailMessage
from io import StringIO
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf


KOREA_TZ = ZoneInfo("Asia/Seoul")
NEW_YORK_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

ECOS_API_KEY = os.environ.get("ECOS_API_KEY", "")
BLS_API_KEY = os.environ.get("BLS_API_KEY", "")

EMAIL_USER = os.environ.get("EMAIL_USER", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))

REQUEST_HEADERS = {
    "User-Agent": "BondMacroDashboard-DailyReport/1.0"
}


def empty_df():
    return pd.DataFrame(columns=['date', 'value'])

def get_latest_change(df, value_type):
    """
    Return: current_date, current_value, change

    change units:
      rate   -> bp
      pp     -> percentage point
      nfp    -> thousand persons
      yoy    -> YoY %
      mom    -> MoM %
      pct    -> daily %
    """
    if df is None or df.empty:
        return (None, None, None)
    clean = df.dropna().sort_values('date').reset_index(drop=True)
    if clean.empty:
        return (None, None, None)
    current = float(clean.iloc[-1]['value'])
    current_date = clean.iloc[-1]['date']
    if len(clean) < 2:
        return (current_date, current, None)
    previous = float(clean.iloc[-2]['value'])
    if value_type == 'rate':
        change = (current - previous) * 100
    elif value_type == 'pp':
        change = current - previous
    elif value_type == 'nfp':
        change = current - previous
    elif value_type == 'yoy':
        target = current_date - pd.DateOffset(months=12)
        exact_past = clean[clean['date'].dt.to_period('M') == pd.Timestamp(target).to_period('M')]
        if not exact_past.empty:
            past_value = float(exact_past.iloc[-1]['value'])
        else:
            past = clean[clean['date'] <= target]
            past_value = float(past.iloc[-1]['value']) if not past.empty else None
        if past_value is None or past_value == 0:
            change = None
        else:
            change = (current / past_value - 1) * 100
    elif value_type == 'mom':
        change = (current / previous - 1) * 100 if previous != 0 else None
    else:
        change = (current / previous - 1) * 100 if previous != 0 else None
    return (current_date, current, change)

def classify_curve(short_change_bp, long_change_bp):
    """
    Yield-curve move classification.

    slope change = Δlong - Δshort
      > 0 : steepening
      < 0 : flattening

    Both yields up   -> Bear
    Both yields down -> Bull
    Mixed signs      -> Twist
    """
    if pd.isna(short_change_bp) or pd.isna(long_change_bp):
        return '판단 불가'
    short_change_bp = float(short_change_bp)
    long_change_bp = float(long_change_bp)
    slope_change = long_change_bp - short_change_bp
    if abs(slope_change) < 0.05:
        if short_change_bp > 0 and long_change_bp > 0:
            return 'Bear Parallel Shift'
        if short_change_bp < 0 and long_change_bp < 0:
            return 'Bull Parallel Shift'
        return 'Parallel / Flat'
    direction = 'Steepening' if slope_change > 0 else 'Flattening'
    if short_change_bp > 0 and long_change_bp > 0:
        prefix = 'Bear'
    elif short_change_bp < 0 and long_change_bp < 0:
        prefix = 'Bull'
    else:
        prefix = 'Twist'
    return f'{prefix} {direction}'

def _clean_series(df):
    if df is None or df.empty:
        return empty_df()
    clean = df[['date', 'value']].dropna().copy()
    clean['date'] = pd.to_datetime(clean['date'], errors='coerce')
    clean['value'] = pd.to_numeric(clean['value'], errors='coerce')
    return clean.dropna().drop_duplicates(subset=['date'], keep='last').sort_values('date').reset_index(drop=True)

def get_horizon_change(df, horizon, value_type='rate'):
    """
    Latest value versus the latest observation on/before:
      1D -> prior trading observation
      1W -> 7 calendar days earlier
      1M -> 1 calendar month earlier

    rate -> bp
    pct  -> %
    """
    clean = _clean_series(df)
    if len(clean) < 2:
        return None
    current_date = pd.Timestamp(clean.iloc[-1]['date'])
    current = float(clean.iloc[-1]['value'])
    if horizon == '1D':
        previous = float(clean.iloc[-2]['value'])
    else:
        if horizon == '1W':
            target = current_date - pd.Timedelta(days=7)
        elif horizon == '1M':
            target = current_date - pd.DateOffset(months=1)
        else:
            return None
        past = clean[clean['date'] <= target]
        if past.empty:
            return None
        previous = float(past.iloc[-1]['value'])
    if value_type == 'rate':
        return (current - previous) * 100
    if previous == 0:
        return None
    return (current / previous - 1) * 100

def historical_percentile(df, years=5):
    """
    Current observation percentile within the trailing N years.
    100 = near the top of the trailing window.
    """
    clean = _clean_series(df)
    if clean.empty:
        return None
    current_date = pd.Timestamp(clean.iloc[-1]['date'])
    current = float(clean.iloc[-1]['value'])
    cutoff = current_date - pd.DateOffset(years=years)
    window = clean[clean['date'] >= cutoff]
    if window.empty:
        return None
    values = window['value'].astype(float)
    return float((values <= current).mean() * 100)

def build_10y_decomposition(nominal_df, real_df):
    """
    Align nominal 10Y and real 10Y on common Treasury dates.

    Nominal ≈ Real + Breakeven
    Therefore the daily changes also add up on aligned dates.
    """
    nominal = _clean_series(nominal_df).rename(columns={'value': 'nominal'})
    real = _clean_series(real_df).rename(columns={'value': 'real'})
    merged = pd.merge(nominal, real, on='date', how='inner').sort_values('date')
    if len(merged) < 2:
        return None
    merged['bei'] = merged['nominal'] - merged['real']
    current = merged.iloc[-1]
    previous = merged.iloc[-2]
    nominal_change = (float(current['nominal']) - float(previous['nominal'])) * 100
    real_change = (float(current['real']) - float(previous['real'])) * 100
    bei_change = (float(current['bei']) - float(previous['bei'])) * 100
    if abs(real_change) > abs(bei_change) + 0.05:
        driver = '실질금리 변화 주도'
    elif abs(bei_change) > abs(real_change) + 0.05:
        driver = '기대인플레이션 변화 주도'
    else:
        driver = '실질금리·기대인플레이션 혼합'
    if real_change > 0:
        real_direction = '실질금리 상승'
    elif real_change < 0:
        real_direction = '실질금리 하락'
    else:
        real_direction = '실질금리 보합'
    if bei_change > 0:
        bei_direction = '인플레이션 보상 상승'
    elif bei_change < 0:
        bei_direction = '인플레이션 보상 하락'
    else:
        bei_direction = '인플레이션 보상 보합'
    return {'date': pd.Timestamp(current['date']), 'nominal': float(current['nominal']), 'real': float(current['real']), 'bei': float(current['bei']), 'nominal_change': nominal_change, 'real_change': real_change, 'bei_change': bei_change, 'driver': driver, 'detail': f'{real_direction} · {bei_direction}'}

def yoy_history(df):
    clean = _clean_series(df)
    if clean.empty:
        return empty_df()
    lookup = {pd.Timestamp(row['date']).to_period('M'): float(row['value']) for _, row in clean.iterrows()}
    rows = []
    for _, row in clean.iterrows():
        date = pd.Timestamp(row['date'])
        prior_period = (date - pd.DateOffset(months=12)).to_period('M')
        if prior_period not in lookup:
            continue
        prior = lookup[prior_period]
        if prior == 0:
            continue
        rows.append({'date': date, 'value': (float(row['value']) / prior - 1) * 100})
    return pd.DataFrame(rows)

def mom_history(df):
    clean = _clean_series(df)
    if len(clean) < 2:
        return empty_df()
    out = clean.copy()
    out['value'] = out['value'].astype(float).pct_change() * 100
    return out.dropna().reset_index(drop=True)

def latest_pair(df):
    clean = _clean_series(df)
    if len(clean) < 2:
        return (None, None, None)
    return (pd.Timestamp(clean.iloc[-1]['date']), float(clean.iloc[-1]['value']), float(clean.iloc[-2]['value']))

def momentum_symbol(current, previous, tolerance=0.02):
    if current is None or previous is None or pd.isna(current) or pd.isna(previous):
        return ('→', '확인 불가', 0)
    diff = float(current) - float(previous)
    if abs(diff) <= tolerance:
        return ('→', '보합', 0)
    if diff > 0:
        return ('↑', '상승', 1)
    return ('↓', '하락', -1)

def build_macro_momentum_rows(cpi_nsa=None, cpi_sa=None, core_nsa=None, core_sa=None, unemployment=None, payroll=None, industrial_original=None, industrial_sa=None, country='US'):
    rows = []
    if cpi_nsa is not None and (not cpi_nsa.empty):
        cpi_yoy_hist = yoy_history(cpi_nsa)
        _, curr, prev = latest_pair(cpi_yoy_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append({'지표': 'CPI YoY', '현재': f'{curr:.1f}%', '이전': '-' if prev is None else f'{prev:.1f}%', 'Momentum': f'{arrow} {label}', '_score': score, '_kind': 'inflation'})
    if cpi_sa is not None and (not cpi_sa.empty):
        cpi_mom_hist = mom_history(cpi_sa)
        _, curr, prev = latest_pair(cpi_mom_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append({'지표': 'CPI MoM', '현재': f'{curr:.1f}%', '이전': '-' if prev is None else f'{prev:.1f}%', 'Momentum': f'{arrow} {label}', '_score': score, '_kind': 'inflation_short'})
    if core_nsa is not None and (not core_nsa.empty):
        core_yoy_hist = yoy_history(core_nsa)
        _, curr, prev = latest_pair(core_yoy_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append({'지표': 'Core CPI YoY', '현재': f'{curr:.1f}%', '이전': '-' if prev is None else f'{prev:.1f}%', 'Momentum': f'{arrow} {label}', '_score': score, '_kind': 'inflation'})
    if core_sa is not None and (not core_sa.empty):
        core_mom_hist = mom_history(core_sa)
        _, curr, prev = latest_pair(core_mom_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append({'지표': 'Core CPI MoM', '현재': f'{curr:.1f}%', '이전': '-' if prev is None else f'{prev:.1f}%', 'Momentum': f'{arrow} {label}', '_score': score, '_kind': 'inflation_short'})
    if payroll is not None and (not payroll.empty):
        payroll_clean = _clean_series(payroll)
        gains = payroll_clean.copy()
        gains['value'] = gains['value'].astype(float).diff()
        gains = gains.dropna()
        _, curr, prev = latest_pair(gains)
        if curr is not None:
            if prev is None:
                arrow, label, score = ('→', '확인 불가', 0)
            else:
                arrow, _, score = momentum_symbol(curr, prev, 5.0)
                label = '증가폭 확대' if score > 0 else '증가폭 축소' if score < 0 else '증가폭 유사'
            rows.append({'지표': 'NFP 증감', '현재': f'{curr:+.0f}K', '이전': '-' if prev is None else f'{prev:+.0f}K', 'Momentum': f'{arrow} {label}', '_score': score, '_kind': 'growth'})
    if unemployment is not None and (not unemployment.empty):
        _, curr, prev = latest_pair(unemployment)
        if curr is not None:
            arrow, label, raw_score = momentum_symbol(curr, prev, 0.02)
            growth_score = -raw_score
            rows.append({'지표': '실업률', '현재': f'{curr:.1f}%', '이전': '-' if prev is None else f'{prev:.1f}%', 'Momentum': f'{arrow} {label}', '_score': growth_score, '_kind': 'growth'})
    if industrial_original is not None and (not industrial_original.empty):
        ip_yoy_hist = yoy_history(industrial_original)
        _, curr, prev = latest_pair(ip_yoy_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.1)
        if curr is not None:
            rows.append({'지표': '전산업생산 YoY', '현재': f'{curr:.1f}%', '이전': '-' if prev is None else f'{prev:.1f}%', 'Momentum': f'{arrow} {label}', '_score': score, '_kind': 'growth'})
    if industrial_sa is not None and (not industrial_sa.empty):
        ip_mom_hist = mom_history(industrial_sa)
        _, curr, prev = latest_pair(ip_mom_hist)
        if curr is not None:
            if abs(curr) <= 0.05:
                score = 0
                label = '보합'
                arrow = '→'
            elif curr > 0:
                score = 1
                label = '생산 증가'
                arrow = '↑'
            else:
                score = -1
                label = '생산 감소'
                arrow = '↓'
            rows.append({'지표': '전산업생산 MoM', '현재': f'{curr:.1f}%', '이전': '-' if prev is None else f'{prev:.1f}%', 'Momentum': f'{arrow} {label}', '_score': score, '_kind': 'growth'})
    return rows

def macro_regime_from_rows(rows, country):
    if not rows:
        return {'name': 'Mixed / 데이터 부족', 'growth': 'Growth →', 'inflation': 'Inflation →', 'evidence': '판단 가능한 모멘텀 지표가 부족합니다.'}
    df = pd.DataFrame(rows)
    growth_scores = df.loc[df['_kind'] == 'growth', '_score'].tolist()
    inflation_scores = df.loc[df['_kind'] == 'inflation', '_score'].tolist()
    growth_sum = sum(growth_scores) if growth_scores else 0
    inflation_sum = sum(inflation_scores) if inflation_scores else 0
    growth_dir = 1 if growth_sum > 0 else -1 if growth_sum < 0 else 0
    inflation_dir = 1 if inflation_sum > 0 else -1 if inflation_sum < 0 else 0
    if growth_dir > 0 and inflation_dir > 0:
        name = 'Reflation'
    elif growth_dir > 0 and inflation_dir < 0:
        name = 'Goldilocks'
    elif growth_dir < 0 and inflation_dir > 0:
        name = 'Stagflation Pressure'
    elif growth_dir < 0 and inflation_dir < 0:
        name = 'Slowdown / Disinflation'
    else:
        name = 'Mixed / Neutral'
    growth_text = 'Growth ↑' if growth_dir > 0 else 'Growth ↓' if growth_dir < 0 else 'Growth →'
    inflation_text = 'Inflation ↑' if inflation_dir > 0 else 'Inflation ↓' if inflation_dir < 0 else 'Inflation →'
    evidence_items = []
    for _, row in df.iterrows():
        if row['_kind'] in ['growth', 'inflation']:
            evidence_items.append(f"{row['지표']} {row['Momentum']}")
    evidence = ' · '.join(evidence_items[:4])
    return {'name': name, 'growth': growth_text, 'inflation': inflation_text, 'evidence': evidence or '데이터 부족'}

def get_treasury_curve():
    """
    Official U.S. Treasury nominal curve.

    Fetch the current year plus the previous five calendar years so the
    dashboard can calculate trailing 5Y percentiles.
    """
    current_year = datetime.now(KOREA_TZ).year
    all_rows = []
    for year in range(current_year - 5, current_year + 1):
        url = f'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={year}'
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception:
            continue
        for entry in root.iter():
            if entry.tag.split('}')[-1] != 'entry':
                continue
            record = {}
            for child in entry.iter():
                key = child.tag.split('}')[-1]
                if child.text and key not in record:
                    record[key] = child.text.strip()
            date = record.get('NEW_DATE')
            if not date:
                continue
            row = {'date': pd.to_datetime(date, errors='coerce')}
            for key in ['BC_2YEAR', 'BC_5YEAR', 'BC_10YEAR', 'BC_30YEAR']:
                row[key] = pd.to_numeric(record.get(key), errors='coerce')
            all_rows.append(row)
    df = pd.DataFrame(all_rows)
    if df.empty:
        return {}
    df = df.dropna(subset=['date']).drop_duplicates(subset=['date'], keep='last').sort_values('date')
    mapping = [('BC_2YEAR', '미국 2Y'), ('BC_5YEAR', '미국 5Y'), ('BC_10YEAR', '미국 10Y'), ('BC_30YEAR', '미국 30Y')]
    out = {}
    for col, name in mapping:
        if col in df:
            out[name] = df[['date', col]].rename(columns={col: 'value'}).dropna().reset_index(drop=True)
    return out

def get_treasury_real_curve():
    """
    Official U.S. Treasury real yield curve.

    Fetch the current year plus the previous five calendar years so real
    yields and breakevens can also have trailing 5Y percentiles.
    """
    current_year = datetime.now(KOREA_TZ).year
    all_rows = []
    for year in range(current_year - 5, current_year + 1):
        url = f'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value={year}'
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception:
            continue
        for entry in root.iter():
            if entry.tag.split('}')[-1] != 'entry':
                continue
            record = {}
            for child in entry.iter():
                key = child.tag.split('}')[-1]
                if child.text and key not in record:
                    record[key] = child.text.strip()
            date = record.get('NEW_DATE')
            if not date:
                continue
            all_rows.append({'date': pd.to_datetime(date, errors='coerce'), 'real5': pd.to_numeric(record.get('TC_5YEAR'), errors='coerce'), 'real10': pd.to_numeric(record.get('TC_10YEAR'), errors='coerce')})
    df = pd.DataFrame(all_rows)
    if df.empty:
        return {}
    df = df.dropna(subset=['date']).drop_duplicates(subset=['date'], keep='last').sort_values('date')
    return {'미국 실질 5Y': df[['date', 'real5']].rename(columns={'real5': 'value'}).dropna().reset_index(drop=True), '미국 실질 10Y': df[['date', 'real10']].rename(columns={'real10': 'value'}).dropna().reset_index(drop=True)}

def get_wti():
    """
    NYMEX WTI Crude Oil Futures (CL=F), daily close.

    GitHub-hosted runners are frequently rate-limited by Yahoo when using
    plain `requests`. Modern yfinance uses a browser-like transport and is
    materially more robust in cloud CI environments.

    A raw Yahoo chart request is retained only as a fallback.
    """
    errors = []

    # 1) yfinance / curl_cffi path
    try:
        ticker = yf.Ticker("CL=F")
        hist = ticker.history(
            period="1mo",
            interval="1d",
            auto_adjust=False,
            actions=False,
            timeout=30,
        )

        if hist is not None and not hist.empty and "Close" in hist.columns:
            out = pd.DataFrame(
                {
                    "date": pd.to_datetime(
                        hist.index
                    ).tz_localize(None).normalize(),
                    "value": pd.to_numeric(
                        hist["Close"],
                        errors="coerce",
                    ),
                }
            )

            out = (
                out.dropna()
                .drop_duplicates(
                    subset=["date"],
                    keep="last",
                )
                .sort_values("date")
                .reset_index(drop=True)
            )

            if not out.empty:
                return out

        errors.append("yfinance returned no usable CL=F data")

    except Exception as exc:
        errors.append(f"yfinance: {exc}")

    # 2) Raw Yahoo chart endpoint fallback
    url = (
        "https://query1.finance.yahoo.com/"
        "v8/finance/chart/CL%3DF"
    )
    params = {
        "range": "1mo",
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    }

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()

        payload = response.json()
        result = payload.get(
            "chart",
            {},
        ).get(
            "result",
            [],
        )

        if not result:
            raise RuntimeError(
                "Yahoo chart returned no result"
            )

        chart = result[0]
        timestamps = chart.get(
            "timestamp",
            [],
        )
        quote = (
            chart.get(
                "indicators",
                {},
            )
            .get(
                "quote",
                [{}],
            )[0]
        )
        closes = quote.get(
            "close",
            [],
        )

        rows = []

        for ts, close in zip(
            timestamps,
            closes,
        ):
            if close is None:
                continue

            dt_et = datetime.fromtimestamp(
                ts,
                tz=UTC_TZ,
            ).astimezone(
                NEW_YORK_TZ
            )

            rows.append(
                {
                    "date": pd.Timestamp(
                        dt_et.date()
                    ),
                    "value": pd.to_numeric(
                        close,
                        errors="coerce",
                    ),
                }
            )

        if not rows:
            raise RuntimeError(
                "Yahoo chart returned no closes"
            )

        return (
            pd.DataFrame(rows)
            .dropna()
            .drop_duplicates(
                subset=["date"],
                keep="last",
            )
            .sort_values("date")
            .reset_index(drop=True)
        )

    except Exception as exc:
        errors.append(f"raw Yahoo: {exc}")

    raise RuntimeError(
        "WTI collection failed: "
        + " | ".join(errors)
    )


def strip_html(raw_html):
    text = re.sub('<script.*?</script>', ' ', raw_html, flags=re.I | re.S)
    text = re.sub('<style.*?</style>', ' ', text, flags=re.I | re.S)
    text = re.sub('<[^>]+>', ' ', text)
    text = html.unescape(text)
    return re.sub('\\s+', ' ', text).strip()

def parse_fraction_rate(token):
    token = token.strip().replace('‑', '-').replace('–', '-').replace('—', '-').replace('−', '-')
    if re.fullmatch('\\d+(?:\\.\\d+)?', token):
        return float(token)
    mixed = re.fullmatch('(\\d+)-(\\d+)/(\\d+)', token)
    if mixed:
        whole, num, den = map(int, mixed.groups())
        return whole + num / den
    fraction = re.fullmatch('(\\d+)/(\\d+)', token)
    if fraction:
        num, den = map(int, fraction.groups())
        return num / den
    return None

def extract_fed_target_range(statement_html):
    text = strip_html(statement_html)
    normalized = text.replace('‑', '-').replace('–', '-').replace('—', '-').replace('−', '-')
    rate_token = '\\d+(?:\\.\\d+)?(?:-\\d+/\\d+)?'
    sentences = re.split('(?<=[.!?])\\s+', normalized)
    for sentence in sentences:
        if 'target range for the federal funds rate' not in sentence.lower():
            continue
        matches = re.findall(f'({rate_token})\\s+to\\s+({rate_token})\\s+percent', sentence, flags=re.I)
        if matches:
            low_token, high_token = matches[-1]
            low = parse_fraction_rate(low_token)
            high = parse_fraction_rate(high_token)
            if low is not None and high is not None:
                return (low, high)
    matches = re.findall(f'target range for the federal funds rate.*?({rate_token})\\s+to\\s+({rate_token})\\s+percent', normalized, flags=re.I | re.S)
    if matches:
        low_token, high_token = matches[-1]
        low = parse_fraction_rate(low_token)
        high = parse_fraction_rate(high_token)
        if low is not None and high is not None:
            return (low, high)
    return None

def get_fed_policy_rate():
    """
    Read the two latest official FOMC statements.
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    today_et = datetime.now(NEW_YORK_TZ).date()
    statement_links = []
    for year in [today_et.year, today_et.year - 1]:
        list_url = f'https://www.federalreserve.gov/newsevents/pressreleases/{year}-press-fomc.htm'
        try:
            r = requests.get(list_url, headers=headers, timeout=20)
            r.raise_for_status()
        except Exception:
            continue
        anchor_pattern = re.compile('<a[^>]+href=[\\"\']([^\\"\']*monetary(\\d{8})a\\.htm)[\\"\'][^>]*>(.*?)</a>', flags=re.I | re.S)
        for href, yyyymmdd, anchor_text in anchor_pattern.findall(r.text):
            title = strip_html(anchor_text).lower()
            if 'fomc statement' not in title:
                continue
            release_date = datetime.strptime(yyyymmdd, '%Y%m%d').date()
            if release_date > today_et:
                continue
            if href.startswith('http'):
                statement_url = href
            else:
                statement_url = 'https://www.federalreserve.gov' + (href if href.startswith('/') else '/' + href)
            statement_links.append((release_date, statement_url))
    statement_links = sorted(set(statement_links), key=lambda x: x[0], reverse=True)
    parsed = []
    for release_date, statement_url in statement_links[:4]:
        try:
            r = requests.get(statement_url, headers=headers, timeout=20)
            r.raise_for_status()
            target = extract_fed_target_range(r.text)
            if target:
                parsed.append({'date': pd.Timestamp(release_date), 'low': target[0], 'high': target[1], 'url': statement_url})
        except Exception:
            continue
        if len(parsed) >= 2:
            break
    if not parsed:
        return None
    current = parsed[0]
    current_mid = (current['low'] + current['high']) / 2
    change_bp = None
    if len(parsed) >= 2:
        previous_mid = (parsed[1]['low'] + parsed[1]['high']) / 2
        change_bp = (current_mid - previous_mid) * 100
    current['change_bp'] = change_bp
    return current

def get_bok_policy_rate():
    """
    Read the latest Bank of Korea policy decision date
    and base rate from the BOK homepage.
    """
    url = 'https://www.bok.or.kr/portal/main/main.do'
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        r.encoding = 'utf-8'
        page_text = strip_html(r.text)
        date_match = re.search('통화정책방향\\s*\\(\\s*(\\d{4})[.]\\s*(\\d{1,2})[.]\\s*(\\d{1,2})\\s*\\)', page_text)
        if not date_match:
            return None
        y, m, d = map(int, date_match.groups())
        release_date = pd.Timestamp(year=y, month=m, day=d)
        rate_match = re.search('한국은행\\s*기준금리\\s*([0-9]+(?:\\.[0-9]+)?)\\s*%', page_text)
        if not rate_match:
            rate_match = re.search('한국은행기준금리\\s*([0-9]+(?:\\.[0-9]+)?)\\s*%', page_text)
        if not rate_match:
            return None
        current_rate = float(rate_match.group(1))
        change_bp = None
        changed_match = re.search('기준금리를\\s*(?:현재의\\s*)?([0-9]+(?:\\.[0-9]+)?)\\s*%\\s*(?:수준에서|에서)\\s*([0-9]+(?:\\.[0-9]+)?)\\s*%\\s*로', page_text)
        if changed_match:
            old_rate = float(changed_match.group(1))
            new_rate = float(changed_match.group(2))
            if abs(new_rate - current_rate) < 1e-09:
                change_bp = (new_rate - old_rate) * 100
        if change_bp is None:
            hold_patterns = ['기준금리를\\s*(?:현재의\\s*)?([0-9]+(?:\\.[0-9]+)?)\\s*%\\s*수준에서\\s*유지', '기준금리를\\s*(?:현재의\\s*)?([0-9]+(?:\\.[0-9]+)?)\\s*%\\s*로\\s*유지']
            for pattern in hold_patterns:
                hold_match = re.search(pattern, page_text)
                if hold_match:
                    held_rate = float(hold_match.group(1))
                    if abs(held_rate - current_rate) < 1e-09:
                        change_bp = 0.0
                        break
        return {'date': release_date, 'rate': current_rate, 'change_bp': change_bp, 'url': url}
    except Exception:
        return None

def get_ecos(stat, item, cycle):
    """
    ECOS StatisticSearch.

    item:
      - str  : ITEM_CODE1 하나만 사용하는 기존 지표
      - list : ITEM_CODE1 ~ ITEM_CODE4의 전체 경로
      - None : 항목 필터 없이 조회

    전산업생산처럼 하나의 통계표 안에 원계열/계절조정계열이
    함께 존재하는 지표는 반드시 전체 item-code 경로를 넘겨
    서로 다른 계열이 섞이지 않도록 합니다.
    """
    if not ECOS_API_KEY:
        return empty_df()
    now = datetime.now(KOREA_TZ)
    if cycle == 'D':
        start = '20200101'
        end = now.strftime('%Y%m%d')
    else:
        start = '202001'
        end = now.strftime('%Y%m')
    url = f'https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/{stat}/{cycle}/{start}/{end}'
    if item:
        if isinstance(item, (list, tuple)):
            codes = [str(code).strip() for code in item if code is not None and str(code).strip() and (str(code).lower() != 'nan')]
            if codes:
                url += '/' + '/'.join(codes)
        else:
            url += f'/{item}'
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        rows = r.json().get('StatisticSearch', {}).get('row', [])
        if not rows:
            return empty_df()
        df = pd.DataFrame(rows)
        fmt = '%Y%m%d' if cycle == 'D' else '%Y%m'
        df['date'] = pd.to_datetime(df['TIME'], format=fmt, errors='coerce')
        df['value'] = pd.to_numeric(df['DATA_VALUE'], errors='coerce')
        out = df[['date', 'value']].dropna().sort_values('date').drop_duplicates(subset=['date'], keep='last')
        return out
    except Exception:
        return empty_df()

def get_ecos_raw_table(stat, cycle):
    """
    ECOS 통계표 전체를 받아서 ITEM_NAME 메타데이터를 유지합니다.

    901Y033처럼 하나의 통계표 안에 원계열과 계절조정계열이 함께 있는 경우,
    URL의 item code를 억지로 추정하지 않고 실제 응답의 ITEM_NAME을 보고
    원하는 계열을 분리합니다.
    """
    if not ECOS_API_KEY:
        return pd.DataFrame()
    now = datetime.now(KOREA_TZ)
    if cycle == 'D':
        start = '20200101'
        end = now.strftime('%Y%m%d')
    else:
        start = '202001'
        end = now.strftime('%Y%m')
    url = f'https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/{stat}/{cycle}/{start}/{end}'
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        rows = r.json().get('StatisticSearch', {}).get('row', [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        fmt = '%Y%m%d' if cycle == 'D' else '%Y%m'
        df['date'] = pd.to_datetime(df['TIME'], format=fmt, errors='coerce')
        df['value'] = pd.to_numeric(df['DATA_VALUE'], errors='coerce')
        item_name_cols = sorted([c for c in df.columns if c.startswith('ITEM_NAME')])
        if item_name_cols:
            df['series_name'] = df.apply(lambda row: ' | '.join((str(row[c]).strip() for c in item_name_cols if c in row.index and pd.notna(row[c]) and str(row[c]).strip())), axis=1)
        else:
            df['series_name'] = ''
        return df.dropna(subset=['date', 'value']).sort_values('date').reset_index(drop=True)
    except Exception:
        return pd.DataFrame()

def select_industrial_series(raw_df, mode):
    """
    901Y033 전체 응답에서 전산업생산 '원계열' 또는 '계절조정계열'을 선택.

    mode:
      - "original" : 원계열. YoY 계산용.
      - "sa"       : 계절조정계열. MoM 계산용.

    ECOS의 세부 항목명이 변경되더라도 최대한 견고하게 동작하도록
    '계절/조정' 포함 여부 + '전산업' 우선순위로 선택합니다.
    """
    if raw_df is None or raw_df.empty:
        return (empty_df(), None)
    work = raw_df.copy()
    work['series_name'] = work['series_name'].fillna('').astype(str)
    unique_names = [name for name in work['series_name'].drop_duplicates().tolist() if name]

    def score_name(name):
        score = 0
        compact = name.replace(' ', '')
        if '전산업생산지수' in compact:
            score += 100
        elif '전산업' in compact:
            score += 80
        for bad in ['광공업', '제조업', '서비스업', '건설업', '공공행정', '농림어업']:
            if bad in compact:
                score -= 40
        if mode == 'sa':
            if '계절조정' in compact:
                score += 80
            elif '계절' in compact or '조정' in compact:
                score += 50
            if '원계열' in compact or '원지수' in compact:
                score -= 100
        else:
            if '원계열' in compact or '원지수' in compact:
                score += 80
            if '계절조정' in compact:
                score -= 120
            elif '계절' in compact or '조정' in compact:
                score -= 80
        return score
    if mode == 'sa':
        candidates = [n for n in unique_names if '계절조정' in n.replace(' ', '') or '계절' in n.replace(' ', '') or '조정' in n.replace(' ', '')]
    else:
        explicit = [n for n in unique_names if '원계열' in n.replace(' ', '') or '원지수' in n.replace(' ', '')]
        if explicit:
            candidates = explicit
        else:
            candidates = [n for n in unique_names if not ('계절조정' in n.replace(' ', '') or '계절' in n.replace(' ', '') or '조정' in n.replace(' ', ''))]
    if not candidates:
        return (empty_df(), None)
    chosen = max(candidates, key=score_name)
    selected = work[work['series_name'] == chosen][['date', 'value']].dropna().drop_duplicates(subset=['date'], keep='last').sort_values('date').reset_index(drop=True)
    return (selected, chosen)


ECOS_SERIES = {
    "한국 국고채 3Y": {"stat": "817Y002", "item": "010200000", "cycle": "D"},
    "한국 국고채 10Y": {"stat": "817Y002", "item": "010210000", "cycle": "D"},
    "한국 회사채 AA- 3Y": {"stat": "817Y002", "item": "010310000", "cycle": "D"},
    "USD/KRW": {"stat": "731Y003", "item": "0000003", "cycle": "D"},
    "한국 CPI": {"stat": "901Y009", "item": "0", "cycle": "M"},
}


def get_bls_macro():
    """
    Load all U.S. macro series in ONE BLS request.

    This consumes one BLS API query per daily email run, not one request
    per indicator. With a registered key this is tiny relative to the
    registered daily allowance.

    Keys returned match the rest of the email-report code.
    """
    series_map = {
        "CUUR0000SA0": "us_cpi_nsa",
        "CUSR0000SA0": "us_cpi_sa",
        "CUUR0000SA0L1E": "us_core_nsa",
        "CUSR0000SA0L1E": "us_core_sa",
        "LNS14000000": "unemployment",
        "CES0000000001": "payroll",
    }

    end_year = datetime.now(KOREA_TZ).year
    start_year = end_year - 2

    if BLS_API_KEY:
        endpoint = (
            "https://api.bls.gov/publicAPI/v2/"
            "timeseries/data/"
        )
    else:
        endpoint = (
            "https://api.bls.gov/publicAPI/v1/"
            "timeseries/data/"
        )

    payload = {
        "seriesid": list(series_map.keys()),
        "startyear": str(start_year),
        "endyear": str(end_year),
    }

    if BLS_API_KEY:
        payload["registrationkey"] = BLS_API_KEY

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "BondMacroDashboard-DailyReport/1.1"
        ),
    }

    last_error = None

    for attempt in range(2):
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()

            body = response.json()
            status = body.get(
                "status",
                "",
            )
            messages = body.get(
                "message",
                [],
            ) or []

            if status != "REQUEST_SUCCEEDED":
                detail = " | ".join(
                    str(x)
                    for x in messages
                    if x
                )
                raise RuntimeError(
                    f"BLS status={status or 'UNKNOWN'}"
                    + (
                        f": {detail}"
                        if detail
                        else ""
                    )
                )

            results = body.get(
                "Results",
                {},
            )

            if isinstance(
                results,
                list,
            ):
                results = (
                    results[0]
                    if results
                    else {}
                )

            series_list = (
                results.get(
                    "series",
                    [],
                )
                if isinstance(
                    results,
                    dict,
                )
                else []
            )

            if not series_list:
                raise RuntimeError(
                    "BLS returned no series"
                )

            valid_months = {
                f"M{i:02d}"
                for i in range(1, 13)
            }

            result = {}

            for series in series_list:
                series_id = series.get(
                    "seriesID"
                )
                target_key = series_map.get(
                    series_id
                )

                if not target_key:
                    continue

                rows = []

                for obs in series.get(
                    "data",
                    [],
                ):
                    period = obs.get(
                        "period",
                        "",
                    )

                    # M13 = annual average.
                    if period not in valid_months:
                        continue

                    rows.append(
                        {
                            "date": pd.to_datetime(
                                f"{obs.get('year')}-{period[1:]}-01",
                                errors="coerce",
                            ),
                            "value": pd.to_numeric(
                                obs.get("value"),
                                errors="coerce",
                            ),
                        }
                    )

                result[target_key] = (
                    pd.DataFrame(rows)
                    .dropna()
                    .drop_duplicates(
                        subset=["date"],
                        keep="last",
                    )
                    .sort_values("date")
                    .reset_index(drop=True)
                )

            if not any(
                isinstance(df, pd.DataFrame)
                and not df.empty
                for df in result.values()
            ):
                raise RuntimeError(
                    "BLS returned no usable monthly observations"
                )

            return result

        except Exception as exc:
            last_error = exc

            if attempt == 0:
                time.sleep(1.0)

    raise last_error


def get_fred_series(series_id, years=3):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    response.raise_for_status()

    df = pd.read_csv(StringIO(response.text))
    if df.empty or "DATE" not in df.columns:
        return empty_df()

    value_cols = [c for c in df.columns if c != "DATE"]
    if not value_cols:
        return empty_df()

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["DATE"], errors="coerce"),
            "value": pd.to_numeric(df[value_cols[0]], errors="coerce"),
        }
    ).dropna()

    cutoff = pd.Timestamp(datetime.now(KOREA_TZ).date()) - pd.DateOffset(years=years)

    return (
        out[out["date"] >= cutoff]
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def signed(value, unit="", decimals=1):
    if value is None or pd.isna(value):
        return "-"
    sign = "+" if float(value) > 0 else ""
    return f"{sign}{float(value):.{decimals}f}{unit}"


def latest_value(df):
    clean = _clean_series(df)
    if clean.empty:
        return None, None

    row = clean.iloc[-1]
    return pd.Timestamp(row["date"]), float(row["value"])


def horizon_pack(df, value_type="rate"):
    return {
        "1D": get_horizon_change(df, "1D", value_type),
        "1W": get_horizon_change(df, "1W", value_type),
        "1M": get_horizon_change(df, "1M", value_type),
        "pct5y": historical_percentile(df, 5),
    }


def fetch_all_data():
    data = {}
    errors = []

    try:
        data["treasury"] = get_treasury_curve()
    except Exception as exc:
        data["treasury"] = {}
        errors.append(f"U.S. Treasury nominal: {exc}")

    try:
        data["treasury_real"] = get_treasury_real_curve()
    except Exception as exc:
        data["treasury_real"] = {}
        errors.append(f"U.S. Treasury real: {exc}")

    # U.S. macro: official BLS API first.
    # The whole set is fetched in a single request per email run.
    try:
        data["us_macro"] = get_bls_macro()
        data["us_macro_source"] = "BLS"
    except Exception as bls_exc:
        errors.append(
            f"BLS macro: {bls_exc}"
        )

        # FRED is only a fallback now, not the primary source.
        fred_map = {
            "us_cpi_nsa": "CPIAUCNS",
            "us_cpi_sa": "CPIAUCSL",
            "us_core_nsa": "CPILFENS",
            "us_core_sa": "CPILFESL",
            "unemployment": "UNRATE",
            "payroll": "PAYEMS",
        }

        data["us_macro"] = {}
        fred_success = 0

        for key, fred_id in fred_map.items():
            try:
                df = get_fred_series(
                    fred_id,
                    years=3,
                )
                data["us_macro"][key] = df

                if not df.empty:
                    fred_success += 1

            except Exception as exc:
                data["us_macro"][key] = empty_df()
                errors.append(
                    f"FRED fallback {fred_id}: {exc}"
                )

        data["us_macro_source"] = (
            "FRED fallback"
            if fred_success > 0
            else "Unavailable"
        )

    try:
        data["wti"] = get_wti()
    except Exception as exc:
        data["wti"] = empty_df()
        errors.append(f"WTI: {exc}")

    try:
        data["fed_policy"] = get_fed_policy_rate()
    except Exception as exc:
        data["fed_policy"] = None
        errors.append(f"Fed policy: {exc}")

    try:
        data["bok_policy"] = get_bok_policy_rate()
    except Exception as exc:
        data["bok_policy"] = None
        errors.append(f"BOK policy: {exc}")

    data["ecos"] = {}

    for name, info in ECOS_SERIES.items():
        try:
            data["ecos"][name] = get_ecos(
                info["stat"],
                info["item"],
                info["cycle"],
            )
        except Exception as exc:
            data["ecos"][name] = empty_df()
            errors.append(f"ECOS {name}: {exc}")

    try:
        raw_ip = get_ecos_raw_table("901Y033", "M")
        ip_original, original_name = select_industrial_series(raw_ip, "original")
        ip_sa, sa_name = select_industrial_series(raw_ip, "sa")

        data["ecos"]["한국 전산업생산 (원계열)"] = ip_original
        data["ecos"]["한국 전산업생산 (계절조정)"] = ip_sa
        data["industrial_series_names"] = {
            "original": original_name,
            "sa": sa_name,
        }
    except Exception as exc:
        data["ecos"]["한국 전산업생산 (원계열)"] = empty_df()
        data["ecos"]["한국 전산업생산 (계절조정)"] = empty_df()
        errors.append(f"ECOS industrial production: {exc}")

    data["errors"] = errors
    return data


def build_derived(data):
    treasury = data["treasury"]
    treasury_real = data["treasury_real"]
    ecos = data["ecos"]
    us_macro = data["us_macro"]

    derived = {}

    us2 = _clean_series(treasury.get("미국 2Y", empty_df())).rename(
        columns={"value": "short"}
    )
    us10 = _clean_series(treasury.get("미국 10Y", empty_df())).rename(
        columns={"value": "long"}
    )
    us_curve = pd.merge(us2, us10, on="date", how="inner")
    if not us_curve.empty:
        us_curve["value"] = us_curve["long"] - us_curve["short"]
        derived["us_curve_series"] = us_curve[["date", "value"]]
    else:
        derived["us_curve_series"] = empty_df()

    kr3 = _clean_series(ecos.get("한국 국고채 3Y", empty_df())).rename(
        columns={"value": "short"}
    )
    kr10 = _clean_series(ecos.get("한국 국고채 10Y", empty_df())).rename(
        columns={"value": "long"}
    )
    kr_curve = pd.merge(kr3, kr10, on="date", how="inner")
    if not kr_curve.empty:
        kr_curve["value"] = kr_curve["long"] - kr_curve["short"]
        derived["kr_curve_series"] = kr_curve[["date", "value"]]
    else:
        derived["kr_curve_series"] = empty_df()

    for maturity, nominal_name, real_name in [
        ("5Y", "미국 5Y", "미국 실질 5Y"),
        ("10Y", "미국 10Y", "미국 실질 10Y"),
    ]:
        nominal = _clean_series(
            treasury.get(nominal_name, empty_df())
        ).rename(columns={"value": "nominal"})

        real = _clean_series(
            treasury_real.get(real_name, empty_df())
        ).rename(columns={"value": "real"})

        merged = pd.merge(nominal, real, on="date", how="inner")
        if not merged.empty:
            merged["value"] = merged["nominal"] - merged["real"]
            derived[f"bei_{maturity}"] = merged[["date", "value"]]
        else:
            derived[f"bei_{maturity}"] = empty_df()

    derived["decomp10"] = build_10y_decomposition(
        treasury.get("미국 10Y", empty_df()),
        treasury_real.get("미국 실질 10Y", empty_df()),
    )

    _, _, us2_chg = get_latest_change(
        treasury.get("미국 2Y", empty_df()),
        "rate",
    )
    _, _, us10_chg = get_latest_change(
        treasury.get("미국 10Y", empty_df()),
        "rate",
    )
    _, _, kr3_chg = get_latest_change(
        ecos.get("한국 국고채 3Y", empty_df()),
        "rate",
    )
    _, _, kr10_chg = get_latest_change(
        ecos.get("한국 국고채 10Y", empty_df()),
        "rate",
    )

    derived["us_curve_label"] = classify_curve(us2_chg, us10_chg)
    derived["kr_curve_label"] = classify_curve(kr3_chg, kr10_chg)

    derived["us2_chg"] = us2_chg
    derived["us10_chg"] = us10_chg
    derived["kr3_chg"] = kr3_chg
    derived["kr10_chg"] = kr10_chg

    us_rows = build_macro_momentum_rows(
        cpi_nsa=us_macro.get("us_cpi_nsa", empty_df()),
        cpi_sa=us_macro.get("us_cpi_sa", empty_df()),
        core_nsa=us_macro.get("us_core_nsa", empty_df()),
        core_sa=us_macro.get("us_core_sa", empty_df()),
        unemployment=us_macro.get("unemployment", empty_df()),
        payroll=us_macro.get("payroll", empty_df()),
        country="US",
    )

    kr_rows = build_macro_momentum_rows(
        cpi_nsa=ecos.get("한국 CPI", empty_df()),
        cpi_sa=ecos.get("한국 CPI", empty_df()),
        industrial_original=ecos.get(
            "한국 전산업생산 (원계열)",
            empty_df(),
        ),
        industrial_sa=ecos.get(
            "한국 전산업생산 (계절조정)",
            empty_df(),
        ),
        country="KR",
    )

    derived["us_momentum_rows"] = us_rows
    derived["kr_momentum_rows"] = kr_rows
    derived["us_regime"] = macro_regime_from_rows(us_rows, "US")
    derived["kr_regime"] = macro_regime_from_rows(kr_rows, "KR")

    return derived


def rate_rows(data, derived):
    treasury = data["treasury"]
    treasury_real = data["treasury_real"]
    ecos = data["ecos"]

    series_map = {
        "US 2Y": treasury.get("미국 2Y", empty_df()),
        "US 10Y": treasury.get("미국 10Y", empty_df()),
        "US 10Y Real": treasury_real.get("미국 실질 10Y", empty_df()),
        "US 10Y BEI": derived.get("bei_10Y", empty_df()),
        "KR 3Y": ecos.get("한국 국고채 3Y", empty_df()),
        "KR 10Y": ecos.get("한국 국고채 10Y", empty_df()),
        "KR Corp AA- 3Y": ecos.get("한국 회사채 AA- 3Y", empty_df()),
    }

    rows = []

    for name, df in series_map.items():
        date, value = latest_value(df)
        if value is None:
            continue

        h = horizon_pack(df, "rate")

        rows.append(
            {
                "name": name,
                "date": date,
                "value": value,
                "d1": h["1D"],
                "w1": h["1W"],
                "m1": h["1M"],
                "pct5y": h["pct5y"],
            }
        )

    return rows


def macro_rows(data):
    us = data["us_macro"]
    kr = data["ecos"]

    rows = []

    for label, df_yoy, df_mom in [
        (
            "U.S. CPI",
            us.get("us_cpi_nsa", empty_df()),
            us.get("us_cpi_sa", empty_df()),
        ),
        (
            "U.S. Core CPI",
            us.get("us_core_nsa", empty_df()),
            us.get("us_core_sa", empty_df()),
        ),
        (
            "Korea CPI",
            kr.get("한국 CPI", empty_df()),
            kr.get("한국 CPI", empty_df()),
        ),
        (
            "Korea Industrial Production",
            kr.get("한국 전산업생산 (원계열)", empty_df()),
            kr.get("한국 전산업생산 (계절조정)", empty_df()),
        ),
    ]:
        date_yoy, current, yoy = get_latest_change(df_yoy, "yoy")
        _, _, mom = get_latest_change(df_mom, "mom")

        if current is not None:
            rows.append(
                {
                    "name": label,
                    "date": date_yoy,
                    "current": current,
                    "yoy": yoy,
                    "mom": mom,
                }
            )

    date, unemployment, change = get_latest_change(
        us.get("unemployment", empty_df()),
        "pp",
    )
    if unemployment is not None:
        rows.append(
            {
                "name": "U.S. Unemployment",
                "date": date,
                "current": unemployment,
                "change": change,
                "kind": "pp",
            }
        )

    date, payroll, change = get_latest_change(
        us.get("payroll", empty_df()),
        "nfp",
    )
    if payroll is not None:
        rows.append(
            {
                "name": "U.S. NFP",
                "date": date,
                "current": payroll,
                "change": change,
                "kind": "nfp",
            }
        )

    return rows


def format_policy(policy):
    if not policy:
        return "-"

    if isinstance(policy, dict):
        if "low" in policy and "high" in policy:
            return f"{policy['low']:.2f}–{policy['high']:.2f}%"
        if "rate" in policy:
            return f"{policy['rate']:.2f}%"

    return "-"


def html_rate_table(rows):
    out = []

    for row in rows:
        pctile = "-" if row["pct5y"] is None else f"{row['pct5y']:.0f}%"

        out.append(
            "<tr>"
            f"<td>{html.escape(row['name'])}</td>"
            f"<td>{row['value']:.2f}%</td>"
            f"<td>{signed(row['d1'], 'bp')}</td>"
            f"<td>{signed(row['w1'], 'bp')}</td>"
            f"<td>{signed(row['m1'], 'bp')}</td>"
            f"<td>{pctile}</td>"
            "</tr>"
        )

    return "".join(out)


def html_macro_table(rows):
    out = []

    for row in rows:
        date = (
            "-"
            if row.get("date") is None
            else pd.Timestamp(row["date"]).strftime("%Y-%m")
        )

        if "yoy" in row:
            change_text = (
                f"YoY {signed(row.get('yoy'), '%')} · "
                f"MoM {signed(row.get('mom'), '%')}"
            )
        elif row.get("kind") == "pp":
            change_text = f"MoM {signed(row.get('change'), 'pp')}"
        elif row.get("kind") == "nfp":
            change_text = f"MoM {signed(row.get('change'), 'K', 0)}"
        else:
            change_text = "-"

        out.append(
            "<tr>"
            f"<td>{html.escape(row['name'])}</td>"
            f"<td>{date}</td>"
            f"<td>{row['current']:.2f}</td>"
            f"<td>{change_text}</td>"
            "</tr>"
        )

    return "".join(out)


def html_momentum_table(rows):
    out = []

    for row in rows:
        out.append(
            "<tr>"
            f"<td>{html.escape(str(row['지표']))}</td>"
            f"<td>{html.escape(str(row['현재']))}</td>"
            f"<td>{html.escape(str(row['이전']))}</td>"
            f"<td><strong>{html.escape(str(row['Momentum']))}</strong></td>"
            "</tr>"
        )

    return "".join(out)


def build_report_html(data, derived):
    now = datetime.now(KOREA_TZ)
    treasury = data["treasury"]
    ecos = data["ecos"]

    rates = rate_rows(data, derived)
    macros = macro_rows(data)

    _, us10 = latest_value(treasury.get("미국 10Y", empty_df()))
    _, kr10 = latest_value(ecos.get("한국 국고채 10Y", empty_df()))
    _, fx = latest_value(ecos.get("USD/KRW", empty_df()))
    _, wti = latest_value(data.get("wti", empty_df()))

    _, _, fx_change = get_latest_change(
        ecos.get("USD/KRW", empty_df()),
        "pct",
    )
    _, _, wti_change = get_latest_change(
        data.get("wti", empty_df()),
        "pct",
    )

    decomp = derived.get("decomp10")

    if decomp:
        decomp_html = f"""
        <div class="callout">
            <div class="label">U.S. 10Y decomposition</div>
            <div class="big">{decomp['nominal']:.2f}%</div>
            <div>
                Nominal {signed(decomp['nominal_change'], 'bp')} ·
                Real {signed(decomp['real_change'], 'bp')} ·
                Breakeven {signed(decomp['bei_change'], 'bp')}
            </div>
            <div class="note">
                {html.escape(decomp['driver'])} · {html.escape(decomp['detail'])}
            </div>
        </div>
        """
    else:
        decomp_html = '<div class="callout">U.S. 10Y decomposition data unavailable.</div>'

    errors_html = ""

    if data.get("errors"):
        items = "".join(
            f"<li>{html.escape(error)}</li>"
            for error in data["errors"]
        )
        errors_html = f"""
        <div class="warning">
            <strong>Data notes</strong>
            <ul>{items}</ul>
        </div>
        """

    us_regime = derived["us_regime"]
    kr_regime = derived["kr_regime"]

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{
    margin:0;
    padding:0;
    background:#f4f6f9;
    color:#152033;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",Arial,sans-serif;
}}
.wrap {{ max-width:900px; margin:0 auto; padding:24px 14px 40px; }}
.hero {{
    background:linear-gradient(120deg,#0e1b35,#1d3b6b);
    color:white;
    border-radius:18px;
    padding:26px 28px;
}}
.eyebrow {{ color:#a9c2ff; font-size:11px; font-weight:700; letter-spacing:.12em; }}
h1 {{ margin:8px 0 5px; font-size:28px; }}
.hero p {{ margin:0; color:rgba(255,255,255,.72); font-size:13px; }}
.grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:14px; }}
.card, .callout {{
    background:white;
    border:1px solid #e2e7ef;
    border-radius:14px;
    padding:16px;
    margin-top:12px;
}}
.label {{ color:#6b778c; font-size:12px; font-weight:700; }}
.value {{ font-size:22px; font-weight:800; margin-top:5px; color:#14203a; }}
.big {{ font-size:22px; font-weight:800; margin:6px 0; }}
.muted {{ color:#7b879a; font-size:12px; }}
.note {{ color:#667289; margin-top:8px; font-size:12px; }}
.section {{ margin-top:25px; }}
.section h2 {{ font-size:18px; margin:0 0 10px; }}
table {{ width:100%; border-collapse:collapse; background:white; }}
th {{
    background:#f5f7fa;
    color:#4d596d;
    text-align:left;
    font-size:12px;
    padding:10px;
    border-bottom:1px solid #e2e7ef;
}}
td {{ font-size:12px; padding:10px; border-bottom:1px solid #edf0f5; }}
.regime {{ font-size:18px; font-weight:800; margin:4px 0; }}
.warning {{
    background:#fff8e8;
    border:1px solid #f1dda9;
    border-radius:12px;
    padding:12px 15px;
    margin-top:15px;
    font-size:12px;
}}
.footer {{ color:#8a94a6; font-size:11px; text-align:center; margin-top:24px; }}
@media (max-width:600px) {{
    .grid {{ grid-template-columns:1fr; }}
}}
</style>
</head>
<body>
<div class="wrap">
    <div class="hero">
        <div class="eyebrow">BOND & MACRO DAILY BRIEF</div>
        <h1>{now.strftime('%Y-%m-%d')}</h1>
        <p>KST {now.strftime('%H:%M')} · Automated fixed-income & macro monitor</p>
    </div>

    <div class="grid">
        <div class="card">
            <div class="label">Fed Funds</div>
            <div class="value">{html.escape(format_policy(data.get('fed_policy')))}</div>
        </div>
        <div class="card">
            <div class="label">BOK Base Rate</div>
            <div class="value">{html.escape(format_policy(data.get('bok_policy')))}</div>
        </div>
        <div class="card">
            <div class="label">U.S. 10Y</div>
            <div class="value">{'-' if us10 is None else f'{us10:.2f}%'}</div>
            <div class="muted">
                {html.escape(derived['us_curve_label'])} ·
                2Y {signed(derived['us2_chg'], 'bp')} /
                10Y {signed(derived['us10_chg'], 'bp')}
            </div>
        </div>
        <div class="card">
            <div class="label">Korea 10Y</div>
            <div class="value">{'-' if kr10 is None else f'{kr10:.2f}%'}</div>
            <div class="muted">
                {html.escape(derived['kr_curve_label'])} ·
                3Y {signed(derived['kr3_chg'], 'bp')} /
                10Y {signed(derived['kr10_chg'], 'bp')}
            </div>
        </div>
        <div class="card">
            <div class="label">USD/KRW</div>
            <div class="value">{'-' if fx is None else f'{fx:,.2f}'}</div>
            <div class="muted">{signed(fx_change, '%')}</div>
        </div>
        <div class="card">
            <div class="label">WTI Futures</div>
            <div class="value">{'-' if wti is None else f'${wti:,.2f}'}</div>
            <div class="muted">{signed(wti_change, '%')}</div>
        </div>
    </div>

    <div class="section">
        <h2>Rates & Curve</h2>
        <table>
            <thead>
                <tr>
                    <th>Indicator</th>
                    <th>Current</th>
                    <th>1D</th>
                    <th>1W</th>
                    <th>1M</th>
                    <th>5Y %ile</th>
                </tr>
            </thead>
            <tbody>{html_rate_table(rates)}</tbody>
        </table>
    </div>

    {decomp_html}

    <div class="section">
        <h2>Macro Snapshot</h2>
        <table>
            <thead>
                <tr>
                    <th>Indicator</th>
                    <th>Reference</th>
                    <th>Current</th>
                    <th>Change</th>
                </tr>
            </thead>
            <tbody>{html_macro_table(macros)}</tbody>
        </table>
    </div>

    <div class="section">
        <h2>Macro Momentum</h2>
        <div class="grid">
            <div>
                <div class="label">United States</div>
                <table>
                    <thead>
                        <tr>
                            <th>Indicator</th>
                            <th>Current</th>
                            <th>Previous</th>
                            <th>Momentum</th>
                        </tr>
                    </thead>
                    <tbody>{html_momentum_table(derived['us_momentum_rows'])}</tbody>
                </table>
            </div>
            <div>
                <div class="label">Korea</div>
                <table>
                    <thead>
                        <tr>
                            <th>Indicator</th>
                            <th>Current</th>
                            <th>Previous</th>
                            <th>Momentum</th>
                        </tr>
                    </thead>
                    <tbody>{html_momentum_table(derived['kr_momentum_rows'])}</tbody>
                </table>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Macro Regime</h2>
        <div class="grid">
            <div class="card">
                <div class="label">United States</div>
                <div class="regime">{html.escape(us_regime['name'])}</div>
                <div>{html.escape(us_regime['growth'])} · {html.escape(us_regime['inflation'])}</div>
                <div class="note">{html.escape(us_regime['evidence'])}</div>
            </div>
            <div class="card">
                <div class="label">Korea</div>
                <div class="regime">{html.escape(kr_regime['name'])}</div>
                <div>{html.escape(kr_regime['growth'])} · {html.escape(kr_regime['inflation'])}</div>
                <div class="note">{html.escape(kr_regime['evidence'])}</div>
            </div>
        </div>
    </div>

    {errors_html}

    <div class="footer">
        Sources: U.S. Treasury · BLS/FRED · Federal Reserve · Bank of Korea · ECOS · Yahoo Finance<br>
        U.S. Macro source for this run: {html.escape(data.get('us_macro_source', '-'))}<br>
        Rule-based monitor. Not an investment recommendation.
    </div>
</div>
</body>
</html>
"""


def build_text_summary(data, derived):
    treasury = data["treasury"]
    ecos = data["ecos"]

    _, us10 = latest_value(treasury.get("미국 10Y", empty_df()))
    _, kr10 = latest_value(ecos.get("한국 국고채 10Y", empty_df()))
    _, fx = latest_value(ecos.get("USD/KRW", empty_df()))
    _, wti = latest_value(data.get("wti", empty_df()))

    lines = [
        f"Bond & Macro Daily Brief - {datetime.now(KOREA_TZ).strftime('%Y-%m-%d')}",
        "",
        f"Fed Funds: {format_policy(data.get('fed_policy'))}",
        f"BOK Base Rate: {format_policy(data.get('bok_policy'))}",
        f"U.S. 10Y: {'-' if us10 is None else f'{us10:.2f}%'} | {derived['us_curve_label']}",
        f"Korea 10Y: {'-' if kr10 is None else f'{kr10:.2f}%'} | {derived['kr_curve_label']}",
        f"USD/KRW: {'-' if fx is None else f'{fx:,.2f}'}",
        f"WTI: {'-' if wti is None else f'${wti:,.2f}'}",
        "",
        f"U.S. Regime: {derived['us_regime']['name']} ({derived['us_regime']['growth']} / {derived['us_regime']['inflation']})",
        f"Korea Regime: {derived['kr_regime']['name']} ({derived['kr_regime']['growth']} / {derived['kr_regime']['inflation']})",
    ]

    if data.get("errors"):
        lines += ["", "Data notes:"]
        lines += [f"- {x}" for x in data["errors"]]

    return "\n".join(lines)


def send_email(subject, html_body, text_body):
    required = {
        "EMAIL_USER": EMAIL_USER,
        "EMAIL_APP_PASSWORD": EMAIL_APP_PASSWORD,
        "EMAIL_TO": EMAIL_TO,
    }

    missing = [
        key
        for key, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing email environment variables: "
            + ", ".join(missing)
        )

    recipients = [
        address.strip()
        for address in EMAIL_TO.split(",")
        if address.strip()
    ]

    if not recipients:
        raise RuntimeError("EMAIL_TO has no valid recipient.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()

    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT,
        context=context,
        timeout=30,
    ) as smtp:
        smtp.login(
            EMAIL_USER,
            EMAIL_APP_PASSWORD,
        )
        smtp.send_message(msg)


def main():
    if not ECOS_API_KEY:
        raise RuntimeError(
            "ECOS_API_KEY is required for Korea data."
        )

    print("[1/4] Fetching market and macro data...")
    data = fetch_all_data()

    print("[2/4] Building curve, decomposition, momentum and regime...")
    derived = build_derived(data)

    print("[3/4] Building email...")
    today = datetime.now(KOREA_TZ).strftime("%Y-%m-%d")
    subject = f"[Bond & Macro] Daily Brief - {today}"

    html_body = build_report_html(data, derived)
    text_body = build_text_summary(data, derived)

    with open(
        "daily_report_preview.html",
        "w",
        encoding="utf-8",
    ) as file:
        file.write(html_body)

    print("[4/4] Sending email...")
    send_email(
        subject,
        html_body,
        text_body,
    )

    print("Email sent successfully.")


if __name__ == "__main__":
    main()
