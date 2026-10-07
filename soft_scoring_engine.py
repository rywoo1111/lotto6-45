"""
Module: soft_scoring_engine.py
Version: 2.1
Last Updated: 2026-10-07

[모듈 책임]
- 1단계 생존 조합 대상 6대 Soft Scoring 가중치(100점 만점) C-레벨 벡터 채점 수행.
- 최근 10회차 기반 장기 미출현수(Cold Numbers) 및 중앙값 이하 미출현 구역(Cold Zones) 동적 산출.
- 상호 배타적 3단계 우선순위 큐(0순위: 마스킹 원천차단 -> 1순위: 지정 고정수 -> 2순위: 우량 풀 통합 Pure Shuffle) 파티셔닝.
- Random Noise 기반 동점자 무작위 정렬, np.random.choice 비복원 층화 추출 및 잔여 잉여량 무작위 배정.
- WEEKLY_AUDIT_LOG 적재 및 predictions/ 폴더 내 단일 통합 TXT/CSV 리빌드 파일 출력.
- [UI 개선] 독립 실행 시 짝수 행 노란색(ANSI Yellow) 교차 하이라이트 지원.
- [대용량 안전 모드] 100게임 이하 전량 콘솔 출력, 100게임 초과 시 상위/하위 요약 출력 및 CMD 렌더링 버퍼 보호.
"""
import sqlite3
import numpy as np
import time
import os
import sys
import traceback
from typing import Tuple, List, Dict, Optional, Any
from hard_filter_engine import HardFilterEngine

# 프로젝트 베이스 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "predictions")

# 기본 상수 설정
GAME_UNIT_PRICE = 1000
SCORE_CUTOFF = 70.0


class SoftScoringEngine:
    """
    [v2.1] 2단계 가중치 평가 및 3단계 확률론적 층화 다각화 추출 엔진.
    """

    def __init__(self, db_path: str = DB_NAME):
        """
        [v2.1] SoftScoringEngine 초기화 및 DB 커넥션, 출력 폴더 설정.

        Args:
            db_path (str): SQLite 데이터베이스 파일 경로.

        Raises:
            FileNotFoundError: 대상 DB 파일이 디스크에 존재하지 않을 경우 발생.
        """
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"[시스템 예외] DB 파일을 찾을 수 없습니다: {db_path}")

        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def _analyze_recent_history(self, lookback_rounds: int = 10) -> Tuple[np.ndarray, np.ndarray, int]:
        """
        [v2.1] 최근 N회차 당첨 데이터를 기반으로 장기 미출현수(Cold) 및 콜드 구역(Cold Zone) 동적 산출.

        - Cold Numbers: 최근 N회차 동안 1회도 등장하지 않은 미출현 번호군.
        - Cold Zones: 최근 N회차 구역별 출현 빈도가 중앙값(Median) 이하인 비과열 구역군.

        Args:
            lookback_rounds (int, optional): 분석할 과거 회차 수 (기본값: 10).

        Returns:
            Tuple[np.ndarray, np.ndarray, int]: (cold_numbers, cold_zones, latest_round)

        Raises:
            ValueError: WINNING_HISTORY 데이터가 비어있어 분석이 불가능할 경우 발생.
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"""
                SELECT round_no, num1, num2, num3, num4, num5, num6, zone_id 
                FROM WINNING_HISTORY 
                ORDER BY round_no DESC 
                LIMIT {lookback_rounds};
            """)
            rows = cursor.fetchall()
        finally:
            cursor.close()

        if not rows:
            raise ValueError("[데이터 결측] WINNING_HISTORY 데이터가 부족합니다.")

        latest_round = max(r[0] for r in rows)

        recent_nums_list = []
        zone_list = []
        for r in rows:
            recent_nums_list.extend(r[1:7])
            zone_list.append(r[7])

        recent_nums = np.unique(recent_nums_list)
        all_lotto_nums = np.arange(1, 46, dtype=np.uint8)
        cold_numbers = np.setdiff1d(all_lotto_nums, recent_nums).astype(np.uint8)

        zone_counts = np.bincount(zone_list, minlength=11)[1:]
        median_freq = np.median(zone_counts)
        cold_zones = (np.where(zone_counts <= median_freq)[0] + 1).astype(np.uint8)

        print(f" - [통계 분석] 직전 {lookback_rounds}회차 기준 통계 지표 산출:")
        print(f"   * 장기 미출현 번호({len(cold_numbers)}개): {list(cold_numbers)}")
        print(f"   * 콜드 분산 구역({len(cold_zones)}개): {list(cold_zones)} (Hot Zone 배제)")

        return cold_numbers, cold_zones, latest_round

    def score_survivors(self, survivor_indices: np.ndarray, combos: np.ndarray, comb_ids: np.ndarray,
                        zone_ids: np.ndarray,
                        user_excluded_zones: Optional[List[int]] = None,
                        user_bonus_score_zones: Optional[List[int]] = None) -> Tuple[
        np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
        """
        [v2.1] NumPy C-Backend 벡터 연산으로 1단계 생존 조합 6대 항목 가중치 채점.

        Args:
            survivor_indices (np.ndarray): 1단계 생존 조합 인덱스 배열.
            combos (np.ndarray): 전체 조합 마스터 2D 배열 (N, 6).
            comb_ids (np.ndarray): 전체 조합 고유 ID 1D 배열 (N,).
            zone_ids (np.ndarray): 전체 조합 Zone ID 1D 배열 (N,).
            user_excluded_zones (Optional[List[int]], optional): -100점 패널티 부여 대상 구역 목록 (Menu 6).
            user_bonus_score_zones (Optional[List[int]], optional): +20점 단순 가점 부여 대상 구역 목록 (Menu 7).

        Returns:
            Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
                (scores, surv_combos, surv_ids, surv_zones, target_round)
        """
        start_time = time.perf_counter()
        total_rows = len(survivor_indices)
        print(f"\n - [연산] Soft Scoring 가중치 평가 엔진 가동 (대상: {total_rows:,}건)")

        cold_numbers, cold_zones, latest_round = self._analyze_recent_history(lookback_rounds=10)
        target_round = latest_round + 1

        surv_combos = combos[survivor_indices]
        surv_ids = comb_ids[survivor_indices]
        surv_zones = zone_ids[survivor_indices]

        scores = np.zeros(total_rows, dtype=np.float32)

        # [Filter 13] 장기 미출현수 1~2개 포함 (+25점)
        if len(cold_numbers) > 0:
            cold_matches = np.isin(surv_combos, cold_numbers).sum(axis=1)
            scores += np.where((cold_matches >= 1) & (cold_matches <= 2), 25.0, 0.0)

        # [Filter 05] 총합 구간 100~175 (+20점)
        num_sums = surv_combos.sum(axis=1)
        scores += np.where((num_sums >= 100) & (num_sums <= 175), 20.0, 0.0)

        # [Filter 01] ID 구역 Cold Zone 속함 (+20점)
        scores += np.where(np.isin(surv_zones, cold_zones), 20.0, 0.0)

        # [Filter 10] 10단위 멸구간 1~2개 존재 (+15점)
        deciles = (surv_combos - 1) // 10
        decile_counts = (deciles[..., None] == np.arange(5, dtype=np.uint8)).sum(axis=1)
        empty_deciles_count = (decile_counts == 0).sum(axis=1)
        scores += np.where((empty_deciles_count >= 1) & (empty_deciles_count <= 2), 15.0, 0.0)

        # [Filter 06] 홀짝 비율 3:3, 4:2, 2:4 (+10점)
        odd_counts = (surv_combos % 2 == 1).sum(axis=1)
        scores += np.where((odd_counts >= 2) & (odd_counts <= 4), 10.0, 0.0)

        # [Filter 07] 고저 비율 3:3, 4:2, 2:4 (+10점)
        high_counts = (surv_combos >= 23).sum(axis=1)
        scores += np.where((high_counts >= 2) & (high_counts <= 4), 10.0, 0.0)

        # 수동 개입: Menu 6 Zone 원천 배제 (-100점 패널티)
        if user_excluded_zones:
            print(f" - [수동 개입 6번] 지정된 제외 Zone {user_excluded_zones} 대상 -100점 페널티 폭격 중...")
            penalty_mask = np.isin(surv_zones, user_excluded_zones)
            scores -= np.where(penalty_mask, 100.0, 0.0)

        # 수동 개입: Menu 7 Zone 단순 가점 (+20점)
        if user_bonus_score_zones:
            print(f" - [수동 개입 7번] 지정된 우대 Zone {user_bonus_score_zones} 대상 +20점 강제 가점 부여 중...")
            score_bonus_mask = np.isin(surv_zones, user_bonus_score_zones)
            scores += np.where(score_bonus_mask, 20.0, 0.0)

        elapsed = time.perf_counter() - start_time
        print(f" - [연산] 채점 완료 (소요 시간: {elapsed:.3f}초)")

        return scores, surv_combos, surv_ids, surv_zones, target_round

    def extract_top_recommendations(self, scores: np.ndarray, surv_combos: np.ndarray, surv_ids: np.ndarray,
                                    surv_zones: np.ndarray,
                                    output_count: int = 10,
                                    user_manual_nums: Optional[List[int]] = None, user_manual_count: int = 0,
                                    user_manual_mode: int = 0,
                                    user_exclude_nums: Optional[List[int]] = None, user_exclude_mode: int = 0,
                                    append_mode: bool = False, target_round: int = 0) -> Tuple[
        np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        [v2.1] 70점 컷오프 및 우선순위 파티셔닝 층화 추출 엔진.

        상호 배타적 우선순위 큐:
          0순위: 누적 추출(Append) 기출력 ID 및 수동 제외수 마스킹
          1순위: 수동 고정수 부분공간 무작위 선별 (원칙: 70점 이상 / 실용: 차상위 강제)
          2순위: 70점 이상 전체 우량 조합 풀 대상 확률적 완전 무작위 셔플 (Pure Shuffle)

        Args:
            scores (np.ndarray): 채점 완료된 점수 배열.
            surv_combos (np.ndarray): 생존 조합 번호 2D 배열.
            surv_ids (np.ndarray): 생존 조합 ID 1D 배열.
            surv_zones (np.ndarray): 생존 조합 Zone 1D 배열.
            output_count (int, optional): 목표 추출 게임 수 (기본값: 10).
            user_manual_nums (Optional[List[int]], optional): 수동 고정수 목록 (Menu 9/10).
            user_manual_count (int, optional): 고정수 할당 요청 게임 수.
            user_manual_mode (int, optional): 고정수 모드 (1: 원칙주의 컷오프 유지, 2: 실용주의 강제 보충).
            user_exclude_nums (Optional[List[int]], optional): 수동 제외수 목록 (Menu 11/12).
            user_exclude_mode (int, optional): 제외수 모드 (1: 원칙주의 컷오프 유지, 2: 실용주의 강제 보충).
            append_mode (bool, optional): 누적 추출 모드 여부.
            target_round (int, optional): 대상 추첨 회차.

        Returns:
            Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
                (top_combos, top_ids, top_zones, top_scores)
        """
        max_score = scores.max()
        cutoff_mask = scores >= SCORE_CUTOFF
        candidate_count = np.count_nonzero(cutoff_mask)

        print("\n" + "=" * 60)
        print(f"[Phase 3] 무작위 다각화 파티셔닝 추출 (목표: {output_count:,}게임)")
        print("=" * 60)
        print(f" - 채점 대상 풀   : {len(scores):,} 개")
        print(f" - {SCORE_CUTOFF}점 이상 후보 : {candidate_count:,} 개 ({candidate_count / len(scores) * 100:.2f}%)")
        print(f" - 시스템 최고 점수: {max_score:.1f} 점")

        selected_indices = []
        used_mask = np.zeros(len(scores), dtype=bool)

        # -------------------------------------------------------------
        # [우선순위 0순위 - A] 누적 추출 중복 배제 (Append Mode)
        # -------------------------------------------------------------
        if append_mode and target_round > 0:
            cursor = self.conn.cursor()
            try:
                cursor.execute("SELECT combination_id FROM WEEKLY_AUDIT_LOG WHERE target_round_no = ?;",
                               (target_round,))
                prev_ids = [row[0] for row in cursor.fetchall()]
            finally:
                cursor.close()

            if prev_ids:
                print(f" - [0순위: 중복 배제] 기 추출된 {len(prev_ids):,}개 조합이 중복 추출되지 않도록 영구 차단합니다.")
                already_extracted_mask = np.isin(surv_ids, prev_ids)
                used_mask |= already_extracted_mask
                print(f"   -> 성공: 기존 발급된 {np.count_nonzero(already_extracted_mask):,}개 조합을 대상 풀에서 격리 완료.")

        # -------------------------------------------------------------
        # [우선순위 0순위 - B] 제외수 Global Mask 적용
        # -------------------------------------------------------------
        if user_exclude_nums:
            print(f" - [0순위: 수동 제외] 제외수 {user_exclude_nums} 포함 조합 원천 배제 마스킹 중...")
            exclude_mask = np.zeros(len(surv_combos), dtype=bool)
            for num in user_exclude_nums:
                exclude_mask |= np.any(surv_combos == num, axis=1)

            excluded_count = np.count_nonzero(exclude_mask)
            used_mask |= exclude_mask
            print(f"   -> 성공: {excluded_count:,}개 조합이 제외수 규정에 의해 영구 차단되었습니다.")

        # -------------------------------------------------------------
        # [우선순위 1순위] 수동 고정수 부분공간 무작위 파티셔닝
        # -------------------------------------------------------------
        if user_manual_nums and user_manual_count > 0:
            print(f" - [1순위: 수동 할당] 고정수 {user_manual_nums} 보유 부분공간 매핑 중...")
            manual_mask = np.ones(len(surv_combos), dtype=bool)
            for num in user_manual_nums:
                manual_mask &= np.any(surv_combos == num, axis=1)

            manual_mask &= ~used_mask
            manual_indices = np.where(manual_mask)[0]

            if len(manual_indices) == 0:
                print(f"   -> [치명적 경고] 고정수를 포함하는 모든 조합이 필터/중복/제외수에 의해 전멸했습니다.")
            else:
                if user_manual_mode == 1:
                    valid_manual_mask = (scores[manual_indices] >= SCORE_CUTOFF)
                else:
                    valid_manual_mask = np.ones(len(manual_indices), dtype=bool)

                valid_manual_indices = manual_indices[valid_manual_mask]

                if len(valid_manual_indices) > 0:
                    manual_scores = scores[valid_manual_indices]
                    # 동점자 내 랜덤 정렬 보장을 위한 미세 난수 가산 (Tie-Breaking Noise)
                    noise = np.random.rand(len(manual_scores)) * 1e-5
                    sorted_idx = np.argsort(-(manual_scores + noise))
                    sorted_manual_indices = valid_manual_indices[sorted_idx]

                    take_count = min(user_manual_count, len(sorted_manual_indices), output_count)
                    sampled_manual = sorted_manual_indices[:take_count]

                    selected_indices.extend(sampled_manual)
                    used_mask[sampled_manual] = True
                    print(f"   -> 성공: 수동 고정수 그룹에서 {len(sampled_manual):,}게임 무작위 할당 완료")

        # -------------------------------------------------------------
        # [우선순위 2순위] AI 점수 통합 기반 완전 무작위 다각화 셔플 (Pure Shuffle)
        # -------------------------------------------------------------
        rem_quota = output_count - len(selected_indices)
        if rem_quota > 0:
            # 70점 컷오프 이상을 통과한 '전체 순수 우량 풀' 통합
            top_tier_mask = (scores >= SCORE_CUTOFF) & ~used_mask
            top_tier_indices = np.where(top_tier_mask)[0]

            if len(top_tier_indices) > 0:
                print(f" - [2순위: 통합 셔플] 통과된 우량 조합 풀 {len(top_tier_indices):,}개 대상 잔여 {rem_quota:,}게임 완전 무작위 셔플링...")

                # 잔여 요구량이 통과 가능한 풀보다 적으면 층화 무작위 추출 (가점 Zone이 포함된 고득점자가 유리)
                if len(top_tier_indices) >= rem_quota:
                    sampled = np.random.choice(top_tier_indices, size=rem_quota, replace=False)
                    selected_indices.extend(sampled)
                    used_mask[sampled] = True
                else:
                    # 통과 풀을 다 털어 넣어도 부족한 경우 전량 긁어모음
                    selected_indices.extend(top_tier_indices)
                    used_mask[top_tier_indices] = True

        # 2순위 셔플 후에도 목표 게임 수가 부족한 경우 잔여 게임 무작위 채우기
            if len(selected_indices) < output_count:
                need = output_count - len(selected_indices)
                remains = np.where((scores >= SCORE_CUTOFF) & ~used_mask)[0]

                if len(remains) >= need:
                    sampled = np.random.choice(remains, size=need, replace=False)
                    selected_indices.extend(sampled)
                    used_mask[sampled] = True
                else:
                    selected_indices.extend(remains)
                    used_mask[remains] = True
                    need = output_count - len(selected_indices)

                    # 70점 미만 컷오프 붕괴 보충 (실용주의 모드)
                    if need > 0 and user_exclude_mode != 1 and user_manual_mode != 1:
                        print(f"   -> [주의] 예산 달성({output_count:,}게임)을 위해 70점 미만 풀에서 {need:,}게임 무작위 추출하여 보충합니다.")
                        fallback_remains = np.where(~used_mask)[0]
                        fallback_scores = scores[fallback_remains]

                        noise = np.random.rand(len(fallback_scores)) * 1e-5
                        sorted_fallback_idx = fallback_remains[np.argsort(-(fallback_scores + noise))]

                        selected_indices.extend(sorted_fallback_idx[:need])
                        used_mask[sorted_fallback_idx[:need]] = True

        selected_indices = np.array(selected_indices[:output_count])

        return (surv_combos[selected_indices],
                surv_ids[selected_indices],
                surv_zones[selected_indices],
                scores[selected_indices])

    def export_and_log(self, top_combos: np.ndarray, top_ids: np.ndarray, top_zones: np.ndarray, top_scores: np.ndarray,
                       target_round: int, output_count: int = 10, append_mode: bool = False) -> None:
        """
        [v2.1] 최종 추천 번호 WEEKLY_AUDIT_LOG 적재 및 predictions/ 폴더 내 단일 통합 파일(TXT, CSV) 리빌드 출력.

        Args:
            top_combos (np.ndarray): 최종 선별된 조합 번호 배열 (N, 6).
            top_ids (np.ndarray): 최종 선별된 조합 ID 배열 (N,).
            top_zones (np.ndarray): 최종 선별된 조합 Zone 배열 (N,).
            top_scores (np.ndarray): 최종 선별된 조합 점수 배열 (N,).
            target_round (int): 대상 추첨 회차.
            output_count (int, optional): 신규 추출 게임 수 (기본값: 10).
            append_mode (bool, optional): 기존 회차 기록 보존 누적 모드 여부.

        Returns:
            None
        """
        cursor = self.conn.cursor()
        try:
            if not append_mode:
                cursor.execute("DELETE FROM WEEKLY_AUDIT_LOG WHERE target_round_no = ?;", (target_round,))

            audit_records = []
            for idx in range(len(top_combos)):
                c = top_combos[idx]
                audit_records.append((
                    target_round, int(top_ids[idx]),
                    int(c[0]), int(c[1]), int(c[2]), int(c[3]), int(c[4]), int(c[5]),
                    float(top_scores[idx]), 1
                ))

            cursor.executemany("""
                               INSERT INTO WEEKLY_AUDIT_LOG
                               (target_round_no, combination_id, num1, num2, num3, num4, num5, num6, soft_score,
                                is_final_top10)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                               """, audit_records)
            self.conn.commit()

            cursor.execute("""
                           SELECT w.num1,
                                  w.num2,
                                  w.num3,
                                  w.num4,
                                  w.num5,
                                  w.num6,
                                  w.combination_id,
                                  w.soft_score,
                                  c.zone_id
                           FROM WEEKLY_AUDIT_LOG w
                                    LEFT JOIN LOTTO_COMBINATIONS_POOL c ON w.combination_id = c.combination_id
                           WHERE w.target_round_no = ?
                             AND w.is_final_top10 = 1
                           ORDER BY w.id ASC;
                           """, (target_round,))
            all_records = cursor.fetchall()
        finally:
            cursor.close()

        total_count = len(all_records)
        dynamic_budget = total_count * GAME_UNIT_PRICE

        txt_path = os.path.join(OUTPUT_DIR, f"lotto_recommendations_{target_round}회.txt")
        csv_path = os.path.join(OUTPUT_DIR, f"lotto_recommendations_{target_round}회.csv")

        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write("===========================================================\n")
            f.write(f" [제 {target_round}회 로또 6/45 최종 추천 조합 - 총 누적 예산: {dynamic_budget:,}원]\n")
            f.write("===========================================================\n")
            for idx, r in enumerate(all_records):
                zone_id = r[8] if r[8] else 0
                label = chr(65 + (idx % 26)) + (str(idx // 26) if idx >= 26 else "")
                f.write(f"게임 {label:<5} : [{r[0]:02d}, {r[1]:02d}, {r[2]:02d}, "
                        f"{r[3]:02d}, {r[4]:02d}, {r[5]:02d}] "
                        f"(ID: {r[6]:>7} | Zone: {zone_id:>2} | 점수: {float(r[7]):.1f}점)\n")
            f.write("===========================================================\n")

        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write("game,num1,num2,num3,num4,num5,num6,combination_id,zone_id,soft_score\n")
            for idx, r in enumerate(all_records):
                label = chr(65 + (idx % 26)) + (str(idx // 26) if idx >= 26 else "")
                zone_id = r[8] if r[8] else 0
                f.write(f"{label},{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]},{r[6]},{zone_id},{r[7]:.1f}\n")

        print("\n - [DB 감사 로그] 신규 추출 조합 동기화 완료.")
        print(f" -> TXT/CSV 통합 출력 완료 : 총 {total_count:,}개 누적 게임이 하나의 파일에 기록되었습니다.")

    def close(self) -> None:
        """
        [v2.1] 데이터베이스 커넥션 자원 명시적 반환.

        Args:
            None

        Returns:
            None
        """
        if self.conn:
            self.conn.close()


if __name__ == "__main__":
    if os.name == 'nt':
        os.system('color')
    print("=" * 60)
    print("[엔드투엔드 파이프라인] Pure NumPy Hard Filter -> Soft Scoring 가동")
    print("=" * 60)

    total_start = time.perf_counter()
    default_output_count = 10

    hard_engine = HardFilterEngine()
    try:
        survivor_indices, combos, comb_ids, zone_ids = hard_engine.apply_filters(adjacent_block_size=10000)
    finally:
        hard_engine.close()

    scoring_engine = SoftScoringEngine()
    try:
        scores, surv_combos, surv_ids, surv_zones, target_round = scoring_engine.score_survivors(
            survivor_indices, combos, comb_ids, zone_ids
        )
        top_combos, top_ids, top_zones, top_scores = scoring_engine.extract_top_recommendations(
            scores, surv_combos, surv_ids, surv_zones, output_count=default_output_count
        )
        scoring_engine.export_and_log(top_combos, top_ids, top_zones, top_scores, target_round, default_output_count)

        total_extracted = len(top_combos)
        print("\n" + "=" * 60)
        print(f" [제 {target_round}회차 최종 선별된 최상위 {total_extracted:,}게임 (Cold Zone 최적 배분)]")
        print("=" * 60)

        def print_line(idx: int) -> None:
            c = top_combos[idx]
            label = chr(65 + (idx % 26)) + (str(idx // 26) if idx >= 26 else "")
            line_str = (f" 게임 {label:<5} : [{int(c[0]):02d}, {int(c[1]):02d}, {int(c[2]):02d}, "
                        f"{int(c[3]):02d}, {int(c[4]):02d}, {int(c[5]):02d}] "
                        f"| Zone {int(top_zones[idx]):>2} | 점수: {float(top_scores[idx]):.1f}점")
            if idx % 2 == 1:
                print(f"\033[93m{line_str}\033[0m")
            else:
                print(line_str)

        if total_extracted <= 100:
            for idx in range(total_extracted):
                print_line(idx)
        else:
            print(f" [대량 데이터 모드: 총 {total_extracted:,}게임 중 상위 10게임 및 하위 5게임 요약 출력]")
            for idx in range(10):
                print_line(idx)
            print(f"  ... [중간 {total_extracted - 15:,}개 게임 화면 출력 생략] ...")
            for idx in range(total_extracted - 5, total_extracted):
                print_line(idx)

        print("=" * 60)
        print(f" -> 전체 파이프라인 총 소요 시간: {time.perf_counter() - total_start:.2f}초")
        print("=" * 60)

    finally:
        scoring_engine.close()
