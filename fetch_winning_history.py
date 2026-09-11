"""
Module: fetch_winning_history.py
Version: 1.9
Last Updated: 2026-09-11

[모듈 책임]
- 동행복권 웹 서버 대상 단 1회 HTTP 요청을 통한 증분(Incremental) 벌크 당첨 이력 수집.
- EUC-KR 엑셀 HTML 응답 파싱 및 YYYY-MM-DD 표준 일자 규격 변환.
- 마스터 조합 풀 복합 인덱스 역추적을 통한 combination_id / zone_id 매핑.
- INSERT OR REPLACE 멱등 적재를 통한 WINNING_HISTORY 무결성 보장.
"""
import sqlite3
import requests
import time
import os
import re
import urllib3
import traceback
from typing import Tuple, List

# SSL 인증서 경고 무시 (로컬 환경 방어)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# DB 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}


def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    """
    [v1.9] WAL 모드 및 정규 동기화 PRAGMA가 적용된 SQLite 커넥션 반환.

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
    [v1.9] WINNING_HISTORY 테이블에 이미 저장된 최신 회차 번호 조회.

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
    [v1.9] 6개 정렬 번호 기반 combination_id 및 zone_id 복합 인덱스 역추적.

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


def sync_winning_history_bulk() -> None:
    """
    [v1.9] 단 1회의 HTTP 요청으로 미수집 전체 당첨 이력을 가져오는 O(1) Bulk ETL 엔진.

    로컬 DB의 최신 회차를 스캔하여 미수집 구간만 서버에 벌크 요청하고,
    HTML 파싱 및 Base DB 역추적 후 100건 단위 INSERT OR REPLACE 멱등 적재를 수행한다.

    Args:
        None

    Returns:
        None
    """
    print("=" * 60)
    print("[1] 역대 당첨 이력 자동 동기화 시작 (단일 호출 Bulk 다운로드 아키텍처)")
    print("=" * 60)

    start_time = time.time()
    conn = None

    try:
        conn = get_connection()
        cursor = conn.cursor()

        latest_round = get_latest_saved_round(cursor)
        start_round = latest_round + 1
        end_round = 10000  # 미래 회차까지 넉넉하게 지정하여 전체 스캔

        print(f" - 로컬 DB 최신 회차: {latest_round}회")
        print(f" - 동기화 대상 구간: {start_round}회 ~ 최신 회차 일괄 수집\n")

        # 동행복권 엑셀 벌크 다운로드 엔드포인트
        bulk_url = f"https://dhlottery.co.kr/gameResult.do?method=allWinExel&gubun=byWin&nowPage=&drwNoStart={start_round}&drwNoEnd={end_round}"

        print(" - [네트워크] 대용량 엑셀(HTML) 데이터 단일 호출 중... (WAF 우회)")
        response = requests.get(bulk_url, headers=REQUEST_HEADERS, timeout=15, verify=False)
        response.raise_for_status()

        # 동행복권 엑셀 데이터는 EUC-KR 인코딩을 사용함
        response.encoding = 'EUC-KR'
        html_content = response.text

        if "회차" not in html_content or "당첨번호" not in html_content:
            print("[수집 종료] 동기화할 신규 회차가 없거나 데이터가 비어있습니다.")
            return

        print(" - [파싱] 메모리 상에서 데이터 고속 매핑 시작...")

        batch_data = []
        success_count = 0

        # <tbody> 안의 모든 <tr>(행)을 찾음
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.IGNORECASE | re.DOTALL)

        for row in rows:
            # 각 행 내부의 <td>(열) 데이터를 추출
            cols = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)

            # 동행복권 공식 엑셀 규격은 총 20개 컬럼을 가짐 (13~18열: 당첨번호, 19열: 보너스)
            if len(cols) >= 20:
                # HTML 태그 및 공백 제거 처리
                clean_cols = [re.sub(r'<[^>]+>', '', str(c)).replace('&nbsp;', '').strip() for c in cols]

                # 1번 인덱스(회차)가 숫자가 아니면(헤더 등) 스킵
                if not clean_cols[1].isdigit():
                    continue

                round_no = int(clean_cols[1])
                # 2002.12.07 형태를 2002-12-07 DB 표준 규격으로 변환
                draw_date = clean_cols[2].replace('.', '-')

                try:
                    nums = sorted([
                        int(clean_cols[13]), int(clean_cols[14]), int(clean_cols[15]),
                        int(clean_cols[16]), int(clean_cols[17]), int(clean_cols[18])
                    ])
                    bonus = int(clean_cols[19])

                    # DB에서 combination_id 및 zone_id 매핑 (복합 인덱스로 0.1ms 이내 고속 스캔)
                    combination_id, zone_id = get_combination_info(cursor, nums)

                    batch_data.append((
                        round_no, draw_date,
                        nums[0], nums[1], nums[2], nums[3], nums[4], nums[5],
                        bonus, combination_id, zone_id
                    ))

                    success_count += 1

                    # 100건 단위 화면 로깅 및 커밋 (INSERT OR REPLACE로 멱등성 보장)
                    if success_count % 100 == 0:
                        print(f" -> [맵핑 진행] {round_no}회차 완료 (추첨일: {draw_date})")
                        cursor.executemany("""
                            INSERT OR REPLACE INTO WINNING_HISTORY 
                            (round_no, draw_date, num1, num2, num3, num4, num5, num6, bonus, combination_id, zone_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, batch_data)
                        conn.commit()
                        batch_data.clear()

                except Exception as parse_err:
                    print(f"[데이터 오염 경고] {round_no}회차 처리 중 파싱 오류 (스킵): {parse_err}")
                    continue

        # 잔여 데이터 일괄 Insert (INSERT OR REPLACE 멱등성 유지)
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
        print(f"[벌크 동기화 결과 리포트]")
        print(f" - 신규 적재 건수 : {success_count}건")
        print(f" - DB 최신 회차  : {final_latest_round}회")
        print(f" - 총 소요 시간  : {elapsed:.2f}초")
        print("=" * 60)

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"\n[치명적 오류] 벌크 동기화 파이프라인 실패: {e}")
        traceback.print_exc()

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sync_winning_history_bulk()