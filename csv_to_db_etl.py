"""
Module: csv_to_db_etl.py
Version: 1.9
Last Updated: 2026-09-11

[모듈 책임]
- 디렉토리 내의 최신 오프라인 로또 당첨 CSV 파일을 자동 탐지.
- CP949 / UTF-8 인코딩 자동 감지 및 필수 컬럼 유효성 검증.
- 마스터 조합 풀(LOTTO_COMBINATIONS_POOL) 복합 인덱스 역추적을 통한 combination_id 및 zone_id 매핑.
- INSERT OR REPLACE 멱등적 적재를 통한 WINNING_HISTORY 동기화.
"""
import sqlite3
import pandas as pd
import time
import os
import glob
import traceback
from typing import Tuple, List

# DB 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")


def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    """
    [v1.9] WAL 모드 및 정규 동기화 PRAGMA가 적용된 SQLite 커넥션 반환.

    Args:
        db_path (str): SQLite 데이터베이스 파일 절대 경로.

    Returns:
        sqlite3.Connection: 설정이 완료된 DB 커넥션 객체.

    Raises:
        FileNotFoundError: 대상 DB 파일이 디스크에 존재하지 않을 경우 발생.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"[시스템 예외] DB 파일을 찾을 수 없습니다: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def find_target_csv() -> str:
    """
    [v1.9] 프로젝트 루트 디렉토리 내에서 최근 수정된 CSV 파일을 자동 탐지.

    Args:
        None

    Returns:
        str: 최신 CSV 파일의 절대 경로.

    Raises:
        FileNotFoundError: 디렉토리 내에 .csv 파일이 전무할 경우 발생.
    """
    csv_files = glob.glob(os.path.join(BASE_DIR, "*.csv"))
    if not csv_files:
        raise FileNotFoundError("[데이터 결측] 현재 디렉토리에 CSV 파일이 존재하지 않습니다.")

    # 여러 개의 CSV가 존재할 경우, 가장 최근에 수정/생성된 파일을 선택
    csv_files.sort(key=os.path.getmtime, reverse=True)
    target_file = csv_files[0]
    print(f" - [I/O] 타겟 CSV 파일 자동 탐지 완료: {os.path.basename(target_file)}")
    return target_file


def extract_data_from_csv(csv_path: str) -> pd.DataFrame:
    """
    [v1.9] CSV 파일을 읽고 CP949 및 UTF-8 인코딩을 순차 시도하여 DataFrame으로 반환.

    Args:
        csv_path (str): 로드할 CSV 파일 경로.

    Returns:
        pd.DataFrame: 적재된 판다스 데이터프레임.
    """
    try:
        df = pd.read_csv(csv_path, encoding='cp949')
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, encoding='utf-8')
    return df


def sync_csv_to_db() -> None:
    """
    [v1.9] 오프라인 정적 CSV 기반 당첨 이력 Bulk ETL 파이프라인.

    마스터 풀 인덱스 스캔을 통해 combination_id/zone_id를 역추적하고,
    1,000건 단위 배치 커밋으로 WINNING_HISTORY 테이블에 멱등 적재한다.

    Args:
        None

    Returns:
        None
    """
    print("=" * 60)
    print("[1] 오프라인 정적 파일 기반 당첨 이력 Bulk ETL 시작")
    print("=" * 60)
    start_time = time.time()

    try:
        csv_path = find_target_csv()
        df = extract_data_from_csv(csv_path)
    except Exception as e:
        print(f"[파일 로드 실패] {e}")
        return

    # 컬럼명 공백 제거 (사용자 수동 변환 과정에서 발생할 수 있는 휴먼 에러 방어)
    df.columns = df.columns.str.strip()

    # 사용자가 제공한 파일 포맷에 맞춘 필수 컬럼 검증
    required_cols = ['회차', 'num1', 'num2', 'num3', 'num4', 'num5', 'num6', '보너스']
    missing_cols = [col for col in required_cols if col not in df.columns]

    if missing_cols:
        print(f"[파싱 오류] 첨부하신 CSV 포맷에 다음 필수 컬럼이 누락되었습니다: {missing_cols}")
        print(f" -> 현재 인식된 컬럼 배열: {list(df.columns)}")
        return

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        batch_data = []
        success_count = 0
        MAX_BATCH_SIZE = 1000  # 벌크 사이즈 확장을 통해 디스크 I/O 최적화

        print(f" - [연산] 마스터 조합 풀 ID 매핑 및 역추적 진행 중...")

        for idx, row in df.iterrows():
            # 회차 추출 및 전처리 (문자열 내 쉼표 등 노이즈 제거)
            round_val = str(row['회차']).replace(',', '').strip()
            if not round_val.isdigit():
                continue

            round_no = int(round_val)

            # 추첨일 정보가 없는 변환된 CSV이므로 "Unknown"으로 강제 할당 (시스템 정합성 유지)
            draw_date = "Unknown"

            try:
                # 6개 번호 추출 후 오름차순 정렬 (DB 인덱스 복합키 스캔을 위한 필수 선행작업)
                nums = sorted([
                    int(row['num1']), int(row['num2']), int(row['num3']),
                    int(row['num4']), int(row['num5']), int(row['num6'])
                ])
                bonus = int(row['보너스'])

                # Base DB 참조 무결성 기반 ID 및 Zone 역추적 (O(log N) 고속 탐색)
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
                db_row = cursor.fetchone()

                if not db_row:
                    print(f"[데이터 무결성 경고] {round_no}회차 조합({nums})이 마스터 풀에 존재하지 않습니다.")
                    continue

                combination_id, zone_id = db_row[0], db_row[1]

                # INSERT OR REPLACE를 통해 멱등성(Idempotency) 확보. 여러 번 실행해도 중복 적재 방지
                batch_data.append((
                    round_no, draw_date,
                    nums[0], nums[1], nums[2], nums[3], nums[4], nums[5],
                    bonus, combination_id, zone_id
                ))
                success_count += 1

                # 벌크 인서트 처리
                if len(batch_data) >= MAX_BATCH_SIZE:
                    cursor.executemany("""
                        INSERT OR REPLACE INTO WINNING_HISTORY 
                        (round_no, draw_date, num1, num2, num3, num4, num5, num6, bonus, combination_id, zone_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, batch_data)
                    conn.commit()
                    batch_data.clear()

            except Exception as parse_err:
                print(f"[파싱 예외] {round_no}회차 데이터 오염. 해당 행을 스킵합니다: {parse_err}")
                continue

        # 잔여 데이터 Commit
        if batch_data:
            cursor.executemany("""
                INSERT OR REPLACE INTO WINNING_HISTORY 
                (round_no, draw_date, num1, num2, num3, num4, num5, num6, bonus, combination_id, zone_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, batch_data)
            conn.commit()

        # 최종 회차 집계
        cursor.execute("SELECT MAX(round_no) FROM WINNING_HISTORY;")
        latest_row = cursor.fetchone()
        final_latest_round = latest_row[0] if latest_row and latest_row[0] is not None else 0

        elapsed = time.time() - start_time

        print("\n" + "=" * 60)
        print(f"[ETL 처리 결과 리포트]")
        print(f" - CSV 총 적재 건수 : {success_count:,} 건")
        print(f" - DB 최신 회차     : {final_latest_round} 회")
        print(f" - 총 소요 시간     : {elapsed:.2f} 초 (O(1) 파일 I/O)")
        print("=" * 60)
        print(" -> [시스템] 당첨 이력 동기화가 성공적으로 완료되었습니다.")
        print(" -> Phase 1 기반 데이터 파이프라인 구축 완료.")

    except Exception as db_err:
        if conn:
            conn.rollback()
        print(f"\n[치명적 오류] ETL 트랜잭션 실패. 롤백을 수행합니다: {db_err}")
        traceback.print_exc()

    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sync_csv_to_db()