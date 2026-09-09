import sqlite3
import numpy as np
import time
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")
NPY_CACHE_PATH = os.path.join(BASE_DIR, "lotto_master_pool.npy")
TOTAL_COMBINATIONS = 8145060


class HardFilterEngine:
    def __init__(self, db_path: str = DB_NAME):
        """DB 커넥션 및 메모리 최적화 바이너리 캐시 로드"""
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"[시스템 예외] DB 파일을 찾을 수 없습니다: {db_path}")
            
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.combos = None
        self.comb_ids = None
        self.zone_ids = None
        
        # 최신 당첨 이력 저장소
        self.history_rows = []
        
    def _ensure_and_load_master_pool(self):
        """
        [고속 I/O 핵심 로직] 
        .npy 캐시 파일이 존재하면 0.15초 만에 메모리에 로드하고, 
        없을 경우 SQLite에서 최초 1회 일괄 추출하여 캐시를 자동 빌드합니다.
        """
        if os.path.exists(NPY_CACHE_PATH):
            print(" - [I/O] 마스터 조합 바이너리 캐시(.npy) 고속 로드 중...")
            start_load = time.perf_counter()
            self.combos = np.load(NPY_CACHE_PATH)
            print(f" - [I/O] 캐시 로드 완료: {self.combos.shape} (소요 시간: {time.perf_counter() - start_load:.3f}초)")
        else:
            print(" - [I/O] 캐시 파일 부재. SQLite에서 8,145,060건 최초 1회 바이너리 빌드 시작...")
            start_build = time.perf_counter()
            cursor = self.conn.cursor()
            cursor.execute("SELECT num1, num2, num3, num4, num5, num6 FROM LOTTO_COMBINATIONS_POOL ORDER BY combination_id ASC;")
            rows = cursor.fetchall()
            
            self.combos = np.array(rows, dtype=np.uint8)
            np.save(NPY_CACHE_PATH, self.combos)
            print(f" - [I/O] 바이너리 캐시 생성 및 저장 완료: {NPY_CACHE_PATH} (소요 시간: {time.perf_counter() - start_build:.2f}초)")

        # 고정된 ID 및 Zone 배열 벡터 생성 (무복사 O(1) 메모리 할당)
        self.comb_ids = np.arange(1, TOTAL_COMBINATIONS + 1, dtype=np.uint32)
        self.zone_ids = np.minimum((np.arange(TOTAL_COMBINATIONS, dtype=np.uint32) // 814506) + 1, 10).astype(np.uint8)

    def _fetch_history_data(self):
        """역대 당첨 이력 및 직전 회차 정보 로드 (zone_id 포함)"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT round_no, num1, num2, num3, num4, num5, num6, bonus, combination_id, zone_id FROM WINNING_HISTORY ORDER BY round_no DESC;")
        self.history_rows = cursor.fetchall()
        
        if not self.history_rows:
            raise ValueError("[데이터 결측] WINNING_HISTORY 테이블이 비어있습니다. 동기화를 먼저 수행하세요.")

        # 직전 회차 정보
        latest_row = self.history_rows[0]
        self.latest_round = latest_row[0]
        self.latest_win_nums = np.array(latest_row[1:7], dtype=np.uint8)
        self.latest_win_id = latest_row[8]
        
        # 역대 1등 조합 ID 배열
        self.past_win_ids = np.array([r[8] for r in self.history_rows if r[8] is not None], dtype=np.uint32)

    def apply_filters(self, adjacent_block_size: int = 10000, dynamic_filters: dict = None) -> tuple:
        """
        Pure NumPy C-Backend를 통한 7중 기본 Hard Filter + 동적 패턴 필터(최대 6중) 동시 타격.
        반환값: (survivor_indices, combos, comb_ids, zone_ids)
        """
        self._ensure_and_load_master_pool()
        self._fetch_history_data()
        
        start_time = time.perf_counter()
        total_count = len(self.combos)
        print(f" - [연산] 순수 NumPy 1단계 벡터라이징 필터 엔진 가동 (대상: {total_count:,}건)")

        # ---------------------------------------------------------
        # [기본 Filter 04] 당첨 이력 제외 (과거 1등 조합 영구 배제)
        # ---------------------------------------------------------
        mask_f04 = ~np.isin(self.comb_ids, self.past_win_ids)
        
        # ---------------------------------------------------------
        # [기본 Filter 02] 인접 ID 블록 제외 (직전 당첨 ID 기준 앞뒤 N개 제외)
        # ---------------------------------------------------------
        mask_f02 = ~((self.comb_ids >= self.latest_win_id - adjacent_block_size) & 
                     (self.comb_ids <= self.latest_win_id + adjacent_block_size))

        # ---------------------------------------------------------
        # [기본 Filter 03] 이월수 중복 제한 (직전 회차 당첨 번호 3개 이상 중복 배제)
        # ---------------------------------------------------------
        match_counts = np.isin(self.combos, self.latest_win_nums).sum(axis=1)
        mask_f03 = match_counts < 3

        # ---------------------------------------------------------
        # [기본 Filter 08] 연속 번호 제한 (3연속 번호 출현 시 배제)
        # ---------------------------------------------------------
        mask_f08 = ~(
            (self.combos[:, 2] - self.combos[:, 0] == 2) |
            (self.combos[:, 3] - self.combos[:, 1] == 2) |
            (self.combos[:, 4] - self.combos[:, 2] == 2) |
            (self.combos[:, 5] - self.combos[:, 3] == 2)
        )

        # ---------------------------------------------------------
        # [기본 Filter 09] 동일 끝수 제한 (일의 자리가 같은 번호 3개 이상 배제)
        # ---------------------------------------------------------
        mod_10 = self.combos % 10
        max_mod_counts = (mod_10[..., None] == np.arange(10, dtype=np.uint8)).sum(axis=1).max(axis=1)
        mask_f09 = max_mod_counts < 3

        # ---------------------------------------------------------
        # [기본 Filter 11] 한 구간 몰림 방지 (10단위 구간 4개 이상 몰림 배제)
        # ---------------------------------------------------------
        deciles = (self.combos - 1) // 10
        max_decile_counts = (deciles[..., None] == np.arange(5, dtype=np.uint8)).sum(axis=1).max(axis=1)
        mask_f11 = max_decile_counts < 4

        # ---------------------------------------------------------
        # [기본 Filter 12] AC값(산술 복잡도) 필터 (유니크 차이값 기반 AC >= 7)
        # ---------------------------------------------------------
        pairs = [(0,1), (0,2), (0,3), (0,4), (0,5), (1,2), (1,3), (1,4), (1,5), (2,3), (2,4), (2,5), (3,4), (3,5), (4,5)]
        diffs = np.column_stack([self.combos[:, j] - self.combos[:, i] for i, j in pairs])
        sorted_diffs = np.sort(diffs, axis=1)
        unique_diff_counts = (np.diff(sorted_diffs, axis=1) > 0).sum(axis=1) + 1
        ac_values = unique_diff_counts - 5
        mask_f12 = ac_values >= 7

        # ---------------------------------------------------------
        # 최종 생존 풀(Survivor Indices) 1차 압축
        # ---------------------------------------------------------
        final_mask = mask_f04 & mask_f02 & mask_f03 & mask_f08 & mask_f09 & mask_f11 & mask_f12

        # =========================================================
        # [동적 패턴 필터 (Dynamic Hard-Filters) 적용]
        # =========================================================
        if dynamic_filters:
            # 5회차 스캔 의존 필터군 (Filter 14 ~ 18)
            if len(self.history_rows) >= 5:
                # [Filter 14] 3주 연속 이월 쌍 제외 (13, 32 딜레마)
                if dynamic_filters.get("pair_ban"):
                    prev1 = set(self.history_rows[0][1:7])
                    prev2 = set(self.history_rows[1][1:7])
                    common_pairs = prev1.intersection(prev2)
                    if len(common_pairs) >= 2:
                        mask_f14 = np.isin(self.combos, list(common_pairs)).sum(axis=1) < 2
                        final_mask &= mask_f14
                        print(f"   -> [동적 적용] 연속 이월 쌍 {sorted(list(common_pairs))} 동시 포함 조합 원천 배제 완료.")

                # [Filter 15] 시작 번호(1구) 극단적 이탈 배제
                if dynamic_filters.get("start_num_limit"):
                    mask_f15 = self.combos[:, 0] <= 15
                    final_mask &= mask_f15
                    print(f"   -> [동적 적용] 시작 번호(1구) 16 이상 극단적 이탈 패턴 배제 완료.")

                # [Filter 16] 직전 회차 이웃수 4개 이상 배제
                if dynamic_filters.get("adjacent_limit"):
                    prev1 = set(self.history_rows[0][1:7])
                    neighbors = {x - 1 for x in prev1 if x > 1} | {x + 1 for x in prev1 if x < 45}
                    neighbors = neighbors - prev1
                    if neighbors:
                        mask_f16 = np.isin(self.combos, list(neighbors)).sum(axis=1) <= 3
                        final_mask &= mask_f16
                        print(f"   -> [동적 적용] 직전 회차 이웃수 풀 4개 이상 편중 조합 배제 완료.")

                # [Filter 17] 단기 초과열 번호 과적합 방지
                if dynamic_filters.get("hyper_hot_ban"):
                    all_recent_nums = []
                    for r in self.history_rows[:5]:
                        all_recent_nums.extend(r[1:7])
                    counts = np.bincount(all_recent_nums, minlength=46)
                    hyper_hot = np.where(counts >= 3)[0]
                    if len(hyper_hot) > 0:
                        mask_f17 = np.isin(self.combos, hyper_hot).sum(axis=1) <= 1
                        final_mask &= mask_f17
                        print(f"   -> [동적 적용] 단기 초과열 번호 2개 이상 독점 조합 배제 완료.")

                # [Filter 18] 끝 번호(6구) 극단적 이탈 배제
                if dynamic_filters.get("end_num_limit"):
                    mask_f18 = self.combos[:, 5] >= 31
                    final_mask &= mask_f18
                    print(f"   -> [동적 적용] 끝 번호(6구) 30 이하 극단적 이탈 패턴 배제 완료.")

            # 10회차 스캔 의존 필터군 (Filter 19)
            if len(self.history_rows) >= 10:
                # [Filter 19] 10주 누적 초과열 Zone 배제
                if dynamic_filters.get("hyper_hot_zone_ban"):
                    recent_10_zones = [r[9] for r in self.history_rows[:10]]
                    counts = np.bincount(recent_10_zones, minlength=11)
                    hyper_hot_zones = np.where(counts >= 3)[0]
                    if len(hyper_hot_zones) > 0:
                        mask_f19 = ~np.isin(self.zone_ids, hyper_hot_zones)
                        final_mask &= mask_f19
                        print(f"   -> [동적 적용] 10주 누적 초과열 Zone {list(hyper_hot_zones)} 소속 조합 통째로 배제 완료.")

        survivor_indices = np.where(final_mask)[0]

        elapsed = time.perf_counter() - start_time
        self._print_audit_report(total_count, len(survivor_indices), elapsed)

        return survivor_indices, self.combos, self.comb_ids, self.zone_ids

    def _print_audit_report(self, total_count: int, survivor_count: int, elapsed: float):
        """Hard Filter 연산 결과 통계 리포트"""
        drop_rate = (total_count - survivor_count) / total_count * 100
        
        print("\n" + "=" * 60)
        print(f"[Phase 2] Hard Filter 연산 리포트")
        print("=" * 60)
        print(f" - 전체 대상 조합 : {total_count:,} 개")
        print(f" - 필터 생존 조합 : {survivor_count:,} 개")
        print(f" - 폐기된 독소조합: {total_count - survivor_count:,} 개 (제거율: {drop_rate:.2f}%)")
        print(f" - 순수 벡터 연산 : {elapsed:.3f} 초")
        print("=" * 60)

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    engine = HardFilterEngine()
    try:
        survivor_indices, combos, comb_ids, zone_ids = engine.apply_filters(adjacent_block_size=10000)
    finally:
        engine.close()