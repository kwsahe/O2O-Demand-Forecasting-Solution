import time
import requests
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta

try:
    from PublicDataReader import TransactionPrice
    PDR_AVAILABLE = True
except ImportError:
    PDR_AVAILABLE = False
    print("[WARNING] PublicDataReader 미설치 → pip install PublicDataReader")

SEOUL_SIGUNGU_CODES = {
    "종로구":   "11110",
    "중구":     "11140",
    "용산구":   "11170",
    "성동구":   "11200",
    "광진구":   "11215",
    "동대문구": "11230",
    "중랑구":   "11260",
    "성북구":   "11290",
    "강북구":   "11305",
    "도봉구":   "11320",
    "노원구":   "11350",
    "은평구":   "11380",
    "서대문구": "11410",
    "마포구":   "11440",
    "양천구":   "11470",
    "강서구":   "11500",
    "구로구":   "11530",
    "금천구":   "11545",
    "영등포구": "11560",
    "동작구":   "11590",
    "관악구":   "11620",
    "서초구":   "11650",
    "강남구":   "11680",
    "송파구":   "11710",
    "강동구":   "11740",
}

INCHEON_SIGUNGU_CODES = {
    "중구":     "28110",
    "동구":     "28140",
    "미추홀구": "28177",
    "연수구":   "28185",
    "남동구":   "28200",
    "부평구":   "28237",
    "계양구":   "28245",
    "서구":     "28260",
    "강화군":   "28710",
    "옹진군":   "28720",
}

GYEONGGI_SIGUNGU_CODES = {
    "수원시 장안구": "41111",
    "수원시 권선구": "41113",
    "수원시 팔달구": "41115",
    "수원시 영통구": "41117",
    "성남시 수정구": "41131",
    "성남시 중원구": "41133",
    "성남시 분당구": "41135",
    "의정부시":     "41150",
    "안양시 만안구": "41171",
    "안양시 동안구": "41173",
    "부천시":       "41190",
    "광명시":       "41210",
    "평택시":       "41220",
    "동두천시":     "41250",
    "안산시 상록구": "41271",
    "안산시 단원구": "41273",
    "고양시 덕양구": "41281",
    "고양시 일산동구": "41285",
    "고양시 일산서구": "41287",
    "과천시":       "41290",
    "구리시":       "41310",
    "남양주시":     "41360",
    "오산시":       "41370",
    "시흥시":       "41390",
    "군포시":       "41410",
    "의왕시":       "41430",
    "하남시":       "41450",
    "용인시 처인구": "41461",
    "용인시 기흥구": "41463",
    "용인시 수지구": "41465",
    "파주시":       "41480",
    "이천시":       "41500",
    "안성시":       "41550",
    "김포시":       "41570",
    "화성시":       "41590",
    "광주시":       "41610",
    "양주시":       "41630",
    "포천시":       "41650",
    "여주시":       "41670",
    "연천군":       "41800",
    "가평군":       "41820",
    "양평군":       "41830",
}

BUSAN_SIGUNGU_CODES = {
    "중구":   "26110",
    "서구":   "26140",
    "동구":   "26170",
    "영도구": "26200",
    "부산진구": "26230",
    "동래구": "26260",
    "남구":   "26290",
    "북구":   "26320",
    "해운대구": "26350",
    "사하구": "26380",
    "금정구": "26410",
    "강서구": "26440",
    "연제구": "26470",
    "수영구": "26500",
    "사상구": "26530",
    "기장군": "26710",
}

DAEGU_SIGUNGU_CODES = {
    "중구":   "27110",
    "동구":   "27140",
    "서구":   "27170",
    "남구":   "27200",
    "북구":   "27230",
    "수성구": "27260",
    "달서구": "27290",
    "달성군": "27710",
}

GWANGJU_SIGUNGU_CODES = {
    "동구":   "29110",
    "서구":   "29140",
    "남구":   "29155",
    "북구":   "29170",
    "광산구": "29200",
}

DAEJEON_SIGUNGU_CODES = {
    "동구":   "30110",
    "중구":   "30140",
    "서구":   "30170",
    "유성구": "30200",
    "대덕구": "30230",
}

ULSAN_SIGUNGU_CODES = {
    "중구":   "31110",
    "남구":   "31140",
    "동구":   "31170",
    "북구":   "31200",
    "울주군": "31710",
}

# 시군구 코드 → "시도 시군구" 전체 명칭 매핑 (수집/정규화에 사용)
SIGUNGU_CODE_TO_FULL_NAME = {}
for _name, _code in SEOUL_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"서울특별시 {_name}"
for _name, _code in INCHEON_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"인천광역시 {_name}"
for _name, _code in GYEONGGI_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"경기도 {_name}"
for _name, _code in BUSAN_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"부산광역시 {_name}"
for _name, _code in DAEGU_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"대구광역시 {_name}"
for _name, _code in GWANGJU_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"광주광역시 {_name}"
for _name, _code in DAEJEON_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"대전광역시 {_name}"
for _name, _code in ULSAN_SIGUNGU_CODES.items():
    SIGUNGU_CODE_TO_FULL_NAME[_code] = f"울산광역시 {_name}"

# 전체 시군구 "전체명칭"(공백제거) → 시군구코드 역매핑 (인테리어업체 데이터 등 명칭 기반 매칭용)
FULL_NAME_TO_SIGUNGU_CODE = {
    full.replace(" ", ""): code for code, full in SIGUNGU_CODE_TO_FULL_NAME.items()
}

# 인테리어업체 데이터 수집 대상 시도명 (수도권 + 5대 광역시)
INTERIOR_TARGET_SIDO = [
    "서울특별시", "인천광역시", "경기도",
    "부산광역시", "대구광역시", "광주광역시", "대전광역시", "울산광역시",
]

# 5대 광역시(인천 제외) 시군구 코드 통합
METRO5_SIGUNGU_CODES = {}
for _d, _prefix in [
    (BUSAN_SIGUNGU_CODES, "부산_"),
    (DAEGU_SIGUNGU_CODES, "대구_"),
    (GWANGJU_SIGUNGU_CODES, "광주_"),
    (DAEJEON_SIGUNGU_CODES, "대전_"),
    (ULSAN_SIGUNGU_CODES, "울산_"),
]:
    for _name, _code in _d.items():
        METRO5_SIGUNGU_CODES[f"{_prefix}{_name}"] = _code

# 전체 수집 대상 시군구 코드 통합(서울+인천+경기+5대 광역시, 102개).
# "중구"·"서구" 등 동명 시군구가 여러 시도에 있으므로 시도명을 접두어로 붙여 키 충돌을 막는다.
# /api/collect가 기본으로 전국을 수집하도록 할 때 사용 — 특정 시도만으로 좁혀서
# 전국 결과를 덮어쓰는 사고를 방지한다.
ALL_SIGUNGU_CODES = {}
for _d, _prefix in [
    (SEOUL_SIGUNGU_CODES, "서울_"),
    (INCHEON_SIGUNGU_CODES, "인천_"),
    (GYEONGGI_SIGUNGU_CODES, "경기_"),
]:
    for _name, _code in _d.items():
        ALL_SIGUNGU_CODES[f"{_prefix}{_name}"] = _code
ALL_SIGUNGU_CODES.update(METRO5_SIGUNGU_CODES)

def generate_year_months(start_ym: str, end_ym: str) -> list:
    start = datetime.strptime(start_ym, "%Y%m")
    end   = datetime.strptime(end_ym, "%Y%m")
    months = []
    cur = start
    while cur <= end:
        months.append(cur.strftime("%Y%m"))
        cur += relativedelta(months=1)
    return months

class ApartmentDataCollector:
    def __init__(self, api_key: str, request_interval: float = 0.5):
        if not api_key or api_key == "인증키":
            raise ValueError(
                "api_key를 입력하세요./n"
                "공공데이터포털 → 마이페이지 → 일반 인증키(Decoding)"
            )
        self.api_key = api_key
        self.request_interval = request_interval

        if PDR_AVAILABLE:
            self._pdr = TransactionPrice(api_key)
        else:
            self._pdr = None

    def _fetch_one_from(self, url: str, sigungu_code: str, year_month: str) -> pd.DataFrame:
        import requests
        import xml.etree.ElementTree as ET

        params = {
        "serviceKey": self.api_key,
        "LAWD_CD":    sigungu_code,
        "DEAL_YMD":   year_month,
        "numOfRows":  1000,
        "pageNo":     1,
        }

        try:
            res = requests.get(url, params=params, timeout=10)
            root = ET.fromstring(res.text)

            result_code = root.findtext(".//resultCode")
            if result_code != "000":
                result_msg = root.findtext(".//resultMsg")
                print(f"  [API ERROR] {result_code}: {result_msg}")
                return pd.DataFrame()

            items = root.findall(".//item")
            if not items:
                return pd.DataFrame()

            rows = [{child.tag: child.text for child in item} for item in items]
            df = pd.DataFrame(rows)

            time.sleep(self.request_interval)
            df["수집_시군구코드"] = sigungu_code
            df["수집_연월"]      = year_month
            return df

        except Exception as e:
            print(f"  [ERROR] {sigungu_code} / {year_month} 수집 실패: {e}")
            return pd.DataFrame()

    def fetch_one(self, sigungu_code: str, year_month: str) -> pd.DataFrame:
        url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
        return self._fetch_one_from(url, sigungu_code, year_month)

    def fetch_one_rent(self, sigungu_code: str, year_month: str) -> pd.DataFrame:
        """아파트 전월세 실거래가 단건 수집 (국토교통부_아파트 전월세 실거래가 자료)"""
        url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
        return self._fetch_one_from(url, sigungu_code, year_month)

    def fetch_range(
        self,
        sigungu_codes,
        start_ym: str,
        end_ym: str,
        save_path: str = None,
        fetch_fn=None,
    ) -> pd.DataFrame:
        if isinstance(sigungu_codes, dict):
            code_list = list(sigungu_codes.values())
            name_map  = {v: k for k, v in sigungu_codes.items()}
        else:
            code_list = sigungu_codes
            name_map  = {}

        fetch_fn = fetch_fn or self.fetch_one

        months    = generate_year_months(start_ym, end_ym)
        total     = len(code_list) * len(months)
        collected = []
        done      = 0

        print(f"[COLLECT] {len(code_list)}개 구 × {len(months)}개월 = 총 {total}회 호출 시작")
        print(f"          {start_ym} ~ {end_ym}\n")

        for code in code_list:
            name = name_map.get(code, code)
            for ym in months:
                done += 1
                print(f"  [{done:>3}/{total}] {name}({code}) / {ym} ...", end=" ")

                df_chunk = fetch_fn(sigungu_code=code, year_month=ym)

                if not df_chunk.empty:
                    collected.append(df_chunk)
                    print(f"{len(df_chunk):,}건 OK")
                else:
                    print("0건")

        if not collected:
            print("[WARNING] 수집된 데이터가 없습니다. API 키를 확인하세요.")
            return pd.DataFrame()

        df_all = pd.concat(collected, ignore_index=True)
        print(f"\n[COLLECT] 완료 - 총 {len(df_all):,}건 수집")

        if save_path:
            df_all.to_csv(save_path, index=False, encoding="utf-8-sig")
            print(f"[SAVE]    {save_path} 저장 완료")
        return df_all
    
    def fetch_recent_months(
        self,
        sigungu_codes=None,
        months: int = 12,
        save_path: str = None,
    ) -> pd.DataFrame:
        if sigungu_codes is None:
            sigungu_codes = SEOUL_SIGUNGU_CODES

        today    = datetime.today()
        end_ym   = today.strftime("%Y%m")
        start_ym = (today - relativedelta(months=months - 1)).strftime("%Y%m")

        print(f"[AUTO] 최근 {months}개월: {start_ym} ~ {end_ym}")
        return self.fetch_range(
            sigungu_codes=sigungu_codes,
            start_ym=start_ym,
            end_ym=end_ym,
            save_path=save_path,
        )

    def fetch_recent_months_rent(
        self,
        sigungu_codes=None,
        months: int = 12,
        save_path: str = None,
    ) -> pd.DataFrame:
        """최근 N개월 아파트 전월세 실거래가 자동 수집 (국토교통부_아파트 전월세 실거래가 자료)"""
        if sigungu_codes is None:
            sigungu_codes = SEOUL_SIGUNGU_CODES

        today    = datetime.today()
        end_ym   = today.strftime("%Y%m")
        start_ym = (today - relativedelta(months=months - 1)).strftime("%Y%m")

        print(f"[AUTO][전월세] 최근 {months}개월: {start_ym} ~ {end_ym}")
        return self.fetch_range(
            sigungu_codes=sigungu_codes,
            start_ym=start_ym,
            end_ym=end_ym,
            save_path=save_path,
            fetch_fn=self.fetch_one_rent,
        )

    def normalize_rent_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """전월세 API 영문 컬럼 → pipeline.py 한글 컬럼 변환"""
        rename_map = {
        "aptNm":      "단지명",
        "excluUseAr": "전용면적(㎡)",
        "deposit":    "보증금(만원)",
        "monthlyRent": "월세(만원)",
        "buildYear":  "건축년도",
        "umdNm":      "법정동",
        "dealYear":   "년",
        "dealMonth":  "월",
        }
        df = df.rename(columns=rename_map)

        if "수집_시군구코드" in df.columns and "법정동" in df.columns:
            df["시군구"] = df["수집_시군구코드"].map(
            lambda c: SIGUNGU_CODE_TO_FULL_NAME.get(str(c), str(c)) + " "
            ) + df["법정동"].fillna("")

        if "년" in df.columns and "월" in df.columns:
            df["계약년월"] = (
            df["년"].astype(str).str.zfill(4) +
            df["월"].astype(str).str.zfill(2)
            )

        keep = ["시군구", "단지명", "전용면적(㎡)", "계약년월",
            "보증금(만원)", "월세(만원)", "건축년도",
            "수집_시군구코드", "수집_연월"]
        return df[[c for c in keep if c in df.columns]]

    def normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        rename_map = {
        "aptNm":      "단지명",
        "excluUseAr": "전용면적(㎡)",
        "dealAmount": "거래금액(만원)",
        "buildYear":  "건축년도",
        "roadNm":     "도로명",
        "umdNm":      "법정동",
        "dealYear":   "년",
        "dealMonth":  "월",
        }
        df = df.rename(columns=rename_map)

        if "수집_시군구코드" in df.columns and "법정동" in df.columns:
            df["시군구"] = df["수집_시군구코드"].map(
            lambda c: SIGUNGU_CODE_TO_FULL_NAME.get(str(c), str(c)) + " "
            ) + df["법정동"].fillna("")

        if "년" in df.columns and "월" in df.columns:
            df["계약년월"] = (
            df["년"].astype(str).str.zfill(4) +
            df["월"].astype(str).str.zfill(2)
            )

        keep = ["시군구", "단지명", "전용면적(㎡)", "계약년월",
            "거래금액(만원)", "건축년도", "도로명",
            "수집_시군구코드", "수집_연월"]
        return df[[c for c in keep if c in df.columns]]

    def fetch_renovation(self, sigungu_code: str, bdong_code: str) -> pd.DataFrame:
        """건축HUB 건축인허가정보 - 대수선 이력 조회 (읍면동 단위)

        주의: 이 API는 시군구 코드만으로는 조회가 불가능하며,
        법정동(읍면동) 코드까지 함께 지정해야 합니다.
        전 지역 수집을 위해서는 시군구별 법정동 코드 목록(LEGAL_DONG_CODES)이 필요합니다.
        """
        import requests

        url = "https://apis.data.go.kr/1613000/ArchPmsHubService/getApImprprInfo"
        params = {
            "serviceKey": self.api_key,
            "sigunguCd": sigungu_code,
            "bjdongCd": bdong_code[-5:],
            "numOfRows": 1000,
            "pageNo": 1,
            "_type": "json",
        }

        try:
            res = requests.get(url, params=params, timeout=5)
            res_json = res.json()

            header = res_json.get("response", {}).get("header", {})
            if header.get("resultCode") != "00":
                print(f"  [API ERROR] {header.get('resultCode')}: {header.get('resultMsg')}", flush=True)
                return pd.DataFrame()

            items = res_json["response"]["body"]["items"]
            if not items:
                return pd.DataFrame()
            item_list = items.get("item", [])
            if isinstance(item_list, dict):
                item_list = [item_list]
            if not item_list:
                return pd.DataFrame()

            df = pd.DataFrame(item_list)
            time.sleep(self.request_interval)
            df["수집_시군구코드"] = sigungu_code
            df["수집_법정동코드"] = bdong_code
            return df
        except Exception as e:
            print(f"  [ERROR] {sigungu_code}/{bdong_code} 대수선 이력 수집 실패: {e}")
            return pd.DataFrame()

    def fetch_renovation_for_region(
        self,
        legal_dong_codes: dict,
        save_path: str = None,
    ) -> pd.DataFrame:
        """시군구별 법정동 코드 목록을 받아 대수선 이력을 일괄 수집

        legal_dong_codes: {시군구코드: [법정동코드, ...]} 형태의 dict
        (TODO: 법정동 코드 목록 데이터 확보 후 채워서 사용)
        """
        collected = []
        total = sum(len(v) for v in legal_dong_codes.values())
        done = 0

        print(f"[COLLECT][대수선] {len(legal_dong_codes)}개 시군구, 총 {total}개 법정동 조회 시작", flush=True)

        for sigungu_code, bdong_list in legal_dong_codes.items():
            for bdong_code in bdong_list:
                done += 1
                df_chunk = self.fetch_renovation(sigungu_code, bdong_code)
                if not df_chunk.empty:
                    collected.append(df_chunk)
                    print(f"  [{done:>4}/{total}] {sigungu_code}/{bdong_code} -> {len(df_chunk):,}건", flush=True)
                elif done % 50 == 0:
                    print(f"  [{done:>4}/{total}] {sigungu_code}/{bdong_code} -> 0건", flush=True)

                if save_path and done % 200 == 0 and collected:
                    pd.concat(collected, ignore_index=True).to_csv(save_path, index=False, encoding="utf-8-sig")
                    print(f"  [CHECKPOINT] {done}/{total} 저장 완료", flush=True)

        if not collected:
            print("[WARNING] 수집된 대수선 데이터가 없습니다.")
            return pd.DataFrame()

        df_all = pd.concat(collected, ignore_index=True)
        print(f"\n[COLLECT][대수선] 완료 - 총 {len(df_all):,}건 수집", flush=True)

        if save_path:
            df_all.to_csv(save_path, index=False, encoding="utf-8-sig")
            print(f"[SAVE]    {save_path} 저장 완료", flush=True)
        return df_all

    def normalize_renovation_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """대수선 이력 API 컬럼 → 시군구 단위 집계용 컬럼 정리"""
        if df.empty:
            return df

        rename_map = {
            "platPlc":          "대지위치",
            "imprprGbCdNm":     "대수선구분",
            "imprprChangGbCdNm": "대수선변경구분",
            "mgmPmsrgstPk":     "건물PK",
            "bldNm":            "건물명",
            "crtnDay":          "생성일자",
        }
        df = df.rename(columns=rename_map)

        if "수집_시군구코드" in df.columns:
            df["시군구"] = df["수집_시군구코드"].map(
                lambda c: SIGUNGU_CODE_TO_FULL_NAME.get(str(c), str(c))
            )

        keep = ["시군구", "대수선구분", "대수선변경구분", "대지위치",
            "건물PK", "건물명", "생성일자",
            "수집_시군구코드", "수집_법정동코드"]
        return df[[c for c in keep if c in df.columns]]

    def fetch_interior_companies(self, ctpv_nm: str, page_no: int = 1, num_of_rows: int = 1000):
        """전국인테리어업체표준데이터 - 시도별 인테리어 업체 현황 조회

        반환: (DataFrame, totalCount)
        """
        import requests

        url = "https://api.data.go.kr/openapi/tn_pubr_public_interior_api"
        params = {
            "serviceKey": self.api_key,
            "pageNo": page_no,
            "numOfRows": num_of_rows,
            "type": "json",
            "CTPV_NM": ctpv_nm,
        }

        try:
            res = requests.get(url, params=params, timeout=10)
            res_json = res.json()

            header = res_json.get("response", {}).get("header", {})
            if header.get("resultCode") not in ("00", "0", 0):
                print(f"  [API ERROR] {header.get('resultCode')}: {header.get('resultMsg')}", flush=True)
                return pd.DataFrame(), 0

            body = res_json.get("response", {}).get("body", {})
            items = body.get("items", [])
            if isinstance(items, dict):
                items = items.get("item", [])
            if not items:
                return pd.DataFrame(), int(body.get("totalCount", 0))

            df = pd.DataFrame(items)
            total_count = int(body.get("totalCount", len(df)))
            time.sleep(self.request_interval)
            return df, total_count

        except Exception as e:
            print(f"  [ERROR] {ctpv_nm} 인테리어업체 수집 실패: {e}")
            return pd.DataFrame(), 0

    def fetch_interior_companies_all(self, ctpv_list=None, save_path: str = None) -> pd.DataFrame:
        """대상 시도 목록 전체에 대해 인테리어업체 현황을 페이지네이션으로 전부 수집"""
        if ctpv_list is None:
            ctpv_list = INTERIOR_TARGET_SIDO

        collected = []
        for ctpv in ctpv_list:
            page = 1
            while True:
                df_chunk, total = self.fetch_interior_companies(ctpv, page_no=page, num_of_rows=1000)
                if df_chunk.empty:
                    break
                collected.append(df_chunk)
                print(f"  [{ctpv}] page {page} -> {len(df_chunk):,}건 (전체 {total:,}건)", flush=True)
                if page * 1000 >= total:
                    break
                page += 1

        if not collected:
            print("[WARNING] 수집된 인테리어업체 데이터가 없습니다.")
            return pd.DataFrame()

        df_all = pd.concat(collected, ignore_index=True)
        print(f"\n[COLLECT][인테리어업체] 완료 - 총 {len(df_all):,}건 수집")

        if save_path:
            df_all.to_csv(save_path, index=False, encoding="utf-8-sig")
            print(f"[SAVE]    {save_path} 저장 완료")
        return df_all

    def normalize_interior_company_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """인테리어업체 API 응답 컬럼 → 시군구 매칭용 컬럼 정리"""
        if df.empty:
            return df

        rename_map = {
            "bzentyNm":    "업체명",
            "ctpvNm":      "시도명",
            "sggNm":       "시군구명",
            "lctnRoadNm":  "도로명주소",
            "telno":       "전화번호",
            "regNo":       "등록번호",
            "rprsvNm":     "대표자명",
            "tnoemp":      "총직원수",
            "BZENTY_NM":   "업체명",
            "CTPV_NM":     "시도명",
            "SGG_NM":      "시군구명",
            "LCTN_ROAD_NM": "도로명주소",
            "TELNO":       "전화번호",
            "REG_NO":      "등록번호",
            "RPRSV_NM":    "대표자명",
            "TNOEMP":      "총직원수",
        }
        df = df.rename(columns=rename_map)

        if "시도명" in df.columns and "시군구명" in df.columns:
            df["시군구_코드"] = df.apply(
                lambda r: FULL_NAME_TO_SIGUNGU_CODE.get(
                    f"{str(r['시도명']).strip()}{str(r['시군구명']).strip()}".replace(" ", "")
                ),
                axis=1,
            )

        keep = ["업체명", "시도명", "시군구명", "도로명주소", "총직원수", "시군구_코드"]
        return df[[c for c in keep if c in df.columns]]


# 소상공인 상가정보 API 기준 인테리어 관련 소분류 업종 코드
INTERIOR_UPJONG_CODES = [
    "M11201",  # 인테리어 디자인업
    "G21002",  # 벽지/장판/마루 소매업
    "G21001",  # 철물/공구 소매업
    "G21003",  # 건설/건축자재 소매업
    "G21099",  # 기타 건설/건축자재 소매업
    "G21101",  # 가구 소매업
    "M10901",  # 건축 설계 및 관련 서비스업
]

# (시도약칭, 시군구명) → API 코드 목록 매핑 (시도+시군구 조합으로 중복 방지)
_SIDO_SHORT_MAP = {
    "서울특별시": "서울", "인천광역시": "인천", "경기도": "경기",
    "부산광역시": "부산", "대구광역시": "대구", "광주광역시": "광주",
    "대전광역시": "대전", "울산광역시": "울산",
}

def _build_sido_sgg_to_api_codes():
    from collections import defaultdict
    mapping = defaultdict(list)
    for code, full in SIGUNGU_CODE_TO_FULL_NAME.items():
        parts = full.split()
        if len(parts) >= 2:
            sido_full = parts[0]
            sido_short = _SIDO_SHORT_MAP.get(sido_full, sido_full)
            sgg_name = parts[1]
            mapping[(sido_short, sgg_name)].append(code)
    return dict(mapping)

SIDO_SGG_TO_API_CODES = _build_sido_sgg_to_api_codes()


class SmallBusinessCollector:
    """소상공인시장진흥공단 상가(상권)정보 API 수집기.

    API 엔드포인트: storeListInDong (행정동/시군구 단위 상가업소 조회)
    활용신청: https://www.data.go.kr/data/15012005/openapi.do
    """

    BASE_URL = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch_count_by_upjong(self, sgg_code: str, sclsCd: str) -> int:
        """시군구코드 + 소분류업종코드로 totalCount만 반환 (numOfRows=1 최소 요청)."""
        params = {
            "serviceKey": self.api_key,
            "pageNo": 1,
            "numOfRows": 1,
            "type": "json",
            "divId": "signguCd",
            "key": sgg_code,
            "indsSclsCd": sclsCd,
        }
        try:
            res = requests.get(self.BASE_URL, params=params, timeout=10)
            if res.status_code == 200 and res.text.strip():
                j = res.json()
                if j.get("header", {}).get("resultCode") == "00":
                    return j.get("body", {}).get("totalCount") or 0
        except Exception:
            pass
        return 0

    def fetch_interior_store_counts(
        self, sido_sgg_pairs: list, delay: float = 0.2
    ) -> pd.DataFrame:
        """(시도, 시군구) 쌍 목록에 대해 인테리어 관련 업종 수를 집계해 DataFrame으로 반환.

        Args:
            sido_sgg_pairs: [(시도약칭, 시군구명), ...] 리스트
        Returns:
            DataFrame(시도, 시군구, 소상공인_인테리어업체수)
        """
        rows = []
        total = len(sido_sgg_pairs)
        for idx, (sido, sgg_name) in enumerate(sido_sgg_pairs, 1):
            api_codes = SIDO_SGG_TO_API_CODES.get((sido, sgg_name), [])
            if not api_codes:
                print(f"  [{idx}/{total}] {sido} {sgg_name}: API 코드 없음 (스킵)")
                rows.append({"시도": sido, "시군구": sgg_name, "소상공인_인테리어업체수": 0})
                continue

            total_cnt = 0
            for sgg_cd in api_codes:
                for upjong_cd in INTERIOR_UPJONG_CODES:
                    cnt = self.fetch_count_by_upjong(sgg_cd, upjong_cd)
                    total_cnt += cnt
                    time.sleep(delay)

            print(f"  [{idx}/{total}] {sido} {sgg_name}: {total_cnt}개")
            rows.append({"시도": sido, "시군구": sgg_name, "소상공인_인테리어업체수": total_cnt})

        return pd.DataFrame(rows)


if __name__ == "__main__":
    import sys

    API_KEY = sys.argv[1] if len(sys.argv) > 1 else "YOUR_DECODING_KEY"

    print("=" * 60)
    print("  collector.py 단독 테스트")
    print("=" * 60)

    collector = ApartmentDataCollector(api_key=API_KEY)

    print("\n[TEST] 서초구 / 202602 단건 수집")
    df_test = collector.fetch_one(sigungu_code="11650", year_month="202602")

    if not df_test.empty:
        print(f"  수집 건수: {len(df_test):,}건")
        print(f"  컬럼 목록: {df_test.columns.tolist()}")
        print(df_test.head(3).to_string())

        df_norm = collector.normalize_columns(df_test)
        print(f"\n[NORMALIZE] 변환 후 컬럼: {df_norm.columns.tolist()}")
        print(df_norm.head(3).to_string())
    else:
        print("  [!] 데이터 없음 - API 키 또는 인터넷 연결 확인")