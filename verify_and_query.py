"""
Module: verify_and_query.py
Version: 1.9
Last Updated: 2026-09-11

[모듈 책임]
- 데이터베이스(lotto.db) 물리 무결성, 경계값(1, 8145060), Zone별 데이터 분포 정합성 검증.
- 복합 인덱스(idx_zone, idx_nums)를 활용한 ID 기반 조회 및 6개 번호 역추적(Reverse Tracking) 속도 벤치마킹.
"""

import sqlite3
import os
import time
from typing import Optional, Tuple, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")


def get_connection(db_path: str = DB_NAME) -> sqlite3.Connection:
    """
    [v1.9 유지] 데이터베이스 연결 객체 반환 및 Row Factory 설정 모듈.

    SQLite3 데이터베이스 커넥션을 생성하고, 컬럼명으로 데이터에 접근할 수 있도록
    row_factory를 sqlite3.Row로 설정하여 반환한다.

    Args:
        db_path (str, optional): 연결할 데이터베이스 파일 경로. 기본값은 DB_NAME.

    Returns:
        sqlite3.Connection: 설정이 완료된 데이터베이스 커넥션 객체.

    Raises:
        FileNotFoundError: 지정된 경로에 DB 파일이 존재하지 않을 경우.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"DB 파일을 찾을 수 없습니다: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def verify_database_integrity() -> None:
    """
    [v1.9 유지] 전체 데이터 건수, 양 끝단(Boundary) 데이터, Zone별 분포 검증 모듈.

    마스터 조합 풀(LOTTO_COMBINATIONS_POOL)의 레코드 수가 정확히 8,145,060건인지 확인하고,
    처음(1)과 끝(8145060) 경계값 및 10개 구역(Zone)의 분할 정합성을 집계하여 출력한다.

    Args:
        None

    Returns:
        None
    """
    print("=" * 60)
    print("[1] 데이터베이스 정합성 및 무결성 검증")
    print("=" * 60)

    start_time = time.time()
    with get_connection() as conn:
        cursor = conn.cursor()

        # 1. 총 행 수 검증
        cursor.execute("SELECT COUNT(*) AS total_count FROM LOTTO_COMBINATIONS_POOL;")
        total_count = cursor.fetchone()["total_count"]

        # 2. 첫 번째 및 마지막 조합 조회
        cursor.execute("""
                       SELECT combination_id,
                              num1,
                              num2,
                              num3,
                              num4,
                              num5,
                              num6,
                              zone_id
                       FROM LOTTO_COMBINATIONS_POOL
                       WHERE combination_id IN (1, 8145060)
                       ORDER BY combination_id ASC;
                       """)
        boundaries = cursor.fetchall()

        # 3. Zone별 분포도 집계
        cursor.execute("""
                       SELECT zone_id, COUNT(*) AS cnt, MIN(combination_id) AS min_id, MAX(combination_id) AS max_id
                       FROM LOTTO_COMBINATIONS_POOL
                       GROUP BY zone_id
                       ORDER BY zone_id ASC;
                       """)
        zone_stats = cursor.fetchall()

    elapsed = time.time() - start_time

    # 출력 리포트
    print(f"* 총 데이터 적재량: {total_count:,} / 8,145,060 건 (상태: {'정상' if total_count == 8145060 else '오류'})")
    print(f"* 검증 연산 소요 시간: {elapsed:.4f}초\n")

    print("[경계값 데이터 확인]")
    for row in boundaries:
        print(f" - ID {row['combination_id']:>7}: [{row['num1']:02d}, {row['num2']:02d}, {row['num3']:02d}, "
              f"{row['num4']:02d}, {row['num5']:02d}, {row['num6']:02d}] | Zone: {row['zone_id']}")

    print("\n[Zone별(10분할) 데이터 분포]")
    print(f"{'Zone':<6} | {'건수(Rows)':<12} | {'시작 ID':<12} | {'종료 ID':<12}")
    print("-" * 50)
    for z in zone_stats:
        print(f"Zone {z['zone_id']:<2} | {z['cnt']:<12,} | {z['min_id']:<12,} | {z['max_id']:<12,}")
    print("=" * 60 + "\n")


def query_by_id(combination_id: int) -> Optional[sqlite3.Row]:
    """
    [v1.9 유지] 고유 Combination ID로 조합 데이터 단건 조회 함수.

    Primary Key인 combination_id를 사용하여 단일 로또 조합 및 구역(zone_id) 정보를
    조회하고 밀리초(ms) 단위의 쿼리 수행 속도를 벤치마킹한다.

    Args:
        combination_id (int): 조회할 대상 조합의 고유 시퀀스 ID (1 ~ 8,145,060).

    Returns:
        Optional[sqlite3.Row]: 조회 성공 시 Row 객체 반환, 실패 또는 범위 초과 시 None 반환.
    """
    if not (1 <= combination_id <= 8145060):
        print(f"[경고] ID 범위 초과: 1 ~ 8,145,060 사이만 유효합니다. (입력값: {combination_id})")
        return None

    start_time = time.perf_counter()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT combination_id,
                              num1,
                              num2,
                              num3,
                              num4,
                              num5,
                              num6,
                              zone_id
                       FROM LOTTO_COMBINATIONS_POOL
                       WHERE combination_id = ?;
                       """, (combination_id,))
        result = cursor.fetchone()

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    if result:
        print(f"[ID 조회 성공] ID: {result['combination_id']} -> "
              f"[{result['num1']:02d}, {result['num2']:02d}, {result['num3']:02d}, "
              f"{result['num4']:02d}, {result['num5']:02d}, {result['num6']:02d}] "
              f"| Zone: {result['zone_id']} (조회 속도: {elapsed_ms:.4f}ms)")
    else:
        print(f"[조회 실패] ID {combination_id}에 해당하는 데이터가 없습니다.")
    return result


def query_by_numbers(numbers: List[int]) -> Optional[sqlite3.Row]:
    """
    [v1.9 유지] 6개 번호로 Combination ID 및 Zone 역추적 조회 함수.

    전달받은 6개의 숫자 배열을 오름차순으로 정렬한 뒤 복합 인덱스(idx_nums)를 활용하여
    마스터 조합 풀에서 원본 조합 ID 및 zone_id를 역추적한다.

    Args:
        numbers (List[int]): 1~45 사이의 고유한 6개 정수 리스트.

    Returns:
        Optional[sqlite3.Row]: 일치하는 조합 존재 시 Row 객체 반환, 실패 시 None 반환.
    """
    if len(numbers) != 6 or len(set(numbers)) != 6:
        print("[경고] 서로 다른 6개의 정수를 전달해야 합니다.")
        return None

    # 오름차순 정렬
    sorted_nums = sorted(numbers)

    start_time = time.perf_counter()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT combination_id,
                              num1,
                              num2,
                              num3,
                              num4,
                              num5,
                              num6,
                              zone_id
                       FROM LOTTO_COMBINATIONS_POOL
                       WHERE num1 = ?
                         AND num2 = ?
                         AND num3 = ?
                         AND num4 = ?
                         AND num5 = ?
                         AND num6 = ?;
                       """, tuple(sorted_nums))
        result = cursor.fetchone()

    elapsed_ms = (time.perf_counter() - start_time) * 1000

    if result:
        print(f"[번호 역추적 성공] {sorted_nums} -> "
              f"ID: {result['combination_id']:,} | Zone: {result['zone_id']} (조회 속도: {elapsed_ms:.4f}ms)")
    else:
        print(f"[조회 실패] 번호 {sorted_nums}에 일치하는 조합이 존재하지 않습니다.")
    return result


def query_sample_by_zone(zone_id: int, limit: int = 5) -> List[sqlite3.Row]:
    """
    [v1.9 유지] 특정 구역(Zone) 내 상위 N개 샘플 조합 조회 함수.

    인덱스(idx_zone)를 활용하여 요청된 zone_id에 해당하는 로또 조합 샘플을 지정한
    limit 개수만큼 고속 추출한다.

    Args:
        zone_id (int): 샘플링할 대상 구역 ID (1 ~ 10).
        limit (int, optional): 추출할 최대 샘플 개수. 기본값은 5.

    Returns:
        List[sqlite3.Row]: 추출된 조합 Row 객체들의 리스트.
    """
    if not (1 <= zone_id <= 10):
        print("[경고] Zone ID는 1부터 10 사이여야 합니다.")
        return []

    start_time = time.perf_counter()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
                       SELECT combination_id,
                              num1,
                              num2,
                              num3,
                              num4,
                              num5,
                              num6,
                              zone_id
                       FROM LOTTO_COMBINATIONS_POOL
                       WHERE zone_id = ? LIMIT ?;
                       """, (zone_id, limit))
        results = cursor.fetchall()

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    print(f"\n[Zone {zone_id} 샘플 {limit}개 추출 (속도: {elapsed_ms:.4f}ms)]")
    for row in results:
        print(f" - ID {row['combination_id']:>7}: [{row['num1']:02d}, {row['num2']:02d}, "
              f"{row['num3']:02d}, {row['num4']:02d}, {row['num5']:02d}, {row['num6']:02d}]")
    return results


if __name__ == "__main__":
    # 1. DB 전체 상태 및 정합성 검증
    verify_database_integrity()

    # 2. 특정 Combination ID 단건 조회 테스트
    print("[2] ID 단건 조회 테스트")
    query_by_id(1)
    query_by_id(4072530)  # 정중앙 ID
    query_by_id(8145060)
    print()

    # 3. 6개 번호 입력 기반 ID/Zone 역추적 조회 테스트 (인덱스 성능 검증)
    print("[3] 번호 역추적 조회 테스트")
    query_by_numbers([1, 2, 3, 4, 5, 6])
    query_by_numbers([7, 12, 19, 23, 34, 42])
    query_by_numbers([40, 41, 42, 43, 44, 45])
    print()

    # 4. Zone 기반 데이터 샘플링 테스트
    print("[4] 구역(Zone)별 데이터 샘플링 테스트")
    query_sample_by_zone(zone_id=5, limit=3)