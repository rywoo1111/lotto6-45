"""
Module: fetch_winning_history.py
Version: 2.0
Last Updated: 2026-10-07

[모듈 책임]
- 동행복권 개편 신규 REST API(/lt645/selectPstLt645InfoNew.do)를 통한 증분(Incremental) 당첨 이력 수집.
- 로컬 DB(WINNING_HISTORY)의 최신 회차를 자동 감지하고 미수집 구간만 10회차 단위 고속 청크 수집.
- YYYY-MM-DD 표준 일자 규격 변환 및 마스터 조합 풀 인덱스 역추적을 통한 combination_id / zone_id 매핑.
- INSERT OR REPLACE 멱등 적재를 통한 WINNING_HISTORY 무결성 보장.
"""
import sqlite3
import requests
import time
import os
import re
import urllib3
import traceback
from typing import Tuple, List, Set, Dict, Any

# SSL 인증서 경고 무시 (로컬 환경 방어)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# DB 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://www.dhlottery.co.kr/lt645/result"
}


def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    """
    [v2.0] WAL 모드 및 정규 동기화 PRAGMA가 적용된 SQLite 커넥션 반환.

    Args:
        db_path (str): 대상 SQLite 데이터베이스 파일 경로.

    Returns:
        sqlite3.Connection: 설정된 DB 커넥션 객체.

    Raises:
        FileNotFoundError: 데이터베이스 파일이 디스크에 없을 경우 발생.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB 파일을 찾을 수 없습니다: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def get_latest_saved_round(cursor: sqlite3.Cursor) -> int:
    """
    [v2.0] WINNING_HISTORY 테이블에 이미 저장된 최신 회차 번호 조회.

    Args:
        cursor (sqlite3.Cursor): 활성화된 데이터베이스 커서.

    Returns:
        int: 기저장된 최대 회차 번호 (데이터 부재 시 0 반환).
    """
    cursor.execute("SELECT MAX(round_no) FROM WINNING_HISTORY;")
    result = cursor.fetchone()[0]
    return result if result is not None else 0


def get_combination_info(cursor: sqlite3.Cursor, nums: List[int]) -> Tuple[int, int]:
    """
    [v2.0] 6개 정렬 번호 기반 combination_id 및 zone_id 복합 인덱스 역추적.

    Args:
        cursor (sqlite3.Cursor): 활성화된 데이터베이스 커서.
        nums (List[int]): 오름차순 정렬된 6개 로또 번호.

    Returns:
        Tuple[int, int]: (combination_id, zone_id) 매핑 튜플.

    Raises:
        ValueError: 마스터 조합 풀(8,145,060건)에 일치하는 조합이 없을 경우 발생.
    """
    cursor.execute("""
                   SELECT combination_id, zone_id
                   FROM LOTTO_COMBINATIONS_POOL
                   WHERE num1 = ?
                     AND num2 = ?
                     AND num3 = ?
                     AND num4 = ?
                     AND num5 = ?
                     AND num6 = ?;
                   """, tuple(nums))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"마스터 풀 데이터 누락. 존재하지 않는 조합: {nums}")
    return row[0], row[1]


def detect_server_latest_round(session: requests.Session) -> int:
    """
    [v2.0] 동행복권 결과 페이지를 스캔하여 현재 서버의 최신 추첨 회차를 감지.

    Args:
        session (requests.Session): 활성화된 HTTP 세션.

    Returns:
        int: 감지된 서버 최신 회차 번호.
    """
    page_url = "https://www.dhlottery.co.kr/lt645/result"
    resp = session.get(page_url, headers=REQUEST_HEADERS, timeout=10)
    resp.raise_for_status()
    page_text = resp.text

    opt_match = re.search(r'id=["\']opt_val["\'][^>]*value=["\']([0-9]+)["\']', page_text)
    if not opt_match:
        opt_match = re.search(r'\$\(["\']#opt_val["\']\)\.val\(["\']([0-9]+)["\']\)', page_text)

    if opt_match:
        return int(opt_match.group(1))

    # 정규식 대체 탐색: 드롭다운 옵션의 최대 숫자 추출
    options = re.findall(r'data-value=["\']([0-9]+)["\']', page_text)
    if options:
        return max([int(x) for x in options])

    return 1200  # 기본 안전값


def sync_winning_history_bulk() -> None:
    """
    [v2.0] 동행복권 개편 REST API 기반 증분(Incremental) 당첨 이력 수집 엔진.

    로컬 DB의 최신 회차를 스캔하여 미수집 구간만 서버에 10회차 단위 청크로 요청하고,
    JSON 데이터 파싱 및 Base DB 역추적 후 INSERT OR REPLACE 멱등 적재를 수행한다.

    Args:
        None

    Returns:
        None
    """
    print("=" * 60)
    print("[1] 역대 당첨 이력 자동 동기화 시작 (동행복권 신규 REST API)")
    print("=" * 60)

    start_time = time.time()
    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        latest_saved = get_latest_saved_round(cursor)
        print(f" - 로컬 DB 최신 회차: {latest_saved}회")

        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)

        # 1. 서버 최신 회차 감지
        print(" - [네트워크] 동행복권 최신 회차 정보 감지 중...")
        server_latest = detect_server_latest_round(session)
        print(f" - 서버 최신 회차: {server_latest}회")

        start_round = latest_saved + 1
        if start_round > server_latest:
            print(f"\n[알림] 로컬 DB가 이미 최신 상태입니다. (최신 회차: {latest_saved}회)")
            print("=" * 60)
            return

        missing_rounds: Set[int] = set(range(start_round, server_latest + 1))
        print(f" - 동기화 대상 구간: {start_round}회 ~ {server_latest}회 (총 {len(missing_rounds)}개 회차 수집 대기)\n")

        api_url = "https://www.dhlottery.co.kr/lt645/selectPstLt645InfoNew.do"
        batch_data = []
        success_count = 0

        while missing_rounds:
            target_round = max(missing_rounds)
            params = {
                "srchDir": "center",
                "srchLtEpsd": str(target_round)
            }

            resp = session.get(api_url, params=params, timeout=10)
            resp.raise_for_status()
            res_json = resp.json()
            items = res_json.get("data", {}).get("list", [])

            if not items:
                # 더 이상 수집할 항목이 없을 경우 방어적 종료
                print(f"[경고] {target_round}회차 구간 API 응답 데이터 부재.")
                break

            found_any = False
            for item in items:
                r_no = item.get("ltEpsd")
                if r_no in missing_rounds:
                    raw_date = str(item.get("ltRflYmd", ""))
                    draw_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else raw_date

                    nums = sorted([
                        int(item["tm1WnNo"]), int(item["tm2WnNo"]), int(item["tm3WnNo"]),
                        int(item["tm4WnNo"]), int(item["tm5WnNo"]), int(item["tm6WnNo"])
                    ])
                    bonus = int(item["bnsWnNo"])

                    # DB에서 combination_id 및 zone_id 매핑 (복합 인덱스 스캔)
                    combination_id, zone_id = get_combination_info(cursor, nums)

                    batch_data.append((
                        r_no, draw_date,
                        nums[0], nums[1], nums[2], nums[3], nums[4], nums[5],
                        bonus, combination_id, zone_id
                    ))

                    missing_rounds.remove(r_no)
                    success_count += 1
                    found_any = True

                    print(f" -> [동기화] 제 {r_no:>4}회 ({draw_date}) : 당첨번호 {nums} + 보너스 [{bonus}]")

            if not found_any:
                # 무한 루프 방지
                missing_rounds.remove(target_round)

            # 50건 단위 일괄 커밋
            if len(batch_data) >= 50:
                cursor.executemany("""
                    INSERT OR REPLACE INTO WINNING_HISTORY 
                    (round_no, draw_date, num1, num2, num3, num4, num5, num6, bonus, combination_id, zone_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch_data)
                conn.commit()
                batch_data.clear()

            time.sleep(0.1)  # 요청 간 0.1초 딜레이

        # 잔여 데이터 일괄 Insert
        if batch_data:
            cursor.executemany("""
                INSERT OR REPLACE INTO WINNING_HISTORY 
                (round_no, draw_date, num1, num2, num3, num4, num5, num6, bonus, combination_id, zone_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch_data)
            conn.commit()

        final_latest_round = get_latest_saved_round(cursor)
        elapsed = time.time() - start_time

        print("\n" + "=" * 60)
        print("[동기화 결과 리포트]")
        print(f" - 신규 적재 건수 : {success_count}건")
        print(f" - DB 최신 회차  : {final_latest_round}회")
        print(f" - 총 소요 시간  : {elapsed:.2f}초")
        print("=" * 60)

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\n[치명적 오류] 동기화 파이프라인 실패: {e}")
        traceback.print_exc()

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sync_winning_history_bulk()
