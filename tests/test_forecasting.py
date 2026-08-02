import pandas as pd

from src.forecasting import compute_trend, verify_trend_consistency


def test_compute_trend_detects_rising_series():
    label, slope, window = compute_trend(pd.Series([100, 110, 120, 130, 140, 150]))
    assert label == "상승"
    assert slope > 0
    assert window == 6


def test_rejects_wrong_signed_slope():
    text = "최근 흐름은 혼조/보합이며 월평균 변화는 -687.9건입니다."
    assert not verify_trend_consistency(text, "혼조/보합", 687.9)


def test_accepts_correct_signed_slope():
    text = "최근 흐름은 혼조/보합이며 월평균 변화는 +687.9건입니다."
    assert verify_trend_consistency(text, "혼조/보합", 687.9)


def test_mixed_trend_must_be_named():
    text = "최근 거래량은 뚜렷한 상승세입니다."
    assert not verify_trend_consistency(text, "혼조/보합", 10.0)
