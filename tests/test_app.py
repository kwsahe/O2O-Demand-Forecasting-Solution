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

    @pytest.mark.parametrize("path", ["/", "/analytics", "/forecast", "/guide"])
    def test_pages_render(self, client, path):
        res = client.get(path)
        assert res.status_code == 200


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
