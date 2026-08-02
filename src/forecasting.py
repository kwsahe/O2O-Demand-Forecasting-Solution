import re

import numpy as np
import pandas as pd


def compute_trend(series: pd.Series, window: int = 6) -> tuple[str, float, int]:
    """Return a deterministic trend label and monthly slope for the recent window."""
    n = min(window, len(series))
    y = series.tail(n).to_numpy(dtype=float)
    x = np.arange(n, dtype=float)
    slope = float(np.polyfit(x, y, 1)[0])
    mean = float(y.mean()) if y.mean() else 1.0
    rel_slope = slope / mean
    if rel_slope > 0.03:
        label = "상승"
    elif rel_slope < -0.03:
        label = "하락"
    else:
        label = "혼조/보합"
    return label, slope, n


def verify_trend_consistency(text: str, trend_label: str, trend_slope: float) -> bool:
    """Reject AI explanations that contradict the computed direction or signed slope."""
    if not text:
        return False

    up_words = ["상승", "증가", "늘어", "오름세", "증가세"]
    down_words = ["하락", "감소", "줄어", "내림세", "감소세"]
    mixed_words = ["혼조", "보합"]
    has_up = any(word in text for word in up_words)
    has_down = any(word in text for word in down_words)
    has_mixed = any(word in text for word in mixed_words)

    if trend_label == "상승" and (has_down and not has_up):
        return False
    if trend_label == "하락" and (has_up and not has_down):
        return False
    if trend_label == "혼조/보합" and not has_mixed:
        return False

    expected_abs = abs(trend_slope)
    if expected_abs == 0:
        return True

    for match in re.finditer(r"([+-]?)\s*([\d,]+(?:\.\d+)?)\s*건", text):
        sign, number = match.groups()
        value = float(number.replace(",", ""))
        if not np.isclose(value, expected_abs, rtol=0.02, atol=0.2):
            continue
        if trend_slope > 0 and sign == "-":
            return False
        if trend_slope < 0 and sign != "-":
            return False

    return True
