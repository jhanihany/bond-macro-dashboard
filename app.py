import html
import time
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


# =========================================================
# App config
# =========================================================
st.set_page_config(
    page_title="Bond & Macro Dashboard",
    page_icon="📊",
    layout="wide",
)

KOREA_TZ = ZoneInfo("Asia/Seoul")
NEW_YORK_TZ = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")

ECOS_API_KEY = st.secrets.get("ECOS_API_KEY", "")
BLS_API_KEY = st.secrets.get("BLS_API_KEY", "")


# =========================================================
# Style
# =========================================================
BASE_DIR = Path(__file__).resolve().parent


def load_css():
    css_path = BASE_DIR / "style.css"

    if not css_path.exists():
        st.warning("style.css 파일을 찾을 수 없습니다.")
        return

    css = css_path.read_text(encoding="utf-8")
    st.markdown(
        f"<style>{css}</style>",
        unsafe_allow_html=True,
    )


load_css()


# =========================================================
# Session / clock / refresh
# =========================================================
now_utc = datetime.now(UTC_TZ)
now_korea = now_utc.astimezone(KOREA_TZ)
now_new_york = now_utc.astimezone(NEW_YORK_TZ)
ny_tz_name = now_new_york.tzname() or "ET"

time_diff_hours = (
    now_korea.utcoffset().total_seconds()
    - now_new_york.utcoffset().total_seconds()
) / 3600

if "last_updated_krt" not in st.session_state:
    st.session_state["last_updated_krt"] = None

st.markdown(
    """
    <div class="dashboard-hero">
        <div class="hero-eyebrow">
            <span class="hero-dot"></span>
            GLOBAL FIXED INCOME MONITOR
        </div>
        <div class="hero-title">
            Bond &amp; Macro <span>Dashboard</span>
        </div>
        <div class="hero-subtitle">
            한국·미국 금리, 수익률곡선, 물가와 성장 모멘텀을
            하나의 화면에서 모니터링합니다.
            
            시장의 방향보다 구조와 변화를 빠르게 파악하는 데 초점을 둡니다.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

time_col, refresh_col = st.columns([5.5, 1.2], gap="small")

with time_col:
    st.markdown(
        f"""
        <div class="control-strip">
            <span class="control-chip live-chip">
                ● DATA MONITOR
            </span>
            <span class="control-chip">
                <strong>KST</strong>
                {now_korea.strftime('%Y-%m-%d %H:%M')}
            </span>
            <span class="control-chip">
                <strong>NEW YORK {ny_tz_name}</strong>
                {now_new_york.strftime('%Y-%m-%d %H:%M')}
            </span>
            <span class="control-chip">
                <strong>TIME GAP</strong>
                +{time_diff_hours:g}H KOREA
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

with refresh_col:
    refresh_clicked = st.button(
        "Refresh Data",
        use_container_width=True,
        type="primary",
    )

if refresh_clicked:
    st.cache_data.clear()

st.markdown('<div style="height:6px;"></div>', unsafe_allow_html=True)


# =========================================================
# Common helpers
# =========================================================
def render_section_header(number, title, description=""):
    desc_html = (
        f'<div class="section-desc">{html.escape(description)}</div>'
        if description
        else ""
    )

    st.markdown(
        f"""
        <div class="section-heading">
            <div class="section-number">{html.escape(number)}</div>
            <div class="section-text">
                <div class="section-title">{html.escape(title)}</div>
                {desc_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_panel_heading(code, title):
    st.markdown(
        f"""
        <div class="panel-heading">
            <span class="panel-flag">{html.escape(code)}</span>
            <span class="panel-title">{html.escape(title)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_subsection_title(title):
    st.markdown(
        f'<div class="subsection-title">{html.escape(title)}</div>',
        unsafe_allow_html=True,
    )


def soft_divider():
    st.markdown(
        '<div class="soft-divider"></div>',
        unsafe_allow_html=True,
    )


def empty_df():
    return pd.DataFrame(columns=["date", "value"])


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        return None, None, None

    clean = df.dropna().sort_values("date").reset_index(drop=True)
    if clean.empty:
        return None, None, None

    current = float(clean.iloc[-1]["value"])
    current_date = clean.iloc[-1]["date"]

    if len(clean) < 2:
        return current_date, current, None

    previous = float(clean.iloc[-2]["value"])

    if value_type == "rate":
        change = (current - previous) * 100
    elif value_type == "pp":
        change = current - previous
    elif value_type == "nfp":
        change = current - previous
    elif value_type == "yoy":
        target = current_date - pd.DateOffset(months=12)

        # 같은 기준월을 우선 사용하고, 해당 월이 없을 때만
        # target 이전의 가장 최근 관측치를 fallback으로 사용합니다.
        exact_past = clean[
            clean["date"].dt.to_period("M")
            == pd.Timestamp(target).to_period("M")
        ]

        if not exact_past.empty:
            past_value = float(exact_past.iloc[-1]["value"])
        else:
            past = clean[clean["date"] <= target]
            past_value = (
                float(past.iloc[-1]["value"])
                if not past.empty
                else None
            )

        if past_value is None or past_value == 0:
            change = None
        else:
            change = (current / past_value - 1) * 100

    elif value_type == "mom":
        change = (
            (current / previous - 1) * 100
            if previous != 0
            else None
        )

    else:
        change = (
            (current / previous - 1) * 100
            if previous != 0
            else None
        )

    return current_date, current, change


def add_row(rows, country, name, df, freq, value_type):
    if df is None or df.empty:
        return

    date, value, change = get_latest_change(df, value_type)
    if date is None:
        return

    rows.append(
        {
            "구분": country,
            "지표": name,
            "기준일": date,
            "현재": value,
            "현재표시": None,
            "변화값": change,
            "빈도": freq,
            "type": value_type,
        }
    )


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
    if value_type == "yoy":
        return f"{sign}{change:.1f}% YoY"
    if value_type == "mom":
        return f"{sign}{change:.1f}% MoM"

    return f"{sign}{change:.1f}%"


def delta_css_class(change):
    if pd.isna(change) or abs(float(change)) < 1e-12:
        return "delta-flat"
    return "delta-up" if change > 0 else "delta-down"


def format_basis_date(date_value, freq):
    if pd.isna(date_value):
        return "-"

    date_value = pd.Timestamp(date_value)

    if freq == "월간":
        return date_value.strftime("%Y-%m")
    if freq == "정책금리":
        return date_value.strftime("%Y-%m-%d") + " 발표"

    return date_value.strftime("%Y-%m-%d")


def get_row(df, indicator_name):
    matched = df[df["지표"] == indicator_name]
    if matched.empty:
        return None
    return matched.iloc[0]


def display_value(row, decimals=2, suffix=""):
    if row is None:
        return "-"

    custom = row.get("현재표시")
    if isinstance(custom, str) and custom:
        return custom

    value = safe_float(row.get("현재"))
    if value is None:
        return "-"

    return f"{value:,.{decimals}f}{suffix}"


def render_metric_card(label, value, change, change_type, basis_date):
    delta_text = format_change(change, change_type)
    css_class = delta_css_class(change)

    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{html.escape(label)}</div>
            <div class="metric-value">{html.escape(value)}</div>
            <div class="{css_class}">{html.escape(delta_text)}</div>
            <div class="metric-date">{html.escape(basis_date)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
        return "판단 불가"

    short_change_bp = float(short_change_bp)
    long_change_bp = float(long_change_bp)
    slope_change = long_change_bp - short_change_bp

    if abs(slope_change) < 0.05:
        if short_change_bp > 0 and long_change_bp > 0:
            return "Bear Parallel Shift"
        if short_change_bp < 0 and long_change_bp < 0:
            return "Bull Parallel Shift"
        return "Parallel / Flat"

    direction = "Steepening" if slope_change > 0 else "Flattening"

    if short_change_bp > 0 and long_change_bp > 0:
        prefix = "Bear"
    elif short_change_bp < 0 and long_change_bp < 0:
        prefix = "Bull"
    else:
        prefix = "Twist"

    return f"{prefix} {direction}"


def render_curve_summary(
    title,
    short_label,
    short_change,
    long_label,
    long_change,
):
    label = classify_curve(short_change, long_change)

    short_text = format_change(short_change, "rate")
    long_text = format_change(long_change, "rate")
    short_class = delta_css_class(short_change)
    long_class = delta_css_class(long_change)

    st.markdown(
        f"""
        <div class="curve-box">
            <div class="curve-title">{html.escape(title)}</div>
            <div class="curve-main">
                {html.escape(short_label)}
                <span class="{short_class}">{html.escape(short_text)}</span>
                &nbsp;·&nbsp;
                {html.escape(long_label)}
                <span class="{long_class}">{html.escape(long_text)}</span>
                <span class="curve-label">{html.escape(label)}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def styled_table(temp):
    display_cols = ["지표", "기준일", "현재", "변화"]
    work = temp.copy()

    work["기준일"] = [
        format_basis_date(d, f)
        for d, f in zip(work["기준일"], work["빈도"])
    ]
    work["변화"] = [
        format_change(c, t)
        for c, t in zip(work["변화값"], work["type"])
    ]
    work["현재"] = work.apply(
        lambda row: (
            row["현재표시"]
            if isinstance(row.get("현재표시"), str)
            and row["현재표시"]
            else f'{float(row["현재"]):,.2f}'
        ),
        axis=1,
    )

    display_df = work[display_cols + ["변화값"]].reset_index(drop=True)

    def style_change_column(col):
        styles = []
        for idx in col.index:
            change = display_df.loc[idx, "변화값"]

            if pd.isna(change) or abs(float(change)) < 1e-12:
                styles.append("font-weight: 700; color: #000000;")
            elif change > 0:
                styles.append("font-weight: 700; color: #d62728;")
            else:
                styles.append("font-weight: 700; color: #1f77b4;")
        return styles

    return (
        display_df[display_cols]
        .style
        .apply(style_change_column, subset=["변화"])
    )


def calc_yoy_mom(yoy_df, mom_df=None):
    """
    하나의 거시지표에 대해 YoY와 MoM을 동시에 계산합니다.

    yoy_df:
      전년동월비 계산에 사용할 계열.
      CPI는 NSA, 산업생산은 원계열을 사용합니다.

    mom_df:
      전월비 계산에 사용할 계열.
      CPI는 SA, 산업생산은 계절조정계열을 사용합니다.
      None이면 yoy_df를 그대로 사용합니다.
    """
    if yoy_df is None or yoy_df.empty:
        return None

    if mom_df is None:
        mom_df = yoy_df

    yoy_date, current_value, yoy = get_latest_change(
        yoy_df,
        "yoy",
    )
    mom_date, _, mom = get_latest_change(
        mom_df,
        "mom",
    )

    if yoy_date is None:
        return None

    # 데이터 제공기관의 최신 기준월이 다르면 더 오래된 공통월을 강제로
    # 섞지 않고 각 최신값을 그대로 쓰되 기준월은 YoY 계열 기준으로 표시합니다.
    return {
        "date": yoy_date,
        "current": current_value,
        "yoy": yoy,
        "mom": mom,
        "mom_date": mom_date,
    }


def format_pct_change(value, label):
    if value is None or pd.isna(value):
        return "-"

    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}% {label}"


def build_dual_macro_row(
    name,
    date,
    current,
    yoy=None,
    mom=None,
    change=None,
    change_type=None,
):
    return {
        "지표": name,
        "기준일": (
            "-"
            if date is None or pd.isna(date)
            else pd.Timestamp(date).strftime("%Y-%m")
        ),
        "현재": (
            "-"
            if current is None or pd.isna(current)
            else f"{float(current):,.2f}"
        ),
        "YoY": format_pct_change(yoy, "YoY"),
        "MoM": format_pct_change(mom, "MoM"),
        "변화": (
            "-"
            if change_type is None
            else format_change(change, change_type)
        ),
        "_yoy": yoy,
        "_mom": mom,
        "_change": change,
    }


def styled_macro_table(rows):
    """
    Macro 전용 표.

    CPI / Core CPI / 산업생산처럼 YoY와 MoM이 모두 중요한 지표는
    두 열을 동시에 보여주고,
    실업률 / NFP처럼 별도 변화값이 더 자연스러운 지표는 '변화' 열을 사용합니다.
    """
    if not rows:
        return None

    df = pd.DataFrame(rows)
    visible_cols = [
        "지표",
        "기준일",
        "현재",
        "YoY",
        "MoM",
        "변화",
    ]

    def style_signed_column(col, hidden_col):
        styles = []

        for idx in col.index:
            value = df.loc[idx, hidden_col]

            if value is None or pd.isna(value) or abs(float(value)) < 1e-12:
                styles.append(
                    "font-weight: 700; color: #000000;"
                )
            elif value > 0:
                styles.append(
                    "font-weight: 700; color: #d62728;"
                )
            else:
                styles.append(
                    "font-weight: 700; color: #1f77b4;"
                )

        return styles

    styler = df[visible_cols].style

    styler = styler.apply(
        lambda col: style_signed_column(col, "_yoy"),
        subset=["YoY"],
    )
    styler = styler.apply(
        lambda col: style_signed_column(col, "_mom"),
        subset=["MoM"],
    )
    styler = styler.apply(
        lambda col: style_signed_column(col, "_change"),
        subset=["변화"],
    )

    return styler


# =========================================================
# Insight helpers
# =========================================================
def _clean_series(df):
    if df is None or df.empty:
        return empty_df()

    clean = (
        df[["date", "value"]]
        .dropna()
        .copy()
    )
    clean["date"] = pd.to_datetime(
        clean["date"],
        errors="coerce",
    )
    clean["value"] = pd.to_numeric(
        clean["value"],
        errors="coerce",
    )

    return (
        clean
        .dropna()
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


def get_horizon_change(df, horizon, value_type="rate"):
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

    current_date = pd.Timestamp(clean.iloc[-1]["date"])
    current = float(clean.iloc[-1]["value"])

    if horizon == "1D":
        previous = float(clean.iloc[-2]["value"])
    else:
        if horizon == "1W":
            target = current_date - pd.Timedelta(days=7)
        elif horizon == "1M":
            target = current_date - pd.DateOffset(months=1)
        else:
            return None

        past = clean[clean["date"] <= target]
        if past.empty:
            return None

        previous = float(past.iloc[-1]["value"])

    if value_type == "rate":
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

    current_date = pd.Timestamp(clean.iloc[-1]["date"])
    current = float(clean.iloc[-1]["value"])
    cutoff = current_date - pd.DateOffset(years=years)

    window = clean[clean["date"] >= cutoff]
    if window.empty:
        return None

    values = window["value"].astype(float)
    return float((values <= current).mean() * 100)


def format_horizon_change(value, value_type="rate"):
    if value is None or pd.isna(value):
        return "-"

    sign = "+" if value > 0 else ""

    if value_type == "rate":
        return f"{sign}{value:.1f}bp"

    return f"{sign}{value:.1f}%"


def build_rates_insight_table(series_map, ordered_names):
    rows = []

    for name in ordered_names:
        df = _clean_series(series_map.get(name, empty_df()))
        if df.empty:
            continue

        current = float(df.iloc[-1]["value"])
        basis_date = pd.Timestamp(df.iloc[-1]["date"])

        rows.append(
            {
                "지표": name,
                "기준일": basis_date.strftime("%Y-%m-%d"),
                "현재": f"{current:,.2f}",
                "1D": format_horizon_change(
                    get_horizon_change(df, "1D", "rate"),
                    "rate",
                ),
                "1W": format_horizon_change(
                    get_horizon_change(df, "1W", "rate"),
                    "rate",
                ),
                "1M": format_horizon_change(
                    get_horizon_change(df, "1M", "rate"),
                    "rate",
                ),
                "5Y %ile": (
                    "-"
                    if historical_percentile(df, 5) is None
                    else f"{historical_percentile(df, 5):.0f}%"
                ),
                "_1D": get_horizon_change(df, "1D", "rate"),
                "_1W": get_horizon_change(df, "1W", "rate"),
                "_1M": get_horizon_change(df, "1M", "rate"),
            }
        )

    return pd.DataFrame(rows)


def styled_rates_insight_table(df):
    if df is None or df.empty:
        return None

    visible = [
        "지표",
        "기준일",
        "현재",
        "1D",
        "1W",
        "1M",
        "5Y %ile",
    ]

    def signed_style(col, hidden):
        styles = []

        for idx in col.index:
            value = df.loc[idx, hidden]

            if value is None or pd.isna(value) or abs(float(value)) < 1e-12:
                styles.append(
                    "font-weight: 700; color: #000000;"
                )
            elif value > 0:
                styles.append(
                    "font-weight: 700; color: #d62728;"
                )
            else:
                styles.append(
                    "font-weight: 700; color: #1f77b4;"
                )

        return styles

    styler = df[visible].style

    for visible_col, hidden_col in [
        ("1D", "_1D"),
        ("1W", "_1W"),
        ("1M", "_1M"),
    ]:
        styler = styler.apply(
            lambda col, h=hidden_col: signed_style(col, h),
            subset=[visible_col],
        )

    return styler


def build_10y_decomposition(nominal_df, real_df):
    """
    Align nominal 10Y and real 10Y on common Treasury dates.

    Nominal ≈ Real + Breakeven
    Therefore the daily changes also add up on aligned dates.
    """
    nominal = _clean_series(nominal_df).rename(
        columns={"value": "nominal"}
    )
    real = _clean_series(real_df).rename(
        columns={"value": "real"}
    )

    merged = pd.merge(
        nominal,
        real,
        on="date",
        how="inner",
    ).sort_values("date")

    if len(merged) < 2:
        return None

    merged["bei"] = (
        merged["nominal"]
        - merged["real"]
    )

    current = merged.iloc[-1]
    previous = merged.iloc[-2]

    nominal_change = (
        float(current["nominal"])
        - float(previous["nominal"])
    ) * 100
    real_change = (
        float(current["real"])
        - float(previous["real"])
    ) * 100
    bei_change = (
        float(current["bei"])
        - float(previous["bei"])
    ) * 100

    if abs(real_change) > abs(bei_change) + 0.05:
        driver = "실질금리 변화 주도"
    elif abs(bei_change) > abs(real_change) + 0.05:
        driver = "기대인플레이션 변화 주도"
    else:
        driver = "실질금리·기대인플레이션 혼합"

    if real_change > 0:
        real_direction = "실질금리 상승"
    elif real_change < 0:
        real_direction = "실질금리 하락"
    else:
        real_direction = "실질금리 보합"

    if bei_change > 0:
        bei_direction = "인플레이션 보상 상승"
    elif bei_change < 0:
        bei_direction = "인플레이션 보상 하락"
    else:
        bei_direction = "인플레이션 보상 보합"

    return {
        "date": pd.Timestamp(current["date"]),
        "nominal": float(current["nominal"]),
        "real": float(current["real"]),
        "bei": float(current["bei"]),
        "nominal_change": nominal_change,
        "real_change": real_change,
        "bei_change": bei_change,
        "driver": driver,
        "detail": f"{real_direction} · {bei_direction}",
    }


def render_decomposition_card(title, change, current, detail):
    css_class = delta_css_class(change)
    st.markdown(
        f"""
        <div class="insight-box">
            <div class="insight-title">{html.escape(title)}</div>
            <div class="insight-main">
                {current:.2f}% ·
                <span class="{css_class}">
                    {html.escape(format_change(change, "rate"))}
                </span>
            </div>
            <div class="insight-detail">{html.escape(detail)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def yoy_history(df):
    clean = _clean_series(df)
    if clean.empty:
        return empty_df()

    lookup = {
        pd.Timestamp(row["date"]).to_period("M"): float(row["value"])
        for _, row in clean.iterrows()
    }

    rows = []
    for _, row in clean.iterrows():
        date = pd.Timestamp(row["date"])
        prior_period = (
            date - pd.DateOffset(months=12)
        ).to_period("M")

        if prior_period not in lookup:
            continue

        prior = lookup[prior_period]
        if prior == 0:
            continue

        rows.append(
            {
                "date": date,
                "value": (
                    float(row["value"]) / prior - 1
                ) * 100,
            }
        )

    return pd.DataFrame(rows)


def mom_history(df):
    clean = _clean_series(df)
    if len(clean) < 2:
        return empty_df()

    out = clean.copy()
    out["value"] = (
        out["value"]
        .astype(float)
        .pct_change()
        * 100
    )

    return out.dropna().reset_index(drop=True)


def latest_pair(df):
    clean = _clean_series(df)
    if len(clean) < 2:
        return None, None, None

    return (
        pd.Timestamp(clean.iloc[-1]["date"]),
        float(clean.iloc[-1]["value"]),
        float(clean.iloc[-2]["value"]),
    )


def momentum_symbol(current, previous, tolerance=0.02):
    if (
        current is None
        or previous is None
        or pd.isna(current)
        or pd.isna(previous)
    ):
        return "→", "확인 불가", 0

    diff = float(current) - float(previous)

    if abs(diff) <= tolerance:
        return "→", "보합", 0

    if diff > 0:
        return "↑", "상승", 1

    return "↓", "하락", -1


def build_macro_momentum_rows(
    cpi_nsa=None,
    cpi_sa=None,
    core_nsa=None,
    core_sa=None,
    unemployment=None,
    payroll=None,
    industrial_original=None,
    industrial_sa=None,
    country="US",
):
    rows = []

    # Headline CPI YoY / MoM
    if cpi_nsa is not None and not cpi_nsa.empty:
        cpi_yoy_hist = yoy_history(cpi_nsa)
        _, curr, prev = latest_pair(cpi_yoy_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append(
                {
                    "지표": "CPI YoY",
                    "현재": f"{curr:.1f}%",
                    "이전": "-" if prev is None else f"{prev:.1f}%",
                    "Momentum": f"{arrow} {label}",
                    "_score": score,
                    "_kind": "inflation",
                }
            )

    if cpi_sa is not None and not cpi_sa.empty:
        cpi_mom_hist = mom_history(cpi_sa)
        _, curr, prev = latest_pair(cpi_mom_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append(
                {
                    "지표": "CPI MoM",
                    "현재": f"{curr:.1f}%",
                    "이전": "-" if prev is None else f"{prev:.1f}%",
                    "Momentum": f"{arrow} {label}",
                    "_score": score,
                    "_kind": "inflation_short",
                }
            )

    # Core CPI: U.S. only in current data set
    if core_nsa is not None and not core_nsa.empty:
        core_yoy_hist = yoy_history(core_nsa)
        _, curr, prev = latest_pair(core_yoy_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append(
                {
                    "지표": "Core CPI YoY",
                    "현재": f"{curr:.1f}%",
                    "이전": "-" if prev is None else f"{prev:.1f}%",
                    "Momentum": f"{arrow} {label}",
                    "_score": score,
                    "_kind": "inflation",
                }
            )

    if core_sa is not None and not core_sa.empty:
        core_mom_hist = mom_history(core_sa)
        _, curr, prev = latest_pair(core_mom_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.03)
        if curr is not None:
            rows.append(
                {
                    "지표": "Core CPI MoM",
                    "현재": f"{curr:.1f}%",
                    "이전": "-" if prev is None else f"{prev:.1f}%",
                    "Momentum": f"{arrow} {label}",
                    "_score": score,
                    "_kind": "inflation_short",
                }
            )

    # U.S. labor: monthly payroll gain acceleration
    if payroll is not None and not payroll.empty:
        payroll_clean = _clean_series(payroll)
        gains = payroll_clean.copy()
        gains["value"] = (
            gains["value"]
            .astype(float)
            .diff()
        )
        gains = gains.dropna()
        _, curr, prev = latest_pair(gains)

        if curr is not None:
            if prev is None:
                arrow, label, score = "→", "확인 불가", 0
            else:
                arrow, _, score = momentum_symbol(curr, prev, 5.0)
                label = (
                    "증가폭 확대"
                    if score > 0
                    else "증가폭 축소"
                    if score < 0
                    else "증가폭 유사"
                )

            rows.append(
                {
                    "지표": "NFP 증감",
                    "현재": f"{curr:+.0f}K",
                    "이전": "-" if prev is None else f"{prev:+.0f}K",
                    "Momentum": f"{arrow} {label}",
                    "_score": score,
                    "_kind": "growth",
                }
            )

    if unemployment is not None and not unemployment.empty:
        _, curr, prev = latest_pair(unemployment)
        if curr is not None:
            arrow, label, raw_score = momentum_symbol(
                curr,
                prev,
                0.02,
            )

            # 실업률 하락은 성장 모멘텀에 +, 상승은 -
            growth_score = -raw_score

            rows.append(
                {
                    "지표": "실업률",
                    "현재": f"{curr:.1f}%",
                    "이전": "-" if prev is None else f"{prev:.1f}%",
                    "Momentum": f"{arrow} {label}",
                    "_score": growth_score,
                    "_kind": "growth",
                }
            )

    # Korea industrial production
    if (
        industrial_original is not None
        and not industrial_original.empty
    ):
        ip_yoy_hist = yoy_history(industrial_original)
        _, curr, prev = latest_pair(ip_yoy_hist)
        arrow, label, score = momentum_symbol(curr, prev, 0.10)

        if curr is not None:
            rows.append(
                {
                    "지표": "전산업생산 YoY",
                    "현재": f"{curr:.1f}%",
                    "이전": "-" if prev is None else f"{prev:.1f}%",
                    "Momentum": f"{arrow} {label}",
                    "_score": score,
                    "_kind": "growth",
                }
            )

    if industrial_sa is not None and not industrial_sa.empty:
        ip_mom_hist = mom_history(industrial_sa)
        _, curr, prev = latest_pair(ip_mom_hist)

        if curr is not None:
            # Current MoM sign is useful for short-term growth direction.
            if abs(curr) <= 0.05:
                score = 0
                label = "보합"
                arrow = "→"
            elif curr > 0:
                score = 1
                label = "생산 증가"
                arrow = "↑"
            else:
                score = -1
                label = "생산 감소"
                arrow = "↓"

            rows.append(
                {
                    "지표": "전산업생산 MoM",
                    "현재": f"{curr:.1f}%",
                    "이전": "-" if prev is None else f"{prev:.1f}%",
                    "Momentum": f"{arrow} {label}",
                    "_score": score,
                    "_kind": "growth",
                }
            )

    return rows


def styled_momentum_table(rows):
    if not rows:
        return None

    df = pd.DataFrame(rows)
    visible = ["지표", "현재", "이전", "Momentum"]

    def momentum_style(col):
        styles = []

        for idx in col.index:
            score = df.loc[idx, "_score"]

            if score > 0:
                styles.append(
                    "font-weight: 750; color: #d62728;"
                )
            elif score < 0:
                styles.append(
                    "font-weight: 750; color: #1f77b4;"
                )
            else:
                styles.append(
                    "font-weight: 750; color: #000000;"
                )

        return styles

    return (
        df[visible]
        .style
        .apply(momentum_style, subset=["Momentum"])
    )


def macro_regime_from_rows(rows, country):
    if not rows:
        return {
            "name": "Mixed / 데이터 부족",
            "growth": "Growth →",
            "inflation": "Inflation →",
            "evidence": "판단 가능한 모멘텀 지표가 부족합니다.",
        }

    df = pd.DataFrame(rows)

    growth_scores = df.loc[
        df["_kind"] == "growth",
        "_score",
    ].tolist()
    inflation_scores = df.loc[
        df["_kind"] == "inflation",
        "_score",
    ].tolist()

    growth_sum = sum(growth_scores) if growth_scores else 0
    inflation_sum = (
        sum(inflation_scores)
        if inflation_scores
        else 0
    )

    growth_dir = (
        1 if growth_sum > 0 else -1 if growth_sum < 0 else 0
    )
    inflation_dir = (
        1 if inflation_sum > 0 else -1 if inflation_sum < 0 else 0
    )

    if growth_dir > 0 and inflation_dir > 0:
        name = "Reflation"
    elif growth_dir > 0 and inflation_dir < 0:
        name = "Goldilocks"
    elif growth_dir < 0 and inflation_dir > 0:
        name = "Stagflation Pressure"
    elif growth_dir < 0 and inflation_dir < 0:
        name = "Slowdown / Disinflation"
    else:
        name = "Mixed / Neutral"

    growth_text = (
        "Growth ↑"
        if growth_dir > 0
        else "Growth ↓"
        if growth_dir < 0
        else "Growth →"
    )
    inflation_text = (
        "Inflation ↑"
        if inflation_dir > 0
        else "Inflation ↓"
        if inflation_dir < 0
        else "Inflation →"
    )

    evidence_items = []

    for _, row in df.iterrows():
        if row["_kind"] in ["growth", "inflation"]:
            evidence_items.append(
                f'{row["지표"]} {row["Momentum"]}'
            )

    evidence = " · ".join(evidence_items[:4])

    return {
        "name": name,
        "growth": growth_text,
        "inflation": inflation_text,
        "evidence": evidence or "데이터 부족",
    }


def render_regime_card(country_label, regime):
    st.markdown(
        f"""
        <div class="regime-box">
            <div class="regime-country">{html.escape(country_label)}</div>
            <div class="regime-name">{html.escape(regime["name"])}</div>
            <div class="insight-main">
                {html.escape(regime["growth"])}
                &nbsp;·&nbsp;
                {html.escape(regime["inflation"])}
            </div>
            <div class="regime-evidence">
                {html.escape(regime["evidence"])}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# Data sources
# =========================================================
@st.cache_data(ttl=600)
def get_treasury_curve():
    """
    Official U.S. Treasury nominal curve.

    Fetch the current year plus the previous five calendar years so the
    dashboard can calculate trailing 5Y percentiles.
    """
    current_year = datetime.now(KOREA_TZ).year
    all_rows = []

    for year in range(current_year - 5, current_year + 1):
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/pages/xml"
            f"?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
        )

        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception:
            continue

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

            row = {"date": pd.to_datetime(date, errors="coerce")}

            for key in [
                "BC_2YEAR",
                "BC_5YEAR",
                "BC_10YEAR",
                "BC_30YEAR",
            ]:
                row[key] = pd.to_numeric(
                    record.get(key),
                    errors="coerce",
                )

            all_rows.append(row)

    df = pd.DataFrame(all_rows)
    if df.empty:
        return {}

    df = (
        df
        .dropna(subset=["date"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
    )

    mapping = [
        ("BC_2YEAR", "미국 2Y"),
        ("BC_5YEAR", "미국 5Y"),
        ("BC_10YEAR", "미국 10Y"),
        ("BC_30YEAR", "미국 30Y"),
    ]

    out = {}

    for col, name in mapping:
        if col in df:
            out[name] = (
                df[["date", col]]
                .rename(columns={col: "value"})
                .dropna()
                .reset_index(drop=True)
            )

    return out


@st.cache_data(ttl=600)
def get_treasury_real_curve():
    """
    Official U.S. Treasury real yield curve.

    Fetch the current year plus the previous five calendar years so real
    yields and breakevens can also have trailing 5Y percentiles.
    """
    current_year = datetime.now(KOREA_TZ).year
    all_rows = []

    for year in range(current_year - 5, current_year + 1):
        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/pages/xml"
            f"?data=daily_treasury_real_yield_curve&field_tdr_date_value={year}"
        )

        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception:
            continue

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

            all_rows.append(
                {
                    "date": pd.to_datetime(
                        date,
                        errors="coerce",
                    ),
                    "real5": pd.to_numeric(
                        record.get("TC_5YEAR"),
                        errors="coerce",
                    ),
                    "real10": pd.to_numeric(
                        record.get("TC_10YEAR"),
                        errors="coerce",
                    ),
                }
            )

    df = pd.DataFrame(all_rows)

    if df.empty:
        return {}

    df = (
        df
        .dropna(subset=["date"])
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
    )

    return {
        "미국 실질 5Y": (
            df[["date", "real5"]]
            .rename(columns={"real5": "value"})
            .dropna()
            .reset_index(drop=True)
        ),
        "미국 실질 10Y": (
            df[["date", "real10"]]
            .rename(columns={"real10": "value"})
            .dropna()
            .reset_index(drop=True)
        ),
    }


@st.cache_data(ttl=1800)
def get_bls(series_ids):
    """
    U.S. BLS Public Data API.

    Robustness strategy:
      1) If a BLS registration key exists, try it first.
      2) If the key is expired/invalid or the registered request fails,
         retry anonymously.
      3) Retry transient network/API failures once.

    The dashboard only needs recent macro history for YoY/MoM and momentum,
    so three calendar years are sufficient and stay comfortably within
    the BLS unregistered request limits.
    """
    end_year = datetime.now(KOREA_TZ).year
    start_year = end_year - 2

    endpoint = (
        "https://api.bls.gov/publicAPI/v2/"
        "timeseries/data/"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "BondMacroDashboard/1.0 "
            "(Streamlit; BLS Public Data API)"
        ),
    }

    base_payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
    }

    def parse_response(data):
        status = data.get("status", "")
        messages = data.get("message", []) or []

        results_block = data.get("Results", {})

        # BLS examples/documentation normally return Results as an object,
        # but handle a one-element list defensively.
        if isinstance(results_block, list):
            if results_block:
                results_block = results_block[0]
            else:
                results_block = {}

        series_list = (
            results_block.get("series", [])
            if isinstance(results_block, dict)
            else []
        )

        if status != "REQUEST_SUCCEEDED":
            detail = " | ".join(
                str(m) for m in messages if m
            )
            raise RuntimeError(
                f"BLS status={status or 'UNKNOWN'}"
                + (f": {detail}" if detail else "")
            )

        if not series_list:
            detail = " | ".join(
                str(m) for m in messages if m
            )
            raise RuntimeError(
                "BLS returned no series"
                + (f": {detail}" if detail else "")
            )

        result = {}

        for series in series_list:
            rows = []

            for x in series.get("data", []):
                period = x.get("period", "")

                # M13 is annual average, not a calendar month.
                if period not in {
                    f"M{i:02d}"
                    for i in range(1, 13)
                }:
                    continue

                date = pd.to_datetime(
                    f"{x.get('year')}-{period[1:]}-01",
                    errors="coerce",
                )
                value = pd.to_numeric(
                    x.get("value"),
                    errors="coerce",
                )

                rows.append(
                    {
                        "date": date,
                        "value": value,
                    }
                )

            series_id = series.get("seriesID")

            if series_id:
                result[series_id] = (
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
            isinstance(v, pd.DataFrame) and not v.empty
            for v in result.values()
        ):
            raise RuntimeError(
                "BLS response contained no usable monthly observations"
            )

        return result

    def request_bls(payload):
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

                data = response.json()
                return parse_response(data)

            except Exception as exc:
                last_error = exc

                if attempt == 0:
                    time.sleep(1.0)

        raise last_error

    errors = []

    # 1) Registered request
    if BLS_API_KEY:
        registered_payload = dict(base_payload)
        registered_payload["registrationkey"] = BLS_API_KEY

        try:
            result = request_bls(registered_payload)
            result["__meta__"] = {
                "mode": "registered",
                "message": "BLS registered API",
            }
            return result

        except Exception as exc:
            errors.append(
                f"registered request failed: {exc}"
            )

    # 2) Anonymous fallback.
    # This request is intentionally free of registrationkey so an expired
    # or invalid secret cannot break the dashboard.
    try:
        result = request_bls(base_payload)

        fallback_used = bool(BLS_API_KEY)

        result["__meta__"] = {
            "mode": (
                "anonymous_fallback"
                if fallback_used
                else "anonymous"
            ),
            "message": (
                "BLS registration key failed; anonymous fallback succeeded."
                if fallback_used
                else "BLS anonymous API"
            ),
            "previous_errors": errors,
        }
        return result

    except Exception as exc:
        errors.append(
            f"anonymous request failed: {exc}"
        )

    return {
        "__meta__": {
            "mode": "error",
            "message": " / ".join(errors),
        }
    }


@st.cache_data(ttl=300)
def get_wti():
    """
    NYMEX WTI Crude Oil Futures (CL=F), daily close.
    """
    url = "https://query1.finance.yahoo.com/v8/finance/chart/CL%3DF"
    params = {
        "range": "1mo",
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits",
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=20,
        )
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

            dt_et = (
                datetime.fromtimestamp(ts, tz=UTC_TZ)
                .astimezone(NEW_YORK_TZ)
            )

            rows.append(
                {
                    "date": pd.Timestamp(dt_et.date()),
                    "value": pd.to_numeric(
                        close,
                        errors="coerce",
                    ),
                }
            )

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


def strip_html(raw_html):
    text = re.sub(
        r"<script.*?</script>",
        " ",
        raw_html,
        flags=re.I | re.S,
    )
    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_fraction_rate(token):
    token = (
        token.strip()
        .replace("‑", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
    )

    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)

    mixed = re.fullmatch(
        r"(\d+)-(\d+)/(\d+)",
        token,
    )
    if mixed:
        whole, num, den = map(int, mixed.groups())
        return whole + num / den

    fraction = re.fullmatch(r"(\d+)/(\d+)", token)
    if fraction:
        num, den = map(int, fraction.groups())
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

    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    for sentence in sentences:
        if (
            "target range for the federal funds rate"
            not in sentence.lower()
        ):
            continue

        matches = re.findall(
            rf"({rate_token})\s+to\s+({rate_token})\s+percent",
            sentence,
            flags=re.I,
        )

        if matches:
            low_token, high_token = matches[-1]
            low = parse_fraction_rate(low_token)
            high = parse_fraction_rate(high_token)

            if low is not None and high is not None:
                return low, high

    matches = re.findall(
        rf"target range for the federal funds rate.*?"
        rf"({rate_token})\s+to\s+({rate_token})\s+percent",
        normalized,
        flags=re.I | re.S,
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
    Read the two latest official FOMC statements.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    today_et = datetime.now(NEW_YORK_TZ).date()
    statement_links = []

    for year in [today_et.year, today_et.year - 1]:
        list_url = (
            "https://www.federalreserve.gov/"
            f"newsevents/pressreleases/{year}-press-fomc.htm"
        )

        try:
            r = requests.get(
                list_url,
                headers=headers,
                timeout=20,
            )
            r.raise_for_status()
        except Exception:
            continue

        anchor_pattern = re.compile(
            r"<a[^>]+href=[\"']([^\"']*monetary(\d{8})a\.htm)"
            r"[\"'][^>]*>(.*?)</a>",
            flags=re.I | re.S,
        )

        for href, yyyymmdd, anchor_text in anchor_pattern.findall(r.text):
            title = strip_html(anchor_text).lower()

            if "fomc statement" not in title:
                continue

            release_date = datetime.strptime(
                yyyymmdd,
                "%Y%m%d",
            ).date()

            if release_date > today_et:
                continue

            if href.startswith("http"):
                statement_url = href
            else:
                statement_url = (
                    "https://www.federalreserve.gov"
                    + (
                        href
                        if href.startswith("/")
                        else "/" + href
                    )
                )

            statement_links.append(
                (release_date, statement_url)
            )

    statement_links = sorted(
        set(statement_links),
        key=lambda x: x[0],
        reverse=True,
    )

    parsed = []

    for release_date, statement_url in statement_links[:4]:
        try:
            r = requests.get(
                statement_url,
                headers=headers,
                timeout=20,
            )
            r.raise_for_status()

            target = extract_fed_target_range(r.text)
            if target:
                parsed.append(
                    {
                        "date": pd.Timestamp(release_date),
                        "low": target[0],
                        "high": target[1],
                        "url": statement_url,
                    }
                )

        except Exception:
            continue

        if len(parsed) >= 2:
            break

    if not parsed:
        return None

    current = parsed[0]
    current_mid = (
        current["low"] + current["high"]
    ) / 2

    change_bp = None
    if len(parsed) >= 2:
        previous_mid = (
            parsed[1]["low"] + parsed[1]["high"]
        ) / 2
        change_bp = (
            current_mid - previous_mid
        ) * 100

    current["change_bp"] = change_bp
    return current


@st.cache_data(ttl=600)
def get_bok_policy_rate():
    """
    Read the latest Bank of Korea policy decision date
    and base rate from the BOK homepage.
    """
    url = "https://www.bok.or.kr/portal/main/main.do"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        r = requests.get(
            url,
            headers=headers,
            timeout=20,
        )
        r.raise_for_status()
        r.encoding = "utf-8"

        page_text = strip_html(r.text)

        date_match = re.search(
            r"통화정책방향\s*\(\s*"
            r"(\d{4})[.]\s*(\d{1,2})[.]\s*(\d{1,2})\s*\)",
            page_text,
        )

        if not date_match:
            return None

        y, m, d = map(int, date_match.groups())
        release_date = pd.Timestamp(
            year=y,
            month=m,
            day=d,
        )

        rate_match = re.search(
            r"한국은행\s*기준금리\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*%",
            page_text,
        )

        if not rate_match:
            rate_match = re.search(
                r"한국은행기준금리\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%",
                page_text,
            )

        if not rate_match:
            return None

        current_rate = float(rate_match.group(1))
        change_bp = None

        changed_match = re.search(
            r"기준금리를\s*(?:현재의\s*)?"
            r"([0-9]+(?:\.[0-9]+)?)\s*%\s*"
            r"(?:수준에서|에서)\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*%\s*로",
            page_text,
        )

        if changed_match:
            old_rate = float(changed_match.group(1))
            new_rate = float(changed_match.group(2))

            if abs(new_rate - current_rate) < 1e-9:
                change_bp = (
                    new_rate - old_rate
                ) * 100

        if change_bp is None:
            hold_patterns = [
                (
                    r"기준금리를\s*(?:현재의\s*)?"
                    r"([0-9]+(?:\.[0-9]+)?)\s*%"
                    r"\s*수준에서\s*유지"
                ),
                (
                    r"기준금리를\s*(?:현재의\s*)?"
                    r"([0-9]+(?:\.[0-9]+)?)\s*%"
                    r"\s*로\s*유지"
                ),
            ]

            for pattern in hold_patterns:
                hold_match = re.search(
                    pattern,
                    page_text,
                )

                if hold_match:
                    held_rate = float(
                        hold_match.group(1)
                    )

                    if abs(
                        held_rate - current_rate
                    ) < 1e-9:
                        change_bp = 0.0
                        break

        return {
            "date": release_date,
            "rate": current_rate,
            "change_bp": change_bp,
            "url": url,
        }

    except Exception:
        return None


@st.cache_data(ttl=1800)
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

    if cycle == "D":
        start = "20200101"
        end = now.strftime("%Y%m%d")
    else:
        start = "202001"
        end = now.strftime("%Y%m")

    url = (
        "https://ecos.bok.or.kr/api/StatisticSearch/"
        f"{ECOS_API_KEY}/json/kr/1/10000/"
        f"{stat}/{cycle}/{start}/{end}"
    )

    if item:
        if isinstance(item, (list, tuple)):
            codes = [
                str(code).strip()
                for code in item
                if code is not None
                and str(code).strip()
                and str(code).lower() != "nan"
            ]
            if codes:
                url += "/" + "/".join(codes)
        else:
            url += f"/{item}"

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()

        rows = (
            r.json()
            .get("StatisticSearch", {})
            .get("row", [])
        )

        if not rows:
            return empty_df()

        df = pd.DataFrame(rows)
        fmt = "%Y%m%d" if cycle == "D" else "%Y%m"

        df["date"] = pd.to_datetime(
            df["TIME"],
            format=fmt,
            errors="coerce",
        )
        df["value"] = pd.to_numeric(
            df["DATA_VALUE"],
            errors="coerce",
        )

        # 동일 월에 여러 계열이 섞여 들어오는 것을 방지합니다.
        # 정확한 item path를 사용했다면 월별 한 관측치가 정상입니다.
        out = (
            df[["date", "value"]]
            .dropna()
            .sort_values("date")
            .drop_duplicates(subset=["date"], keep="last")
        )

        return out

    except Exception:
        return empty_df()


ecos_series = {
    "한국 국고채 3Y": {
        "stat": "817Y002",
        "item": "010200000",
        "cycle": "D",
        "freq": "일간",
        "type": "rate",
    },
    "한국 국고채 10Y": {
        "stat": "817Y002",
        "item": "010210000",
        "cycle": "D",
        "freq": "일간",
        "type": "rate",
    },
    "한국 회사채 AA- 3Y": {
        "stat": "817Y002",
        "item": "010310000",
        "cycle": "D",
        "freq": "일간",
        "type": "rate",
    },
    "USD/KRW": {
        "stat": "731Y003",
        "item": "0000003",
        "cycle": "D",
        "freq": "일간",
        "type": "pct",
    },
    "한국 CPI": {
        "stat": "901Y009",
        "item": "0",
        "cycle": "M",
        "freq": "월간",
        "type": "yoy",
    },
    "한국 전산업생산 (원계열)": {
        "stat": "901Y033",
        "item": None,
        "cycle": "M",
        "freq": "월간",
        "type": "yoy",
    },
    "한국 전산업생산 (계절조정)": {
        "stat": "901Y033",
        "item": None,
        "cycle": "M",
        "freq": "월간",
        "type": "mom",
    },
}


@st.cache_data(ttl=1800)
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

    if cycle == "D":
        start = "20200101"
        end = now.strftime("%Y%m%d")
    else:
        start = "202001"
        end = now.strftime("%Y%m")

    url = (
        "https://ecos.bok.or.kr/api/StatisticSearch/"
        f"{ECOS_API_KEY}/json/kr/1/10000/"
        f"{stat}/{cycle}/{start}/{end}"
    )

    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()

        rows = (
            r.json()
            .get("StatisticSearch", {})
            .get("row", [])
        )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
        df["date"] = pd.to_datetime(
            df["TIME"],
            format=fmt,
            errors="coerce",
        )
        df["value"] = pd.to_numeric(
            df["DATA_VALUE"],
            errors="coerce",
        )

        item_name_cols = sorted(
            [
                c for c in df.columns
                if c.startswith("ITEM_NAME")
            ]
        )

        if item_name_cols:
            df["series_name"] = df.apply(
                lambda row: " | ".join(
                    str(row[c]).strip()
                    for c in item_name_cols
                    if c in row.index
                    and pd.notna(row[c])
                    and str(row[c]).strip()
                ),
                axis=1,
            )
        else:
            df["series_name"] = ""

        return (
            df
            .dropna(subset=["date", "value"])
            .sort_values("date")
            .reset_index(drop=True)
        )

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
        return empty_df(), None

    work = raw_df.copy()
    work["series_name"] = (
        work["series_name"]
        .fillna("")
        .astype(str)
    )

    unique_names = [
        name for name in work["series_name"].drop_duplicates().tolist()
        if name
    ]

    def score_name(name):
        score = 0
        compact = name.replace(" ", "")

        # 전체 전산업 지수를 가장 우선
        if "전산업생산지수" in compact:
            score += 100
        elif "전산업" in compact:
            score += 80

        # 구성산업보다 총지수를 우선
        for bad in [
            "광공업", "제조업", "서비스업",
            "건설업", "공공행정", "농림어업",
        ]:
            if bad in compact:
                score -= 40

        if mode == "sa":
            if "계절조정" in compact:
                score += 80
            elif "계절" in compact or "조정" in compact:
                score += 50
            if "원계열" in compact or "원지수" in compact:
                score -= 100

        else:
            if "원계열" in compact or "원지수" in compact:
                score += 80
            if "계절조정" in compact:
                score -= 120
            elif "계절" in compact or "조정" in compact:
                score -= 80

        return score

    # 먼저 명확한 후보만 추림
    if mode == "sa":
        candidates = [
            n for n in unique_names
            if (
                "계절조정" in n.replace(" ", "")
                or "계절" in n.replace(" ", "")
                or "조정" in n.replace(" ", "")
            )
        ]
    else:
        # 원계열은 원계열/원지수 표시가 있으면 그것을 우선.
        explicit = [
            n for n in unique_names
            if (
                "원계열" in n.replace(" ", "")
                or "원지수" in n.replace(" ", "")
            )
        ]
        if explicit:
            candidates = explicit
        else:
            # 별도 '원계열' 표기가 없는 ECOS 응답도 있으므로
            # 계절조정 관련 이름만 제외하고 전산업 계열을 찾음.
            candidates = [
                n for n in unique_names
                if not (
                    "계절조정" in n.replace(" ", "")
                    or "계절" in n.replace(" ", "")
                    or "조정" in n.replace(" ", "")
                )
            ]

    if not candidates:
        return empty_df(), None

    chosen = max(candidates, key=score_name)

    selected = (
        work[work["series_name"] == chosen][["date", "value"]]
        .dropna()
        .drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )

    return selected, chosen


# =========================================================
# Load data with visible progress
# =========================================================
loading_title = (
    "최신 데이터를 다시 수집하는 중입니다..."
    if refresh_clicked
    else "데이터를 불러오는 중입니다..."
)

loading_status = st.status(
    f"📡 {loading_title}",
    expanded=True,
    state="running",
)
loading_message = st.empty()
loading_progress = st.progress(0)

# US 5 + BOK policy 1 + 일반 ECOS series + 전산업생산 raw table 1
total_steps = 5 + 1 + (len(ecos_series) - 2) + 1
current_step = 0


def show_loading(message):
    global current_step
    current_step += 1
    pct = int(
        current_step / total_steps * 100
    )

    loading_message.info(
        f"⏳ {message}"
    )
    loading_progress.progress(
        pct,
        text=(
            f"{current_step}/{total_steps} · "
            f"{message}"
        ),
    )
    loading_status.write(
        f"⏳ {message}"
    )


show_loading(
    "U.S. Treasury · 미국 명목 국채금리(2Y·5Y·10Y·30Y) 로딩 중"
)
treasury = get_treasury_curve()

show_loading(
    "U.S. Treasury · 미국 실질금리(5Y·10Y) 로딩 중"
)
treasury_real = get_treasury_real_curve()

show_loading(
    "U.S. BLS · CPI·Core CPI·실업률·NFP 로딩 중"
)
bls = get_bls(
    [
        # CPI / Core CPI
        # CUUR = not seasonally adjusted -> YoY
        # CUSR = seasonally adjusted     -> MoM
        "CUUR0000SA0",
        "CUSR0000SA0",
        "CUUR0000SA0L1E",
        "CUSR0000SA0L1E",

        # Labor market
        "LNS14000000",
        "CES0000000001",
    ]
)

bls_meta = bls.get("__meta__", {})
bls_mode = bls_meta.get("mode", "")

if bls_mode == "anonymous_fallback":
    loading_status.write(
        "⚠️ BLS 등록키 요청은 실패했지만 "
        "비등록 API fallback으로 미국 거시지표를 정상 수집했습니다."
    )
elif bls_mode == "anonymous":
    loading_status.write(
        "✅ BLS 비등록 API로 미국 거시지표를 수집했습니다."
    )
elif bls_mode == "registered":
    loading_status.write(
        "✅ BLS 등록 API로 미국 거시지표를 수집했습니다."
    )
elif bls_mode == "error":
    loading_status.write(
        "❌ BLS 수집 실패: "
        f"{bls_meta.get('message', '원인 확인 불가')}"
    )

show_loading(
    "시장데이터 · WTI 선물가격(CL=F) 로딩 중"
)
wti = get_wti()

show_loading(
    "Federal Reserve · FOMC 기준금리 발표일·Target Range 로딩 중"
)
fed_policy = get_fed_policy_rate()

show_loading(
    "한국은행 · 최신 기준금리 발표일·기준금리 로딩 중"
)
bok_policy = get_bok_policy_rate()

ecos = {}

# 전산업생산을 제외한 일반 ECOS 지표
for name, info in ecos_series.items():
    if name.startswith("한국 전산업생산"):
        continue

    show_loading(
        f"한국은행 ECOS · {name} 로딩 중"
    )
    ecos[name] = get_ecos(
        info["stat"],
        info["item"],
        info["cycle"],
    )

# 전산업생산은 901Y033 전체 응답의 실제 ITEM_NAME을 보고 분리
show_loading(
    "한국은행 ECOS · 전산업생산 원계열·계절조정계열 분리 중"
)
industrial_raw = get_ecos_raw_table(
    "901Y033",
    "M",
)

industrial_original, industrial_original_name = (
    select_industrial_series(
        industrial_raw,
        "original",
    )
)

industrial_sa, industrial_sa_name = (
    select_industrial_series(
        industrial_raw,
        "sa",
    )
)

ecos["한국 전산업생산 (원계열)"] = industrial_original
ecos["한국 전산업생산 (계절조정)"] = industrial_sa

if industrial_original.empty:
    loading_status.write(
        "⚠️ 전산업생산 원계열을 ECOS 응답에서 찾지 못했습니다."
    )
else:
    loading_status.write(
        "✅ 전산업생산 원계열 선택: "
        f"{industrial_original_name}"
    )

if industrial_sa.empty:
    loading_status.write(
        "⚠️ 전산업생산 계절조정계열을 ECOS 응답에서 찾지 못했습니다."
    )
else:
    loading_status.write(
        "✅ 전산업생산 계절조정계열 선택: "
        f"{industrial_sa_name}"
    )

loading_message.success(
    "✅ 모든 데이터를 불러왔습니다."
)
loading_progress.progress(
    100,
    text="데이터 로딩 완료",
)
loading_status.update(
    label="✅ 미국·한국 채권 및 거시경제 데이터 로딩 완료",
    state="complete",
    expanded=False,
)

if refresh_clicked:
    st.session_state["last_updated_krt"] = (
        datetime.now(KOREA_TZ)
        .strftime("%H:%M:%S")
    )

if st.session_state["last_updated_krt"]:
    st.markdown(
        f"""
        <div class="section-note" style="margin-top:8px;">
            <strong>Updated</strong> &nbsp;
            KST {html.escape(st.session_state["last_updated_krt"])}
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# Build result table
# =========================================================
rows = []

# Derived market series are kept separately so 1D/1W/1M changes and
# historical percentiles can be calculated from their full histories.
us_curve_series = empty_df()
be5_series = empty_df()
be10_series = empty_df()
kr_curve_series = empty_df()

for name in [
    "미국 2Y",
    "미국 5Y",
    "미국 10Y",
    "미국 30Y",
]:
    add_row(
        rows,
        "미국",
        name,
        treasury.get(name, empty_df()),
        "일간",
        "rate",
    )

# U.S. 10Y-2Y
if (
    not treasury.get("미국 2Y", empty_df()).empty
    and not treasury.get("미국 10Y", empty_df()).empty
):
    us_curve = pd.merge(
        treasury["미국 2Y"],
        treasury["미국 10Y"],
        on="date",
        suffixes=("_2Y", "_10Y"),
    )
    us_curve["value"] = (
        us_curve["value_10Y"]
        - us_curve["value_2Y"]
    )
    us_curve_series = (
        us_curve[["date", "value"]]
        .dropna()
        .sort_values("date")
        .reset_index(drop=True)
    )

    add_row(
        rows,
        "미국",
        "미국 10Y-2Y",
        us_curve_series,
        "일간",
        "rate",
    )

# Breakevens
if (
    not treasury.get("미국 5Y", empty_df()).empty
    and not treasury_real.get(
        "미국 실질 5Y",
        empty_df(),
    ).empty
):
    be5 = pd.merge(
        treasury["미국 5Y"],
        treasury_real["미국 실질 5Y"],
        on="date",
        suffixes=("_nominal", "_real"),
    )
    be5["value"] = (
        be5["value_nominal"]
        - be5["value_real"]
    )
    be5_series = (
        be5[["date", "value"]]
        .dropna()
        .sort_values("date")
        .reset_index(drop=True)
    )

    add_row(
        rows,
        "미국",
        "미국 5Y 기대인플레이션",
        be5_series,
        "일간",
        "rate",
    )

if (
    not treasury.get("미국 10Y", empty_df()).empty
    and not treasury_real.get(
        "미국 실질 10Y",
        empty_df(),
    ).empty
):
    be10 = pd.merge(
        treasury["미국 10Y"],
        treasury_real["미국 실질 10Y"],
        on="date",
        suffixes=("_nominal", "_real"),
    )
    be10["value"] = (
        be10["value_nominal"]
        - be10["value_real"]
    )
    be10_series = (
        be10[["date", "value"]]
        .dropna()
        .sort_values("date")
        .reset_index(drop=True)
    )

    add_row(
        rows,
        "미국",
        "미국 10Y 기대인플레이션",
        be10_series,
        "일간",
        "rate",
    )

add_row(
    rows,
    "미국",
    "미국 실질 10Y",
    treasury_real.get(
        "미국 실질 10Y",
        empty_df(),
    ),
    "일간",
    "rate",
)

add_row(
    rows,
    "미국",
    "WTI 선물 (CL=F)",
    wti,
    "일간",
    "pct",
)

# Fed policy rate: use bp change from latest two FOMC decisions
if fed_policy:
    fed_mid = (
        fed_policy["low"]
        + fed_policy["high"]
    ) / 2

    rows.append(
        {
            "구분": "미국",
            "지표": (
                "연준 기준금리 "
                "(Fed Funds Target Range)"
            ),
            "기준일": fed_policy["date"],
            "현재": fed_mid,
            "현재표시": (
                f'{fed_policy["low"]:.2f}'
                f'–{fed_policy["high"]:.2f}%'
            ),
            "변화값": fed_policy["change_bp"],
            "빈도": "정책금리",
            "type": "policy",
        }
    )

# BLS monthly macro
# CPI/Core CPI의 기본 행은 YoY 계산에 맞는 NSA series 사용
for sid, name, value_type in [
    (
        "CUUR0000SA0",
        "미국 CPI",
        "yoy",
    ),
    (
        "CUUR0000SA0L1E",
        "미국 Core CPI",
        "yoy",
    ),
    (
        "LNS14000000",
        "미국 실업률",
        "pp",
    ),
    (
        "CES0000000001",
        "미국 NFP",
        "nfp",
    ),
]:
    add_row(
        rows,
        "미국",
        name,
        bls.get(sid, empty_df()),
        "월간",
        value_type,
    )

# BOK policy rate
if bok_policy:
    rows.append(
        {
            "구분": "한국",
            "지표": "한국은행 기준금리",
            "기준일": bok_policy["date"],
            "현재": bok_policy["rate"],
            "현재표시": (
                f'{bok_policy["rate"]:.2f}%'
            ),
            "변화값": bok_policy["change_bp"],
            "빈도": "정책금리",
            "type": "policy",
        }
    )

# Korea market / macro
for name, info in ecos_series.items():
    add_row(
        rows,
        "한국",
        name,
        ecos.get(name, empty_df()),
        info["freq"],
        info["type"],
    )

# Korea 10Y-3Y
if (
    not ecos.get(
        "한국 국고채 3Y",
        empty_df(),
    ).empty
    and not ecos.get(
        "한국 국고채 10Y",
        empty_df(),
    ).empty
):
    kr_curve = pd.merge(
        ecos["한국 국고채 3Y"],
        ecos["한국 국고채 10Y"],
        on="date",
        suffixes=("_3Y", "_10Y"),
    )
    kr_curve["value"] = (
        kr_curve["value_10Y"]
        - kr_curve["value_3Y"]
    )

    kr_curve_series = (
        kr_curve[["date", "value"]]
        .dropna()
        .sort_values("date")
        .reset_index(drop=True)
    )

    add_row(
        rows,
        "한국",
        "한국 10Y-3Y",
        kr_curve_series,
        "일간",
        "rate",
    )


# =========================================================
# Dashboard layout
# =========================================================
result = pd.DataFrame(rows)

us_rate_series = {
    "미국 2Y": treasury.get("미국 2Y", empty_df()),
    "미국 5Y": treasury.get("미국 5Y", empty_df()),
    "미국 10Y": treasury.get("미국 10Y", empty_df()),
    "미국 30Y": treasury.get("미국 30Y", empty_df()),
    "미국 10Y-2Y": us_curve_series,
    "미국 실질 10Y": treasury_real.get(
        "미국 실질 10Y",
        empty_df(),
    ),
    "미국 5Y 기대인플레이션": be5_series,
    "미국 10Y 기대인플레이션": be10_series,
}

kr_rate_series = {
    "한국 국고채 3Y": ecos.get(
        "한국 국고채 3Y",
        empty_df(),
    ),
    "한국 국고채 10Y": ecos.get(
        "한국 국고채 10Y",
        empty_df(),
    ),
    "한국 10Y-3Y": kr_curve_series,
    "한국 회사채 AA- 3Y": ecos.get(
        "한국 회사채 AA- 3Y",
        empty_df(),
    ),
}

if result.empty:
    st.error(
        "데이터를 불러오지 못했습니다. "
        "Streamlit Secrets의 ECOS_API_KEY와 "
        "각 데이터 제공기관 상태를 확인해주세요."
    )
    st.stop()

result["기준일"] = pd.to_datetime(
    result["기준일"],
    errors="coerce",
)

st.markdown(
    '<div class="section-note">'
    '<strong>Data convention</strong> &nbsp; '
    '일간 지표는 실제 관측일 · 월간 지표는 기준월 · '
    '정책금리는 실제 정책결정 발표일'
    '</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------
# 상단: Today Market Snapshot
# ---------------------------------------------------------
render_section_header(
    "01",
    "Market Snapshot",
    "정책금리와 핵심 시장가격의 최신 레벨과 직전 변화를 한눈에 확인합니다.",
)

fed_row = get_row(
    result,
    "연준 기준금리 (Fed Funds Target Range)",
)
bok_row = get_row(
    result,
    "한국은행 기준금리",
)
us10_row = get_row(result, "미국 10Y")
kr10_row = get_row(result, "한국 국고채 10Y")
fx_row = get_row(result, "USD/KRW")
wti_row = get_row(result, "WTI 선물 (CL=F)")

snapshot_cols = st.columns(6)

snapshot_data = [
    (
        "Fed Funds",
        display_value(fed_row),
        None if fed_row is None else fed_row["변화값"],
        "policy",
        "-"
        if fed_row is None
        else format_basis_date(
            fed_row["기준일"],
            fed_row["빈도"],
        ),
    ),
    (
        "BOK Base Rate",
        display_value(bok_row),
        None if bok_row is None else bok_row["변화값"],
        "policy",
        "-"
        if bok_row is None
        else format_basis_date(
            bok_row["기준일"],
            bok_row["빈도"],
        ),
    ),
    (
        "U.S. 10Y",
        display_value(
            us10_row,
            decimals=2,
            suffix="%",
        ),
        None if us10_row is None else us10_row["변화값"],
        "rate",
        "-"
        if us10_row is None
        else format_basis_date(
            us10_row["기준일"],
            us10_row["빈도"],
        ),
    ),
    (
        "Korea 10Y",
        display_value(
            kr10_row,
            decimals=2,
            suffix="%",
        ),
        None if kr10_row is None else kr10_row["변화값"],
        "rate",
        "-"
        if kr10_row is None
        else format_basis_date(
            kr10_row["기준일"],
            kr10_row["빈도"],
        ),
    ),
    (
        "USD/KRW",
        display_value(
            fx_row,
            decimals=2,
        ),
        None if fx_row is None else fx_row["변화값"],
        "pct",
        "-"
        if fx_row is None
        else format_basis_date(
            fx_row["기준일"],
            fx_row["빈도"],
        ),
    ),
    (
        "WTI Futures",
        (
            "-"
            if wti_row is None
            else "$"
            + display_value(
                wti_row,
                decimals=2,
            )
        ),
        None if wti_row is None else wti_row["변화값"],
        "pct",
        "-"
        if wti_row is None
        else format_basis_date(
            wti_row["기준일"],
            wti_row["빈도"],
        ),
    ),
]

for col, item in zip(
    snapshot_cols,
    snapshot_data,
):
    with col:
        render_metric_card(*item)

soft_divider()

# ---------------------------------------------------------
# 중단: Rates & Curve
# ---------------------------------------------------------
render_section_header(
    "02",
    "Rates & Curve",
    "금리 레벨, 기간별 변화, 수익률곡선 구조와 실질금리·기대인플레이션 분해를 확인합니다.",
)

us2_row = get_row(result, "미국 2Y")
us10_row = get_row(result, "미국 10Y")
kr3_row = get_row(result, "한국 국고채 3Y")
kr10_row = get_row(result, "한국 국고채 10Y")

curve_col1, curve_col2 = st.columns(2)

with curve_col1:
    render_curve_summary(
        "미국 금리 변화 요약",
        "2Y",
        None if us2_row is None else us2_row["변화값"],
        "10Y",
        None if us10_row is None else us10_row["변화값"],
    )

with curve_col2:
    render_curve_summary(
        "한국 금리 변화 요약",
        "3Y",
        None if kr3_row is None else kr3_row["변화값"],
        "10Y",
        None if kr10_row is None else kr10_row["변화값"],
    )

st.markdown(
    '<div class="section-note">'
    "분류 기준: 장·단기 금리가 모두 상승하면 Bear, "
    "모두 하락하면 Bull. "
    "장기금리 변화가 단기보다 크면 Steepening, "
    "작으면 Flattening으로 표시합니다."
    "</div>",
    unsafe_allow_html=True,
)

st.write("")

rates_left, rates_right = st.columns(2)

with rates_left:
    render_panel_heading("US", "U.S. Rates")

    us_rate_names = [
        "미국 2Y",
        "미국 5Y",
        "미국 10Y",
        "미국 30Y",
        "미국 10Y-2Y",
        "미국 실질 10Y",
        "미국 5Y 기대인플레이션",
        "미국 10Y 기대인플레이션",
    ]

    us_rates = result[
        result["지표"].isin(us_rate_names)
    ].copy()

    if us_rates.empty:
        st.warning("미국 금리 데이터를 불러오지 못했습니다.")
    else:
        order_map = {
            name: idx
            for idx, name in enumerate(us_rate_names)
        }
        us_rates["order"] = (
            us_rates["지표"]
            .map(order_map)
        )
        us_rates = (
            us_rates
            .sort_values("order")
            .drop(columns=["order"])
        )

        us_rates_insight = build_rates_insight_table(
            us_rate_series,
            us_rate_names,
        )

        st.dataframe(
            styled_rates_insight_table(us_rates_insight),
            use_container_width=True,
            hide_index=True,
        )

with rates_right:
    render_panel_heading("KR", "Korea Rates")

    kr_rate_names = [
        "한국 국고채 3Y",
        "한국 국고채 10Y",
        "한국 10Y-3Y",
        "한국 회사채 AA- 3Y",
    ]

    kr_rates = result[
        result["지표"].isin(kr_rate_names)
    ].copy()

    if kr_rates.empty:
        st.warning("한국 금리 데이터를 불러오지 못했습니다.")
    else:
        order_map = {
            name: idx
            for idx, name in enumerate(kr_rate_names)
        }
        kr_rates["order"] = (
            kr_rates["지표"]
            .map(order_map)
        )
        kr_rates = (
            kr_rates
            .sort_values("order")
            .drop(columns=["order"])
        )

        kr_rates_insight = build_rates_insight_table(
            kr_rate_series,
            kr_rate_names,
        )

        st.dataframe(
            styled_rates_insight_table(kr_rates_insight),
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    "1D는 직전 거래일, 1W는 7일 전, 1M은 1개월 전의 가장 가까운 "
    "이전 관측치와 비교합니다. 5Y %ile은 현재 값이 최근 5년 분포에서 "
    "어느 위치에 있는지를 나타냅니다."
)

# U.S. 10Y decomposition
render_subsection_title("U.S. 10Y Yield Decomposition")

decomp10 = build_10y_decomposition(
    treasury.get("미국 10Y", empty_df()),
    treasury_real.get("미국 실질 10Y", empty_df()),
)

if decomp10 is None:
    st.warning("미국 10Y 명목금리·실질금리 공통 관측일 데이터를 충분히 불러오지 못했습니다.")
else:
    decomp_cols = st.columns(3)

    with decomp_cols[0]:
        render_decomposition_card(
            "Nominal 10Y",
            decomp10["nominal_change"],
            decomp10["nominal"],
            "명목 10년물 금리",
        )

    with decomp_cols[1]:
        render_decomposition_card(
            "Real 10Y",
            decomp10["real_change"],
            decomp10["real"],
            decomp10["driver"],
        )

    with decomp_cols[2]:
        render_decomposition_card(
            "10Y Breakeven",
            decomp10["bei_change"],
            decomp10["bei"],
            decomp10["detail"],
        )

    st.caption(
        "동일한 U.S. Treasury 관측일을 맞춰 Nominal 10Y ≈ Real 10Y + "
        "10Y Breakeven으로 분해합니다. 인과관계가 아니라 금리 구성요소의 "
        "당일 변화를 보여주는 회계적 분해입니다."
    )

soft_divider()

# ---------------------------------------------------------
# 하단: Macro
# ---------------------------------------------------------
render_section_header(
    "03",
    "Macro",
    "물가와 고용·생산의 최신 수치를 YoY·MoM 기준으로 비교합니다.",
)

st.caption(
    "YoY와 MoM이 모두 중요한 지표는 두 변화를 동시에 표시합니다. "
    "CPI는 YoY에 비계절조정(NSA), MoM에 계절조정(SA) 계열을 사용하며, "
    "전산업생산은 원계열 YoY와 계절조정계열 MoM을 사용합니다."
)

macro_left, macro_right = st.columns(2)

with macro_left:
    render_panel_heading("US", "U.S. Macro")

    us_macro_rows = []

    # Headline CPI: NSA YoY + SA MoM
    us_cpi = calc_yoy_mom(
        bls.get("CUUR0000SA0", empty_df()),
        bls.get("CUSR0000SA0", empty_df()),
    )
    if us_cpi:
        us_macro_rows.append(
            build_dual_macro_row(
                "미국 CPI",
                us_cpi["date"],
                us_cpi["current"],
                yoy=us_cpi["yoy"],
                mom=us_cpi["mom"],
            )
        )

    # Core CPI: NSA YoY + SA MoM
    us_core_cpi = calc_yoy_mom(
        bls.get("CUUR0000SA0L1E", empty_df()),
        bls.get("CUSR0000SA0L1E", empty_df()),
    )
    if us_core_cpi:
        us_macro_rows.append(
            build_dual_macro_row(
                "미국 Core CPI",
                us_core_cpi["date"],
                us_core_cpi["current"],
                yoy=us_core_cpi["yoy"],
                mom=us_core_cpi["mom"],
            )
        )

    # 실업률: level + 전월 대비 %p
    unemp_date, unemp_value, unemp_change = get_latest_change(
        bls.get("LNS14000000", empty_df()),
        "pp",
    )
    if unemp_date is not None:
        us_macro_rows.append(
            build_dual_macro_row(
                "미국 실업률",
                unemp_date,
                unemp_value,
                change=unemp_change,
                change_type="pp",
            )
        )

    # NFP: payroll level + 직전월 대비 증감
    nfp_date, nfp_value, nfp_change = get_latest_change(
        bls.get("CES0000000001", empty_df()),
        "nfp",
    )
    if nfp_date is not None:
        us_macro_rows.append(
            build_dual_macro_row(
                "미국 NFP",
                nfp_date,
                nfp_value,
                change=nfp_change,
                change_type="nfp",
            )
        )

    if not us_macro_rows:
        st.warning(
            "미국 거시지표 데이터를 불러오지 못했습니다. "
            "상단 데이터 수집 상태에서 BLS 오류 메시지를 확인해주세요."
        )
    else:
        st.dataframe(
            styled_macro_table(us_macro_rows),
            use_container_width=True,
            hide_index=True,
        )

with macro_right:
    render_panel_heading("KR", "Korea Macro")

    kr_macro_rows = []

    # 한국 CPI: 같은 headline index에서 YoY와 MoM을 함께 계산
    kr_cpi = calc_yoy_mom(
        ecos.get("한국 CPI", empty_df()),
        ecos.get("한국 CPI", empty_df()),
    )
    if kr_cpi:
        kr_macro_rows.append(
            build_dual_macro_row(
                "한국 CPI",
                kr_cpi["date"],
                kr_cpi["current"],
                yoy=kr_cpi["yoy"],
                mom=kr_cpi["mom"],
            )
        )

    # 전산업생산: 원계열 YoY + 계절조정계열 MoM
    kr_ip = calc_yoy_mom(
        ecos.get(
            "한국 전산업생산 (원계열)",
            empty_df(),
        ),
        ecos.get(
            "한국 전산업생산 (계절조정)",
            empty_df(),
        ),
    )
    if kr_ip:
        kr_macro_rows.append(
            build_dual_macro_row(
                "한국 전산업생산",
                kr_ip["date"],
                kr_ip["current"],
                yoy=kr_ip["yoy"],
                mom=kr_ip["mom"],
            )
        )

    if not kr_macro_rows:
        st.warning("한국 거시지표 데이터를 불러오지 못했습니다.")
    else:
        st.dataframe(
            styled_macro_table(kr_macro_rows),
            use_container_width=True,
            hide_index=True,
        )

    missing_industrial = []

    if ecos.get(
        "한국 전산업생산 (원계열)",
        empty_df(),
    ).empty:
        missing_industrial.append("원계열")

    if ecos.get(
        "한국 전산업생산 (계절조정)",
        empty_df(),
    ).empty:
        missing_industrial.append("계절조정계열")

    if missing_industrial:
        st.warning(
            "ECOS 전산업생산 "
            + ", ".join(missing_industrial)
            + "을 찾지 못했습니다. "
            "최신 데이터 수집을 한 번 더 눌러보세요."
        )

soft_divider()

# ---------------------------------------------------------
# 4. Macro Momentum
# ---------------------------------------------------------
render_section_header(
    "04",
    "Macro Momentum",
    "현재 수준보다 직전월 대비 가속·둔화 방향에 초점을 맞춥니다.",
)

st.caption(
    "현재 수치 자체뿐 아니라 직전월 대비 물가·성장 모멘텀이 "
    "가속/둔화되는지를 규칙 기반으로 표시합니다."
)

us_momentum_rows = build_macro_momentum_rows(
    cpi_nsa=bls.get("CUUR0000SA0", empty_df()),
    cpi_sa=bls.get("CUSR0000SA0", empty_df()),
    core_nsa=bls.get("CUUR0000SA0L1E", empty_df()),
    core_sa=bls.get("CUSR0000SA0L1E", empty_df()),
    unemployment=bls.get("LNS14000000", empty_df()),
    payroll=bls.get("CES0000000001", empty_df()),
    country="US",
)

kr_momentum_rows = build_macro_momentum_rows(
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

mom_left, mom_right = st.columns(2)

with mom_left:
    render_panel_heading("US", "U.S. Momentum")

    if not us_momentum_rows:
        st.warning(
            "미국 Macro Momentum을 계산할 데이터가 부족합니다. "
            "BLS 데이터 수집 상태를 확인해주세요."
        )
    else:
        st.dataframe(
            styled_momentum_table(us_momentum_rows),
            use_container_width=True,
            hide_index=True,
        )

with mom_right:
    render_panel_heading("KR", "Korea Momentum")

    if not kr_momentum_rows:
        st.warning("한국 Macro Momentum을 계산할 데이터가 부족합니다.")
    else:
        st.dataframe(
            styled_momentum_table(kr_momentum_rows),
            use_container_width=True,
            hide_index=True,
        )

st.caption(
    "색상은 지표 값의 좋고 나쁨이 아니라 방향만 표시합니다. "
    "예를 들어 CPI 상승은 빨간색, 하락은 파란색이며, "
    "실업률의 성장 모멘텀 점수는 상승 시 약화 방향으로 반영됩니다."
)

# ---------------------------------------------------------
# 5. Rule-based Macro Regime
# ---------------------------------------------------------
render_section_header(
    "05",
    "Macro Regime",
    "성장과 물가 모멘텀의 조합을 단순 규칙으로 요약합니다.",
)

st.caption(
    "아래 Regime은 예측모형이 아니라 현재 보유한 지표의 직전월 모멘텀을 "
    "단순 집계한 휴리스틱입니다. 투자판단이나 경기국면의 공식 판정이 아닙니다."
)

us_regime = macro_regime_from_rows(
    us_momentum_rows,
    "US",
)
kr_regime = macro_regime_from_rows(
    kr_momentum_rows,
    "KR",
)

regime_left, regime_right = st.columns(2)

with regime_left:
    render_regime_card(
        "🇺🇸 United States",
        us_regime,
    )

with regime_right:
    render_regime_card(
        "🇰🇷 Korea",
        kr_regime,
    )

st.caption(
    "Regime 규칙: Growth↑·Inflation↑ = Reflation, "
    "Growth↑·Inflation↓ = Goldilocks, "
    "Growth↓·Inflation↑ = Stagflation Pressure, "
    "Growth↓·Inflation↓ = Slowdown / Disinflation. "
    "신호가 엇갈리면 Mixed / Neutral로 표시합니다."
)

soft_divider()

st.markdown(
    """
    <div style="
        text-align:center;
        color:#8a94a6;
        font-size:0.70rem;
        line-height:1.6;
        padding:12px 0 2px 0;
    ">
        DATA SOURCES &nbsp;·&nbsp;
        U.S. Treasury &nbsp;·&nbsp;
        U.S. Bureau of Labor Statistics &nbsp;·&nbsp;
        Federal Reserve Board &nbsp;·&nbsp;
        Bank of Korea &nbsp;·&nbsp;
        ECOS &nbsp;·&nbsp;
        Yahoo Finance
    </div>
    """,
    unsafe_allow_html=True,
)
