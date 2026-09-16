# Bond & Macro Dashboard

한국·미국의 채권시장과 핵심 거시경제 지표를 한 화면에서 확인하기 위한 Streamlit 대시보드입니다.

## 화면 구성

### 1. Today Market Snapshot
- Fed Funds Target Range
- 한국은행 기준금리
- 미국 10년물 국채금리
- 한국 10년물 국고채금리
- USD/KRW
- WTI 선물

### 2. Rates & Curve
- 미국 2Y / 5Y / 10Y / 30Y
- 미국 10Y-2Y
- 미국 10Y 실질금리
- 5Y / 10Y 기대인플레이션
- 한국 3Y / 10Y
- 한국 10Y-3Y
- 한국 회사채 AA- 3Y
- 미국·한국 금리 변화에 따른 Curve Move 자동 분류
  - Bear Steepening
  - Bear Flattening
  - Bull Steepening
  - Bull Flattening
  - Twist / Parallel Shift

예:
`미국 2Y +8bp · 10Y +3bp → Bear Flattening`

### 3. Macro
미국:
- CPI
- Core CPI
- 실업률
- NFP

한국:
- CPI
- 전산업생산 원계열: 전년동월비(YoY)
- 전산업생산 계절조정계열: 전월비(MoM)

## 데이터 출처

- U.S. Treasury
- U.S. Bureau of Labor Statistics (BLS)
- Federal Reserve Board (FOMC)
- Bank of Korea
- Bank of Korea ECOS
- Yahoo Finance (`CL=F`, WTI Futures)

## 로컬 실행

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. Streamlit Secrets 생성

`.streamlit/secrets.toml.example`을 복사해서 `.streamlit/secrets.toml` 파일을 만듭니다.

```toml
ECOS_API_KEY = "YOUR_ECOS_API_KEY"
BLS_API_KEY = "YOUR_BLS_API_KEY"
```

- `ECOS_API_KEY`: 필수
- `BLS_API_KEY`: 선택 사항. 키가 없어도 BLS Public API 제한 내에서 동작할 수 있습니다.

### 3. 실행

```bash
streamlit run app.py
```

## Streamlit Community Cloud 배포

1. 이 폴더의 파일들을 GitHub repository에 업로드합니다.
2. Streamlit Community Cloud에서 repository를 선택합니다.
3. Main file path를 `app.py`로 지정합니다.
4. App settings → Secrets에 아래 내용을 입력합니다.

```toml
ECOS_API_KEY = "YOUR_ECOS_API_KEY"
BLS_API_KEY = "YOUR_BLS_API_KEY"
```

5. Deploy 합니다.

## 기준일 표시

- 일간 시장지표: 실제 관측일 `YYYY-MM-DD`
- 월간 거시지표: 기준월 `YYYY-MM`
- 정책금리: 실제 정책결정 발표일 `YYYY-MM-DD 발표`

## 변화 표시

- 상승: 빨간색 Bold
- 하락: 파란색 Bold
- 변화 없음: 검은색 Bold

## 주의

외부 기관의 페이지 구조/API 응답 형식이 변경되면 일부 데이터 수집 함수도 수정이 필요할 수 있습니다.


## 전산업생산 처리 방식

`901Y033` 통계표에는 원계열과 계절조정계열 등 여러 세부 계열이 함께 존재합니다.

이 앱은 `StatisticItemList`에서 `ITEM_CODE1`만 선택하지 않고
`ITEM_CODE1 ~ ITEM_CODE4` 전체 경로를 찾아 정확한 세부 계열만 조회합니다.

- 원계열: 같은 달의 12개월 전 지수와 비교하여 YoY 계산
- 계절조정계열: 직전월과 비교하여 MoM 계산
- 정확한 세부 항목을 찾지 못하면 통계표 전체를 불러오지 않고 표시를 생략하여
  서로 다른 계열이 섞이는 오류를 방지합니다.


## v2 - 전산업생산 로딩 수정

이전 버전은 ECOS `StatisticItemList`의 이름이 예상한 문자열과 정확히 일치하지 않으면
전산업생산을 숨기도록 되어 있어 CPI만 표시될 수 있었습니다.

v2에서는 `901Y033` 전체 데이터를 받아 실제 `ITEM_NAME` 메타데이터를 유지한 뒤,
원계열과 계절조정계열을 각각 분리합니다.

- 원계열 → YoY
- 계절조정계열 → MoM

로딩 상태창에는 실제로 선택한 ECOS 계열명도 표시합니다.


## YoY + MoM 동시 표시

Macro 표에서는 두 비교가 모두 중요한 지표를 한 행에서 동시에 표시합니다.

미국:
- CPI: NSA index로 YoY, SA index로 MoM
- Core CPI: NSA index로 YoY, SA index로 MoM
- 실업률: 현재 수준 + 전월 대비 %p 변화
- NFP: 현재 payroll level + 전월 대비 증감

한국:
- CPI: YoY + MoM
- 전산업생산: 원계열 YoY + 계절조정계열 MoM

표 구조:
`지표 | 기준일 | 현재 | YoY | MoM | 변화`


## Insight v5

대시보드의 데이터 표시를 넘어 시장 구조와 모멘텀이 바로 보이도록 다음을 추가했습니다.

### 1. 1D / 1W / 1M
미국·한국 금리 표에 직전 거래일, 1주, 1개월 변화가 함께 표시됩니다.

### 2. U.S. 10Y decomposition
동일 관측일 기준으로 다음 관계를 사용합니다.

`Nominal 10Y ≈ Real 10Y + 10Y Breakeven`

당일 명목금리 변화를 실질금리와 기대인플레이션 변화로 분해해
어느 구성요소의 변화가 더 컸는지 표시합니다.

### 3. Macro Momentum
CPI, Core CPI, NFP, 실업률, 한국 전산업생산 등의 직전월 대비
가속/둔화 방향을 표시합니다.

### 4. 5Y Historical Percentile
각 금리·스프레드·실질금리·Breakeven의 현재 값이 최근 5년 분포에서
어느 위치인지 백분위로 표시합니다.

이를 위해 U.S. Treasury nominal / real yield history를
현재 연도 포함 최근 6개 calendar year에서 수집합니다.

### 5. Rule-based Macro Regime
현재 대시보드에 있는 모멘텀 지표만으로 Growth / Inflation 방향을 단순 집계해:

- Reflation
- Goldilocks
- Stagflation Pressure
- Slowdown / Disinflation
- Mixed / Neutral

중 하나를 표시합니다.

이는 투자 시그널이나 공식 경기판정이 아니라 dashboard용 휴리스틱입니다.
