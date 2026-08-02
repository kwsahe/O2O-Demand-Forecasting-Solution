"""app.py Flask API에 대한 회귀 테스트.

data/ 아래 실제 결과 CSV가 존재해야 하는 통합 테스트 성격이 강하다
(이 저장소에는 이미 결과물이 생성돼 있어 로컬에서 바로 돌아간다).
/api/collect처럼 실제 공공데이터 API를 호출하는 엔드포인트는 여기서 실행하지 않고,
그 안에서 쓰이는 순수 헬퍼 함수(_upsert_raw, _atomic_write_csv)만 별도로 검증한다.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import app as app_module

DATA_MISSING = not os.path.exists("data/인테리어_수요점수_결과.csv")
FORECAST_MISSING = not os.path.exists("data/timeseries_forecast_result.json")


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.mark.skipif(DATA_MISSING, reason="data/인테리어_수요점수_결과.csv 없음")
class TestDemandApi:
    def test_top_non_integer_returns_400(self, client):
        res = client.get("/api/demand?top=abc")
        assert res.status_code == 400
        assert res.get_json()["status"] == "error"

    def test_top_negative_returns_400(self, client):
        res = client.get("/api/demand?top=-1")
        assert res.status_code == 400

    def test_top_zero_returns_400(self, client):
        res = client.get("/api/demand?top=0")
        assert res.status_code == 400

    def test_default_returns_ok(self, client):
        res = client.get("/api/demand")
        body = res.get_json()
        assert res.status_code == 200
        assert body["status"] == "ok"
        assert body["count"] <= 25

    def test_top_limits_row_count(self, client):
        res = client.get("/api/demand?top=3")
        body = res.get_json()
        assert body["count"] <= 3


@pytest.mark.skipif(DATA_MISSING, reason="data/인테리어_수요점수_결과.csv 없음")
class TestSearchApi:
    def test_missing_query_returns_400(self, client):
        res = client.get("/api/search?type=region")
        assert res.status_code == 400

    def test_query_too_long_returns_400(self, client):
        res = client.get("/api/search?" + "q=" + "가" * 51 + "&type=region")
        assert res.status_code == 400

    def test_bracket_query_does_not_500(self, client):
        """정규식 특수문자가 포함된 검색어가 regex 인젝션으로 500을 내지 않아야 한다."""
        res = client.get("/api/search?q=%5B&type=region")
        assert res.status_code == 200
        body = res.get_json()
        assert body["status"] == "ok"
        assert body["count"] == 0

    def test_region_search_returns_ok(self, client):
        res = client.get("/api/search?q=서울&type=region")
        assert res.status_code == 200
        assert res.get_json()["status"] == "ok"


class TestHealthAndPages:
    def test_health(self, client):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.get_json()["status"] == "ok"

    @pytest.mark.parametrize("path", ["/", "/analytics", "/forecast", "/region", "/guide"])
    def test_pages_render(self, client, path):
        res = client.get(path)
        assert res.status_code == 200


@pytest.mark.skipif(FORECAST_MISSING, reason="시계열 예측 결과 JSON 없음")
class TestForecastApi:
    def test_future_predictions_extend_through_2026_december(self, client):
        res = client.get("/api/forecast")
        body = res.get_json()

        assert res.status_code == 200
        assert body["status"] == "ok"
        assert body["forecast_horizon"] == 6
        assert body["future_predictions"]["future_ym"] == [
            "2026-07", "2026-08", "2026-09",
            "2026-10", "2026-11", "2026-12",
        ]
        for model in ["SARIMA", "Prophet", "LightGBM"]:
            assert len(body["future_predictions"][model]) == 6


@pytest.mark.skipif(DATA_MISSING, reason="지역 분석 결과 CSV 없음")
class TestRegionApi:
    def test_region_search_returns_exact_canonical_key(self, client):
        res = client.get("/api/regions?q=강남")
        body = res.get_json()

        assert res.status_code == 200
        assert body["status"] == "ok"
        assert body["data"][0]["key"] == "서울|강남구"
        assert body["data"][0]["codes"] == ["11680"]

    def test_region_detail_rejects_free_text(self, client):
        res = client.get("/api/region?key=강남구")
        assert res.status_code == 400
        assert res.get_json()["status"] == "error"

    def test_region_detail_returns_analysis_payload(self, client):
        res = client.get("/api/region?key=서울%7C강남구")
        body = res.get_json()

        assert res.status_code == 200
        assert body["status"] == "ok"
        assert body["summary"]["name"] == "서울 강남구"
        assert body["coverage"]["end_ym"] == "202606"
        assert len(body["trend"]) == 12
        assert len(body["metrics"]) == 7
        assert body["apartments"]

    def test_region_insight_uses_selected_region(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "_ollama_chat", lambda *args, **kwargs: "확정 지표 기반 해설")
        res = client.post("/api/region-insight", json={"key": "서울|강남구"})
        body = res.get_json()

        assert res.status_code == 200
        assert body["status"] == "ok"
        assert body["insight"] == "확정 지표 기반 해설"
        assert body["model"] == app_module.CHAT_MODEL


@pytest.mark.skipif(DATA_MISSING, reason="지역 분석 결과 CSV 없음")
class TestLlmContext:
    def test_demand_context_retrieves_only_relevant_region(self):
        df = pd.read_csv(app_module.RESULT_PATH, encoding="utf-8-sig").sort_values(
            "인테리어_수요점수", ascending=False
        )
        sido = pd.read_csv(app_module.SIDO_SUMMARY_PATH, encoding="utf-8-sig")

        context = app_module.build_demand_context("강남구 수요 점수 알려줘", df, sido)

        assert "강남구" in context
        assert "질문에 언급된 시군구의 정확한 데이터" in context
        assert len(context) < 5000

    def test_chat_rejects_overlong_message_before_model_call(self, client):
        res = client.post("/api/chat", json={"message": "가" * (app_module.CHAT_MAX_MESSAGE + 1)})
        assert res.status_code == 400


class TestOllamaClient:
    def test_uses_low_variance_options_and_cleans_thinking(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": "<think>내부 추론</think>\n\n확정 답변"}}

        def fake_post(url, json, timeout):
            captured.update({"url": url, "payload": json, "timeout": timeout})
            return FakeResponse()

        monkeypatch.setattr(app_module.requests, "post", fake_post)
        answer = app_module._ollama_chat([{"role": "user", "content": "질문"}])

        assert answer == "확정 답변"
        assert captured["payload"]["options"]["temperature"] == 0.15
        assert captured["payload"]["options"]["seed"] == 42
        assert captured["payload"]["keep_alive"] == "30m"

    def test_demand_audit_removes_unsupported_region_story(self):
        answer = (
            "강남구 수요 점수는 43.8점입니다. "
            "평균 거래금액은 275,271.6만원입니다. "
            "고급 주거지로 알려져 있어 주민 투자 성향이 높습니다."
        )

        audited = app_module._audit_demand_answer(answer)

        assert "43.8점" in audited
        assert "275,271.6만원" in audited
        assert "고급 주거지" not in audited


class TestUpsertRaw:
    def test_returns_new_when_no_existing_file(self, tmp_path):
        df_new = pd.DataFrame({
            "수집_시군구코드": ["11110"], "수집_연월": ["202601"], "val": [1],
        })
        result = app_module._upsert_raw(str(tmp_path / "nope.csv"), df_new)
        assert len(result) == 1

    def test_replaces_only_matching_keys(self, tmp_path):
        existing_path = tmp_path / "existing.csv"
        df_existing = pd.DataFrame({
            "수집_시군구코드": ["11110", "11110", "28110"],
            "수집_연월": ["202601", "202602", "202601"],
            "val": ["old1", "old2", "old3"],
        })
        df_existing.to_csv(existing_path, index=False, encoding="utf-8-sig")

        df_new = pd.DataFrame({
            "수집_시군구코드": ["11110"], "수집_연월": ["202601"], "val": ["new1"],
        })
        result = app_module._upsert_raw(str(existing_path), df_new)

        # 재수집한 (11110, 202601)의 값은 새 데이터로 교체되고,
        # 나머지 (11110,202602) / (28110,202601)은 그대로 남는다.
        # CSV 라운드트립을 거친 컬럼은 int로 읽힐 수 있어 문자열로 캐스팅해 비교한다.
        assert len(result) == 3
        code = result["수집_시군구코드"].astype(str)
        ym = result["수집_연월"].astype(str)
        replaced = result[(code == "11110") & (ym == "202601")]
        assert list(replaced["val"]) == ["new1"]
        untouched = result[(code == "11110") & (ym == "202602")]
        assert list(untouched["val"]) == ["old2"]

    def test_empty_new_data_returns_existing_unchanged(self, tmp_path):
        existing_path = tmp_path / "existing.csv"
        df_existing = pd.DataFrame({
            "수집_시군구코드": ["11110"], "수집_연월": ["202601"], "val": ["old1"],
        })
        df_existing.to_csv(existing_path, index=False, encoding="utf-8-sig")

        result = app_module._upsert_raw(str(existing_path), pd.DataFrame())
        assert len(result) == 1
        assert result.iloc[0]["val"] == "old1"

    def test_replace_keys_removes_old_rows_when_new_result_is_zero(self, tmp_path):
        existing_path = tmp_path / "existing.csv"
        pd.DataFrame({
            "수집_시군구코드": ["11110", "11140", "11110"],
            "수집_연월": ["202601", "202601", "202512"],
            "val": ["old-a", "old-zero-region", "keep"],
        }).to_csv(existing_path, index=False, encoding="utf-8-sig")
        new_data = pd.DataFrame({
            "수집_시군구코드": ["11110"],
            "수집_연월": ["202601"],
            "val": ["new-a"],
        })

        result = app_module._upsert_raw(
            str(existing_path),
            new_data,
            {("11110", "202601"), ("11140", "202601")},
        )

        assert set(result["val"]) == {"keep", "new-a"}


class TestAtomicWriteCsv:
    def test_writes_file_and_cleans_up_tmp(self, tmp_path):
        target = tmp_path / "out.csv"
        df = pd.DataFrame({"a": [1, 2]})
        app_module._atomic_write_csv(df, str(target))

        assert target.exists()
        assert not (tmp_path / "out.csv.tmp").exists()
        loaded = pd.read_csv(target, encoding="utf-8-sig")
        assert list(loaded["a"]) == [1, 2]

    def test_overwrites_existing_file(self, tmp_path):
        target = tmp_path / "out.csv"
        pd.DataFrame({"a": [999]}).to_csv(target, index=False, encoding="utf-8-sig")

        app_module._atomic_write_csv(pd.DataFrame({"a": [1, 2, 3]}), str(target))

        loaded = pd.read_csv(target, encoding="utf-8-sig")
        assert list(loaded["a"]) == [1, 2, 3]
