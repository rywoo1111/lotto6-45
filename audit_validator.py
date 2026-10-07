"""
Module: audit_validator.py
Version: 2.1
Last Updated: 2026-10-07

[모듈 책임]
- 회차별 실제 로또 1등 당첨 번호 기반 시스템 2중 사후 감사(Audit) 수행.
- [Recall Audit] 실제 1등 조합의 기본 7대 Hard Filter 통과 여부 역추적 및 오버필터링 검증.
- [Precision Audit] WEEKLY_AUDIT_LOG 추천 조합 대상 등수(1~5등/낙첨) 판정 및 DB 영구 동기화.
- [대용량 안전 모드] 100게임 이하 전량 콘솔 출력, 100게임 초과 시 당첨 게임(1~5등) 중심 스마트 필터링 출력 및 CMD 버퍼 오버플로 방지.
- [정산 리포트 및 파일 출력] 투자금 대비 당첨금/순손익/수익률(ROI) 통계 산출 및 predictions/audit_result_{target_round}회 TXT/CSV 자동 익스포트.
"""
import sqlite3
import numpy as np
import os
import sys
import traceback
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "lotto.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "predictions")

# 표준 등수별 고정/추정 당첨금 정의 (단위: 원)
PRIZE_MAP = {
    1: 2000000000,  # 1등 (통상 20억 추정)
    2: 50000000,    # 2등 (통상 5,000만 추정)
    3: 1500000,     # 3등 (통상 150만 추정)
    4: 50000,       # 4등 (고정 5만 원)
    5: 5000         # 5등 (고정 5천 원)
}
GAME_UNIT_PRICE = 1000  # 게임당 단가 (1,000원)


class AuditValidator:
    """
    [v2.1] 사후 감사 및 정밀도/재현율 검증 엔진.
    대용량(100게임 초과) 데이터 처리 시 콘솔 I/O 보호 및 파일 내보내기 지원.
    """

    def __init__(self, db_path: str = DB_NAME):
        """
        [v2.1] AuditValidator 초기화 및 sqlite3.Row 팩토리 커넥션, 출력 폴더 설정.

        Args:
            db_path (str): SQLite 데이터베이스 파일 절대 경로.

        Raises:
            FileNotFoundError: 데이터베이스 파일이 존재하지 않을 경우 발생.
        """
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"[시스템 예외] DB 파일을 찾을 수 없습니다: {db_path}")

        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def audit_target_round(self, target_round: int) -> Dict[str, Any]:
        """
        [v2.1] 지정 회차에 대한 Hard Filter 생존율(Recall) 및 추천 정확도(Precision) 2중 감사.

        1. 대상 회차 실제 1등 번호 및 직전 회차 정보를 조회하여 기본 7대 Hard Filter 위반 여부를 검증한다.
        2. 기추출된 추천 조합(WEEKLY_AUDIT_LOG)과의 일치 개수를 대조하여 1~5등 등수를 채점한다.
        3. 채점된 결과를 WEEKLY_AUDIT_LOG에 영구 갱신하고 터미널 리포트를 출력한다.
           - 100게임 이하: 전체 게임 콘솔 출력
           - 100게임 초과: 당첨 게임(1~5등)만 콘솔 우선 출력 + 종합 수익률 리포트 + TXT/CSV 파일 저장

        Args:
            target_round (int): 사후 감사를 진행할 대상 추첨 회차.

        Returns:
            Dict[str, Any]: 감사 결과 요약 딕셔너리 (target_round, recall, results).
        """
        cursor = self.conn.cursor()

        try:
            # 1. 대상 회차 실제 당첨 정보 조회
            cursor.execute("""
                           SELECT round_no,
                                  draw_date,
                                  num1,
                                  num2,
                                  num3,
                                  num4,
                                  num5,
                                  num6,
                                  bonus,
                                  combination_id,
                                  zone_id
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
            cursor.execute("SELECT COUNT(*) FROM WINNING_HISTORY WHERE combination_id = ? AND round_no < ?;",
                           (win_comb_id, target_round))
            if cursor.fetchone()[0] > 0:
                filter_violations.append("Filter 04 (과거 1등 기출 조합)")

            if prev_row:
                # Filter 02: 인접 블록 (±10,000 범위)
                prev_id = prev_row['combination_id']
                if prev_id is not None and abs(win_comb_id - prev_id) <= 10000:
                    filter_violations.append("Filter 02 (직전 ID 인접 블록)")

                # Filter 03: 이월수 3개 이상
                prev_nums = {prev_row['num1'], prev_row['num2'], prev_row['num3'],
                             prev_row['num4'], prev_row['num5'], prev_row['num6']}
                if len(win_nums.intersection(prev_nums)) >= 3:
                    filter_violations.append("Filter 03 (이월수 3개 이상 과다)")

            # Filter 08: 3연속 번호
            if (w_arr[2] - w_arr[0] == 2) or (w_arr[3] - w_arr[1] == 2) or (w_arr[4] - w_arr[2] == 2) or (
                    w_arr[5] - w_arr[3] == 2):
                filter_violations.append("Filter 08 (3연속 번호 출현)")

            # Filter 09: 동일 끝수 3개 이상
            mod_10 = w_arr % 10
            if np.bincount(mod_10).max() >= 3:
                filter_violations.append("Filter 09 (동일 끝수 3개 이상)")

            # Filter 11: 한 구간 몰림 방지 (10단위 4개 이상)
            deciles = (w_arr - 1) // 10
            if np.bincount(deciles).max() >= 4:
                filter_violations.append("Filter 11 (10단위 구간 4개 이상 몰림)")

            # Filter 12: AC값(산술 복잡도) 필터 (AC >= 7)
            pairs = [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (1, 2), (1, 3), (1, 4), (1, 5), (2, 3), (2, 4), (2, 5),
                     (3, 4), (3, 5), (4, 5)]
            diffs = {w_arr[j] - w_arr[i] for i, j in pairs}
            ac_val = len(diffs) - 5
            if ac_val < 7:
                filter_violations.append(f"Filter 12 (AC값 저조: {ac_val})")

            hard_filter_passed = (len(filter_violations) == 0)

            # ---------------------------------------------------------
            # [Precision Audit] 추천 게임과의 매칭 및 당첨 결과 채점
            # ---------------------------------------------------------
            cursor.execute("""
                           SELECT id,
                                  combination_id,
                                  num1,
                                  num2,
                                  num3,
                                  num4,
                                  num5,
                                  num6,
                                  soft_score
                           FROM WEEKLY_AUDIT_LOG
                           WHERE target_round_no = ?
                             AND is_final_top10 = 1
                           ORDER BY id ASC;
                           """, (target_round,))
            logged_games = cursor.fetchall()

            game_results = []
            rank_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, "낙첨": 0}
            update_batch = []

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

                # 배치 업데이트용 데이터 적재
                update_batch.append((match_cnt, 1 if bonus_matched else 0, rank, g['id']))

                # 26게임 초과 시에도 라벨 깨짐 없는 포맷팅 적용 (A, B ... Z, A1, B1 ...)
                label = chr(65 + (idx % 26)) + (str(idx // 26) if idx >= 26 else "")

                game_results.append({
                    "id": g['id'],
                    "combination_id": g['combination_id'],
                    "game_label": label,
                    "nums": sorted(list(g_nums)),
                    "match_cnt": match_cnt,
                    "bonus_matched": bonus_matched,
                    "rank_num": rank,
                    "rank_str": f"{rank}등" if rank > 0 else "낙첨",
                    "score": g['soft_score']
                })

            # DB 대량 배치 업데이트
            if update_batch:
                cursor.executemany("""
                                   UPDATE WEEKLY_AUDIT_LOG
                                   SET match_count   = ?,
                                       bonus_matched = ?,
                                       winning_rank  = ?
                                   WHERE id = ?;
                                   """, update_batch)
                self.conn.commit()

            # ---------------------------------------------------------
            # 파일 출력: predictions/audit_result_{target_round}회.txt & .csv
            # ---------------------------------------------------------
            total_games = len(game_results)
            total_cost = total_games * GAME_UNIT_PRICE
            total_prize = sum(rank_counts[r] * PRIZE_MAP[r] for r in [1, 2, 3, 4, 5])
            net_profit = total_prize - total_cost
            roi = (total_prize / total_cost * 100) if total_cost > 0 else 0.0

            if total_games > 0:
                txt_path = os.path.join(OUTPUT_DIR, f"audit_result_{target_round}회.txt")
                csv_path = os.path.join(OUTPUT_DIR, f"audit_result_{target_round}회.csv")

                with open(txt_path, 'w', encoding='utf-8') as f:
                    f.write("===========================================================\n")
                    f.write(f" [제 {target_round}회차 로또 사후 감사 상세 성적표 (총 {total_games:,}게임)]\n")
                    f.write("===========================================================\n")
                    f.write(f" * 실제 1등 당첨번호 : {sorted(list(win_nums))} + 보너스 [{bonus_num}]\n")
                    f.write(f" * 총 구매 예산     : {total_cost:,}원 ({total_games:,}게임)\n")
                    f.write(f" * 총 당첨 환급액   : {total_prize:,}원\n")
                    f.write(f" * 순 손익 (수익률) : {net_profit:+,}원 ({roi:.2f}%)\n")
                    f.write(f" * 등수별 당첨 내역 : 1등({rank_counts[1]}), 2등({rank_counts[2]}), "
                            f"3등({rank_counts[3]}), 4등({rank_counts[4]}), 5등({rank_counts[5]}), 낙첨({rank_counts['낙첨']})\n")
                    f.write("===========================================================\n")
                    for r in game_results:
                        b_str = "+보너스" if r['bonus_matched'] else ""
                        nums_str = ", ".join(f"{n:02d}" for n in r['nums'])
                        f.write(f"게임 {r['game_label']:<5} : [{nums_str}] -> 일치: {r['match_cnt']}개 {b_str:<5} | "
                                f"등수: {r['rank_str']:<3} | 점수: {r['score']:.1f}점 (ID: {r['combination_id']})\n")
                    f.write("===========================================================\n")

                with open(csv_path, 'w', encoding='utf-8') as f:
                    f.write("game,combination_id,num1,num2,num3,num4,num5,num6,match_count,bonus_matched,rank,score\n")
                    for r in game_results:
                        f.write(f"{r['game_label']},{r['combination_id']},"
                                f"{r['nums'][0]},{r['nums'][1]},{r['nums'][2]},{r['nums'][3]},{r['nums'][4]},{r['nums'][5]},"
                                f"{r['match_cnt']},{1 if r['bonus_matched'] else 0},{r['rank_num']},{r['score']:.1f}\n")

            # ---------------------------------------------------------
            # 콘솔 출력 리포트 (100게임 이하 vs 100게임 초과 분기)
            # ---------------------------------------------------------
            print("\n" + "=" * 65)
            print(f" [제 {target_round}회차 시스템 사후 감사(Audit) 리포트]")
            print("=" * 65)
            print(f" * 실제 1등 번호 : {sorted(list(win_nums))} + 보너스 [{bonus_num}]")
            print(f" * 1등 조합 정보 : ID {win_comb_id:,} | Zone {win_zone_id} | AC값 {ac_val}")
            print("-" * 65)
            print(" [1] Hard Filter 생존율(Recall) 감사:")
            if hard_filter_passed:
                print("  -> PASS: 실제 1등 조합이 1단계 Hard Filter 생존 풀에 정상 포함되는 유효 패턴이었습니다.")
            else:
                print("  -> FAIL: 실제 1등 조합이 필터에 의해 탈락하는 예외 패턴이었습니다.")
                print(f"     (위반 항목: {', '.join(filter_violations)})")
            print("-" * 65)
            print(f" [2] 최종 추천 Top N 적중률(Precision) 감사 (총 {total_games:,}게임):")

            if not logged_games:
                print("  -> 해당 회차 추첨 전에 기록된 추천 이력(WEEKLY_AUDIT_LOG)이 없습니다.")
            else:
                def format_game_line(r: Dict[str, Any]) -> str:
                    b_str = "+보너스" if r['bonus_matched'] else ""
                    formatted_nums = []
                    for n in r['nums']:
                        if n in win_nums:
                            formatted_nums.append(f"\033[1;93m[{n:02d}]\033[0m")
                        elif r['bonus_matched'] and n == bonus_num:
                            formatted_nums.append(f"\033[1;96m({n:02d})\033[0m")
                        else:
                            formatted_nums.append(f"{n:02d}")
                    nums_str = ", ".join(formatted_nums)
                    rank_display = r['rank_str']
                    if r['rank_num'] == 1:
                        rank_display = f"\033[1;91m{r['rank_str']}\033[0m"
                    elif r['rank_num'] in [2, 3]:
                        rank_display = f"\033[1;92m{r['rank_str']}\033[0m"
                    elif r['rank_num'] in [4, 5]:
                        rank_display = f"\033[1;93m{r['rank_str']}\033[0m"
                    return f"  게임 {r['game_label']:<5} : [{nums_str}] -> 일치: {r['match_cnt']}개 {b_str:<5} | 등수: {rank_display:<3} | 점수: {r['score']:.1f}점"

                # 100게임 이하: 전체 게임 콘솔 출력
                if total_games <= 100:
                    for idx, r in enumerate(game_results):
                        line_str = format_game_line(r)
                        # 짝수 행 노란색 교차 (단, 등수/번호 하이라이트와 조화)
                        print(line_str)
                else:
                    # 100게임 초과 대용량: 당첨 게임(1~5등)만 콘솔 우선 출력
                    winning_games = [r for r in game_results if r['rank_num'] > 0]
                    won_count = len(winning_games)
                    print(f"  [대량 데이터 모드: 총 {total_games:,}게임 중 \033[1;93m당첨 게임 {won_count:,}건\033[0m 우선 출력]")

                    if won_count > 0:
                        # 당첨 건수가 100건 이하이면 전량 출력, 100건 초과 시 상위 100건 출력
                        display_limit = min(won_count, 100)
                        for r in winning_games[:display_limit]:
                            print(format_game_line(r))
                        if won_count > display_limit:
                            print(f"  ... (외 당첨 {won_count - display_limit:,}건 추가 존재 - 파일 참조)")
                    else:
                        print("  -> 이번 회차에서는 1~5등 당첨 조합이 발생하지 않았습니다.")

                print("-" * 65)

                # 종합 성적 등수별 ANSI 컬러 강조 포맷팅 생성
                f_1st = f"\033[1;91m1등({rank_counts[1]:,})\033[0m" if rank_counts[1] > 0 else f"1등({rank_counts[1]})"
                f_2nd = f"\033[1;96m2등({rank_counts[2]:,})\033[0m" if rank_counts[2] > 0 else f"2등({rank_counts[2]})"
                f_3rd = f"\033[1;92m3등({rank_counts[3]:,})\033[0m" if rank_counts[3] > 0 else f"3등({rank_counts[3]})"
                f_4th = f"\033[1;93m4등({rank_counts[4]:,})\033[0m" if rank_counts[4] > 0 else f"4등({rank_counts[4]})"
                f_5th = f"\033[1;94m5등({rank_counts[5]:,})\033[0m" if rank_counts[5] > 0 else f"5등({rank_counts[5]})"
                f_fail = f"낙첨({rank_counts['낙첨']:,})"

                print(f" * 종합 성적: {f_1st}, {f_2nd}, {f_3rd}, {f_4th}, {f_5th}, {f_fail}")
                print(f" * 정산 내역: 총 구매 \033[1;93m{total_cost:,}원\033[0m -> 총 당첨금 \033[1;93m{total_prize:,}원\033[0m (수익률: \033[1;93m{roi:.2f}%\033[0m)")
                if total_games > 100:
                    print(f" -> 전체 {total_games:,}게임 상세 채점 결과: predictions/audit_result_{target_round}회.txt / .csv 저장 완료")

            print("=" * 65)

            return {"target_round": target_round, "recall": hard_filter_passed, "results": game_results}

        except Exception as e:
            self.conn.rollback()
            print(f"\n[오류] 사후 감사 수행 중 예외 발생. 롤백을 수행합니다: {e}")
            traceback.print_exc()
            return {}

        finally:
            cursor.close()

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
    validator = AuditValidator()
    try:
        cursor = validator.conn.cursor()
        cursor.execute("SELECT MAX(round_no) FROM WINNING_HISTORY;")
        latest_row = cursor.fetchone()
        latest_round = latest_row[0] if latest_row and latest_row[0] is not None else 0
        cursor.close()

        if latest_round > 0:
            validator.audit_target_round(latest_round)
        else:
            print("[오류] 당첨 데이터가 존재하지 않습니다.")
    finally:
        validator.close()
