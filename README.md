# 🏠 O2O-Demand-Forecasting-Solution

> **오늘의집 O2O 서비스팀** | 부동산 거래 데이터 기반 인테리어 수요 예측 파이프라인

---

## 프로젝트 개요

아파트 **실거래가 데이터**와 **입주예정 물량 데이터**를 결합해  
지역별 인테리어 시공 수요를 수치화하는 분석 파이프라인입니다.

공공데이터 API를 연결하여 **매월 자동 수집 → 분석 → 대시보드 갱신**까지  
end-to-end로 동작하는 것을 목표로 합니다.

---

## 📁 프로젝트 구조

```
O2O-Demand-Forecasting-Solution/
│
├── data/
│   ├── 아파트(매매)_실거래가_20260311170739.csv            # 국토부 실거래가 (초기 입력용)
│   └── 한국부동산원_주택공급정보_입주예정물량정보_20251231.csv  # 입주예정 (초기 입력용)
│
├── notebooks/
│   ├── 01_EDA_and_Hypothesis.ipynb               # 탐색적 데이터 분석
│   └── 02_Pipeline_and_DemandScore.ipynb         # 파이프라인 실행 & 시각화
│
├── src/
│   ├── collector.py                              # ★ 공공데이터 API 자동 수집 모듈
│   ├── pipeline.py                               # ★ 인테리어 수요 점수 산출 파이프라인
│   └── log_design.py                             # 로깅 유틸리티
│
├── dashboard/
│   └── index.html                                # 인터랙티브 수요 대시보드
│
├── venv/                                         # 가상환경 (git 제외)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## ⚙️ 환경 세팅

### 1. 가상환경 생성 및 활성화

```powershell
# 가상환경 생성
python -m venv venv

# 최초 1회 — PowerShell 실행 정책 허용
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 활성화 (작업 시작할 때마다 실행)
venv\Scripts\activate
```

터미널 앞에 `(venv)` 가 붙으면 성공입니다.

### 2. 패키지 설치

```powershell
pip install PublicDataReader python-dateutil pandas jupyter
```

> **Python 3.14 사용 시 주의**  
> pandas 2.x 는 3.14 미지원 → `pandas>=3.0` 이 자동 설치됩니다.

---

## 🔑 API 키 설정

### 공공데이터포털 API 신청

1. [data.go.kr](https://www.data.go.kr) 로그인
2. **"국토교통부\_아파트 매매 실거래가 상세 자료"** 검색 → 활용신청
   - 직접 링크: https://www.data.go.kr/data/15126468/openapi.do
   - 심의유형: 자동승인 (즉시 사용 가능)
3. 마이페이지 → 인증키 관리 → **일반 인증키** 복사

> ⚠️ **키 보안 주의사항**  
> 인증키를 코드나 GitHub에 직접 입력하지 마세요.  
> `.env` 파일에 보관하고 `.gitignore`에 추가하세요.

```
# .env 파일 예시
API_KEY=여기에키값
```

### API 연결 테스트

```powershell
python src/collector.py 일반인증키값
```

성공 시 출력:
```
[TEST] 서초구 / 202602 단건 수집
  수집 건수: 84건
[NORMALIZE] 변환 후 컬럼: ['시군구', '단지명', '전용면적(㎡)', ...]
  시군구                단지명     거래금액(만원)  건축년도
  서울특별시 서초구 서초동  현대슈퍼빌  270,000    2003
```

---

## 🚀 사용법

### 데이터 수집 (API 자동 수집)

```python
from src.collector import ApartmentDataCollector

collector = ApartmentDataCollector(api_key="일반인증키값")

# 최근 12개월 서울 전체 수집 후 CSV 저장
df = collector.fetch_recent_months(
    months=12,
    save_path="data/raw_api_collected.csv"
)
```

### 파이프라인 실행

```python
from src.pipeline import DemandForecastingPipeline

pipeline = DemandForecastingPipeline(
    transactions_path="data/아파트(매매)_실거래가_20260311170739.csv",
    supply_path="data/한국부동산원_주택공급정보_입주예정물량정보_20251231.csv",
)

df_result, sido_summary = pipeline.run()
```

### 가중치 커스터마이징

```python
# 예: 신규 입주 수요 중심 전략으로 변경
pipeline = DemandForecastingPipeline(
    transactions_path="...",
    supply_path="...",
    score_weights={
        "거래건수": 0.25,
        "거래금액": 0.20,
        "노후도":   0.15,
        "면적":     0.10,
        "신규입주": 0.30,  # 가중치 상향
    }
)
```

---

## 🎯 핵심 로직

### 아파트 세그먼트 분류

| 세그먼트 | 노후도 | 인테리어 수요 특성 |
|---|---|---|
| New_Apartment | 0~5년 | 가구·소품·부분 인테리어 |
| Mid_Apartment | 6~14년 | 벽지·바닥재 부분 교체 |
| **Old_Apartment ★** | **15~20년** | **전면 리모델링 — 핵심 타겟** |
| Very_Old_Apartment | 21년+ | 재건축 검토, 시공 수요 낮음 |

> **핵심 타겟 선정 이유**  
> 1기 신도시 재정비 연식대와 겹쳐 리모델링 관심 최고조.  
> 욕실·주방·바닥재 등 전면 교체 수요 + 고단가 시공 상품 구매 가능성 높음.

### 인테리어 수요 점수 가중치

| 지표 | 가중치 | 비즈니스 이유 |
|---|---|---|
| 거래건수 | 30% | 시장 볼륨 — 수요 규모 |
| 거래금액 | 25% | 구매력 — 고가 지역일수록 고급 시공 |
| 노후도 | 20% | 리모델링 시급성 |
| 전용면적 | 15% | 시공 규모 — 매출 기여 |
| 신규입주 | 10% | 신규 입주 수요 |

---

## 📦 src/ 모듈 상세

### `collector.py` — API 자동 수집 모듈

| 함수 | 역할 |
|---|---|
| `fetch_one(code, ym)` | 단일 구 + 단일 월 API 호출 |
| `fetch_range(codes, start, end)` | 여러 구 × 연월 범위 순차 수집 |
| `fetch_recent_months(months=12)` | 오늘 기준 최근 N개월 자동 수집 |
| `normalize_columns(df)` | API 영문 컬럼 → pipeline.py 한글 컬럼 변환 |

**사용 API:** 국토교통부 아파트매매 실거래 상세 자료  
**엔드포인트:** `https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev`

### `pipeline.py` — 수요 점수 산출 파이프라인

| 메서드 | 역할 |
|---|---|
| `load_data()` | CSV 로드 |
| `preprocess_transactions()` | 실거래가 정제 + 노후도 계산 + 세그먼트 분류 |
| `preprocess_supply()` | 입주예정 정제 + 연도 필터링 |
| `aggregate_and_merge()` | 시군구 단위 집계 + LEFT JOIN |
| `calculate_demand_score()` | 5개 지표 Min-Max 정규화 후 가중합산 |

---

## 📊 분석 결과 (서울 기준, Old Apartment 기준)

| 순위 | 지역 | 수요 점수 | 거래건수 | 평균거래금액 |
|---|---|---|---|---|
| 1 | 서울 서초구 | 70.57 | 456건 | 3억 332만원 |
| 2 | 서울 송파구 | 59.51 | 1,091건 | 2억 4,681만원 |
| 3 | 서울 강남구 | 55.31 | 525건 | 2억 8,764만원 |
| 4 | 서울 성북구 | 48.48 | 1,606건 | 8,984만원 |
| 5 | 서울 광진구 | 45.65 | 262건 | 1억 8,026만원 |

---

## 🗺️ 개발 로드맵

```
✅ STEP 1   data/            CSV 데이터 확보 (국토부 + 한국부동산원)
✅ STEP 2   src/pipeline.py  인테리어 수요 점수 파이프라인 구축
✅ STEP 3   src/collector.py 공공데이터 API 자동 수집 모듈 구축
✅ STEP 4   dashboard/       인터랙티브 수요 대시보드
⬜ STEP 5   notebooks/03     API 수집 → 파이프라인 end-to-end 연결
⬜ STEP 6   자동화            cron 스케줄러로 매월 자동 갱신
```

---

## 📓 노트북 가이드

| 노트북 | 내용 |
|---|---|
| `01_EDA_and_Hypothesis.ipynb` | 데이터 탐색, 분포 확인, 가설 수립 |
| `02_Pipeline_and_DemandScore.ipynb` | 파이프라인 실행, 수요 점수 산출, 시각화 |

---

## 🔧 데이터 출처

| 데이터 | 출처 | 수집 방식 |
|---|---|---|
| 아파트 매매 실거래가 | 국토교통부 실거래가 공개시스템 | API 자동 수집 (`collector.py`) |
| 입주예정 물량 | 한국부동산원 주택공급정보 | CSV 수동 다운로드 |
