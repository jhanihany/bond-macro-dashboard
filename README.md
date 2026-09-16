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
