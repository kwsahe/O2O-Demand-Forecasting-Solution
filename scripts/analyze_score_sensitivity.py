"""수요 점수 가중치 ±20% 변화에 대한 순위 민감도를 계산한다."""
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pipeline import SCORE_WEIGHTS, min_max_scale


RESULT_PATH = ROOT / "data" / "인테리어_수요점수_결과.csv"
OUTPUT_PATH = ROOT / "data" / "score_sensitivity_result.json"

METRIC_COLUMNS = {
    "거래건수": "거래건수",
    "거래금액": "평균거래금액_만원",
    "노후도": "평균노후도_년",
    "면적": "평균면적_m2",
    "신규입주": "신규입주_세대수",
    "전월세거래건수": "전월세거래건수",
    "대수선이력": "대수선이력건수",
}


def calculate_score(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    scaled = {
        metric: min_max_scale(pd.to_numeric(df[column], errors="coerce").fillna(0))
        for metric, column in METRIC_COLUMNS.items()
    }
    covered = df["대수선이력_수집됨"].astype(bool)
    active_weight = 1.0 - (~covered) * weights["대수선이력"]
    score = sum(
        scaled[metric] * weight * (covered if metric == "대수선이력" else 1)
        for metric, weight in weights.items()
    ) / active_weight
    return score


def ranks(score: pd.Series) -> pd.Series:
    return score.rank(ascending=False, method="min")


def main() -> None:
    df = pd.read_csv(RESULT_PATH, encoding="utf-8-sig")
    names = df["시도"] + " " + df["시군구"]
    baseline_score = calculate_score(df, SCORE_WEIGHTS)
    baseline_rank = ranks(baseline_score)
    baseline_top10 = set(baseline_rank.nsmallest(10).index)

    scenarios = []
    for metric in SCORE_WEIGHTS:
        for factor in (0.8, 1.2):
            raw_weights = dict(SCORE_WEIGHTS)
            raw_weights[metric] *= factor
            total = sum(raw_weights.values())
            weights = {key: value / total for key, value in raw_weights.items()}
            scenario_score = calculate_score(df, weights)
            scenario_rank = ranks(scenario_score)
            rank_change = (scenario_rank - baseline_rank).abs()
            max_index = rank_change.idxmax()
            scenario_top10 = set(scenario_rank.nsmallest(10).index)
            scenarios.append({
                "metric": metric,
                "change": "-20%" if factor < 1 else "+20%",
                "spearman_rank_correlation": round(float(baseline_rank.corr(scenario_rank)), 4),
                "top10_overlap_count": len(baseline_top10 & scenario_top10),
                "top10_overlap_percent": len(baseline_top10 & scenario_top10) * 10,
                "mean_absolute_score_change": round(float((scenario_score - baseline_score).abs().mean()), 3),
                "max_rank_change": int(rank_change.max()),
                "max_changed_region": names.loc[max_index],
            })

    reconstructed_gap = (baseline_score - pd.to_numeric(df["인테리어_수요점수"])).abs()
    result = {
        "method": "각 지표 가중치를 ±20% 변경한 뒤 전체 가중치 합을 1로 재정규화",
        "regions": len(df),
        "scenario_count": len(scenarios),
        "reconstructed_score_max_abs_diff": round(float(reconstructed_gap.max()), 3),
        "summary": {
            "minimum_spearman": min(item["spearman_rank_correlation"] for item in scenarios),
            "minimum_top10_overlap_percent": min(item["top10_overlap_percent"] for item in scenarios),
            "maximum_rank_change": max(item["max_rank_change"] for item in scenarios),
            "maximum_mean_absolute_score_change": max(item["mean_absolute_score_change"] for item in scenarios),
        },
        "scenarios": scenarios,
    }
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
