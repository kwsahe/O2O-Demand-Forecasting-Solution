"""src/pipeline.py 핵심 로직에 대한 단위 테스트.

평소 손으로 검증하기 어려운 경계값(노후도 세그먼트 14/15/20/21년)과
결측 지표 재정규화(대수선이력 미수집 지역)를 회귀 방지 목적으로 고정한다.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from src.pipeline import (
    SCORE_WEIGHTS,
    classify_apartment,
    deal_year_from_ymd,
    extract_sido,
    min_max_scale,
    DemandForecastingPipeline,
)


class TestClassifyApartment:
    def test_new_apartment_lower_bound(self):
        assert classify_apartment(0) == "New_Apartment"

    def test_new_mid_boundary(self):
        assert classify_apartment(5) == "New_Apartment"
        assert classify_apartment(6) == "Mid_Apartment"

    def test_mid_old_boundary(self):
        assert classify_apartment(14) == "Mid_Apartment"
        assert classify_apartment(15) == "Old_Apartment"

    def test_old_very_old_boundary(self):
        assert classify_apartment(20) == "Old_Apartment"
        assert classify_apartment(21) == "Very_Old_Apartment"

    def test_very_old_far_future(self):
        assert classify_apartment(999) == "Very_Old_Apartment"


class TestExtractSido:
    @pytest.mark.parametrize("addr,expected", [
        ("서울특별시 강남구 역삼동", "서울"),
        ("경기도 용인시 수지구", "경기"),
        ("부산광역시 해운대구", "부산"),
        ("세종특별자치시 한누리대로", "세종"),
        ("강원특별자치도 춘천시", "강원"),
        ("전북특별자치도 전주시", "전북"),
    ])
    def test_known_sido(self, addr, expected):
        assert extract_sido(addr) == expected

    def test_unknown_falls_back_to_first_token(self):
        assert extract_sido("어딘가 알수없는동") == "어딘가"

    def test_empty_string(self):
        assert extract_sido("") == "Unknown"


class TestMinMaxScale:
    def test_scales_to_0_100_range(self):
        s = pd.Series([10, 20, 30, 40, 50])
        scaled = min_max_scale(s)
        assert scaled.min() == 0
        assert scaled.max() == 100

    def test_constant_series_returns_50(self):
        s = pd.Series([7, 7, 7])
        scaled = min_max_scale(s)
        assert (scaled == 50).all()


class TestScoreWeights:
    def test_weights_sum_to_one(self):
        assert SCORE_WEIGHTS["거래건수"] + SCORE_WEIGHTS["거래금액"] + SCORE_WEIGHTS["노후도"] + \
            SCORE_WEIGHTS["면적"] + SCORE_WEIGHTS["신규입주"] + SCORE_WEIGHTS["전월세거래건수"] + \
            SCORE_WEIGHTS["대수선이력"] == pytest.approx(1.0)


class TestDealYearFromYmd:
    def test_uses_transaction_year_when_available(self):
        df = pd.DataFrame({"계약년월": ["202401", "202512"]})
        years = deal_year_from_ymd(df, reference_year=1999)
        assert list(years) == [2024, 2025]

    def test_falls_back_to_reference_year_when_missing(self):
        df = pd.DataFrame({"foo": [1, 2]})
        years = deal_year_from_ymd(df, reference_year=2030)
        assert list(years) == [2030, 2030]


class TestCalculateDemandScoreCoverage:
    """대수선이력이 원천 미수집인 지역(covered=False)의 가중치가
    재정규화되어 다른 지역과 공정하게 비교되는지 확인한다."""

    def _make_pipeline_with_processed(self, df_processed):
        pipeline = DemandForecastingPipeline.__new__(DemandForecastingPipeline)
        pipeline.score_weights = SCORE_WEIGHTS
        pipeline.df_processed = df_processed
        return pipeline

    def test_uncovered_region_score_ignores_its_own_renovation_value(self):
        # covered=False인 두 지역이 대수선이력건수만 극단적으로 다르면(0 vs 999999),
        # 재정규화로 그 지표가 완전히 배제되어야 하므로 최종 점수가 같아야 한다.
        # (배제되지 않는다면 min-max scale 상 두 값이 크게 갈려 점수가 달라진다.)
        df = pd.DataFrame({
            "거래건수":       [100, 100, 100],
            "평균거래금액":   [5000, 5000, 5000],
            "평균노후도":     [15, 15, 15],
            "평균면적":       [80, 80, 80],
            "신규입주":       [10, 10, 10],
            "전월세거래건수": [50, 50, 50],
            "대수선이력건수": [500, 0, 999999],
            "대수선이력_covered": [True, False, False],
        })
        pipeline = self._make_pipeline_with_processed(df)
        pipeline.calculate_demand_score()
        result = pipeline.df_processed

        # calculate_demand_score()가 마지막에 점수순으로 정렬하지만 인덱스는 보존하므로,
        # 원래 행을 iloc이 아니라 .loc(원본 인덱스 라벨)로 찾아야 순서 변화에 흔들리지 않는다.
        score_uncovered_zero = result.loc[1, "인테리어_수요점수"]
        score_uncovered_huge = result.loc[2, "인테리어_수요점수"]
        assert score_uncovered_zero == pytest.approx(score_uncovered_huge)

    def test_uncovered_region_weight_is_renormalized_not_zeroed(self):
        # 재정규화가 적용되면 대수선이력을 제외한 나머지 가중치 합(0.90)으로 나누므로,
        # 단순히 그 항을 0점 처리하고 1.0으로 나누는 것보다 uncovered 지역 점수가 더 높아야 한다.
        df = pd.DataFrame({
            "거래건수":       [10, 100],
            "평균거래금액":   [1000, 9000],
            "평균노후도":     [10, 20],
            "평균면적":       [60, 100],
            "신규입주":       [0, 50],
            "전월세거래건수": [5, 80],
            "대수선이력건수": [500, 0],
            "대수선이력_covered": [True, False],
        })
        pipeline = self._make_pipeline_with_processed(df)
        pipeline.calculate_demand_score()
        result = pipeline.df_processed

        # 원본 인덱스 라벨(1)로 조회 — sort_values 이후 위치(iloc)가 바뀌어도 안전하다.
        uncovered_score = result.loc[1, "인테리어_수요점수"]

        # 재정규화 없이 단순히 0점 처리(나누기 1.0)했을 때의 점수를 수동 계산
        naive_weighted = (
            100 * SCORE_WEIGHTS["거래건수"] + 100 * SCORE_WEIGHTS["거래금액"] +
            100 * SCORE_WEIGHTS["노후도"] + 100 * SCORE_WEIGHTS["면적"] +
            100 * SCORE_WEIGHTS["신규입주"] + 100 * SCORE_WEIGHTS["전월세거래건수"] +
            0 * SCORE_WEIGHTS["대수선이력"]
        )
        assert uncovered_score > naive_weighted

    def test_missing_covered_column_defaults_to_covered(self):
        # 커버리지 컬럼 자체가 없는 경우(예: 구버전 df_processed) 기존처럼 전원 커버된 것으로 취급.
        df = pd.DataFrame({
            "거래건수": [100],
            "평균거래금액": [5000],
            "평균노후도": [15],
            "평균면적": [80],
            "신규입주": [10],
            "전월세거래건수": [50],
            "대수선이력건수": [0],
        })
        pipeline = self._make_pipeline_with_processed(df)
        pipeline.calculate_demand_score()
        assert not pipeline.df_processed["인테리어_수요점수"].isna().any()
