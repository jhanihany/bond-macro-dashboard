# Bond & Macro Dashboard

미국과 한국의 채권시장 및 주요 거시경제 지표를 한눈에 확인하는 Streamlit 대시보드입니다.

## 데이터 출처
- 미국 국채: U.S. Department of the Treasury
- 미국 CPI·실업률·NFP: U.S. Bureau of Labor Statistics (BLS)
- WTI: U.S. Energy Information Administration (EIA)
- EFFR·Fed 목표금리: Federal Reserve Bank of New York
- 한국 지표: Bank of Korea ECOS

## Streamlit Secrets
```toml
ECOS_API_KEY = "YOUR_ECOS_KEY"
EIA_API_KEY = "YOUR_EIA_KEY"
BLS_API_KEY = "YOUR_BLS_KEY"
```

Treasury와 New York Fed 데이터는 별도 API 키가 필요하지 않습니다.
