"""전국 월별 아파트 매매 거래량을 SARIMA/Prophet/LightGBM으로 예측해 비교하고,
결과를 data/timeseries_forecast_result.json으로 저장한다 (대시보드 /forecast 페이지가 읽음).

이 스크립트는 두 가지를 분리해서 계산한다.
1. 백테스트: 마지막 몇 개월을 검증 구간으로 떼어놓고, 학습 구간까지의 정보만으로
   그 구간을 예측해 실제값과 비교한다 (MAPE 계산용). 세 모델 모두 "학습 구간 이후
   정보를 전혀 보지 않고 여러 달을 한 번에/재귀적으로 예측"하는 동일한 조건으로 맞춘다.
   (기존에는 LightGBM만 검증 구간 실제값을 다음 스텝의 lag 피처로 재사용해
   SARIMA/Prophet과 평가 조건이 달랐다 — 재귀적(recursive) 예측으로 통일함.)
2. 미래 예측: 전체 관측 데이터(학습+검증)로 모델을 다시 학습해, 마지막 관측월
   이후 FUTURE_HORIZON개월을 예측한다. 백테스트와 완전히 분리된 결과다.

notebooks/04_TimeSeries_Forecasting.ipynb와 동일한 분석을 스크립트로 실행한다.
마지막 단계에서 로컬 LLM(Ollama)에게 이미 계산된 숫자만 주고 "AI 판단 결과" 문단을
한 번 생성해 JSON에 캐싱한다 (페이지 로드마다 LLM을 부르면 느리고 불안정해서, 이 스크립트를
재실행할 때만 갱신).
"""
import os
import json
import warnings
warnings.filterwarnings("ignore")

import requests
import pandas as pd
import numpy as np
from dateutil.relativedelta import relativedelta
from sklearn.metrics import mean_absolute_percentage_error
from statsmodels.tsa.statespace.sarimax import SARIMAX
from prophet import Prophet
import lightgbm as lgb

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:3b")

FUTURE_HORIZON = 3   # 마지막 관측월 이후 몇 개월을 미래 예측할지
LGB_FEATURES = ["lag1", "lag2", "lag3", "month"]

df = pd.read_csv("data/raw_api_collected_all.csv", encoding="utf-8-sig", low_memory=False)
df["ym"] = pd.to_datetime(
    df["dealYear"].astype(str) + "-" + df["dealMonth"].astype(str).str.zfill(2) + "-01"
)
monthly = df.groupby("ym").size().rename("y").reset_index().rename(columns={"ym": "ds"})
monthly = monthly[monthly["ds"] < "2026-06-01"].sort_values("ds").reset_index(drop=True)

train = monthly.iloc[:9].copy()
test = monthly.iloc[9:].copy()


def lgb_train(train_df: pd.DataFrame) -> lgb.LGBMRegressor:
    """lag 피처를 학습 구간 내부에서만 shift로 만들어 학습한다(미래 정보 누수 없음)."""
    feat = train_df.copy()
    for lag in [1, 2, 3]:
        feat[f"lag{lag}"] = feat["y"].shift(lag)
    feat["month"] = feat["ds"].dt.month
    feat = feat.dropna().reset_index(drop=True)
    model = lgb.LGBMRegressor(n_estimators=50, max_depth=3, min_child_samples=1, verbose=-1)
    model.fit(feat[LGB_FEATURES], feat["y"])
    return model


def lgb_forecast_recursive(model: lgb.LGBMRegressor, known_y: list, future_ds: list) -> list:
    """SARIMA/Prophet처럼 학습 구간 이후 실제값을 전혀 보지 않고, 자신의 이전 예측값만
    lag 피처로 재사용해 여러 달을 재귀적으로(recursive multi-step) 예측한다."""
    y = list(known_y)
    preds = []
    for ds in future_ds:
        lag1, lag2, lag3 = y[-1], y[-2], y[-3]
        x = pd.DataFrame([[lag1, lag2, lag3, ds.month]], columns=LGB_FEATURES)
        p = float(model.predict(x)[0])
        preds.append(p)
        y.append(p)
    return preds


# ── 백테스트 (검증 구간 MAPE 계산) ──────────────────────────────
sarima_fit = SARIMAX(train["y"], order=(1, 1, 1), seasonal_order=(0, 0, 0, 0)).fit(disp=False)
sarima_pred = sarima_fit.forecast(steps=len(test))
sarima_mape = mean_absolute_percentage_error(test["y"], sarima_pred) * 100

prophet_model = Prophet(yearly_seasonality=False, weekly_seasonality=False, daily_seasonality=False)
prophet_model.fit(train[["ds", "y"]])
future = prophet_model.make_future_dataframe(periods=len(test), freq="MS")
forecast = prophet_model.predict(future)
prophet_pred = forecast["yhat"].iloc[-len(test):].values
prophet_mape = mean_absolute_percentage_error(test["y"], prophet_pred) * 100

lgb_model = lgb_train(train)
lgb_pred = lgb_forecast_recursive(lgb_model, train["y"].tolist(), list(test["ds"]))
lgb_mape = mean_absolute_percentage_error(test["y"], lgb_pred) * 100

models = {
    "SARIMA": {"mape": round(float(sarima_mape), 2), "predictions": [round(float(v)) for v in sarima_pred]},
    "Prophet": {"mape": round(float(prophet_mape), 2), "predictions": [round(float(v)) for v in prophet_pred]},
    "LightGBM": {"mape": round(float(lgb_mape), 2), "predictions": [round(float(v)) for v in lgb_pred]},
}
best_model = min(models, key=lambda k: models[k]["mape"])


# ── 미래 예측 (전체 데이터로 재학습, 백테스트와 완전히 분리) ──────────
future_ds = [monthly["ds"].max() + relativedelta(months=i) for i in range(1, FUTURE_HORIZON + 1)]

sarima_full_fit = SARIMAX(monthly["y"], order=(1, 1, 1), seasonal_order=(0, 0, 0, 0)).fit(disp=False)
sarima_future = sarima_full_fit.forecast(steps=FUTURE_HORIZON)

prophet_full_model = Prophet(yearly_seasonality=False, weekly_seasonality=False, daily_seasonality=False)
prophet_full_model.fit(monthly[["ds", "y"]])
future_full = prophet_full_model.make_future_dataframe(periods=FUTURE_HORIZON, freq="MS")
forecast_full = prophet_full_model.predict(future_full)
prophet_future = forecast_full["yhat"].iloc[-FUTURE_HORIZON:].values

lgb_full_model = lgb_train(monthly)
lgb_future = lgb_forecast_recursive(lgb_full_model, monthly["y"].tolist(), future_ds)

future_predictions = {
    "future_ym": [d.strftime("%Y-%m") for d in future_ds],
    "SARIMA": [round(float(v)) for v in sarima_future],
    "Prophet": [round(float(v)) for v in prophet_future],
    "LightGBM": [round(float(v)) for v in lgb_future],
}


def compute_trend(series: pd.Series, window: int = 6) -> tuple:
    """LLM에게 추세 판단을 맡기지 않고, 최근 window개월의 선형회귀 기울기로
    서버에서 먼저 추세를 확정한다. LLM은 이 판단을 문장으로 풀어쓰기만 한다."""
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


def verify_trend_consistency(text: str, trend_label: str) -> bool:
    """LLM 응답이 서버가 계산한 추세와 모순되는 단어를 쓰지 않았는지 확인한다.
    (작은 로컬 모델은 계산은 못 시키더라도 서술 자체가 틀릴 수 있어 사후 검증한다.)"""
    if not text:
        return False
    up_words = ["상승", "증가", "늘어", "오름세", "증가세"]
    down_words = ["하락", "감소", "줄어", "내림세", "감소세"]
    has_up = any(w in text for w in up_words)
    has_down = any(w in text for w in down_words)
    if trend_label == "상승":
        return not (has_down and not has_up)
    if trend_label == "하락":
        return not (has_up and not has_down)
    return True  # 혼조/보합은 어느 쪽 표현이 섞여도 허용


def generate_ai_judgment(monthly, train, test, models, best_model, trend_label, trend_slope, trend_window):
    """이미 계산된 숫자와 추세 판단만 프롬프트에 넣어 LLM이 그것을 자연어로 설명/포장하게 한다.
    LLM에게 계산이나 추세 판단을 맡기지 않는다 — 문장화만 맡긴다."""
    monthly_text = "\n".join(f"{r.ds.strftime('%Y-%m')}: {int(r.y):,}건" for r in monthly.itertuples())
    model_text = "\n".join(
        f"- {name}: 백테스트 MAPE {info['mape']}% (검증 구간 예측값 {info['predictions']})"
        for name, info in models.items()
    )

    prompt = (
        "당신은 부동산 거래량 시계열 백테스트 결과를 해설하는 분석가입니다. "
        "아래 [확정된 데이터]에 있는 숫자와 판단만 사용하세요. 새로운 숫자를 계산하거나 "
        "추세를 스스로 재판단하지 마세요 — [확정된 추세 판단]과 다른 방향(상승/하락)을 "
        "주장하면 안 됩니다. 한국어 3~5문장으로 작성하세요. "
        "이 결과는 미래 예측이 아니라 과거 검증 구간을 얼마나 잘 재현했는지 평가한 "
        "백테스트라는 점을 명확히 하세요.\n\n"
        "[확정된 데이터]\n"
        f"전국 월별 아파트 매매 거래건수 (학습 {len(train)}개월 + 검증 {len(test)}개월):\n"
        f"{monthly_text}\n\n"
        f"검증 구간 실제 거래건수: {[int(v) for v in test['y']]}\n\n"
        f"모델별 백테스트 정확도(MAPE, 낮을수록 정확):\n{model_text}\n\n"
        f"백테스트 최우수 모델: {best_model} (MAPE {models[best_model]['mape']}%)\n\n"
        f"[확정된 추세 판단]\n"
        f"최근 {trend_window}개월 선형회귀 기울기 기준 추세: {trend_label} "
        f"(월평균 {trend_slope:+.1f}건 변화) — 이 판단을 그대로 사용하세요.\n\n"
        f"위 데이터만 근거로: (1) 추세가 왜 '{trend_label}'인지 [확정된 추세 판단]을 그대로 설명하고, "
        f"(2) {best_model}이 왜 검증 구간을 가장 잘 재현했는지, (3) 데이터가 11개월뿐이라 "
        "계절성 판단에 한계가 있다는 점을 반드시 포함해 짧게 답변하세요. "
        "반드시 한국어로만 답변하세요."
    )

    try:
        res = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model": CHAT_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_ctx": 4096, "num_predict": 512},
            },
            timeout=120,
        )
        res.raise_for_status()
        return res.json()["message"]["content"].strip()
    except requests.exceptions.RequestException as e:
        print(f"[WARNING] AI 판단 결과 생성 실패 (Ollama 연결 불가): {e}")
        return None


trend_label, trend_slope, trend_window = compute_trend(monthly["y"])

ai_judgment_raw = generate_ai_judgment(
    monthly, train, test, models, best_model, trend_label, trend_slope, trend_window
)

if ai_judgment_raw is None:
    ai_judgment = None
    ai_judgment_status = "failed_generation"
elif not verify_trend_consistency(ai_judgment_raw, trend_label):
    print(f"[WARNING] AI 판단 결과가 계산된 추세({trend_label})와 모순되어 폐기합니다.")
    ai_judgment = None
    ai_judgment_status = "failed_verification"
else:
    ai_judgment = ai_judgment_raw
    ai_judgment_status = "ok"

result = {
    "monthly": [
        {"ym": d.strftime("%Y-%m"), "거래건수": int(v)}
        for d, v in zip(monthly["ds"], monthly["y"])
    ],
    "train_months": int(len(train)),
    "test_months": int(len(test)),
    "test_ym": [d.strftime("%Y-%m") for d in test["ds"]],
    "test_actual": [int(v) for v in test["y"]],
    "models": models,
    "best_model": best_model,
    "forecast_horizon": FUTURE_HORIZON,
    "future_predictions": future_predictions,
    "trend_label": trend_label,
    "trend_slope": round(trend_slope, 1),
    "trend_window_months": trend_window,
    "ai_judgment": ai_judgment,
    "ai_judgment_status": ai_judgment_status,
}

with open("data/timeseries_forecast_result.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print(f"[DONE] best_model(backtest)={best_model}, MAPE={models[best_model]['mape']}%")
print(f"[FUTURE] {FUTURE_HORIZON}개월 미래 예측: {future_predictions['future_ym']}")
print(f"[TREND] 최근 {trend_window}개월 추세: {trend_label} ({trend_slope:+.1f}건/월)")
print(f"[AI 판단 결과] status={ai_judgment_status}")
print("저장 완료: data/timeseries_forecast_result.json")
