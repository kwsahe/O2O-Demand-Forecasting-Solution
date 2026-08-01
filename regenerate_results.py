"""이미 수집된 원본 CSV(data/raw_*.csv)만 사용해 파이프라인을 재실행하고
결과 CSV(data/인테리어_수요점수_결과.csv, data/시도별_수요집계_요약.csv)를 갱신한다.
공공 API를 다시 호출하지 않는다 — pipeline.py 로직 변경분(기준연도 동적화,
결측 지표 가중치 재정규화 등)만 반영해 결과를 다시 산출할 때 사용."""

import os
import sys
import pandas as pd

sys.path.append(os.path.dirname(__file__))

from src.pipeline import DemandForecastingPipeline

RESULT_PATH = "data/인테리어_수요점수_결과.csv"

df_trade_all = pd.read_csv("data/raw_api_collected_all.csv", encoding="utf-8-sig", low_memory=False)
df_rent_all = pd.read_csv("data/raw_rent_collected_all.csv", encoding="utf-8-sig", low_memory=False)

pipeline = DemandForecastingPipeline(
    supply_path="data/한국부동산원_주택공급정보_입주예정물량정보_20251231.csv"
)
df_result, sido_summary = pipeline.run(df_transactions=df_trade_all, df_rent=df_rent_all)

# 소상공인_인테리어업체수는 SBIZ API로 별도 수집한 값(collect_interior_stores.py)이라
# 파이프라인 산출물이 아니다. API를 다시 호출하지 않고 기존 결과 CSV에서 그대로 이어받는다.
if os.path.exists(RESULT_PATH):
    df_prev = pd.read_csv(RESULT_PATH, encoding="utf-8-sig")
    if "소상공인_인테리어업체수" in df_prev.columns:
        df_result = df_result.merge(
            df_prev[["시도", "시군구", "소상공인_인테리어업체수"]],
            on=["시도", "시군구"], how="left",
        )
        df_result["소상공인_인테리어업체수"] = df_result["소상공인_인테리어업체수"].fillna(0).astype(int)

df_result.to_csv(RESULT_PATH, index=False, encoding="utf-8-sig")
sido_summary.to_csv("data/시도별_수요집계_요약.csv", index=False, encoding="utf-8-sig")

print(f"\n[DONE] 결과 시군구 수: {len(df_result)}")
print(df_result.head(10).to_string(index=False))
