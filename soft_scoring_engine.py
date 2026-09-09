import sqlite3
import numpy as np
import time
import os
import sys
from typing import Tuple, List, Dict
from hard_filter_engine import HardFilterEngine

# 프로젝트 베이스 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "predictions")

# 기본 상수 설정 
GAME_UNIT_PRICE = 1000
SCORE_CUTOFF = 70.0  


class SoftScoringEngine:
    def __init__(self, db_path: str = DB_NAME):
        """DB 커넥션 및 출력 디렉토리 초기화"""
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"[시스템 예외] DB 파일을 찾을 수 없습니다: {db_path}")
            
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def _analyze_recent_history(self, lookback_rounds: int = 10) -> Tuple[np.ndarray, np.ndarray, int]:
        """최근 N회차 당첨 데이터를 기반으로 장기 미출현수(Cold) 및 콜드 구역(Cold Zone) 산출"""
        cursor = self.conn.cursor()
        cursor.execute(f"""
            SELECT round_no, num1, num2, num3, num4, num5, num6, zone_id 
            FROM WINNING_HISTORY 
            ORDER BY round_no DESC 
            LIMIT {lookback_rounds};
        """)
        rows = cursor.fetchall()
        
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

    def score_survivors(self, survivor_indices: np.ndarray, combos: np.ndarray, comb_ids: np.ndarray, zone_ids: np.ndarray, 
                        user_excluded_zones: List[int] = None, user_bonus_score_zones: List[int] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
        """NumPy 벡터 연산으로 생존 조합 가중치 채점"""
        start_time = time.perf_counter()
        total_rows = len(survivor_indices)
        print(f"\n - [연산] Soft Scoring 가중치 평가 엔진 가동 (대상: {total_rows:,}건)")

        cold_numbers, cold_zones, latest_round = self._analyze_recent_history(lookback_rounds=10)
        target_round = latest_round + 1

        surv_combos = combos[survivor_indices]
        surv_ids = comb_ids[survivor_indices]
        surv_zones = zone_ids[survivor_indices]

        scores = np.zeros(total_rows, dtype=np.float32)

        if len(cold_numbers) > 0:
            cold_matches = np.isin(surv_combos, cold_numbers).sum(axis=1)
            scores += np.where((cold_matches >= 1) & (cold_matches <= 2), 25.0, 0.0)

        num_sums = surv_combos.sum(axis=1)
        scores += np.where((num_sums >= 100) & (num_sums <= 175), 20.0, 0.0)

        scores += np.where(np.isin(surv_zones, cold_zones), 20.0, 0.0)

        deciles = (surv_combos - 1) // 10
        decile_counts = (deciles[..., None] == np.arange(5, dtype=np.uint8)).sum(axis=1)
        empty_deciles_count = (decile_counts == 0).sum(axis=1)
        scores += np.where((empty_deciles_count >= 1) & (empty_deciles_count <= 2), 15.0, 0.0)

        odd_counts = (surv_combos % 2 == 1).sum(axis=1)
        scores += np.where((odd_counts >= 2) & (odd_counts <= 4), 10.0, 0.0)

        high_counts = (surv_combos >= 23).sum(axis=1)
        scores += np.where((high_counts >= 2) & (high_counts <= 4), 10.0, 0.0)

        if user_excluded_zones:
            print(f" - [수동 개입 5번] 지정된 제외 Zone {user_excluded_zones} 대상 -100점 페널티 폭격 중...")
            penalty_mask = np.isin(surv_zones, user_excluded_zones)
            scores -= np.where(penalty_mask, 100.0, 0.0)

        if user_bonus_score_zones:
            print(f" - [수동 개입 6번] 지정된 우대 Zone {user_bonus_score_zones} 대상 +20점 강제 가점 부여 중...")
            score_bonus_mask = np.isin(surv_zones, user_bonus_score_zones)
            scores += np.where(score_bonus_mask, 20.0, 0.0)

        elapsed = time.perf_counter() - start_time
        print(f" - [연산] 채점 완료 (소요 시간: {elapsed:.3f}초)")

        return scores, surv_combos, surv_ids, surv_zones, target_round

    def extract_top_recommendations(self, scores: np.ndarray, surv_combos: np.ndarray, surv_ids: np.ndarray, surv_zones: np.ndarray, 
                                    user_bonus_quota_zones: List[int] = None, output_count: int = 10,
                                    user_manual_nums: List[int] = None, user_manual_count: int = 0, user_manual_mode: int = 0,
                                    user_exclude_nums: List[int] = None, user_exclude_mode: int = 0,
                                    append_mode: bool = False, target_round: int = 0) -> tuple:
        """점수 평가 컷오프 및 우선순위 파티셔닝 추출 엔진 (Stochastic 난수 다각화 메커니즘 적용)"""
        max_score = scores.max()
        cutoff_mask = scores >= SCORE_CUTOFF
        candidate_count = np.count_nonzero(cutoff_mask)
        
        print("\n" + "=" * 60)
        print(f"[Phase 3] 무작위 다각화 파티셔닝 추출 (목표: {output_count}게임)")
        print("=" * 60)
        print(f" - 채점 대상 풀   : {len(scores):,} 개")
        print(f" - {SCORE_CUTOFF}점 이상 후보 : {candidate_count:,} 개 ({candidate_count/len(scores)*100:.2f}%)")
        print(f" - 시스템 최고 점수: {max_score:.1f} 점")

        selected_indices = []
        used_mask = np.zeros(len(scores), dtype=bool) 

        # -------------------------------------------------------------
        # [우선순위 0순위 - A] 누적 추출 중복 배제 (Append Mode)
        # -------------------------------------------------------------
        if append_mode and target_round > 0:
            cursor = self.conn.cursor()
            cursor.execute("SELECT combination_id FROM WEEKLY_AUDIT_LOG WHERE target_round_no = ?;", (target_round,))
            prev_ids = [row[0] for row in cursor.fetchall()]
            if prev_ids:
                print(f" - [0순위: 중복 배제] 기 추출된 {len(prev_ids)}개 조합이 중복 추출되지 않도록 영구 차단합니다.")
                already_extracted_mask = np.isin(surv_ids, prev_ids)
                used_mask |= already_extracted_mask
                print(f"   -> 성공: 기존 발급된 {np.count_nonzero(already_extracted_mask)}개 조합을 대상 풀에서 격리 완료.")

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
            print(f" - [1순위: 수동 할당] 고정수 {user_manual_nums} 보유 부분공간 맵핑 중...")
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
                    # 등간격(Step) 슬라이싱 폐기 -> 상위 N개 무작위 표본 추출 반영 완료
                    sampled_manual = sorted_manual_indices[:take_count]
                    
                    selected_indices.extend(sampled_manual)
                    used_mask[sampled_manual] = True
                    print(f"   -> 성공: 수동 고정수 그룹에서 {len(sampled_manual)}게임 무작위 할당 완료")

        # -------------------------------------------------------------
        # [우선순위 2순위] 우대 Zone 구조적 할당 (Stochastic 셔플링)
        # -------------------------------------------------------------
        rem_quota = output_count - len(selected_indices)
        if user_bonus_quota_zones and rem_quota > 0:
            max_bonus_quota = max(1, int(rem_quota * 0.3))
            print(f" - [2순위: 우대 할당] 잔여 예산 기준 우대 Zone 최대 {max_bonus_quota}게임 주입 시도...")
            
            bonus_mask = np.isin(surv_zones, user_bonus_quota_zones)
            valid_bonus_mask = bonus_mask & (scores >= SCORE_CUTOFF) & ~used_mask
            valid_bonus_indices = np.where(valid_bonus_mask)[0]

            if len(valid_bonus_indices) > 0:
                bonus_scores = scores[valid_bonus_indices]
                noise = np.random.rand(len(bonus_scores)) * 1e-5
                sorted_idx = np.argsort(-(bonus_scores + noise))
                sorted_bonus_indices = valid_bonus_indices[sorted_idx]

                take_count = min(max_bonus_quota, len(sorted_bonus_indices))
                sampled_bonus = sorted_bonus_indices[:take_count]
                
                selected_indices.extend(sampled_bonus)
                used_mask[sampled_bonus] = True
                print(f"   -> 성공: 우대 Zone에서 {len(sampled_bonus)}게임 무작위 할당 완료")

        # -------------------------------------------------------------
        # [우선순위 3순위] AI 자연 우량 조합 균등 분산 무작위 할당 
        # -------------------------------------------------------------
        rem_quota = output_count - len(selected_indices)
        if rem_quota > 0:
            top_tier_mask = (scores == max_score) & ~used_mask
            top_tier_indices = np.where(top_tier_mask)[0]
            
            # 최상위 점수 풀이 부족하면 컷오프 통과 풀 전체로 확장
            if len(top_tier_indices) < rem_quota:
                top_tier_mask = (scores >= SCORE_CUTOFF) & ~used_mask
                top_tier_indices = np.where(top_tier_mask)[0]

            top_zones = surv_zones[top_tier_indices]
            unique_zones = np.unique(top_zones)
            
            if len(unique_zones) > 0:
                print(f" - [3순위: AI 다각화] 잔여 {rem_quota}게임 -> 자연 우량 {len(unique_zones)}개 구역 무작위 균등 분산 배치 중...")
                
                num_zones = len(unique_zones)
                quota_per_zone = rem_quota // max(1, num_zones)
                remainder = rem_quota % max(1, num_zones)

                # [편향 방어] 잉여 할당(+1)을 부여받을 Zone 인덱스를 무작위 비복원 추출
                bonus_zone_indices = set(np.random.choice(num_zones, size=remainder, replace=False)) if remainder > 0 else set()

                for i, z in enumerate(unique_zones):
                    z_indices = top_tier_indices[top_zones == z]
                    take_count = quota_per_zone + (1 if i in bonus_zone_indices else 0)
                    take_count = min(take_count, len(z_indices))
                    
                    if len(z_indices) > 0 and take_count > 0:
                        # 결정론적 [::step] 폐기 -> 완벽한 np.random.choice 비복원 랜덤 추출
                        sampled = np.random.choice(z_indices, size=take_count, replace=False)
                        selected_indices.extend(sampled)
                        used_mask[sampled] = True

            # 3순위 분산 후에도 목표 게임 수가 부족한 경우 잔여 게임 무작위 채우기
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
                    if need > 0 and (user_exclude_mode == 2 or user_manual_mode == 2):
                        print(f"   -> [주의] '실용(강제추출)' 모드 규정에 따라 70점 미만 풀에서 {need}게임 무작위 보충합니다.")
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

    def export_and_log(self, top_combos: np.ndarray, top_ids: np.ndarray, top_zones: np.ndarray, top_scores: np.ndarray, target_round: int, output_count: int = 10, append_mode: bool = False) -> None:
        """최종 추천 번호 DB 적재 및 누적 통합 파일(TXT, CSV) 리빌드 출력"""
        cursor = self.conn.cursor()
        
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
            (target_round_no, combination_id, num1, num2, num3, num4, num5, num6, soft_score, is_final_top10)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, audit_records)
        self.conn.commit()

        cursor.execute("""
            SELECT w.num1, w.num2, w.num3, w.num4, w.num5, w.num6, w.combination_id, w.soft_score, c.zone_id
            FROM WEEKLY_AUDIT_LOG w
            LEFT JOIN LOTTO_COMBINATIONS_POOL c ON w.combination_id = c.combination_id
            WHERE w.target_round_no = ? AND w.is_final_top10 = 1
            ORDER BY w.id ASC;
        """, (target_round,))
        all_records = cursor.fetchall()
        
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
                f.write(f"게임 {label:<3} : [{r[0]:02d}, {r[1]:02d}, {r[2]:02d}, "
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
        print(f" -> TXT/CSV 통합 출력 완료 : 총 {total_count}개 누적 게임이 하나의 파일에 기록되었습니다.")

    def close(self):
        self.conn.close()


if __name__ == "__main__":
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
        
        print("\n" + "=" * 60)
        print(f" [제 {target_round}회차 최종 선별된 최상위 {default_output_count}게임 (Cold Zone 최적 배분)]")
        print("=" * 60)
        for idx in range(len(top_combos)):
            c = top_combos[idx]
            print(f" 게임 {chr(65+idx)} : [{int(c[0]):02d}, {int(c[1]):02d}, {int(c[2]):02d}, "
                  f"{int(c[3]):02d}, {int(c[4]):02d}, {int(c[5]):02d}] "
                  f"| Zone {int(top_zones[idx]):>2} | 점수: {float(top_scores[idx]):.1f}점")
        print("=" * 60)
        print(f" -> 전체 파이프라인 총 소요 시간: {time.perf_counter() - total_start:.2f}초")
        print("=" * 60)

    finally:
        scoring_engine.close()