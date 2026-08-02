# 실행: venv 활성화 후 `python app.py` (http://localhost:8300)
from flask import Flask, jsonify, request, render_template
from dotenv import load_dotenv
from functools import lru_cache
import pandas as pd
import numpy as np
import os
import sys
import json
import sqlite3
from datetime import datetime
import requests

load_dotenv()

sys.path.append(os.path.dirname(__file__))

from src.collector import (
    ApartmentDataCollector,
    SEOUL_SIGUNGU_CODES,
    INCHEON_SIGUNGU_CODES,
    GYEONGGI_SIGUNGU_CODES,
    METRO5_SIGUNGU_CODES,
    ALL_SIGUNGU_CODES,
    SIGUNGU_CODE_TO_FULL_NAME,
)
from src.pipeline import DemandForecastingPipeline, extract_sido

app = Flask(__name__)

API_KEY = os.getenv("API_KEY", "")
APT_BASIC_INFO_API_KEY = os.getenv("APT_BASIC_INFO_API_KEY", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:3b")
CHAT_TIMEOUT = int(os.getenv("CHAT_TIMEOUT", "60"))
CHAT_MAX_MESSAGE = int(os.getenv("CHAT_MAX_MESSAGE", "500"))
# /api/collect(전국 재수집) 보호용 관리자 토큰. 비워두면(로컬 개발) 인증 없이 허용.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

RAW_TRADE_PATH = "data/raw_api_collected_all.csv"
RAW_RENT_PATH = "data/raw_rent_collected_all.csv"
RESULT_PATH = "data/인테리어_수요점수_결과.csv"
SIDO_SUMMARY_PATH = "data/시도별_수요집계_요약.csv"


def _atomic_write_csv(df: pd.DataFrame, path: str) -> None:
    """임시 파일에 먼저 쓰고 성공 시에만 원자적으로 교체 — 쓰는 도중 실패해도
    기존 결과 파일이 훼손되거나 부분 상태로 노출되지 않는다."""
    tmp_path = f"{path}.tmp"
    df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    os.replace(tmp_path, path)


def _upsert_raw(existing_path: str, df_new: pd.DataFrame, replace_keys=None) -> pd.DataFrame:
    """새로 수집한 (수집_시군구코드, 수집_연월) 조합의 기존 행만 제거하고 새 데이터로
    교체한다. 나머지 지역·기간 데이터는 그대로 유지되므로 부분 수집이 전국 결과를
    덮어쓰지 않는다. replace_keys를 주면 신규 0건인 조합도 기존 행을 제거한다."""
    if (df_new is None or df_new.empty) and not replace_keys:
        if os.path.exists(existing_path):
            return pd.read_csv(existing_path, encoding="utf-8-sig", low_memory=False)
        return pd.DataFrame()

    if df_new is None:
        df_new = pd.DataFrame()
    if not os.path.exists(existing_path):
        return df_new.copy()

    df_existing = pd.read_csv(existing_path, encoding="utf-8-sig", low_memory=False)
    if replace_keys is not None:
        new_keys = {f"{code}|{ym}" for code, ym in replace_keys}
    else:
        new_keys = set(
            df_new["수집_시군구코드"].astype(str) + "|" + df_new["수집_연월"].astype(str)
        )
    existing_keys = (
        df_existing["수집_시군구코드"].astype(str) + "|" + df_existing["수집_연월"].astype(str)
    )
    df_existing = df_existing[~existing_keys.isin(new_keys)]
    if df_new.empty:
        return df_existing.reset_index(drop=True)
    return pd.concat([df_existing, df_new], ignore_index=True)
NAVER_MAP_CLIENT_ID = os.getenv("NAVER_MAP_CLIENT_ID", "")

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
CHAT_DB_PATH = os.path.join(DATA_DIR, "chat_history.db")


def init_chat_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(CHAT_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_message TEXT NOT NULL,
            bot_answer TEXT,
            status TEXT NOT NULL,
            chat_type TEXT NOT NULL DEFAULT 'demand'
        )
    """)
    # 기존 테이블에 chat_type 컬럼이 없으면 추가 (구버전 DB 호환)
    try:
        conn.execute("ALTER TABLE chat_logs ADD COLUMN chat_type TEXT NOT NULL DEFAULT 'demand'")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


def save_chat_log(user_message, bot_answer, status, chat_type="demand"):
    conn = sqlite3.connect(CHAT_DB_PATH)
    conn.execute(
        "INSERT INTO chat_logs (created_at, user_message, bot_answer, status, chat_type) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(timespec="seconds"), user_message, bot_answer, status, chat_type),
    )
    conn.commit()
    conn.close()


init_chat_db()

print("Flask 앱 초기화 완료")

@app.route("/")
def index():
    return render_template("index.html", naver_map_client_id=NAVER_MAP_CLIENT_ID)


@app.route("/analytics")
def analytics():
    return render_template("analytics.html")


@app.route("/guide")
def guide():
    return render_template("guide.html")


@app.route("/forecast")
def forecast():
    return render_template("forecast.html")


@app.route("/region")
def region():
    return render_template("region.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/forecast", methods=["GET"])
def get_forecast():
    try:
        with open("data/timeseries_forecast_result.json", encoding="utf-8") as f:
            result = json.load(f)
        return jsonify({"status": "ok", **result})
    except FileNotFoundError:
        return jsonify({
            "status": "error",
            "message": "예측 결과 파일이 없습니다. analyze_timeseries.py를 먼저 실행하세요.",
        }), 404

@app.route("/api/demand", methods=["GET"])
def get_demand():
    try:
        df = pd.read_csv("data/인테리어_수요점수_결과.csv", encoding="utf-8-sig")

        sido = request.args.get("sido", None)
        if sido:
            df = df[df["시도"] == sido]

        try:
            top = int(request.args.get("top", 25))
        except ValueError:
            return jsonify({"status": "error", "message": "top은 정수여야 합니다."}), 400
        if top < 1:
            return jsonify({"status": "error", "message": "top은 1 이상이어야 합니다."}), 400
        df = df.head(top)

        return jsonify({
            "status": "ok",
            "count": len(df),
            "data": df.to_dict(orient="records"),
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route("/api/map-data", methods=["GET"])
def get_map_data():
    try:
        df = pd.read_csv("data/인테리어_수요점수_결과.csv", encoding="utf-8-sig")
        coords = pd.read_csv("data/sigungu_coordinates.csv", encoding="utf-8-sig")

        merged = df.merge(coords, on=["시도", "시군구"], how="inner")

        sido = request.args.get("sido", None)
        if sido:
            merged = merged[merged["시도"] == sido]

        return jsonify({
            "status": "ok",
            "naver_map_client_id": NAVER_MAP_CLIENT_ID,
            "count": len(merged),
            "data": merged.to_dict(orient="records"),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/sido-summary", methods=["GET"])
def get_sido_summary():
    try:
        df = pd.read_csv("data/시도별_수요집계_요약.csv", encoding="utf-8-sig")
        return jsonify({
            "status": "ok",
            "count": len(df),
            "data": df.to_dict(orient="records"),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


REGION_METRICS = [
    ("거래건수", "매매 거래", "건"),
    ("평균거래금액_만원", "평균 거래금액", "만원"),
    ("전월세거래건수", "전월세 거래", "건"),
    ("평균노후도_년", "평균 노후도", "년"),
    ("평균면적_m2", "평균 면적", "㎡"),
    ("신규입주_세대수", "신규 입주", "세대"),
    ("대수선이력건수", "대수선 이력", "건"),
]


def _region_key(sido: str, sigungu: str) -> str:
    return f"{sido}|{sigungu}"


def _region_codes(sido: str, sigungu: str) -> list[str]:
    return sorted(
        code
        for code, full_name in SIGUNGU_CODE_TO_FULL_NAME.items()
        if extract_sido(full_name) == sido
        and len(full_name.split()) >= 2
        and full_name.split()[1] == sigungu
    )


def _records(df: pd.DataFrame) -> list:
    if df.empty:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False))


@lru_cache(maxsize=1)
def _load_region_sources(trade_mtime: float, rent_mtime: float):
    trade_columns = [
        "aptNm", "buildYear", "dealAmount", "dealDay", "dealMonth", "dealYear",
        "excluUseAr", "floor", "umdNm", "수집_시군구코드", "수집_연월",
    ]
    rent_columns = [
        "aptNm", "buildYear", "dealMonth", "dealYear", "deposit", "excluUseAr",
        "monthlyRent", "umdNm", "수집_시군구코드", "수집_연월",
    ]
    trade = pd.read_csv(
        RAW_TRADE_PATH,
        encoding="utf-8-sig",
        usecols=trade_columns,
        dtype={"수집_시군구코드": str, "수집_연월": str},
        low_memory=False,
    )
    rent = pd.read_csv(
        RAW_RENT_PATH,
        encoding="utf-8-sig",
        usecols=rent_columns,
        dtype={"수집_시군구코드": str, "수집_연월": str},
        low_memory=False,
    )
    return trade, rent


def _get_region_sources():
    return _load_region_sources(
        os.path.getmtime(RAW_TRADE_PATH),
        os.path.getmtime(RAW_RENT_PATH),
    )


def _region_catalog() -> pd.DataFrame:
    df = pd.read_csv(RESULT_PATH, encoding="utf-8-sig")
    df["key"] = df.apply(lambda row: _region_key(row["시도"], row["시군구"]), axis=1)
    df["name"] = df["시도"] + " " + df["시군구"]
    df["codes"] = df.apply(lambda row: _region_codes(row["시도"], row["시군구"]), axis=1)
    return df


@app.route("/api/regions", methods=["GET"])
def get_regions():
    try:
        query = request.args.get("q", "").strip()
        if len(query) > 50:
            return jsonify({"status": "error", "message": "검색어가 너무 깁니다."}), 400

        catalog = _region_catalog().sort_values("인테리어_수요점수", ascending=False)
        if query:
            normalized = query.replace(" ", "").lower()
            mask = catalog["name"].str.replace(" ", "", regex=False).str.lower().str.contains(
                normalized, regex=False
            )
            catalog = catalog[mask]

        data = catalog.head(12)[["key", "name", "시도", "시군구", "인테리어_수요점수", "codes"]]
        return jsonify({"status": "ok", "count": len(data), "data": _records(data)})
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "지역 분석 결과 파일이 없습니다."}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def _build_region_analysis(summary: dict, metrics: list[dict]) -> dict:
    score = float(summary["인테리어_수요점수"])
    if score >= 60:
        recommendation = "최우선 진입"
        recommendation_detail = "전면 리모델링과 입주 인테리어 영업을 함께 집중할 지역입니다."
    elif score >= 30:
        recommendation = "선별 진입"
        recommendation_detail = "수요가 강한 동·단지와 상품군을 선별해 접근하는 것이 적합합니다."
    else:
        recommendation = "관찰 지역"
        recommendation_detail = "대규모 집행보다 단지 단위 테스트와 변화 추적이 적합합니다."

    strongest = sorted(metrics, key=lambda item: item["percentile"], reverse=True)[:3]
    weakest = sorted(metrics, key=lambda item: item["percentile"])[:2]
    return {
        "recommendation": recommendation,
        "detail": recommendation_detail,
        "strengths": [
            f"{item['label']} 지표가 전체 분석 지역의 상위 {max(1, 101 - round(item['percentile']))}% 수준입니다."
            for item in strongest
        ],
        "risks": [
            f"{item['label']} 지표는 전체 분석 지역 중 {round(item['percentile'])}백분위로 상대적으로 낮습니다."
            for item in weakest
        ],
    }


@app.route("/api/region", methods=["GET"])
def get_region_detail():
    try:
        key = request.args.get("key", "").strip()
        if not key or len(key) > 50 or "|" not in key:
            return jsonify({"status": "error", "message": "목록에서 정확한 지역을 선택하세요."}), 400

        catalog = _region_catalog()
        matched = catalog[catalog["key"] == key]
        if matched.empty:
            return jsonify({"status": "error", "message": "분석 대상 지역을 찾을 수 없습니다."}), 404

        row = matched.iloc[0]
        sido, sigungu = row["시도"], row["시군구"]
        codes = [str(code) for code in row["codes"]]
        ranked = catalog.sort_values("인테리어_수요점수", ascending=False).reset_index(drop=True)
        ranked["overall_rank"] = ranked.index + 1
        sido_ranked = ranked[ranked["시도"] == sido].reset_index(drop=True)
        sido_rank = int(sido_ranked.index[sido_ranked["key"] == key][0]) + 1
        overall_rank = int(ranked.loc[ranked["key"] == key, "overall_rank"].iloc[0])

        metrics = []
        for column, label, unit in REGION_METRICS:
            values = pd.to_numeric(catalog[column], errors="coerce")
            value = float(row[column])
            metrics.append({
                "key": column,
                "label": label,
                "unit": unit,
                "value": value,
                "national_average": round(float(values.mean()), 1),
                "sido_average": round(float(pd.to_numeric(catalog[catalog["시도"] == sido][column], errors="coerce").mean()), 1),
                "percentile": round(float(values.rank(pct=True).loc[row.name] * 100), 1),
            })

        trade_all, rent_all = _get_region_sources()
        trade = trade_all[trade_all["수집_시군구코드"].isin(codes)].copy()
        rent = rent_all[rent_all["수집_시군구코드"].isin(codes)].copy()
        trade["거래금액_만원"] = pd.to_numeric(
            trade["dealAmount"].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
        trade["전용면적"] = pd.to_numeric(trade["excluUseAr"], errors="coerce")
        trade["건축년도"] = pd.to_numeric(trade["buildYear"], errors="coerce")
        trade["계약년도"] = pd.to_numeric(trade["dealYear"], errors="coerce")
        rent["보증금_만원"] = pd.to_numeric(
            rent["deposit"].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
        rent["월세_만원"] = pd.to_numeric(rent["monthlyRent"], errors="coerce")

        month_index = sorted(set(trade["수집_연월"].dropna()) | set(rent["수집_연월"].dropna()))
        trade_monthly = trade.groupby("수집_연월").agg(
            trade_count=("aptNm", "size"),
            average_price=("거래금액_만원", "mean"),
        )
        rent_monthly = rent.groupby("수집_연월").size().rename("rent_count")
        trend = pd.DataFrame(index=month_index).join(trade_monthly).join(rent_monthly).fillna({
            "trade_count": 0, "rent_count": 0,
        })
        trend.index.name = "ym"
        trend = trend.reset_index()
        trend["average_price"] = trend["average_price"].round(1)

        age = trade["계약년도"] - trade["건축년도"]
        segment = pd.cut(
            age,
            bins=[-np.inf, 5, 14, 20, np.inf],
            labels=["New", "Mid", "Old", "Very Old"],
        )
        segment_counts = segment.value_counts(sort=False)
        segment_df = segment_counts.rename_axis("segment").reset_index(name="count")
        segment_df["percentage"] = (segment_df["count"] / max(1, segment_df["count"].sum()) * 100).round(1)

        apartments = trade.groupby(["aptNm", "umdNm"], dropna=False).agg(
            trade_count=("aptNm", "size"),
            average_price=("거래금액_만원", "mean"),
            average_area=("전용면적", "mean"),
            build_year=("건축년도", "median"),
            latest_ym=("수집_연월", "max"),
        ).reset_index().sort_values("trade_count", ascending=False).head(10)
        apartments.columns = [
            "apartment", "dong", "trade_count", "average_price", "average_area", "build_year", "latest_ym",
        ]
        for column in ["average_price", "average_area", "build_year"]:
            apartments[column] = apartments[column].round(1)

        recent = trade.sort_values(
            ["dealYear", "dealMonth", "dealDay"], ascending=False
        ).head(15)[[
            "aptNm", "umdNm", "dealYear", "dealMonth", "dealDay", "거래금액_만원", "전용면적", "floor", "건축년도",
        ]].copy()
        recent.columns = [
            "apartment", "dong", "year", "month", "day", "price", "area", "floor", "build_year",
        ]

        comparison_columns = [item[0] for item in REGION_METRICS]
        comparison_values = catalog[comparison_columns].apply(pd.to_numeric, errors="coerce")
        standardized = (comparison_values - comparison_values.mean()) / comparison_values.std().replace(0, 1)
        distances = ((standardized - standardized.loc[row.name]) ** 2).mean(axis=1).pow(0.5)
        similar = catalog.assign(_distance=distances)
        similar = similar[similar["key"] != key].sort_values(
            ["_distance", "인테리어_수요점수"], ascending=[True, False]
        ).head(5)[["key", "name", "인테리어_수요점수", "거래건수", "전월세거래건수", "_distance"]]
        similar = similar.rename(columns={"_distance": "distance"})
        similar["distance"] = similar["distance"].round(2)

        summary = json.loads(row.drop(labels=["codes"]).to_json(force_ascii=False))
        summary.update({
            "codes": codes,
            "overall_rank": overall_rank,
            "overall_total": len(catalog),
            "sido_rank": sido_rank,
            "sido_total": int((catalog["시도"] == sido).sum()),
        })
        coverage = {
            "start_ym": month_index[0] if month_index else None,
            "end_ym": month_index[-1] if month_index else None,
            "trade_rows": len(trade),
            "rent_rows": len(rent),
            "source_codes": codes,
            "updated_at": datetime.fromtimestamp(max(
                os.path.getmtime(RAW_TRADE_PATH), os.path.getmtime(RAW_RENT_PATH)
            )).isoformat(timespec="minutes"),
        }

        return jsonify({
            "status": "ok",
            "summary": summary,
            "metrics": metrics,
            "trend": _records(trend),
            "segments": _records(segment_df),
            "apartments": _records(apartments),
            "recent_transactions": _records(recent),
            "similar_regions": _records(similar),
            "analysis": _build_region_analysis(summary, metrics),
            "coverage": coverage,
        })
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "지역 분석에 필요한 데이터 파일이 없습니다."}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/collect", methods=["POST"])
def collect():
    try:
        if ADMIN_TOKEN and request.headers.get("X-Admin-Token", "") != ADMIN_TOKEN:
            return jsonify({"status": "error", "message": "관리자 인증이 필요합니다."}), 401

        body = request.get_json() or {}
        months = int(body.get("months", 12))
        sigungu_code = body.get("sigungu_code", None)

        if not API_KEY:
            return jsonify({"status": "error", "message": "API 키가 설정되지 않았습니다."}), 400

        collector = ApartmentDataCollector(api_key=API_KEY)

        if sigungu_code:
            codes = {k: v for k, v in ALL_SIGUNGU_CODES.items() if v == sigungu_code}
            if not codes:
                return jsonify({"status": "error", "message": f"알 수 없는 시군구 코드: {sigungu_code}"}), 400
        else:
            # 기본값: 서울/인천/경기/5대 광역시 지원 행정구역 전체 — 특정 지역으로
            # 좁혀서 전국 결과를 덮어쓰는 사고를 방지한다.
            codes = ALL_SIGUNGU_CODES

        # 매매/전월세 실거래가 수집 (요청 범위만 — 저장은 아래에서 upsert로 처리)
        df_raw_new = collector.fetch_recent_months(sigungu_codes=codes, months=months, save_path=None)
        if df_raw_new.empty:
            return jsonify({"status": "error", "message": "수집된 매매 데이터가 없습니다. API 키/지역 코드를 확인하세요."}), 502

        df_rent_raw_new = collector.fetch_recent_months_rent(sigungu_codes=codes, months=months, save_path=None)
        if df_rent_raw_new.empty or not df_rent_raw_new.attrs.get("fetch_ok", True):
            return jsonify({"status": "error", "message": "전월세 데이터 수집이 불완전하여 저장하지 않았습니다."}), 502

        end_period = pd.Period(pd.Timestamp.today(), freq="M")
        requested_months = [
            (end_period - offset).strftime("%Y%m")
            for offset in range(months)
        ]
        requested_keys = {
            (str(code), ym)
            for code in codes.values()
            for ym in requested_months
        }

        # 신규 0건인 지역·월도 기존 행을 제거해야 오래된 데이터가 남지 않는다.
        df_trade_all = _upsert_raw(RAW_TRADE_PATH, df_raw_new, requested_keys)
        df_rent_all = _upsert_raw(RAW_RENT_PATH, df_rent_raw_new, requested_keys)

        # 파이프라인은 항상 전국 데이터로 재실행하므로, 부분 수집이어도 최종 결과는 전국 범위를 유지한다.
        pipeline = DemandForecastingPipeline(
            supply_path="data/한국부동산원_주택공급정보_입주예정물량정보_20251231.csv"
        )
        df_result, sido_summary = pipeline.run(df_transactions=df_trade_all, df_rent=df_rent_all)

        # 소상공인_인테리어업체수는 파이프라인 산출물이 아니라 별도 API로 수집한 값이라
        # 여기서 API를 재호출하지 않고 기존 결과 CSV에서 그대로 이어받는다.
        if os.path.exists(RESULT_PATH):
            df_prev = pd.read_csv(RESULT_PATH, encoding="utf-8-sig")
            if "소상공인_인테리어업체수" in df_prev.columns:
                df_result = df_result.merge(
                    df_prev[["시도", "시군구", "소상공인_인테리어업체수"]],
                    on=["시도", "시군구"], how="left",
                )
                df_result["소상공인_인테리어업체수"] = df_result["소상공인_인테리어업체수"].fillna(0).astype(int)

        # 원본·결과 모두 임시 파일에 먼저 쓰고 원자적으로 교체 — 중간에 실패해도 기존 파일 보존
        _atomic_write_csv(df_trade_all, RAW_TRADE_PATH)
        _atomic_write_csv(df_rent_all, RAW_RENT_PATH)
        _atomic_write_csv(df_result, RESULT_PATH)
        _atomic_write_csv(sido_summary, SIDO_SUMMARY_PATH)

        return jsonify({
            "status": "ok",
            "collected_regions": len(codes),
            "collected_trade_rows": len(df_raw_new),
            "total_sigungu": len(df_result),
            "top5": df_result.head(5).to_dict(orient="records"),
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route("/api/search", methods=["GET"])
def search():
    try:
        q = request.args.get("q", "")
        search_type = request.args.get("type", "region")

        if not q:
            return jsonify({"status": "error", "message": "검색어를 입력하세요."}), 400
        if len(q) > 50:
            return jsonify({"status": "error", "message": "검색어가 너무 깁니다."}), 400

        if search_type == "apt":
            # 단지명 검색 — 전국 원본 데이터에서 검색
            df = pd.read_csv(RAW_TRADE_PATH, encoding="utf-8-sig", low_memory=False)
            mask = df["aptNm"].str.contains(q, na=False, regex=False)
            df_filtered = df[mask][["aptNm", "umdNm", "dealAmount", "buildYear", "excluUseAr", "dealYear", "dealMonth"]].copy()
            df_filtered.columns = ["단지명", "법정동", "거래금액_만원", "건축년도", "전용면적", "년", "월"]
            df_filtered = df_filtered.head(50)
        else:
            # 지역 검색 — 수요 점수 결과에서 검색
            df = pd.read_csv("data/인테리어_수요점수_결과.csv", encoding="utf-8-sig")
            mask = (
                df["시도"].str.contains(q, na=False, regex=False) |
                df["시군구"].str.contains(q, na=False, regex=False)
            )
            df_filtered = df[mask]

        return jsonify({
            "status": "ok",
            "query": q,
            "type": search_type,
            "count": len(df_filtered),
            "data": df_filtered.to_dict(orient="records"),
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# 챗봇 메시지에서 단지명/주소(법정동)를 인식할 때 무시할 일반 단어
CHAT_STOPWORDS = {
    "수요", "점수", "지역", "시군구", "아파트", "단지", "거래", "거래금액", "건축년도",
    "전용면적", "가격", "시세", "노후도", "등급", "설명", "알려줘", "가르쳐줘", "어디",
    "얼마", "얼마나", "어떻게", "무엇", "뭐야", "뭐예요", "궁금해", "정보", "최근",
    "그리고", "그럼", "그래서", "에서", "에게", "에는", "으로", "한테", "대해",
}

def find_apartment_context(message: str, raw_path: str = RAW_TRADE_PATH, limit: int = 10) -> str:
    """메시지에 포함된 단지명/법정동 키워드로 원본 실거래 데이터를 검색해 컨텍스트 문자열을 만든다.
    공백 차이나 약간의 오타가 있어도 인식할 수 있도록 정규화 매칭과 유사도 매칭을 함께 사용한다."""
    if not os.path.exists(raw_path):
        return ""

    import re
    from difflib import get_close_matches

    tokens = {t for t in re.split(r"[^\w가-힣]+", message) if len(t) >= 2 and t not in CHAT_STOPWORDS}
    if not tokens:
        return ""

    try:
        df = pd.read_csv(raw_path, encoding="utf-8-sig", low_memory=False)
    except Exception:
        return ""

    apt_names = df["aptNm"].astype(str)
    umd_names = df["umdNm"].astype(str)

    # 1) 부분 문자열 매칭 — 공백을 제거하고 비교해 "래미안 서초"/"래미안서초" 같은 표기 차이를 흡수
    norm_apt = apt_names.str.replace(r"\s+", "", regex=True)
    norm_umd = umd_names.str.replace(r"\s+", "", regex=True)

    mask = pd.Series(False, index=df.index)
    matched_any = False
    for token in tokens:
        norm_token = re.sub(r"\s+", "", token)
        m = (
            norm_apt.str.contains(norm_token, na=False, regex=False) |
            norm_umd.str.contains(norm_token, na=False, regex=False)
        )
        if m.any():
            matched_any = True
        mask |= m

    # 2) 부분 문자열 매칭이 실패하면 — 오타·유사 표기를 허용하는 유사도 매칭(difflib)
    if not matched_any:
        unique_apts = apt_names.unique().tolist()
        unique_umds = umd_names.unique().tolist()
        for token in tokens:
            for name in get_close_matches(token, unique_apts, n=3, cutoff=0.6):
                mask |= (apt_names == name)
            for name in get_close_matches(token, unique_umds, n=3, cutoff=0.6):
                mask |= (umd_names == name)

    matched = df[mask]
    if matched.empty:
        return ""

    cols = ["aptNm", "umdNm", "dealAmount", "buildYear", "excluUseAr", "dealYear", "dealMonth"]
    matched = matched[cols].copy()
    matched.columns = ["단지명", "법정동", "거래금액_만원", "건축년도", "전용면적_m2", "년", "월"]
    matched = matched.sort_values(["년", "월"], ascending=False).head(limit)

    return (
        "\n\n[질문과 관련된 단지 최근 실거래 내역 (국토교통부 실거래가 데이터)]\n"
        + matched.to_csv(index=False)
    )

def find_region_context(message: str, df: pd.DataFrame, sido_summary: pd.DataFrame) -> str:
    """메시지에 언급된 시군구/시도를 찾아 해당 행만 따로 추려 컨텍스트로 제공한다.
    전체 표를 한꺼번에 주면 작은 모델이 시도 합계와 시군구 개별값을 혼동하므로,
    질문 대상 지역의 정확한 행을 별도로 강조해 전달한다."""
    import re

    tokens = {t for t in re.split(r"[^\w가-힣]+", message) if len(t) >= 2 and t not in CHAT_STOPWORDS}
    if not tokens:
        return ""

    sigungu_mask = pd.Series(False, index=df.index)
    sido_mask = pd.Series(False, index=sido_summary.index)
    for token in tokens:
        sigungu_mask |= df["시군구"].str.contains(token, na=False, regex=False)
        sido_mask |= sido_summary["시도"].str.contains(token, na=False, regex=False)

    parts = []
    matched_sigungu = df[sigungu_mask].copy()
    if not matched_sigungu.empty:
        score = pd.to_numeric(df["인테리어_수요점수"], errors="coerce")
        matched_sigungu["전국순위"] = score.rank(ascending=False, method="min").loc[matched_sigungu.index].astype(int)
        matched_sigungu["시도내순위"] = (
            df.groupby("시도")["인테리어_수요점수"].rank(ascending=False, method="min")
            .loc[matched_sigungu.index].astype(int)
        )
        matched_sigungu["시도지역수"] = matched_sigungu["시도"].map(df.groupby("시도").size())
        matched_sigungu["등급"] = pd.cut(
            pd.to_numeric(matched_sigungu["인테리어_수요점수"], errors="coerce"),
            bins=[-np.inf, 30, 60, np.inf], labels=["B", "A", "S"], right=False,
        ).astype(str)
        for column, label, _ in REGION_METRICS:
            percentile = pd.to_numeric(df[column], errors="coerce").rank(pct=True) * 100
            matched_sigungu[f"{label}_전국백분위"] = percentile.loc[matched_sigungu.index].round(1)
        region_lines = []
        for _, row in matched_sigungu.iterrows():
            region_lines.extend([
                f"지역={row['시도']} {row['시군구']} | 수요점수={float(row['인테리어_수요점수']):.1f}점 | "
                f"등급={row['등급']} | 전국순위={int(row['전국순위'])}위/{len(df)}개 | "
                f"시도내순위={int(row['시도내순위'])}위/{int(row['시도지역수'])}개",
                f"핵심지표: 매매 거래={int(row['거래건수']):,}건 | 평균 거래금액={float(row['평균거래금액_만원']):,.1f}만원 | "
                f"전월세 거래={int(row['전월세거래건수']):,}건 | 평균 노후도={float(row['평균노후도_년']):.1f}년 | "
                f"평균 면적={float(row['평균면적_m2']):.1f}㎡ | 신규 입주={int(row['신규입주_세대수']):,}세대 | "
                f"대수선 이력={int(row['대수선이력건수']):,}건 | 추정 시장규모={float(row['시장규모_추정_억']):,.1f}억원",
                "전국백분위: " + " | ".join(
                    f"{label}={float(row[f'{label}_전국백분위']):.1f}"
                    for _, label, _ in REGION_METRICS
                ),
                f"필수 사실: {row['시군구']} 수요점수는 {float(row['인테리어_수요점수']):.1f}점이며 "
                f"전국 {int(row['전국순위'])}위, {row['시도']} 내 {int(row['시도내순위'])}위입니다.",
            ])
        parts.append(
            "[질문에 언급된 시군구의 정확한 데이터 — 순위·등급·백분위를 그대로 사용하고 새 원인을 추론하지 마세요]\n"
            + "\n".join(region_lines)
        )

    matched_sido = sido_summary[sido_mask]
    if not matched_sido.empty:
        sido_lines = []
        for _, row in matched_sido.iterrows():
            sido_lines.append(" | ".join(f"{column}={row[column]}" for column in matched_sido.columns))
        parts.append(
            "[질문에 언급된 시/도의 전체 요약 데이터 — 시/도 전체 합계·평균값이며, 개별 시군구의 값이 아닙니다]\n"
            + "\n".join(sido_lines)
        )

    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


def build_ranking_summary(df: pd.DataFrame, top_n: int = 10) -> str:
    """수요 점수 상위/하위 지역을 명확한 순위 텍스트로 정리한다.
    작은 모델이 63행짜리 전체 표에서 직접 최댓값을 찾는 데 자주 실패하므로,
    '몇 위 = 어디, 몇 점'을 미리 계산해 명시적으로 알려준다."""
    lines = ["[수요 점수 상위 지역 순위 — 반드시 이 순위를 기준으로 답변하세요]"]
    for i, (_, row) in enumerate(df.head(top_n).iterrows(), start=1):
        lines.append(
            f"{i}위: {row['시도']} {row['시군구']} "
            f"(인테리어_수요점수 {row['인테리어_수요점수']}점, "
            f"거래건수 {int(row['거래건수']):,}건, "
            f"전월세거래건수 {int(row['전월세거래건수']):,}건, "
            f"대수선이력건수 {int(row['대수선이력건수']):,}건, "
            f"평균노후도 {row['평균노후도_년']}년)"
        )
    lines.append("")
    lines.append("[수요 점수 하위 지역 순위]")
    bottom = df.tail(5).iloc[::-1]
    for _, row in bottom.iterrows():
        rank = int(df["인테리어_수요점수"].rank(ascending=False, method="min")[row.name])
        lines.append(f"{rank}위: {row['시도']} {row['시군구']} (인테리어_수요점수 {row['인테리어_수요점수']}점)")
    return "\n".join(lines)


DEMAND_CHAT_SYSTEM_PROMPT = """당신은 오늘의집 O2O 인테리어 수요 분석 도우미입니다.
반드시 한국어로만, 결론부터 간결하게 답하세요.

규칙:
1. [근거 데이터]에 있는 수치만 사용하고 없는 사실은 추측하지 마세요.
2. 계산, 순위 결정, 단위 변환을 새로 하지 마세요. 코드가 제공한 결과를 그대로 설명하세요.
3. 시도 전체 합계와 시군구 개별값을 혼동하지 마세요.
4. 지역의 주거 특성, 주민 성향, 상권 등 제공되지 않은 배경 원인을 상식으로 덧붙이지 마세요.
5. '가장 높다', '1위'는 근거 데이터의 순위가 실제 1일 때만 사용하세요.
6. 답변은 보통 3~6문장으로 작성하고, 핵심 수치 2~4개를 포함하세요.
7. 데이터가 없으면 '현재 데이터에서 확인할 수 없습니다'라고 명확히 답하세요.
8. 이모지, 과도한 인사, 같은 결론의 반복은 사용하지 마세요.

수요 점수는 0~100점이며 매매 거래 20%, 평균 거래금액 15%, 평균 노후도 15%, 평균 면적 10%,
신규 입주 10%, 전월세 거래 20%, 대수선 이력 10%를 정규화해 합산합니다.
등급은 S 60점 이상, A 30~59.9점, B 30점 미만입니다.
거래건수와 전월세거래건수는 15~20년 아파트 표본이며, 시장규모와 예상시공비는 추정치입니다."""


GUIDE_CHAT_SYSTEM_PROMPT = """당신은 생애 첫 주택 구매자를 돕는 한국어 가이드입니다.
결론부터 쉽고 차분하게 답하고, 절차 질문은 3~7개의 번호 목록으로 정리하세요.
사용자가 알려준 예산·지역·소득 조건만 사용하며 모르는 조건을 지어내지 마세요.
금리, 세율, LTV·DSR, 정책대출 자격처럼 바뀔 수 있는 값은 단정하지 말고 기준일과 공식 확인 필요성을 알리세요.
법률·세무·대출의 최종 판단은 은행, 법무사, 세무사 등 전문가 확인이 필요하다고 안내하세요.
과도한 인사와 이모지는 사용하지 말고 답변은 700자 안팎으로 제한하세요.

표준 흐름: 자금 계획 → 매물·실거래 확인 → 등기부·건축물대장 확인 → 계약 및 특약 → 대출 실행 준비
→ 잔금과 소유권 이전 → 취득세·전입·보험 등 사후 절차."""


def build_demand_context(message: str, df: pd.DataFrame, sido_summary: pd.DataFrame) -> str:
    """3B 모델에 전체 표 대신 질문에 필요한 결정론적 검색 결과만 제공한다."""
    score = pd.to_numeric(df["인테리어_수요점수"], errors="coerce")
    parts = [
        "[근거 데이터: 전체 요약]",
        f"분석 지역 수: {len(df)}개",
        f"수요 점수 평균: {score.mean():.1f}점",
        f"데이터 기준: 2025-07~2026-06",
    ]

    ranking_words = ("순위", "상위", "하위", "최고", "최저", "가장", "추천", "어디")
    if any(word in message for word in ranking_words):
        parts.extend(["", build_ranking_summary(df)])

    region_context = find_region_context(message, df, sido_summary).strip()
    if region_context:
        parts.extend(["", region_context])

    apartment_context = find_apartment_context(message).strip()
    if apartment_context:
        parts.extend(["", apartment_context])

    if not region_context and not apartment_context and not any(word in message for word in ranking_words):
        parts.extend([
            "",
            "질문에서 특정 지역이나 단지를 찾지 못했습니다. 일반적인 점수 산식 설명만 가능하며 지역 수치는 답하지 마세요.",
        ])
    return "\n".join(parts)


def _clean_llm_answer(answer: str) -> str:
    """사고 태그와 불필요한 공백을 제거하고 비정상 응답을 거부한다."""
    import re

    answer = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.DOTALL | re.IGNORECASE)
    answer = re.sub(r"\n{3,}", "\n\n", answer).strip()
    if not answer:
        raise ValueError("AI가 빈 답변을 반환했습니다.")
    return answer[:4000]


def _audit_demand_answer(answer: str) -> str:
    """데이터에 없는 지역 이미지를 원인으로 붙인 문장을 최종 응답에서 제외한다."""
    import re

    unsupported_phrases = (
        "고급 주거지", "상권 활성", "상업 시설", "시설 밀집", "주민 성향",
        "투자 성향", "부유층", "소득 수준", "알려져 있", "잠재적 성장",
        "고객 만족도", "신뢰성을 높",
    )
    sentences = re.split(r"(?<=[.!?。])\s+", answer.strip())
    grounded = [
        sentence for sentence in sentences
        if sentence and not any(phrase in sentence for phrase in unsupported_phrases)
    ]
    return " ".join(grounded).strip() or "제공된 데이터만으로는 해당 질문에 답하기 어렵습니다."


def _ollama_chat(messages: list[dict], num_ctx: int = 4096, num_predict: int = 512) -> str:
    """Qwen 3B에 맞춘 공통 Ollama 호출 경로."""
    res = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": CHAT_MODEL,
            "messages": messages,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": 0.15,
                "top_p": 0.85,
                "repeat_penalty": 1.12,
                "seed": 42,
            },
        },
        timeout=CHAT_TIMEOUT,
    )
    res.raise_for_status()
    return _clean_llm_answer(res.json().get("message", {}).get("content", ""))


@app.route("/api/region-insight", methods=["POST"])
def region_insight():
    """선택 지역의 확정 지표만 사용해 Qwen이 짧은 실행 해설을 생성한다."""
    key = ""
    try:
        body = request.get_json() or {}
        key = str(body.get("key", "")).strip()
        if not key or "|" not in key or len(key) > 50:
            return jsonify({"status": "error", "message": "정확한 지역을 먼저 선택하세요."}), 400

        catalog = _region_catalog()
        matched = catalog[catalog["key"] == key]
        if matched.empty:
            return jsonify({"status": "error", "message": "분석 대상 지역을 찾을 수 없습니다."}), 404

        row = matched.iloc[0]
        ranked = catalog.sort_values("인테리어_수요점수", ascending=False).reset_index(drop=True)
        overall_rank = int(ranked.index[ranked["key"] == key][0]) + 1
        summary = row.to_dict()
        metrics = []
        for column, label, unit in REGION_METRICS:
            values = pd.to_numeric(catalog[column], errors="coerce")
            percentile = float(values.rank(pct=True).loc[row.name] * 100)
            metrics.append({"label": label, "value": float(row[column]), "unit": unit, "percentile": percentile})
        decision = _build_region_analysis(summary, metrics)
        grade = "S" if float(row["인테리어_수요점수"]) >= 60 else "A" if float(row["인테리어_수요점수"]) >= 30 else "B"

        evidence = "\n".join(
            f"- {item['label']}: {item['value']:,.1f}{item['unit']} / 전국 {item['percentile']:.0f}백분위"
            for item in metrics
        )
        prompt = f"""[확정된 지역 데이터]
지역: {row['name']}
수요 점수: {float(row['인테리어_수요점수']):.1f}점
확정 등급: {grade}등급
전국 순위: {overall_rank}위 / {len(catalog)}개
코드 기반 진입 판단: {decision['recommendation']}
추정 시장규모: {float(row['시장규모_추정_억']):,.1f}억원
{evidence}
확정 강점: {' / '.join(decision['strengths'])}
유의 지표: {' / '.join(decision['risks'])}

위 수치만 사용해 이 지역의 인테리어 사업 관점 해설을 작성하세요.
첫 문장은 반드시 '{row['name']}은 {decision['recommendation']} 지역입니다.'로 시작하세요.
이후 '근거' 2개와 '실행 제안' 2개를 작성하며 총 7문장 이내입니다.
새 숫자를 계산하거나 등급·순위·진입 판단을 바꾸지 마세요. 제공되지 않은 지역 특성은 언급하지 마세요."""
        answer = _audit_demand_answer(_ollama_chat(
            [
                {"role": "system", "content": DEMAND_CHAT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            num_ctx=3072,
            num_predict=420,
        ))
        save_chat_log(f"[지역 AI 해설] {row['name']}", answer, "ok", chat_type="region")
        return jsonify({"status": "ok", "insight": answer, "model": CHAT_MODEL})
    except requests.exceptions.ConnectionError:
        save_chat_log(f"[지역 AI 해설] {key}", None, "error: ollama_connection", chat_type="region")
        return jsonify({"status": "error", "message": "로컬 AI 서버에 연결할 수 없습니다."}), 503
    except requests.exceptions.Timeout:
        save_chat_log(f"[지역 AI 해설] {key}", None, "error: ollama_timeout", chat_type="region")
        return jsonify({"status": "error", "message": "AI 응답 시간이 초과되었습니다."}), 504
    except Exception as e:
        save_chat_log(f"[지역 AI 해설] {key}", None, f"error: {e}", chat_type="region")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    message = ""
    try:
        body = request.get_json() or {}
        message = body.get("message", "").strip()

        if not message:
            return jsonify({"status": "error", "message": "메시지를 입력하세요."}), 400
        if len(message) > CHAT_MAX_MESSAGE:
            return jsonify({"status": "error", "message": f"질문은 {CHAT_MAX_MESSAGE}자 이내로 입력하세요."}), 400

        df = pd.read_csv("data/인테리어_수요점수_결과.csv", encoding="utf-8-sig")
        df = df.sort_values("인테리어_수요점수", ascending=False)
        sido_summary = pd.read_csv("data/시도별_수요집계_요약.csv", encoding="utf-8-sig")

        system_prompt = (
            "당신은 '오늘의집 O2O 인테리어 수요 예측 대시보드'의 친절한 데이터 분석 도우미입니다. "
            "항상 따뜻하고 다정한 말투로, 처음 보는 사람도 이해할 수 있도록 쉽게 풀어서 한국어로 답변하세요. "
            "숫자만 툭 던지지 말고, 왜 그 지역의 점수가 높은지/낮은지 '근거 원인'까지 함께 설명해주세요. "
            "예를 들어 '거래건수가 OO건으로 많고, 전월세거래건수도 OO건으로 활발해서 점수가 높습니다'처럼 "
            "그 지역의 어떤 세부 지표(거래건수, 거래금액, 노후도, 면적, 신규입주, 전월세거래건수, 대수선이력건수) 값이 "
            "다른 지역에 비해 두드러지는지 구체적인 숫자를 들어 설명하세요. "
            "'순위'나 '최고/최저'를 묻는 질문에는 아래 [수요 점수 상위/하위 지역 순위] 블록의 순서를 그대로 사용하세요 "
            "(직접 표를 다시 계산하거나 추측하지 마세요). "
            "지역별 수치를 묻는 질문은 아래 데이터를 근거로 답하고, "
            "'인테리어 수요 점수가 무엇인지/어떻게 계산되는지' 같은 질문은 아래 [인테리어 수요 점수 산출 방식] 설명을 활용해 "
            "단계별로 자세하고 친절하게 설명하세요. "
            "사용자가 특정 아파트 단지명이나 주소(법정동)를 언급하면, 아래 [질문과 관련된 단지 최근 실거래 내역]을 참고해 "
            "해당 단지의 최근 거래금액, 건축년도, 전용면적 등 정보를 알려주세요. "
            "필요하면 예시를 들어 설명하고, 적절히 이모지를 곁들여도 좋습니다. "
            "데이터에 없는 내용은 추측하지 말고 모른다고 솔직하게 답하세요. "
            "'~것으로 보입니다', '~인 것 같습니다', '~로 추정됩니다'(예상시공비/시장규모처럼 원래 추정치인 "
            "컬럼은 예외) 같은 모호한 말투는 쓰지 마세요. 데이터에 있는 값은 단정적으로("
            "'~입니다', '~건입니다') 말하고, 데이터에 없으면 '~데이터가 없습니다'라고 명확히 말하세요.\n\n"
            "[매우 중요 — 표 혼동 금지 규칙]\n"
            "이 시스템에는 두 종류의 표가 있습니다. 절대 서로 혼동하지 마세요.\n"
            "  1) [시군구별 인테리어 수요 점수]: '시군구'(예: 서울특별시 서초구) 단위의 개별 데이터입니다. "
            "사용자가 특정 시(군/구) 이름을 언급하면 반드시 이 표에서 해당 행만 찾아 답하세요.\n"
            "  2) [시도별 요약]: '시도'(서울/경기/인천 등) 전체의 합계·평균·최고값입니다. "
            "이 값들은 그 시/도에 속한 여러 시군구를 모두 합치거나 평균낸 값이므로, "
            "개별 시군구(예: 서초구)의 거래건수나 점수로 절대 사용하면 안 됩니다. "
            "예를 들어 '서울'의 총거래건수·총신규입주세대수는 서울 전체 25개 구의 합계이며, "
            "'서초구' 한 곳의 값이 아닙니다.\n"
            "  3) 만약 아래에 [질문에 언급된 시군구의 정확한 데이터] 또는 [질문에 언급된 시/도의 전체 요약 데이터] 블록이 있다면, "
            "그 블록의 값을 최우선으로 사용하세요.\n\n"
            "[인테리어 수요 점수 산출 방식]\n"
            "아파트 실거래 데이터를 분석해 '어느 지역에 인테리어 수요가 얼마나 있는가'를 0~100점으로 수치화한 지표입니다. "
            "점수가 높을수록 리모델링 수요가 많고, 구매력이 높고, 거래가 활발한 지역입니다.\n\n"
            "1단계. 핵심 타겟 아파트 추출 — 건축연식(노후도)에 따라 4단계로 분류하고, "
            "15~20년 된 'Old_Apartment'만 분석 대상으로 삼습니다.\n"
            "  - New_Apartment (0~5년): 가구·소품 인테리어 수요\n"
            "  - Mid_Apartment (6~14년): 벽지·바닥재 부분 교체 수요\n"
            "  - Old_Apartment ★ (15~20년, 핵심 타겟): 욕실·주방·바닥재 등 전면 리모델링 수요. "
            "1기 신도시 재정비 연식대와 겹쳐 리모델링 관심이 가장 높고 고단가 시공 상품 구매 가능성이 높음\n"
            "  - Very_Old_Apartment (21년+): 재건축 검토 단계, 인테리어 시공 수요 낮음\n\n"
            "2단계. 7가지 지표를 0~100점으로 정규화(Min-Max) 후 가중치를 곱해 합산\n"
            "  - 거래건수 (가중치 20%): 매매 시장 활성도, 도달 가능 고객 수\n"
            "  - 거래금액 (가중치 15%): 구매력, 고단가 시공 가능성\n"
            "  - 노후도 (가중치 15%): 리모델링 시급성\n"
            "  - 전용면적 (가중치 10%): 시공 규모, 매출 기여도\n"
            "  - 신규입주 (가중치 10%): 신규 입주 세대의 인테리어 수요\n"
            "  - 전월세거래건수 (가중치 20%): 임대(전월세) 거래가 많을수록 새 세입자가 입주 전 부분 인테리어를 하는 수요가 많음\n"
            "  - 대수선이력건수 (가중치 10%): 건축물대장 대수선(증축·구조변경·마감재 교체 등) 허가 이력이 많을수록 "
            "리모델링 시장이 활발한 지역\n\n"
            "3단계. 최종 점수 계산식\n"
            "  인테리어_수요점수 = 거래건수점수×0.20 + 거래금액점수×0.15 + 노후도점수×0.15 + 면적점수×0.10 "
            "+ 신규입주점수×0.10 + 전월세거래건수점수×0.20 + 대수선이력점수×0.10\n\n"
            "등급 해석\n"
            "  - S등급 (60~100점): 최우선 타겟, 마케팅 예산 집중\n"
            "  - A등급 (30~59점): 중간 타겟, 선별적 마케팅\n"
            "  - B등급 (0~29점): 관찰 지역, 투자 보류\n\n"
            "[매우 중요 — 컬럼명을 글자 그대로 정확히 읽으세요]\n"
            "각 컬럼은 이름이 비슷해도 서로 다른 값입니다. 절대 다른 컬럼의 값을 가져와 쓰지 마세요.\n"
            "  - '거래건수'와 '전월세거래건수'는 완전히 다른 컬럼입니다 (매매 거래 수 vs 전월세 거래 수). "
            "같은 행이라도 두 값이 다를 수 있으니 질문에 맞는 컬럼만 정확히 골라 답하세요.\n"
            "  - 컬럼명에 단위가 적혀 있으면(예: '_만원', '_년', '_m2') 그 단위가 곧 데이터의 단위입니다. "
            "절대 임의로 100을 곱하거나 나누는 등 추가 환산을 하지 마세요. "
            "예: '평균거래금액_만원' 값이 83920.1이면 그대로 '83,920.1만원(약 8억 3,920만원)'이라고 답하세요. "
            "'84,000원'이나 '839.2만원'처럼 자체적으로 단위를 바꿔 계산하면 안 됩니다.\n\n"
            "[데이터 컬럼 설명 — 시군구별 표]\n"
            "- 인테리어_수요점수: 위 산출 방식으로 계산된 0~100점 값 (해당 시군구 자체의 점수)\n"
            "- 거래건수: 해당 시군구의 노후도 15~20년(Old_Apartment) 세그먼트 '매매' 거래 건수\n"
            "- 평균거래금액_만원: 해당 시군구 Old_Apartment 평균 매매가, 단위는 '만원'(예: 83920.1 → 83,920.1만원)\n"
            "- 평균노후도_년 / 평균면적_m2: 해당 시군구 Old_Apartment 평균값\n"
            "- 신규입주_세대수 / 입주단지수: 해당 시군구의 입주예정 신규 세대수 / 단지 수\n"
            "- 전월세거래건수: 해당 시군구의 노후도 15~20년(Old_Apartment) 세그먼트 '전월세' 거래 건수 (거래건수와 다른 값)\n"
            "- 대수선이력건수: 해당 시군구의 건축물대장 대수선 허가 이력 건수 (전체 건축물 대상)\n"
            "- 인테리어업체수: 전국인테리어업체표준데이터(공공데이터) 기준 등록 인테리어 업체 수. "
            "지자체 자율등록 방식이라 일부 지역만 값이 있고 대부분 0으로 나타남 — 0이어도 '업체가 없다'가 아니라 "
            "'등록 데이터가 없다'는 뜻이므로 그렇게 안내하세요.\n"
            "- 소상공인_인테리어업체수: 소상공인시장진흥공단 상가(상권)정보 기준, 인테리어 디자인업·건축자재·가구 "
            "소매 등 인테리어 관련 업종 상가 수. 전국 102개 시군구 전체에 값이 있어 인테리어업체수보다 신뢰도 높은 "
            "지표입니다. 해당 지역의 인테리어 관련 사업체/경쟁 밀도를 물으면 이 값을 우선 사용하세요.\n"
            "- 예상시공비_하한_만원 / 예상시공비_상한_만원: 해당 시군구 평균 면적을 평으로 환산해 추정한 1세대당 "
            "예상 시공비 범위(만원). 하한은 올수리 기준 평당 150만원, 상한은 평당 220만원(노후도 20년 이상이면 "
            "300만원)을 적용한 비공식 참고용 추정치입니다. '예상 시공비', '평당 비용', '인테리어 비용'을 물으면 "
            "이 두 값을 범위로 안내하세요.\n"
            "- 시장규모_추정_억: 거래건수 × 예상 시공비 중간값으로 추정한 해당 시군구의 인테리어 시장 규모(억원). "
            "'시장 규모', '시장이 얼마나 큰지'를 물으면 이 값을 사용하세요.\n"
            "- 총인구수: 행정안전부 주민등록 인구통계 기준 해당 시군구의 총인구수(명).\n"
            "- 청년인구비율: 해당 시군구 인구 중 20~30대(20~39세) 비율(%). 비율이 높을수록 신혼·1인가구 등 "
            "소형 평수 인테리어 수요가 상대적으로 많을 것으로 참고할 수 있는 지표입니다.\n"
            "- 고령인구비율: 해당 시군구 인구 중 60대 이상(60세+) 비율(%). 비율이 높으면 안전바·미끄럼방지 등 "
            "시니어 친화적 인테리어 수요를 참고할 수 있는 지표입니다.\n\n"
            "[데이터 컬럼 설명 — 시도별 요약 표]\n"
            "- 시군구수: 해당 시/도에 속한 시군구의 개수\n"
            "- 총거래건수: 해당 시/도에 속한 모든 시군구 거래건수의 합계\n"
            "- 평균수요점수 / 최고수요점수: 해당 시/도에 속한 시군구들의 인테리어_수요점수 평균값 / 최고값\n"
            "- 총신규입주세대수: 해당 시/도에 속한 모든 시군구 신규입주_세대수의 합계\n\n"
            "반드시 한국어로만 답변하세요. 다른 언어(영어, 중국어, 일본어 등)는 절대 사용하지 마세요."
        )

        # 3B 모델에는 전체 CSV 대신 질문과 관련된 행과 사전 계산 순위만 전달한다.
        system_prompt = DEMAND_CHAT_SYSTEM_PROMPT
        data_context = build_demand_context(message, df, sido_summary)

        grounded_question = (
            f"{data_context}\n\n"
            f"[사용자 질문]\n{message}\n\n"
            "[답변 직전 검수]\n"
            "- 점수·등급·전국순위·시도순위는 위 근거와 한 글자도 다르게 쓰지 마세요.\n"
            "- 높은/낮은 이유는 위 전국백분위 지표만 사용하세요.\n"
            "- 고급 주거지, 상권, 주민 성향, 투자 성향, 시설 밀집처럼 근거에 없는 설명은 금지합니다.\n"
            "- 근거 데이터에 없는 명사를 원인으로 추가하지 마세요."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": grounded_question},
        ]

        answer = _audit_demand_answer(_ollama_chat(messages))

        save_chat_log(message, answer, "ok")
        return jsonify({"status": "ok", "answer": answer})

    except requests.exceptions.ConnectionError:
        save_chat_log(message, None, "error: ollama_connection")
        return jsonify({"status": "error", "message": f"Ollama 서버({OLLAMA_HOST})에 연결할 수 없습니다."}), 500
    except requests.exceptions.Timeout:
        save_chat_log(message, None, "error: ollama_timeout")
        return jsonify({"status": "error", "message": "AI 응답 시간이 초과되었습니다. 질문을 조금 짧게 다시 시도하세요."}), 504
    except Exception as e:
        save_chat_log(message, None, f"error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/guide-chat", methods=["POST"])
def guide_chat():
    message = ""
    try:
        body = request.get_json() or {}
        message = body.get("message", "").strip()

        if not message:
            return jsonify({"status": "error", "message": "메시지를 입력하세요."}), 400
        if len(message) > CHAT_MAX_MESSAGE:
            return jsonify({"status": "error", "message": f"질문은 {CHAT_MAX_MESSAGE}자 이내로 입력하세요."}), 400

        system_prompt = (
            "당신은 '첫 집 구매 가이드' 챗봇입니다. 사회초년생이나 생애 첫 주택 구매자가 막연하고 두려운 "
            "내 집 마련 과정을 쉽고 친절하게 이해할 수 있도록 돕는 것이 목표입니다. "
            "항상 따뜻하고 다정한 말투로, 어려운 용어는 풀어서 설명하고, 필요하면 구체적인 절차를 "
            "번호를 매겨 단계별로 안내하세요. 적절히 이모지를 곁들여도 좋습니다.\n\n"
            "[주택 매수 절차 — 표준 흐름]\n"
            "1단계. 예산·자금 계획 — 보유 자금, 신용대출, 주택담보대출(LTV/DTI/DSR 한도) 등을 따져 "
            "구매 가능한 가격 범위를 정한다. 생애최초 구매자는 LTV 우대, 디딤돌대출/보금자리론 등 "
            "정책금융 상품을 확인하면 유리하다.\n"
            "2단계. 매물 탐색 — 직방/네이버부동산/호갱노노 등으로 시세 파악, 관심 지역의 실거래가 확인. "
            "임장(현장 방문)을 통해 채광, 소음, 주변 인프라, 건물 노후도를 직접 확인한다.\n"
            "3단계. 가계약/계약금 — 매물이 정해지면 보통 매매가의 5~10%를 계약금으로 지급하고 가계약서를 "
            "작성한다. 계약 전 등기부등본(을구 근저당 확인), 건축물대장, 토지이용계획확인서를 반드시 확인한다.\n"
            "4단계. 본계약(매매계약서 작성) — 공인중개사를 통해 매도인과 매매계약서를 작성하고 계약금을 "
            "최종 지급한다. 특약사항(잔금일, 인도일, 하자 처리 등)을 꼼꼼히 확인한다.\n"
            "5단계. 중도금 — 계약 금액이 클 경우 중도금을 지급하며, 이 시점에 대출 상담/한도 조회를 "
            "구체화해 잔금일에 맞춰 대출 실행을 준비한다.\n"
            "6단계. 잔금 지급 및 등기 이전 — 잔금일에 나머지 금액을 지급하고 동시에 소유권이전등기를 "
            "신청한다(법무사 대행 일반적). 이때 취득세(주택 가격에 따라 1~3%+지방교육세 등)를 납부해야 한다.\n"
            "7단계. 입주 및 전입신고 — 잔금 지급 후 입주하며, 14일 이내 전입신고를 하면 전세권/대항력 "
            "관련 권리가 보호된다(전세/임차인 입장에서 특히 중요).\n"
            "8단계. 사후 관리 — 재산세(매년 6/1 기준 소유자에게 부과), 장기수선충당금(아파트의 경우 "
            "관리비에 포함), 화재보험 가입 등을 챙긴다.\n\n"
            "[자주 헷갈리는 용어]\n"
            "- LTV(주택담보대출비율): 집값 대비 대출 가능 비율\n"
            "- DSR(총부채원리금상환비율): 연소득 대비 모든 대출의 연간 원리금 상환액 비율, 대출 한도에 영향\n"
            "- 등기부등본: 부동산의 소유권/권리관계(근저당, 압류 등)를 기록한 공식 문서, 계약 전 필수 확인\n"
            "- 중개수수료: 매매가에 따라 법정 한도 내에서 중개사와 협의 (보통 0.4~0.9% 수준)\n"
            "- 취득세: 매수 시 납부하는 지방세, 주택 가격·규모·보유 주택 수에 따라 1~3% 이상 차등\n\n"
            "사용자가 자신의 상황(예산, 지역, 생애최초 여부 등)을 알려주면 그에 맞춰 구체적으로 안내하고, "
            "법률/세무/대출의 최종 판단은 법무사·세무사·은행 등 전문가 상담이 필요하다는 점도 안내하세요. "
            "데이터에 없는 최신 법령/금리/세율은 변동될 수 있으니 반드시 최신 정보를 직접 확인하라고 안내하세요. "
            "반드시 한국어로만 답변하세요. 다른 언어(영어, 중국어, 일본어 등)는 절대 사용하지 마세요."
        )

        system_prompt = GUIDE_CHAT_SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

        answer = _ollama_chat(messages, num_ctx=4096, num_predict=640)

        save_chat_log(message, answer, "ok", chat_type="guide")
        return jsonify({"status": "ok", "answer": answer})

    except requests.exceptions.ConnectionError:
        save_chat_log(message, None, "error: ollama_connection", chat_type="guide")
        return jsonify({"status": "error", "message": f"Ollama 서버({OLLAMA_HOST})에 연결할 수 없습니다."}), 500
    except requests.exceptions.Timeout:
        save_chat_log(message, None, "error: ollama_timeout", chat_type="guide")
        return jsonify({"status": "error", "message": "AI 응답 시간이 초과되었습니다. 질문을 조금 짧게 다시 시도하세요."}), 504
    except Exception as e:
        save_chat_log(message, None, f"error: {e}", chat_type="guide")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    # 같은 와이파이의 다른 기기(모바일 등)에서 접속하려면
    # .env에 FLASK_HOST=0.0.0.0 을 설정할 것 (기본값은 로컬만 허용)
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "8300")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
