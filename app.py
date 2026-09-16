import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import xml.etree.ElementTree as ET

st.set_page_config(page_title="Bond & Macro Dashboard", page_icon="📊", layout="wide")
ECOS_API_KEY = st.secrets.get("ECOS_API_KEY", "")
EIA_API_KEY = st.secrets.get("EIA_API_KEY", "")
BLS_API_KEY = st.secrets.get("BLS_API_KEY", "")

# 미국 국채: 미 재무부 Daily Treasury XML
def get_treasury():
    url = f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value={datetime.now().year}"
    try:
        root = ET.fromstring(requests.get(url, timeout=20).content)
        rows = []
        for entry in root.iter():
            if entry.tag.split("}")[-1] != "entry": continue
            d = {}
            for x in entry.iter():
                key = x.tag.split("}")[-1]
                if key in ["record_date", "2_yr", "10_yr", "30_yr"]: d[key] = x.text
            if d: rows.append(d)
        df = pd.DataFrame(rows).rename(columns={"record_date":"date","2_yr":"2Y","10_yr":"10Y","30_yr":"30Y"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for c in ["2Y","10Y","30Y"]: df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["date"]).sort_values("date")
    except: return pd.DataFrame()

# 미국 고용·물가: BLS API
def get_bls(series_ids):
    payload = {"seriesid":series_ids,"startyear":"2020","endyear":str(datetime.now().year)}
    if BLS_API_KEY: payload["registrationkey"] = BLS_API_KEY
    try:
        data = requests.post("https://api.bls.gov/publicAPI/v2/timeseries/data/", json=payload, timeout=30).json()
        out = {}
        for s in data.get("Results",{}).get("series",[]):
            rows = [{"date":pd.to_datetime(f'{x["year"]}-{x["period"][1:]}-01'),"value":pd.to_numeric(x["value"],errors="coerce")} for x in s["data"] if x["period"].startswith("M") and x["period"]!="M13"]
            out[s["seriesID"]] = pd.DataFrame(rows).dropna().sort_values("date")
        return out
    except: return {}

# EFFR: 뉴욕연은 API
def get_effr():
    try:
        rows = requests.get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/10.json",timeout=20).json().get("refRates",[])
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["effectiveDate"],errors="coerce")
        df["value"] = pd.to_numeric(df["percentRate"],errors="coerce")
        return df[["date","value"]].dropna().sort_values("date")
    except: return pd.DataFrame()

# 미국 기준금리 목표범위
def get_fed_target():
    try:
        rows = requests.get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json",timeout=20).json().get("refRates",[])
        if not rows: return None
        x = rows[-1]
        return float(x["targetRateFrom"]), float(x["targetRateTo"]), x["effectiveDate"]
    except: return None

# WTI: EIA API
def get_wti():
    if not EIA_API_KEY: return pd.DataFrame()
    url = f"https://api.eia.gov/v2/petroleum/pri/spt/data/?api_key={EIA_API_KEY}&frequency=daily&data[0]=value&facets[series][]=RWTC&start=2020-01-01&sort[0][column]=period&sort[0][direction]=asc&length=5000"
    try:
        df = pd.DataFrame(requests.get(url,timeout=30).json().get("response",{}).get("data",[]))
        df["date"] = pd.to_datetime(df["period"],errors="coerce")
        df["value"] = pd.to_numeric(df["value"],errors="coerce")
        return df[["date","value"]].dropna().sort_values("date")
    except: return pd.DataFrame()

# 한국은행 ECOS
def get_ecos(stat,item,cycle):
    if not ECOS_API_KEY or not item: return pd.DataFrame()
    start,end = ("20200101",datetime.now().strftime("%Y%m%d")) if cycle=="D" else ("202001",datetime.now().strftime("%Y%m"))
    fmt = "%Y%m%d" if cycle=="D" else "%Y%m"
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}/json/kr/1/10000/{stat}/{cycle}/{start}/{end}/{item}"
    try:
        rows = requests.get(url,timeout=30).json().get("StatisticSearch",{}).get("row",[])
        df = pd.DataFrame(rows)
        if df.empty: return pd.DataFrame()
        df["date"] = pd.to_datetime(df["TIME"],format=fmt,errors="coerce")
        df["value"] = pd.to_numeric(df["DATA_VALUE"],errors="coerce")
        return df[["date","value"]].dropna().sort_values("date")
    except: return pd.DataFrame()

# ECOS 항목 자동 검색
def find_item(stat,keywords):
    if not ECOS_API_KEY: return None
    try:
        rows = requests.get(f"https://ecos.bok.or.kr/api/StatisticItemList/{ECOS_API_KEY}/json/kr/1/10000/{stat}",timeout=30).json().get("StatisticItemList",{}).get("row",[])
        for x in rows:
            text = " ".join(str(x.get(k,"")) for k in x if k.startswith("ITEM_NAME"))
            if all(k in text for k in keywords): return x.get("ITEM_CODE1")
    except: pass
    return None

ecos_series = {
    "한국 국고채 3Y":("817Y002","010200000","D","rate","일간"),
    "한국 국고채 10Y":("817Y002","010210000","D","rate","일간"),
    "한국 회사채 AA- 3Y":("817Y002","010310000","D","rate","일간"),
    "USD/KRW":("731Y003","0000003","D","pct","일간"),
    "한국 기준금리":("722Y001","0101000","D","policy","정책금리"),
    "한국 CPI":("901Y009","0","M","yoy","월간"),
    "한국 산업생산":("901Y033",None,"M","yoy","월간"),
    "한국 수출":("901Y011",None,"M","yoy","월간")
}
ecos_series["한국 산업생산"] = ("901Y033",find_item("901Y033",["전산업생산지수"]),"M","yoy","월간")
ecos_series["한국 수출"] = ("901Y011",find_item("901Y011",["총계"]),"M","yoy","월간")

# 데이터 수집
treasury = get_treasury()
bls = get_bls(["CUSR0000SA0","CUSR0000SA0L1E","LNS14000000","CES0000000001"])
effr = get_effr()
wti = get_wti()
fed_target = get_fed_target()
ecos = {name:get_ecos(*x[:3]) for name,x in ecos_series.items()}

rows = []
def add_row(name,df,country,typ,freq):
    if df.empty: return
    df = df.dropna().sort_values("date")
    cur,date = float(df.iloc[-1]["value"]),df.iloc[-1]["date"]
    ch = None if len(df)<2 else (cur-float(df.iloc[-2]["value"]))*100 if typ in ["rate","policy"] else cur-float(df.iloc[-2]["value"]) if typ=="pp" else cur-float(df.iloc[-2]["value"]) if typ=="nfp" else (cur/float(df.iloc[-2]["value"])-1)*100
    rows.append({"구분":country,"지표":name,"기준일":date,"현재":cur,"변화":ch,"빈도":freq,"type":typ})

if not treasury.empty:
    for c,n in [("2Y","미국 2Y"),("10Y","미국 10Y"),("30Y","미국 30Y")]:
        add_row(n,treasury[["date",c]].rename(columns={c:"value"}),"미국","rate","일간")
    x=treasury[["date","2Y","10Y"]].dropna().copy(); x["value"]=x["10Y"]-x["2Y"]; add_row("미국 10Y-2Y",x[["date","value"]],"미국","rate","일간")
add_row("EFFR",effr,"미국","rate","일간")
add_row("WTI",wti,"미국","pct","일간")

bls_map={"CUSR0000SA0":("미국 CPI","yoy"),"CUSR0000SA0L1E":("미국 Core CPI","yoy"),"LNS14000000":("미국 실업률","pp"),"CES0000000001":("미국 NFP","nfp")}
for sid,(name,typ) in bls_map.items():
    if sid in bls and not bls[sid].empty:
        d=bls[sid].copy()
        if typ=="yoy": d["value"]=d["value"].pct_change(12)*100; d=d.dropna()
        add_row(name,d,"미국",typ,"월간")

for name,df in ecos.items():
    typ,freq=ecos_series[name][3],ecos_series[name][4]
    if typ=="yoy" and not df.empty: df=df.copy(); df["value"]=df["value"].pct_change(12)*100; df=df.dropna()
    add_row(name,df,"한국",typ,freq)

if not ecos["한국 국고채 3Y"].empty and not ecos["한국 국고채 10Y"].empty:
    x=pd.concat([ecos["한국 국고채 3Y"].set_index("date")["value"].rename("3Y"),ecos["한국 국고채 10Y"].set_index("date")["value"].rename("10Y")],axis=1).dropna().reset_index()
    x["value"]=x["10Y"]-x["3Y"]; add_row("한국 10Y-3Y",x[["date","value"]],"한국","rate","일간")

# 화면
st.title("📊 Bond & Macro Dashboard")
st.caption(f"조회 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')} KST")
if fed_target: st.metric("🇺🇸 Fed 목표금리",f"{fed_target[0]:.2f}% – {fed_target[1]:.2f}%",f"기준일 {fed_target[2]}")
if not rows: st.error("데이터가 없습니다. Streamlit Secrets의 API 키를 확인하세요.")
else:
    result=pd.DataFrame(rows)
    def fmt(x,t):
        if pd.isna(x): return "-"
        s="+" if x>0 else ""; icon="🔴" if x>0 else "🔵" if x<0 else "⚪"
        if t in ["rate","policy"]: return f"{icon} {s}{x:.1f}bp"
        if t=="pp": return f"{icon} {s}{x:.1f}%p"
        if t=="nfp": return f"{icon} {s}{x:,.0f}K"
        return f"{icon} {s}{x:.1f}%"
    result["변화"]=[fmt(x,t) for x,t in zip(result["변화"],result["type"])]
    result["기준일"]=pd.to_datetime(result["기준일"]).dt.strftime("%Y-%m-%d")
    result["현재"]=result["현재"].map(lambda x:f"{x:,.2f}")
    for country in ["미국","한국"]:
        st.subheader("🇺🇸 미국" if country=="미국" else "🇰🇷 한국")
        d=result[result["구분"]==country][["지표","기준일","현재","변화"]].reset_index(drop=True)
        st.dataframe(d,use_container_width=True,hide_index=True)
st.caption("미국 국채: U.S. Treasury · 고용/물가: BLS · WTI: EIA · EFFR/Fed 목표금리: New York Fed · 한국: ECOS")

if st.button("🔄 데이터 새로고침"):
    st.rerun()
