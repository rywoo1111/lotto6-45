"""
Module: init_db.py
Version: 1.9
Last Updated: 2026-09-11

[모듈 책임]
- lotto.db 마스터 DB 생성 및 3개 핵심 테이블 스키마(LOTTO_COMBINATIONS_POOL, WINNING_HISTORY, WEEKLY_AUDIT_LOG) 구축.
- 전체 8,145,060개 번호 조합 및 구역(zone_id) 분할 계산, 10만 건 단위 배치 삽입.
- 데이터베이스 벤치마크 및 검색 성능 최적화를 위한 복합 인덱스(idx_zone, idx_nums) 생성.
"""
import sqlite3
import itertools
import time
import os
import traceback

# D: 드라이브 프로젝트 폴더 내 lotto.db 생성
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")


def init_database() -> None:
    """
    [v1.9] 마스터 데이터베이스 스키마 초기화 및 814만 마스터 풀 생성 함수.

    기존 단일 커넥션 방식의 트랜잭션 위험성을 제거하고, try-except-finally 블록을 통해
    대용량 I/O 도중 예외 발생 시 안전하게 롤백(Rollback) 및 자원을 반환하도록 리팩토링 됨.

    Args:
        None

    Returns:
        None
    """
    print(f"DB 생성 경로: {DB_NAME}")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    try:
        # 성능 최적화 PRAGMA 설정
        cursor.execute("PRAGMA synchronous = OFF;")
        cursor.execute("PRAGMA journal_mode = MEMORY;")

        print("[1/3] 테이블 스키마 생성 중...")

        # 1. 마스터 조합 풀 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS LOTTO_COMBINATIONS_POOL (
                combination_id INTEGER PRIMARY KEY,
                num1 INTEGER NOT NULL,
                num2 INTEGER NOT NULL,
                num3 INTEGER NOT NULL,
                num4 INTEGER NOT NULL,
                num5 INTEGER NOT NULL,
                num6 INTEGER NOT NULL,
                zone_id INTEGER NOT NULL
            );
        """)

        # 2. 역대 당첨 이력 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS WINNING_HISTORY (
                round_no INTEGER PRIMARY KEY,
                draw_date TEXT,
                num1 INTEGER NOT NULL,
                num2 INTEGER NOT NULL,
                num3 INTEGER NOT NULL,
                num4 INTEGER NOT NULL,
                num5 INTEGER NOT NULL,
                num6 INTEGER NOT NULL,
                bonus INTEGER NOT NULL,
                combination_id INTEGER,
                zone_id INTEGER
            );
        """)

        # 3. 주간 검증 및 감사 로그 테이블
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS WEEKLY_AUDIT_LOG (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_round_no INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                combination_id INTEGER NOT NULL,
                num1 INTEGER,
                num2 INTEGER,
                num3 INTEGER,
                num4 INTEGER,
                num5 INTEGER,
                num6 INTEGER,
                soft_score REAL,
                is_final_top10 BOOLEAN DEFAULT 0,
                match_count INTEGER,
                bonus_matched BOOLEAN,
                winning_rank INTEGER
            );
        """)

        print("[2/3] 8,145,060개 마스터 번호 조합 생성 및 DB 삽입 시작...")
        start_time = time.time()

        BATCH_SIZE = 100000
        batch_data = []

        combinations = itertools.combinations(range(1, 46), 6)

        for idx, combo in enumerate(combinations, start=1):
            zone_id = min((idx - 1) // 814506 + 1, 10)
            batch_data.append((idx, combo[0], combo[1], combo[2], combo[3], combo[4], combo[5], zone_id))

            if len(batch_data) == BATCH_SIZE:
                cursor.executemany("""
                    INSERT INTO LOTTO_COMBINATIONS_POOL
                        (combination_id, num1, num2, num3, num4, num5, num6, zone_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, batch_data)
                batch_data.clear()
                print(f" -> {idx:,} / 8,145,060 조합 처리 완료...")

        if batch_data:
            cursor.executemany("""
                INSERT INTO LOTTO_COMBINATIONS_POOL
                    (combination_id, num1, num2, num3, num4, num5, num6, zone_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, batch_data)

        print(f"조합 데이터 삽입 완료! (소요 시간: {time.time() - start_time:.2f}초)")

        print("[3/3] 조회 속도 최적화 인덱스 생성 중...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_zone ON LOTTO_COMBINATIONS_POOL (zone_id);")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_nums ON LOTTO_COMBINATIONS_POOL (num1, num2, num3, num4, num5, num6);")

        conn.commit()
        print(f"모든 DB 초기화 작업이 성공적으로 끝났습니다. (생성 파일: {DB_NAME})")

    except Exception as e:
        conn.rollback()
        print(f"\n[오류] 데이터베이스 초기화 중 치명적 예외 발생. 롤백을 수행합니다.\n{e}")
        traceback.print_exc()

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    init_database()