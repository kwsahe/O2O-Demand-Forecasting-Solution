import pandas as pd
import numpy as np
import warnings
from datetime import datetime
warnings.filterwarnings("ignore")

# 거래 건마다 실제 계약연도(계약년월)로 노후도를 계산하므로, 이 값은
# 계약년월을 알 수 없는 경우(외부 주입 DataFrame 등)에만 쓰이는 폴백이다.
REFERENCE_YEAR = datetime.now().year

SEGMENT_RULES = {
    "New_Apartment":      (0,  5),
    "Mid_Apartment":      (6,  14),
    "Old_Apartment":      (15, 20),
    "Very_Old_Apartment": (21, 999),
}

SCORE_WEIGHTS = {
    "거래건수":     0.20,
    "거래금액":     0.15,
    "노후도":       0.15,
    "면적":         0.10,
    "신규입주":     0.10,
    "전월세거래건수": 0.20,
    "대수선이력":   0.10,
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
    return address.split()[0] if address else "Unknown"

def classify_apartment(age: int) -> str:
    for seg, (lo, hi) in SEGMENT_RULES.items():
        if lo <= age <= hi:
            return seg
    return "Very_Old_Apartment"

def deal_year_from_ymd(df: pd.DataFrame, reference_year: int) -> pd.Series:
    """'계약년월'(YYYYMM) 컬럼이 있으면 거래연도를 그대로 노후도 기준연도로 쓰고,
    없으면(외부 주입 DataFrame 등) reference_year로 폴백한다."""
    if "계약년월" in df.columns:
        year = pd.to_numeric(df["계약년월"].astype(str).str[:4], errors="coerce")
        return year.fillna(reference_year)
    return pd.Series(reference_year, index=df.index)

def min_max_scale(series: pd.Series) -> pd.Series:
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series(50, index=series.index)
    return (series - mn) / (mx - mn) * 100

class DemandForecastingPipeline:
    def __init__(
        self,
        transactions_path: str = None,
        supply_path: str = None,
        rent_path: str = None,
        renovation_path: str = "data/raw_renovation_collected.csv",
        interior_company_path: str = "data/raw_interior_companies.csv",
        population_path: str = "data/202605_202605_연령별인구현황_월간_전체시군구현황.csv",
        reference_year: int = REFERENCE_YEAR,
        supply_year_range: tuple = ("2025", "2026"),
        target_segment: str = "Old_Apartment",
        score_weights: dict = None,
    ):
        self.transactions_path = transactions_path
        self.supply_path = supply_path
        self.rent_path = rent_path
        self.renovation_path = renovation_path
        self.interior_company_path = interior_company_path
        self.population_path = population_path
        self.reference_year = reference_year
        self.supply_year_range = supply_year_range
        self.target_segment = target_segment
        self.score_weights = score_weights or SCORE_WEIGHTS

        self.df_transactions = None
        self.df_supply = None
        self.df_rent = None
        self.df_renovation = None
        self.df_interior_company = None
        self.df_population = None
        self.df_processed = None

    def load_data(self, df_transactions = None, df_rent = None):
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

        if df_rent is not None:
            self.df_rent = df_rent
            print(f"[LOAD] 전월세 외부 DataFrame 입력: {len(df_rent):,}건")
        elif self.rent_path:
            self.df_rent = pd.read_csv(self.rent_path, encoding="utf-8-sig")
            print(f"[LOAD] 전월세 CSV 로드: {len(self.df_rent):,}건")
        else:
            self.df_rent = None

        if self.renovation_path:
            try:
                self.df_renovation = pd.read_csv(self.renovation_path, encoding="utf-8-sig")
                print(f"[LOAD] 대수선 이력 CSV 로드: {len(self.df_renovation):,}건")
            except FileNotFoundError:
                self.df_renovation = None
                print("[LOAD] 대수선 이력 CSV 없음 (대수선이력 점수는 0으로 처리됩니다)")
        else:
            self.df_renovation = None

        if self.interior_company_path:
            try:
                self.df_interior_company = pd.read_csv(self.interior_company_path, encoding="utf-8-sig")
                print(f"[LOAD] 인테리어업체 CSV 로드: {len(self.df_interior_company):,}건")
            except FileNotFoundError:
                self.df_interior_company = None
                print("[LOAD] 인테리어업체 CSV 없음 (인테리어업체수는 0으로 처리됩니다)")
        else:
            self.df_interior_company = None

        if self.population_path:
            try:
                self.df_population = pd.read_csv(self.population_path, encoding="cp949")
                print(f"[LOAD] 인구통계 CSV 로드: {len(self.df_population):,}건")
            except FileNotFoundError:
                self.df_population = None
                print("[LOAD] 인구통계 CSV 없음 (인구 지표는 0으로 처리됩니다)")
        else:
            self.df_population = None
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

            from src.collector import SIGUNGU_CODE_TO_FULL_NAME
            df["시군구"] = df["수집_시군구코드"].map(
                lambda c: SIGUNGU_CODE_TO_FULL_NAME.get(str(c), str(c)) + " "
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

        df["노후도"] = deal_year_from_ymd(df, self.reference_year) - df["건축년도"].astype(int)
        df = df[df["노후도"] >= 0].copy()

        df["세그먼트"] = df["노후도"].apply(classify_apartment)

        df["시도"]      = df["시군구"].apply(extract_sido)
        df["시군구_코드"] = df["시군구"].apply(lambda x: str(x).strip().split()[1] if len(str(x).strip().split()) >= 2 else x)

        self.df_transactions = df
        print(f"[PREPROCESS] 실거래가 정제 완료: {len(df):,}건")
        print(f"  세그먼트 분포:\n{df['세그먼트'].value_counts().to_string()}")
        return self
    
    def preprocess_rent(self):
        if self.df_rent is None or self.df_rent.empty:
            self.df_rent = None
            print("[PREPROCESS] 전월세 데이터 없음 (전월세거래건수는 0으로 처리됩니다)")
            return self

        df = self.df_rent.copy()

        if "aptNm" in df.columns:
            from src.collector import SIGUNGU_CODE_TO_FULL_NAME
            rename_map = {
                "aptNm":      "단지명",
                "excluUseAr": "전용면적(㎡)",
                "buildYear":  "건축년도",
                "umdNm":      "법정동",
                "dealYear":   "년",
                "dealMonth":  "월",
            }
            df = df.rename(columns=rename_map)
            if "수집_시군구코드" in df.columns and "법정동" in df.columns:
                df["시군구"] = df["수집_시군구코드"].map(
                    lambda c: SIGUNGU_CODE_TO_FULL_NAME.get(str(c), str(c)) + " "
                ) + df["법정동"].fillna("")
            if "년" in df.columns and "월" in df.columns:
                df["계약년월"] = (
                    df["년"].astype(str).str.zfill(4) +
                    df["월"].astype(str).str.zfill(2)
                )

        df = df[[c for c in ["시군구", "건축년도", "계약년월"] if c in df.columns]].copy()
        df["건축년도"] = pd.to_numeric(df["건축년도"], errors="coerce")
        df.dropna(subset=["건축년도", "시군구"], inplace=True)

        df["노후도"] = deal_year_from_ymd(df, self.reference_year) - df["건축년도"].astype(int)
        df = df[df["노후도"] >= 0].copy()
        df["세그먼트"] = df["노후도"].apply(classify_apartment)

        df["시도"]       = df["시군구"].apply(extract_sido)
        df["시군구_코드"] = df["시군구"].apply(lambda x: str(x).strip().split()[1] if len(str(x).strip().split()) >= 2 else x)

        self.df_rent = df
        print(f"[PREPROCESS] 전월세 정제 완료: {len(df):,}건")
        return self

    def preprocess_renovation(self):
        if self.df_renovation is None or self.df_renovation.empty:
            self.df_renovation = None
            print("[PREPROCESS] 대수선 이력 데이터 없음 (대수선이력건수는 0으로 처리됩니다)")
            return self

        from src.collector import SIGUNGU_CODE_TO_FULL_NAME

        df = self.df_renovation.copy()
        df["시군구"] = df["수집_시군구코드"].map(
            lambda c: SIGUNGU_CODE_TO_FULL_NAME.get(str(c), str(c))
        )
        df["시도"]      = df["시군구"].apply(extract_sido)
        df["시군구_코드"] = df["시군구"].apply(
            lambda x: str(x).strip().split()[1] if len(str(x).strip().split()) >= 2 else x
        )

        self.df_renovation = df
        print(f"[PREPROCESS] 대수선 이력 정제 완료: {len(df):,}건")
        return self

    def preprocess_interior_company(self):
        if self.df_interior_company is None or self.df_interior_company.empty:
            self.df_interior_company = None
            print("[PREPROCESS] 인테리어업체 데이터 없음 (인테리어업체수는 0으로 처리됩니다)")
            return self

        from src.collector import SIGUNGU_CODE_TO_FULL_NAME

        # 숫자코드("11650.0") → 파이프라인 시군구_코드("서초구") 변환 맵
        # SIGUNGU_CODE_TO_FULL_NAME: {"11650": "서울특별시 서초구"} → split()[1] = "서초구"
        code_to_name = {
            code: full.split()[1]
            for code, full in SIGUNGU_CODE_TO_FULL_NAME.items()
            if len(full.split()) >= 2
        }

        df = self.df_interior_company.copy()

        def resolve_sigungu_code(row):
            code = row.get("시군구_코드")
            # 숫자 코드가 있으면 파이프라인 시군구명으로 변환
            if pd.notna(code) and str(code).replace(".", "").isdigit():
                return code_to_name.get(str(int(float(code))), "")
            # 숫자 코드 없으면 시군구명 직접 사용 (경기 수원시·안양시 등 상위 시 케이스)
            return str(row.get("시군구명", ""))

        df["시군구_코드"] = df.apply(resolve_sigungu_code, axis=1)
        df = df[df["시군구_코드"] != ""].copy()

        sido_map = {
            "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
            "부산광역시": "부산", "대구광역시": "대구", "광주광역시": "광주",
            "대전광역시": "대전", "울산광역시": "울산",
        }
        df["시도"] = df["시도명"].map(lambda x: sido_map.get(str(x).strip(), str(x).strip()))

        self.df_interior_company = df
        print(f"[PREPROCESS] 인테리어업체 정제 완료: {len(df):,}건 (시군구 매칭됨)")
        return self

    def preprocess_population(self):
        """행정안전부 연령별 인구현황(시군구 단위) 정제.
        '행정구역' 컬럼은 시도 전체("서울특별시")/시도+시군구("경기도 수원시")/
        시도+시+구("경기도 수원시 장안구")가 계층적으로 섞여 있음 — 공백 2토큰(시도+시군구)
        행만 골라내면 파이프라인의 102개 시군구 단위와 정확히 일치한다."""
        if self.df_population is None or self.df_population.empty:
            self.df_population = None
            print("[PREPROCESS] 인구통계 데이터 없음 (인구 지표는 0으로 처리됩니다)")
            return self

        df = self.df_population.copy()
        df.columns = [c.strip() for c in df.columns]

        def find_col(suffix):
            return next(c for c in df.columns if c.endswith(suffix))

        col_total = find_col("_계_총인구수")
        col_20 = find_col("_계_20~29세")
        col_30 = find_col("_계_30~39세")
        col_60 = find_col("_계_60~69세")
        col_70 = find_col("_계_70~79세")
        col_80 = find_col("_계_80~89세")
        col_90 = find_col("_계_90~99세")
        col_100 = find_col("_계_100세 이상")

        for c in [col_total, col_20, col_30, col_60, col_70, col_80, col_90, col_100]:
            df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce").fillna(0)

        df["행정구역_정제"] = df["행정구역"].str.replace(r"\s*\(\d+\)", "", regex=True).str.strip()
        tokens = df["행정구역_정제"].str.split()
        df = df[tokens.apply(len) == 2].copy()

        df["시도"] = df["행정구역_정제"].apply(lambda x: extract_sido(x.split()[0]))
        df["시군구_코드"] = df["행정구역_정제"].apply(lambda x: x.split()[1])
        df["총인구수"] = df[col_total]
        df["청년인구비율"] = np.where(
            df[col_total] > 0, ((df[col_20] + df[col_30]) / df[col_total] * 100).round(1), 0
        )
        df["고령인구비율"] = np.where(
            df[col_total] > 0,
            ((df[col_60] + df[col_70] + df[col_80] + df[col_90] + df[col_100]) / df[col_total] * 100).round(1),
            0,
        )

        self.df_population = df[["시도", "시군구_코드", "총인구수", "청년인구비율", "고령인구비율"]]
        print(f"[PREPROCESS] 인구통계 정제 완료: {len(self.df_population):,}개 시군구")
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

        if self.df_rent is not None and not self.df_rent.empty:
            df_rent_core = self.df_rent[self.df_rent["세그먼트"] == self.target_segment]
            agg_rent = (
                df_rent_core.groupby(["시도", "시군구_코드"])
                .size()
                .reset_index(name="전월세거래건수")
            )
            df = pd.merge(df, agg_rent, on=["시도", "시군구_코드"], how="left")
            df["전월세거래건수"] = df["전월세거래건수"].fillna(0)
        else:
            df["전월세거래건수"] = 0

        if self.df_renovation is not None and not self.df_renovation.empty:
            agg_renovation = (
                self.df_renovation.groupby(["시도", "시군구_코드"])
                .size()
                .reset_index(name="대수선이력건수")
            )
            df = pd.merge(df, agg_renovation, on=["시도", "시군구_코드"], how="left")
            df["대수선이력건수"] = df["대수선이력건수"].fillna(0)
            # 대수선 이력은 일부 시도(예: 5대 광역시)가 원천 수집 범위 밖일 수 있다.
            # 이 경우 "0건"이 아니라 "관측 안 됨"이므로 해당 시도는 커버리지 False로 표시해
            # calculate_demand_score()에서 그 지역만 가중치를 재정규화한다.
            covered_sido = set(self.df_renovation["시도"].unique())
            df["대수선이력_covered"] = df["시도"].isin(covered_sido)
        else:
            df["대수선이력건수"] = 0
            df["대수선이력_covered"] = False

        if self.df_interior_company is not None and not self.df_interior_company.empty:
            agg_interior = (
                self.df_interior_company.groupby(["시도", "시군구_코드"])
                .size()
                .reset_index(name="인테리어업체수")
            )
            df = pd.merge(df, agg_interior, on=["시도", "시군구_코드"], how="left")
            df["인테리어업체수"] = df["인테리어업체수"].fillna(0)
        else:
            df["인테리어업체수"] = 0

        if self.df_population is not None and not self.df_population.empty:
            df = pd.merge(df, self.df_population, on=["시도", "시군구_코드"], how="left")
            df["총인구수"] = df["총인구수"].fillna(0)
            df["청년인구비율"] = df["청년인구비율"].fillna(0)
            df["고령인구비율"] = df["고령인구비율"].fillna(0)
        else:
            df["총인구수"] = 0
            df["청년인구비율"] = 0
            df["고령인구비율"] = 0

        self.df_processed = df
        print(f"[MERGE] 집계 완료: {len(df):,}개 시군구")
        return self
    
    def calculate_demand_score(self):

        df = self.df_processed.copy()
        w = self.score_weights

        df["s_거래건수"]     = min_max_scale(df["거래건수"])
        df["s_거래금액"]     = min_max_scale(df["평균거래금액"])
        df["s_노후도"]       = min_max_scale(df["평균노후도"])
        df["s_면적"]         = min_max_scale(df["평균면적"])
        df["s_신규입주"]     = min_max_scale(df["신규입주"])
        df["s_전월세거래건수"] = min_max_scale(df["전월세거래건수"])
        df["s_대수선이력"]    = min_max_scale(df["대수선이력건수"])

        # 대수선이력이 원천 미수집 지역("관측 안 됨")이면 해당 지표를 가중합에서
        # 제외하고, 나머지 지표 가중치 합으로 나눠 그 지역만 재정규화한다.
        # (수집 안 됨 ≠ 실제로 대수선 이력이 0건 — 동일 취급하면 커버리지 없는
        # 지역이 구조적으로 불리해진다.)
        covered = df.get("대수선이력_covered", pd.Series(True, index=df.index)).astype(bool)
        active_weight = pd.Series(1.0, index=df.index) - (~covered) * w["대수선이력"]

        df["인테리어_수요점수"] = (
            (
                df["s_거래건수"]     * w["거래건수"] +
                df["s_거래금액"]     * w["거래금액"] +
                df["s_노후도"]       * w["노후도"]   +
                df["s_면적"]         * w["면적"]     +
                df["s_신규입주"]     * w["신규입주"] +
                df["s_전월세거래건수"] * w["전월세거래건수"] +
                df["s_대수선이력"]    * w["대수선이력"] * covered
            ) / active_weight
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
            "신규입주", "입주단지", "전월세거래건수", "대수선이력건수", "대수선이력_covered", "인테리어업체수",
            "총인구수", "청년인구비율", "고령인구비율",
            "인테리어_수요점수",
        ]
        df_result = self.df_processed[result_cols].copy()
        df_result.columns = [
            "시도", "시군구",
            "거래건수", "평균거래금액_만원", "평균노후도_년", "평균면적_m2",
            "신규입주_세대수", "입주단지수", "전월세거래건수", "대수선이력건수", "대수선이력_수집됨", "인테리어업체수",
            "총인구수", "청년인구비율", "고령인구비율",
            "인테리어_수요점수",
        ]

        # ── 예상 시공비 추정 (interior_cost_reference.md 기반, 비공식 참고용)
        # 평형 환산: 1평 = 3.3058㎡
        # 단가 범위: 올수리 기준 하한 150만/평, 중급 리모델링 기준 상한 220만/평
        # 노후도가 20년 이상이면 전체 리모델링 상한을 300만/평으로 상향
        pyeong = df_result["평균면적_m2"] / 3.3058
        is_very_old = df_result["평균노후도_년"] >= 20

        df_result["예상시공비_하한_만원"] = (pyeong * 150).round(0).astype(int)
        df_result["예상시공비_상한_만원"] = np.where(
            is_very_old, (pyeong * 300).round(0), (pyeong * 220).round(0)
        ).astype(int)

        # 시장 규모 추정: 거래건수 × 예상 시공비 중간값
        mid = (df_result["예상시공비_하한_만원"] + df_result["예상시공비_상한_만원"]) / 2
        df_result["시장규모_추정_억"] = (df_result["거래건수"] * mid / 10000).round(1)

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

    def run(self, df_transactions=None, df_rent=None):
        print("=" * 60)
        print("  오늘의집 인테리어 수요 예측 파이프라인 시작")
        print("=" * 60)

        return (
            self
            .load_data(df_transactions=df_transactions, df_rent=df_rent)
            .preprocess_transactions()
            .preprocess_rent()
            .preprocess_renovation()
            .preprocess_interior_company()
            .preprocess_population()
            .preprocess_supply()
            .aggregate_and_merge()
            .calculate_demand_score()
            .get_results()
        )
    