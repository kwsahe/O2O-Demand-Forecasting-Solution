import time
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

    def fetch_one(self, sigungu_code: str, year_month: str) -> pd.DataFrame:
        import requests
        import xml.etree.ElementTree as ET

        url = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
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
        
    def fetch_range(
        self,
        sigungu_codes,
        start_ym: str,
        end_ym: str,
        save_path: str = None,
    ) -> pd.DataFrame:
        if isinstance(sigungu_codes, dict):
            code_list = list(sigungu_codes.values())
            name_map  = {v: k for k, v in sigungu_codes.items()}
        else:
            code_list = sigungu_codes
            name_map  = {}

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

                df_chunk = self.fetch_one(sigungu_code=code, year_month=ym)

                if not df_chunk.empty:
                    collected.append(df_chunk)
                    print(f"{len(df_chunk):,}건 ✓")
                else:
                    print("0건")

        if not collected:
            print("[WARNING] 수집된 데이터가 없습니다. API 키를 확인하세요.")
            return pd.DataFrame()

        df_all = pd.concat(collected, ignore_index=True)
        print(f"\n[COLLECT] 완료 — 총 {len(df_all):,}건 수집")

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

        code_to_name = {v: k for k, v in SEOUL_SIGUNGU_CODES.items()}
        if "수집_시군구코드" in df.columns and "법정동" in df.columns:
            df["시군구"] = df["수집_시군구코드"].map(
            lambda c: f"서울특별시 {code_to_name.get(c, c)} "
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
        print("  [!] 데이터 없음 — API 키 또는 인터넷 연결 확인")