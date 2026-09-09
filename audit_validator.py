import sqlite3
import numpy as np
import os
import sys
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")


class AuditValidator:
    def __init__(self, db_path: str = DB_NAME):
        """DB 커넥션 초기화"""
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"[시스템 예외] DB 파일을 찾을 수 없습니다: {db_path}")
            
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def audit_target_round(self, target_round: int) -> Dict[str, Any]:
        """지정 회차에 대한 Hard Filter 생존율(Recall) 및 추천 정확도(Precision) 2중 감사"""
        cursor = self.conn.cursor()

        # 1. 대상 회차 실제 당첨 정보 조회
        cursor.execute("""
            SELECT round_no, draw_date, num1, num2, num3, num4, num5, num6, bonus, combination_id, zone_id
            FROM WINNING_HISTORY
            WHERE round_no = ?;
        """, (target_round,))
        win_row = cursor.fetchone()

        if not win_row:
            print(f"[감사 불가] {target_round}회차 당첨 정보가 DB(WINNING_HISTORY)에 존재하지 않습니다.")
            return {}

        win_nums = {win_row['num1'], win_row['num2'], win_row['num3'], 
                    win_row['num4'], win_row['num5'], win_row['num6']}
        bonus_num = win_row['bonus']
        win_comb_id = win_row['combination_id']
        win_zone_id = win_row['zone_id']

        # 2. 직전 회차 당첨 정보 조회 (Filter 연산용)
        cursor.execute("""
            SELECT num1, num2, num3, num4, num5, num6, combination_id
            FROM WINNING_HISTORY
            WHERE round_no = ?;
        """, (target_round - 1,))
        prev_row = cursor.fetchone()

        # ---------------------------------------------------------
        # [Recall Audit] 실제 1등 조합의 Hard Filter 7대 규격 통과 검증
        # ---------------------------------------------------------
        w_arr = np.array(sorted(list(win_nums)), dtype=np.uint8)
        filter_violations = []

        # Filter 04: 과거 당첨 이력 여부
        cursor.execute("SELECT COUNT(*) FROM WINNING_HISTORY WHERE combination_id = ? AND round_no < ?;", (win_comb_id, target_round))
        if cursor.fetchone()[0] > 0:
            filter_violations.append("Filter 04 (과거 1등 기출 조합)")

        if prev_row:
            # Filter 02: 인접 블록
            prev_id = prev_row['combination_id']
            if abs(win_comb_id - prev_id) <= 10000:
                filter_violations.append("Filter 02 (직전 ID 인접 블록)")

            # Filter 03: 이월수 3개 이상
            prev_nums = {prev_row['num1'], prev_row['num2'], prev_row['num3'], 
                         prev_row['num4'], prev_row['num5'], prev_row['num6']}
            if len(win_nums.intersection(prev_nums)) >= 3:
                filter_violations.append("Filter 03 (이월수 3개 이상 과다)")

        # Filter 08: 3연속 번호
        if (w_arr[2] - w_arr[0] == 2) or (w_arr[3] - w_arr[1] == 2) or (w_arr[4] - w_arr[2] == 2) or (w_arr[5] - w_arr[3] == 2):
            filter_violations.append("Filter 08 (3연속 번호 출현)")

        # Filter 09: 동일 끝수 3개 이상
        mod_10 = w_arr % 10
        if np.bincount(mod_10).max() >= 3:
            filter_violations.append("Filter 09 (동일 끝수 3개 이상)")

        # Filter 11: 한 구간 몰림 방지
        deciles = (w_arr - 1) // 10
        if np.bincount(deciles).max() >= 4:
            filter_violations.append("Filter 11 (10단위 구간 4개 이상 몰림)")

        # Filter 12: AC값(산술 복잡도) 필터
        pairs = [(0,1), (0,2), (0,3), (0,4), (0,5), (1,2), (1,3), (1,4), (1,5), (2,3), (2,4), (2,5), (3,4), (3,5), (4,5)]
        diffs = {w_arr[j] - w_arr[i] for i, j in pairs}
        ac_val = len(diffs) - 5
        if ac_val < 7:
            filter_violations.append(f"Filter 12 (AC값 저조: {ac_val})")

        hard_filter_passed = (len(filter_violations) == 0)

        # ---------------------------------------------------------
        # [Precision Audit] 추천 10게임과의 매칭 및 당첨 결과 채점
        # ---------------------------------------------------------
        cursor.execute("""
            SELECT id, combination_id, num1, num2, num3, num4, num5, num6, soft_score
            FROM WEEKLY_AUDIT_LOG
            WHERE target_round_no = ? AND is_final_top10 = 1
            ORDER BY id ASC;
        """, (target_round,))
        logged_games = cursor.fetchall()

        game_results = []
        rank_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, "낙첨": 0}

        for idx, g in enumerate(logged_games):
            g_nums = {g['num1'], g['num2'], g['num3'], g['num4'], g['num5'], g['num6']}
            match_cnt = len(g_nums.intersection(win_nums))
            bonus_matched = (bonus_num in g_nums)

            # 등수 판정 로직
            if match_cnt == 6:
                rank = 1
            elif match_cnt == 5 and bonus_matched:
                rank = 2
            elif match_cnt == 5:
                rank = 3
            elif match_cnt == 4:
                rank = 4
            elif match_cnt == 3:
                rank = 5
            else:
                rank = 0

            if rank > 0:
                rank_counts[rank] += 1
            else:
                rank_counts["낙첨"] += 1

            # DB에 등수 및 결과 영구 업데이트
            cursor.execute("""
                UPDATE WEEKLY_AUDIT_LOG
                SET match_count = ?, bonus_matched = ?, winning_rank = ?
                WHERE id = ?;
            """, (match_cnt, 1 if bonus_matched else 0, rank, g['id']))

            game_results.append({
                "game_label": chr(65 + idx),
                "nums": sorted(list(g_nums)),
                "match_cnt": match_cnt,
                "bonus_matched": bonus_matched,
                "rank": f"{rank}등" if rank > 0 else "낙첨",
                "score": g['soft_score']
            })

        self.conn.commit()

        # ---------------------------------------------------------
        # 콘솔 출력 리포트
        # ---------------------------------------------------------
        print("\n" + "=" * 65)
        print(f" [제 {target_round}회차 시스템 사후 감사(Audit) 리포트]")
        print("=" * 65)
        print(f" * 실제 1등 번호 : {sorted(list(win_nums))} + 보너스 [{bonus_num}]")
        print(f" * 1등 조합 정보 : ID {win_comb_id:,} | Zone {win_zone_id} | AC값 {ac_val}")
        print("-" * 65)
        print(f" [1] Hard Filter 생존율(Recall) 감사:")
        if hard_filter_passed:
            print("  -> PASS: 실제 1등 조합이 1단계 Hard Filter 생존 풀에 정상 포함되는 유효 패턴이었습니다.")
        else:
            print(f"  -> FAIL: 실제 1등 조합이 필터에 의해 탈락하는 예외 패턴이었습니다.")
            print(f"     (위반 항목: {', '.join(filter_violations)})")
        print("-" * 65)
        print(f" [2] 최종 추천 Top 10 적중률(Precision) 감사:")
        if not logged_games:
            print("  -> 해당 회차 추첨 전에 기록된 추천 이력(WEEKLY_AUDIT_LOG)이 없습니다.")
        else:
            for r in game_results:
                b_str = "+보너스" if r['bonus_matched'] else ""
                
                # ANSI Bold/Color 및 대괄호/소괄호 마스킹 시각 강조 포맷팅 생성
                formatted_nums = []
                for n in r['nums']:
                    if n in win_nums:
                        # 당첨 번호 일치: ANSI Bold + High-Intensity Yellow + [NN]
                        formatted_nums.append(f"\033[1;93m[{n:02d}]\033[0m")
                    elif r['bonus_matched'] and n == bonus_num:
                        # 보너스 번호 일치: ANSI Bold + Cyan + (NN)
                        formatted_nums.append(f"\033[1;96m({n:02d})\033[0m")
                    else:
                        formatted_nums.append(f"{n:02d}")
                nums_str = ", ".join(formatted_nums)

                print(f"  게임 {r['game_label']} : [{nums_str}] -> 일치: {r['match_cnt']}개 {b_str:<5} | 등수: {r['rank']:<3} | 점수: {r['score']:.1f}점")
            print("-" * 65)
            
            # 종합 성적 등수별 ANSI 컬러 강조 포맷팅 생성 (당첨 건수 > 0 일 때 시각적 강조)
            f_1st = f"\033[1;91m1등({rank_counts[1]})\033[0m" if rank_counts[1] > 0 else f"1등({rank_counts[1]})"
            f_2nd = f"\033[1;96m2등({rank_counts[2]})\033[0m" if rank_counts[2] > 0 else f"2등({rank_counts[2]})"
            f_3rd = f"\033[1;92m3등({rank_counts[3]})\033[0m" if rank_counts[3] > 0 else f"3등({rank_counts[3]})"
            f_4th = f"\033[1;93m4등({rank_counts[4]})\033[0m" if rank_counts[4] > 0 else f"4등({rank_counts[4]})"
            f_5th = f"\033[1;94m5등({rank_counts[5]})\033[0m" if rank_counts[5] > 0 else f"5등({rank_counts[5]})"
            f_fail = f"낙첨({rank_counts['낙첨']})"

            print(f" * 종합 성적: {f_1st}, {f_2nd}, {f_3rd}, {f_4th}, {f_5th}, {f_fail}")
        print("=" * 65)

        return {"target_round": target_round, "recall": hard_filter_passed, "results": game_results}

    def close(self):
        self.conn.close()


if __name__ == "__main__":
    validator = AuditValidator()
    try:
        cursor = validator.conn.cursor()
        cursor.execute("SELECT MAX(round_no) FROM WINNING_HISTORY;")
        latest_round = cursor.fetchone()[0]
        
        if latest_round:
            validator.audit_target_round(latest_round)
        else:
            print("[오류] 당첨 데이터가 존재하지 않습니다.")
    finally:
        validator.close()