import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

REFERENCE_YEAR = 2025

SEGMENT_RULES = {
    "New_Apartment":      (0,  5),
    "Mid_Apartment":      (6,  14),
    "Old_Apartment":      (15, 20),
    "Very_Old_Apartment": (21, 999),
}

SCORE_WEIGHTS = {
    "거래건수": 0.30,
    "거래금액": 0.25,
    "노후도":   0.20,
    "면적":     0.15,
    "신규입주": 0.10,
}

def extract_sido(address: str) -> str:
    sido_map = {
        "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구",
        "인천광역시": "인천", "광주광역시": "광주", "대전광역시": "대전",
        "울산광역시": "울산", "세종특별자치시": "세종",
        "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원",
        "충청북도": "충북", "충청남도": "충남",
        "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
        "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주",
    }
    address = str(address).strip()
    for full, short in sido_map.items():
        if address.startswith(full):
            return short
    return address.spliat()[0] if address else "Unknown"

def classify_apartment(age: int) -> str:
    for seg, (lo, hi) in SEGMENT_RULES.items():
        if lo <= age <= hi:
            return seg
    return "Very_Old_Apartment"

def min_max_scale(series: pd.Series) -> pd.Series:
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series([50, 0] * len(series), index = series.index)
    return (series - mn) / (mx - mn) * 100

class DemandForecastingPipeline:
    def __init__(
        self,
        transactions_path: str = None,
        supply_path: str = None,
        reference_year: int = REFERENCE_YEAR,
        supply_year_range: tuple = ("2025", "2026"),
        target_segment: str = "Old_Apartment",
        score_weights: dict = None,
    ):
        self.transactions_path = transactions_path
        self.supply_path = supply_path
        self.reference_year = reference_year
        self.supply_year_range = supply_year_range
        self.target_segment = target_segment
        self.score_weights = score_weights or SCORE_WEIGHTS

        self.df_transactions = None
        self.df_supply = None
        self.df_processed = None

    def load_data(self, df_transactions = None):
        if df_transactions is not None:
            self.df_transactions = df_transactions
            print(f"[LOAD] 외부 DataFrame 입력: {len(df_transactions):,}건")
        elif self.transactions_path:
            self.df_transactions = pd.read_csv(
                self.transactions_path, encoding="cp949", skiprows=15
            )
            print(f"[LOAD] CSV 로드: {len(self.df_transactions):,}건")
        else:
            raise ValueError("transactions_path 또는 df_transactions 중 하나는 필요합니다.")
        
        self.df_supply = pd.read_csv(
            self.supply_path, encoding="utf-8-sig"
        )
        print(f"[LOAD] 입주예정 로드: {len(self.df_supply):,}건")
        return self
    
    def preprocess_transactions(self):
        if "dealAmount" in self.df_transactions.columns:
            rename_map = {
                "aptNm":      "단지명",
                "excluUseAr": "전용면적(㎡)",
                "dealAmount": "거래금액(만원)",
                "buildYear":  "건축년도",
                "roadNm":     "도로명",
                "umdNm":      "법정동",
                "dealYear":   "년",
                "dealMonth":  "월",
            }
            df = self.df_transactions.rename(columns=rename_map)

            from src.collector import SEOUL_SIGUNGU_CODES
            code_to_name = {v: k for k, v in SEOUL_SIGUNGU_CODES.items()}
            df["시군구"] = df["수집_시군구코드"].map(
                lambda c: f"서울특별시 {code_to_name.get(c, c)} "
            ) + df["법정동"].fillna("")

            df["계약년월"] = (
                df["년"].astype(str).str.zfill(4) +
                df["월"].astype(str).str.zfill(2)
            )
        else:
            df = self.df_transactions.copy()

        cols = ["시군구", "단지명", "전용면적(㎡)", "계약년월",
                "거래금액(만원)", "건축년도", "도로명"]
        df = df[[c for c in cols if c in df.columns]].copy()

        df["거래금액(만원)"] = (
            df["거래금액(만원)"].astype(str)
            .str.replace(",", "", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )
        df["건축년도"]   = pd.to_numeric(df["건축년도"],   errors="coerce")
        df["전용면적(㎡)"] = pd.to_numeric(df["전용면적(㎡)"], errors="coerce")

        df.dropna(subset=["건축년도", "거래금액(만원)"], inplace=True)

        df["노후도"] = self.reference_year - df["건축년도"].astype(int)
        df = df[df["노후도"] >= 0].copy()

        df["세그먼트"] = df["노후도"].apply(classify_apartment)

        df["시도"]      = df["시군구"].apply(extract_sido)
        df["시군구_코드"] = df["시군구"].apply(lambda x: str(x).strip().split()[1] if len(str(x).strip().split()) >= 2 else x)

        self.df_transactions = df
        print(f"[PREPROCESS] 실거래가 정제 완료: {len(df):,}건")
        print(f"  세그먼트 분포:\n{df['세그먼트'].value_counts().to_string()}")
        return self
    
    def preprocess_supply(self):
        df = self.df_supply.copy()
        df["세대수"] = pd.to_numeric(df["세대수"], errors="coerce").fillna(0)
        df["입주예정월"] = df["입주예정월"].astype(str)

        mask = df["입주예정월"].str.startswith(self.supply_year_range)
        df = df[mask].copy()

        df["시도"] = df["지역"].str.strip()
        df["시군구_코드"] = df["주소"].apply(
            lambda x: str(x).strip().split()[1]
            if len(str(x).strip().split()) >= 2 else x
        )

        self.df_supply = df
        print(f"[PREPROCESS] 입주예정 정제 완료: {len(df):,}건")
        return self
    
    def aggregate_and_merge(self):
        df_core = self.df_transactions[
            self.df_transactions["세그먼트"] == self.target_segment
        ].copy()

        agg_tx = (
            df_core.groupby(["시도", "시군구_코드"])
            .agg(
                거래건수=("거래금액(만원)", "count"),
                평균거래금액=("거래금액(만원)", "mean"),
                평균노후도=("노후도", "mean"),
                평균면적=("전용면적(㎡)", "mean"),
            )
            .reset_index()
            .round(1)
        )

        agg_sup = (
            self.df_supply.groupby(["시도", "시군구_코드"])
            .agg(
                신규입주=("세대수", "sum"),
                입주단지=("아파트명", "count"),
            )
            .reset_index()
        )

        df = pd.merge(agg_tx, agg_sup, on=["시도", "시군구_코드"], how="left")
        df["신규입주"] = df["신규입주"].fillna(0)
        df["입주단지"] = df["입주단지"].fillna(0)

        self.df_processed = df
        print(f"[MERGE] 집계 완료: {len(df):,}개 시군구")
        return self
    
    def calculate_demand_score(self):

        df = self.df_processed.copy()
        w = self.score_weights

        df["s_거래건수"] = min_max_scale(df["거래건수"])
        df["s_거래금액"] = min_max_scale(df["평균거래금액"])
        df["s_노후도"]   = min_max_scale(df["평균노후도"])
        df["s_면적"]     = min_max_scale(df["평균면적"])
        df["s_신규입주"] = min_max_scale(df["신규입주"])

        df["인테리어_수요점수"] = (
            df["s_거래건수"] * w["거래건수"] +
            df["s_거래금액"] * w["거래금액"] +
            df["s_노후도"]   * w["노후도"]   +
            df["s_면적"]     * w["면적"]     +
            df["s_신규입주"] * w["신규입주"]
        ).round(2)

        self.df_processed = df.sort_values("인테리어_수요점수", ascending=False)
        print(f"[SCORE] 수요 점수 계산 완료")
        print(f"  최고: {df['인테리어_수요점수'].max():.1f} | "
              f"평균: {df['인테리어_수요점수'].mean():.1f}")
        return self
    
    def get_results(self):
        result_cols = [
            "시도", "시군구_코드",
            "거래건수", "평균거래금액", "평균노후도", "평균면적",
            "신규입주", "입주단지",
            "인테리어_수요점수",
        ]
        df_result = self.df_processed[result_cols].copy()
        df_result.columns = [
            "시도", "시군구",
            "거래건수", "평균거래금액_만원", "평균노후도_년", "평균면적_m2",
            "신규입주_세대수", "입주단지수",
            "인테리어_수요점수",
        ]

        sido_summary = (
            df_result.groupby("시도")
            .agg(
                시군구수=("시군구", "count"),
                총거래건수=("거래건수", "sum"),
                평균수요점수=("인테리어_수요점수", "mean"),
                최고수요점수=("인테리어_수요점수", "max"),
                총신규입주=("신규입주_세대수", "sum"),
            )
            .round(2)
            .sort_values("평균수요점수", ascending=False)
            .reset_index()
        )
        return df_result, sido_summary

    def run(self, df_transactions=None):
        print("=" * 60)
        print("  오늘의집 인테리어 수요 예측 파이프라인 시작")
        print("=" * 60)

        return (
            self
            .load_data(df_transactions=df_transactions)
            .preprocess_transactions()
            .preprocess_supply()
            .aggregate_and_merge()
            .calculate_demand_score()
            .get_results()
        )
    