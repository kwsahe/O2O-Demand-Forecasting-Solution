from flask import Flask, jsonify, request, render_template
import pandas as pd
import os
import sys

sys.path.append(os.path.dirname(__file__))

from src.collector import ApartmentDataCollector, SEOUL_SIGUNGU_CODES
from src.pipeline import DemandForecastingPipeline

app = Flask(__name__)

API_KEY = os.getenv("API_KEY", "")

print("Flask 앱 초기화 완료 ✅")

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/demand", methods=["GET"])
def get_demand():
    try:
        df = pd.read_csv("data/인테리어_수요점수_결과.csv", encoding="utf-8-sig")

        sido = request.args.get("sido", None)
        if sido:
            df = df[df["시도"] == sido]

        top = int(request.args.get("top", 25))
        df = df.head(top)

        return jsonify({
            "status": "ok",
            "count": len(df),
            "data": df.to_dict(orient="records"),
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route("/api/collect", methods=["POST"])
def collect():
    try:
        body = request.get_json() or {}
        months = int(body.get("months", 12))
        sigungu_code = body.get("sigungu_code", None)

        if not API_KEY:
            return jsonify({"status": "error", "message": "API 키가 설정되지 않았습니다."}), 400

        collector = ApartmentDataCollector(api_key=API_KEY)

        if sigungu_code:
            codes = {k: v for k, v in SEOUL_SIGUNGU_CODES.items() if v == sigungu_code}
        else:
            codes = SEOUL_SIGUNGU_CODES

        # 수집 & 정규화
        df_raw = collector.fetch_recent_months(
            sigungu_codes=codes,
            months=months,
            save_path="data/raw_api_collected.csv"
        )
        df_normalized = collector.normalize_columns(df_raw)

        # 파이프라인 실행
        pipeline = DemandForecastingPipeline(
            supply_path="data/한국부동산원_주택공급정보_입주예정물량정보_20251231.csv"
        )
        df_result, sido_summary = pipeline.run(df_transactions=df_normalized)

        # 결과 저장
        df_result.to_csv("data/인테리어_수요점수_결과.csv", index=False, encoding="utf-8-sig")
        sido_summary.to_csv("data/시도별_수요집계_요약.csv", index=False, encoding="utf-8-sig")

        return jsonify({
            "status": "ok",
            "collected": len(df_raw),
            "top5": df_result.head(5).to_dict(orient="records"),
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@app.route("/api/search", methods=["GET"])
def search():
    try:
        q = request.args.get("q", "")
        if not q:
            return jsonify({"status": "error", "message": "검색어를 입력하세요."}), 400

        df = pd.read_csv("data/인테리어_수요점수_결과.csv", encoding="utf-8-sig")

        mask = (
            df["시도"].str.contains(q, na=False) |
            df["시군구"].str.contains(q, na=False)
        )
        df_filtered = df[mask]

        return jsonify({
            "status": "ok",
            "query": q,
            "count": len(df_filtered),
            "data": df_filtered.to_dict(orient="records"),
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",  # 외부 접속 허용 (배포 시 필요)
        port=5000,
        debug=True,      # 코드 변경 시 자동 재시작
    )