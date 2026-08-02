"""Collect and atomically upsert apartment trade/rent data for a fixed month range."""

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from dotenv import load_dotenv

from app import RAW_RENT_PATH, RAW_TRADE_PATH, _atomic_write_csv, _upsert_raw
from src.collector import ALL_SIGUNGU_CODES, ApartmentDataCollector, generate_year_months


def collect_concurrently(collector, start_ym, end_ym, fetch_fn, workers, label):
    tasks = [
        (code, ym)
        for code in ALL_SIGUNGU_CODES.values()
        for ym in generate_year_months(start_ym, end_ym)
    ]
    chunks = []

    def fetch_with_retry(code, ym):
        for attempt in range(1, 4):
            chunk = fetch_fn(sigungu_code=code, year_month=ym)
            if chunk.attrs.get("fetch_ok", False):
                return chunk
            if attempt < 3:
                time.sleep(attempt)
        return chunk

    failed = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch_with_retry, code, ym): (code, ym)
            for code, ym in tasks
        }
        for done, future in enumerate(as_completed(futures), start=1):
            code, ym = futures[future]
            chunk = future.result()
            ok = chunk.attrs.get("fetch_ok", False)
            if ok and not chunk.empty:
                chunks.append(chunk)
            elif not ok:
                failed.append((code, ym))
            print(
                f"[{label}] {done}/{len(tasks)} {code}/{ym}: "
                f"{len(chunk):,}건 ({'OK' if ok else '실패'})",
                flush=True,
            )
    if failed:
        preview = ", ".join(f"{code}/{ym}" for code, ym in failed[:10])
        raise RuntimeError(f"{label} API 호출 실패: {len(failed)}개 ({preview})")
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="시작월 YYYYMM")
    parser.add_argument("--end", required=True, help="종료월 YYYYMM")
    parser.add_argument("--interval", type=float, default=0.2, help="API 요청 간 대기 초")
    parser.add_argument("--workers", type=int, default=4, help="동시 API 요청 수")
    args = parser.parse_args()

    load_dotenv()
    collector = ApartmentDataCollector(
        os.environ.get("API_KEY", ""),
        request_interval=args.interval,
    )

    trade = collect_concurrently(
        collector, args.start, args.end, collector.fetch_one, args.workers, "매매"
    )
    rent = collect_concurrently(
        collector,
        args.start,
        args.end,
        collector.fetch_one_rent,
        args.workers,
        "전월세",
    )
    replace_keys = {
        (str(code), ym)
        for code in ALL_SIGUNGU_CODES.values()
        for ym in generate_year_months(args.start, args.end)
    }
    merged_trade = _upsert_raw(RAW_TRADE_PATH, trade, replace_keys)
    merged_rent = _upsert_raw(RAW_RENT_PATH, rent, replace_keys)
    _atomic_write_csv(merged_trade, RAW_TRADE_PATH)
    _atomic_write_csv(merged_rent, RAW_RENT_PATH)

    print(f"[DONE] 매매 {len(trade):,}건, 전월세 {len(rent):,}건을 교체했습니다.")


if __name__ == "__main__":
    main()
