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
- 산업생산

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
