import sys
import os
import time
import sqlite3

# 파이프라인 모듈 임포트
from csv_to_db_etl import sync_csv_to_db
from hard_filter_engine import HardFilterEngine
from soft_scoring_engine import SoftScoringEngine
from audit_validator import AuditValidator


def pattern_analyzer_menu(dynamic_filters: dict):
    """최근 당첨 패턴을 정밀 진단하고 동적 하드필터를 설정하는 대화형 콘솔"""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, "lotto.db")
        if not os.path.exists(db_path):
            print("[오류] DB 파일이 없습니다. 동기화를 먼저 진행하십시오.")
            return

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # 10주 초과열 Zone 분석을 위해 스캔 범위를 LIMIT 10으로 확장하고 zone_id 추가 추출
        cursor.execute("SELECT round_no, num1, num2, num3, num4, num5, num6, zone_id FROM WINNING_HISTORY ORDER BY round_no DESC LIMIT 10;")
        history = cursor.fetchall()
        conn.close()

        if len(history) < 5:
            print("[오류] 분석을 위한 최소 5회차 이상의 당첨 데이터가 부족합니다.")
            return

        # 1. 연속 이월 쌍 분석 (N-1 과 N-2 교집합)
        prev1_nums = set(history[0][1:7])
        prev2_nums = set(history[1][1:7])
        common_pairs = prev1_nums.intersection(prev2_nums)
        
        # 2. 1구 트렌드 분석 (최근 5주)
        start_nums = [row[1] for row in history[:5]]
        
        # 3. 6구 트렌드 분석 (최근 5주)
        end_nums = [row[6] for row in history[:5]]
        
        # 4. 이웃수 계산 (N-1 기준)
        neighbors = {x - 1 for x in prev1_nums if x > 1} | {x + 1 for x in prev1_nums if x < 45}
        neighbors = neighbors - prev1_nums
        
        # 5. 단기 초과열 번호 (최근 5주 기준 3회 이상 출현)
        all_recent_nums = []
        for r in history[:5]:
            all_recent_nums.extend(r[1:7])
        counts = {}
        for n in all_recent_nums:
            counts[n] = counts.get(n, 0) + 1
        hyper_hot = [k for k, v in counts.items() if v >= 3]
        
        # 6. 10주 누적 초과열 Zone 분석 (최근 10주 기준 3회 이상 출현 Zone)
        zones_10w = [row[7] for row in history]
        zone_counts = {}
        for z in zones_10w:
            zone_counts[z] = zone_counts.get(z, 0) + 1
        hyper_hot_zones = [k for k, v in zone_counts.items() if v >= 3]

        while True:
            print("\n" + "=" * 80)
            print(" [데이터 사이언스] 최근 당첨 패턴 정밀 진단 리포트")
            print("=" * 80)
            
            # ● 1. 3주 연속 이월 쌍 딜레마
            if len(common_pairs) >= 2:
                print(f" ● [위험] 직전 2회차 연속 동반 출현 쌍 발견: {sorted(list(common_pairs))}")
                print("   ※ 직전 2주 연속으로 함께 나온 번호쌍이 이번 주에 또 같이 나올 확률은 0에 수렴하므로 차단 권장")
            else:
                print(f" ● [안전] 직전 2회차 연속 동반 출현 쌍 없음 (현재 교집합: {sorted(list(common_pairs))})")
                print("   ※ 2주 연속 겹친 번호쌍이 없으므로 안전한 분산 상태입니다.")

            # ● 2. 1구 트렌드 분석
            print(f" ● [경향] 최근 5주 1구(시작) 번호 트렌드: {start_nums}")
            if max(start_nums) >= 16:
                print("   ※ 첫 번호가 16부터 시작하는 희귀 조합(역대 7.3%)이 관측되었습니다. 1구 16 이상 컷오프 권장")
            else:
                print("   ※ 첫 번호가 1~15 정상 주류 구간 내에서 안정적으로 시작되고 있습니다.")

            # ● 3. 6구 트렌드 분석
            print(f" ● [경향] 최근 5주 6구(끝) 번호 트렌드  : {end_nums}")
            if min(end_nums) <= 30:
                print("   ※ 마지막 번호가 30 이하로 끝나는 희귀 조합(역대 7.3%)이 관측되었습니다. 6구 30 이하 컷오프 권장")
            else:
                print("   ※ 마지막 번호가 31~45 고번호 영역에서 정상적으로 마감되고 있습니다.")
            
            # ● 4. 이웃수 풀 분석
            print(f" ● [정보] 직전 회차 파생 이웃수 풀: {sorted(list(neighbors))} (총 {len(neighbors)}개)")
            print("   ※ 직전 당첨번호 바로 옆 번호가 다음 회차에 4개 이상 몰려 나오는 억지 패턴 컷오프 권장")
            
            # ● 5. 단기 번호 Hyper-Hot 분석
            if hyper_hot:
                print(f" ● [경고] 단기 과열(최근 5주 3회 이상) 번호 발견: {sorted(hyper_hot)}")
                print("   ※ 최근 5주간 너무 자주 나온 번호들이 한 게임에 2개 이상 뭉쳐서 나오는 현상 컷오프 권장")
            else:
                print(" ● [안정] 최근 5주 기준 단기 초과열 번호 없음.")
                print("   ※ 특정 번호의 극단적인 단기 쏠림 없이 고르게 출현하고 있습니다.")
                
            # ● 6. 10주 구역(Zone) Hyper-Hot 분석
            if hyper_hot_zones and len(history) >= 10:
                print(f" ● [과열] 10주간 3회 이상 출현한 초과열 Zone 발견: Zone {sorted(hyper_hot_zones)}")
                print("   ※ 10주간 3번 이상 터져 평균회귀에 진입한 과열 구역의 81만개 조합 통째로 컷오프 권장")
            elif len(history) >= 10:
                print(" ● [안정] 10주 기준 초과열 Zone(3회 이상 출현) 없음.")
                print("   ※ 10개 구역 전체가 통계적 기대치 범위 내에서 균등 순환 중입니다.")
            
            print("-" * 80)
            print(" [동적 하드필터 적용 스위치 - 번호를 입력하여 ON/OFF 토글]")
            
            st_1 = "[ON]" if dynamic_filters.get('pair_ban') else "[OFF]"
            st_2 = "[ON]" if dynamic_filters.get('start_num_limit') else "[OFF]"
            st_3 = "[ON]" if dynamic_filters.get('end_num_limit') else "[OFF]"
            st_4 = "[ON]" if dynamic_filters.get('adjacent_limit') else "[OFF]"
            st_5 = "[ON]" if dynamic_filters.get('hyper_hot_ban') else "[OFF]"
            st_6 = "[ON]" if dynamic_filters.get('hyper_hot_zone_ban') else "[OFF]"
            
            print(f"  [1](현재: {st_1:<5}) 3주 연속 이월 쌍 제외")
            print(f"  [2](현재: {st_2:<5}) 시작 번호(1구) 16 이상 배제")
            print(f"  [3](현재: {st_3:<5}) 끝 번호(6구) 30 이하 배제")
            print(f"  [4](현재: {st_4:<5}) 직전 회차 이웃수 4개 이상 배제")
            print(f"  [5](현재: {st_5:<5}) 단기 초과열 번호 2개 이상 배제")
            print(f"  [6](현재: {st_6:<5}) 10주 누적 초과열 Zone 배제")
            print("  [0] 분석 메뉴 종료 및 메인으로 돌아가기")
            print("=" * 80)

            sub_choice = input(" 토글할 필터 번호를 선택하십시오 (0~6): ").strip()
            
            if sub_choice == '1':
                dynamic_filters['pair_ban'] = not dynamic_filters.get('pair_ban', False)
            elif sub_choice == '2':
                dynamic_filters['start_num_limit'] = not dynamic_filters.get('start_num_limit', False)
            elif sub_choice == '3':
                dynamic_filters['end_num_limit'] = not dynamic_filters.get('end_num_limit', False)
            elif sub_choice == '4':
                dynamic_filters['adjacent_limit'] = not dynamic_filters.get('adjacent_limit', False)
            elif sub_choice == '5':
                dynamic_filters['hyper_hot_ban'] = not dynamic_filters.get('hyper_hot_ban', False)
            elif sub_choice == '6':
                dynamic_filters['hyper_hot_zone_ban'] = not dynamic_filters.get('hyper_hot_zone_ban', False)
            elif sub_choice == '0':
                break
            else:
                print("[경고] 올바른 번호를 입력하십시오.")

    except Exception as e:
        print(f"[시스템 예외] 패턴 분석 중 오류 발생: {e}")


def run_prediction_pipeline(user_excluded_zones=None, user_bonus_score_zones=None, user_bonus_quota_zones=None, 
                            output_count=10, user_manual_nums=None, user_manual_count=0, user_manual_mode=0,
                            user_exclude_nums=None, user_exclude_mode=0, append_mode=False, dynamic_filters=None):
    """다음 회차 추천 번호 예측 및 N게임 추출 파이프라인 가동"""
    if dynamic_filters is None:
        dynamic_filters = {}

    print("\n" + "=" * 65)
    mode_title = "누적 추가 추출(Append)" if append_mode else "신규 추출"
    print(f" [시스템 가동] 로또 6/45 AI 최적화 추천 파이프라인 ({mode_title} / 목표: {output_count}게임)")
    
    # 활성화된 동적 하드필터 내역 출력
    active_dyn = [k for k, v in dynamic_filters.items() if v]
    if active_dyn:
        print(f" [동적 제어] 활성화된 패턴 분석 하드필터: {len(active_dyn)}개 가동 중")
    if user_excluded_zones:
        print(f" [수동 제어 6번] 영구 제외 Zone (-100점 페널티): {user_excluded_zones}")
    if user_bonus_score_zones:
        print(f" [수동 제어 7번] 우대 Zone 단순 가점 (+20점 부여): {user_bonus_score_zones}")
    if user_bonus_quota_zones:
        print(f" [수동 제어 8번] 우대 Zone 구조적 할당 (최대 30% 주입): {user_bonus_quota_zones}")
    if user_manual_nums and user_manual_count > 0:
        mode_str = "원칙주의(70점 컷오프)" if user_manual_mode == 1 else "실용주의(점수 무시 강제 추출)"
        print(f" [수동 제어 9/10번] 고정수 {user_manual_nums} -> {user_manual_count}게임 할당 ({mode_str})")
    if user_exclude_nums:
        mode_str = "원칙주의(70점 컷오프 유지)" if user_exclude_mode == 1 else "실용주의(미달 시 강제 보충)"
        print(f" [수동 제어 11/12번] 제외수 {user_exclude_nums} 원천 배제 ({mode_str})")
    print("=" * 65)
    
    start_total = time.perf_counter()

    # 1. Hard Filter Engine (동적 필터 파라미터 주입)
    hard_engine = HardFilterEngine()
    try:
        survivor_indices, combos, comb_ids, zone_ids = hard_engine.apply_filters(adjacent_block_size=10000, dynamic_filters=dynamic_filters)
    except Exception as e:
        print(f"[1단계 치명적 에러] Hard Filter 연산 중 예외 발생: {e}")
        return
    finally:
        hard_engine.close()

    # 2. Soft Scoring Engine
    scoring_engine = SoftScoringEngine()
    try:
        scores, surv_combos, surv_ids, surv_zones, target_round = scoring_engine.score_survivors(
            survivor_indices, combos, comb_ids, zone_ids, 
            user_excluded_zones, user_bonus_score_zones
        )
        
        top_combos, top_ids, top_zones, top_scores = scoring_engine.extract_top_recommendations(
            scores, surv_combos, surv_ids, surv_zones, 
            user_bonus_quota_zones, output_count,
            user_manual_nums, user_manual_count, user_manual_mode,
            user_exclude_nums, user_exclude_mode,
            append_mode, target_round
        )
        
        scoring_engine.export_and_log(top_combos, top_ids, top_zones, top_scores, target_round, output_count, append_mode)

        print("\n" + "=" * 65)
        print(f" [제 {target_round}회차 신규 선별 {output_count}게임 (수동 제어 및 누적 적용 완료)]")
        print("=" * 65)
        for idx in range(len(top_combos)):
            c = top_combos[idx]
            print(f" 게임 {chr(65+idx)} : [{int(c[0]):02d}, {int(c[1]):02d}, {int(c[2]):02d}, "
                  f"{int(c[3]):02d}, {int(c[4]):02d}, {int(c[5]):02d}] "
                  f"| Zone {int(top_zones[idx]):>2} | 점수: {float(top_scores[idx]):.1f}점")
        print("=" * 65)
        print(f" -> 파이프라인 총 소요 시간: {time.perf_counter() - start_total:.2f}초")
        print("=" * 65)

    except Exception as e:
        print(f"[2단계 치명적 에러] Soft Scoring 연산 중 예외 발생: {e}")
    finally:
        scoring_engine.close()


def run_audit_pipeline():
    """지정 회차 실제 당첨 번호 사후 검증 (Recall & Precision Audit) 가동"""
    validator = AuditValidator()
    try:
        round_input = input("\n감사(Audit)할 회차 번호를 입력하십시오 (엔터 시 최신 회차 자동 선택): ").strip()
        
        cursor = validator.conn.cursor()
        if round_input.isdigit():
            target_round = int(round_input)
        else:
            cursor.execute("SELECT MAX(round_no) FROM WINNING_HISTORY;")
            max_round = cursor.fetchone()[0]
            target_round = max_round if max_round else 0

        if target_round > 0:
            validator.audit_target_round(target_round)
        else:
            print("[오류] 감사할 당첨 이력이 DB에 존재하지 않습니다.")
    except Exception as e:
        print(f"[시스템 예외] 사후 감사 실행 중 오류 발생: {e}")
    finally:
        validator.close()


def view_past_recommendations():
    """지정 회차의 시스템 추천 내역을 DB에서 다시 조회하여 출력 (Read-Only)"""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(base_dir, "lotto.db")
        
        if not os.path.exists(db_path):
            print("[오류] DB 파일이 존재하지 않습니다. 시스템을 먼저 가동하십시오.")
            return

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT MAX(target_round_no) FROM WEEKLY_AUDIT_LOG WHERE is_final_top10 = 1;")
        max_target_round = cursor.fetchone()[0]
        
        if max_target_round:
            latest_round = max_target_round
        else:
            cursor.execute("SELECT MAX(round_no) FROM WINNING_HISTORY;")
            max_win_round = cursor.fetchone()[0]
            latest_round = (max_win_round + 1) if max_win_round else 1000
        
        round_input = input(f"\n다시 조회할 회차 번호를 입력하십시오 (엔터 시 최신 {latest_round}회차 자동 조회): ").strip()
        
        if not round_input:
            target_round = latest_round
            print(f" -> [시스템] 입력 생략 감지. 최신 예측 대상 {target_round}회차를 기본값으로 바인딩하여 자동 조회합니다.")
        elif not round_input.isdigit():
            print("[오류] 유효한 회차 번호를 숫자로 입력하십시오.")
            conn.close()
            return
        else:
            target_round = int(round_input)
        
        cursor.execute("""
            SELECT w.num1, w.num2, w.num3, w.num4, w.num5, w.num6, w.soft_score, c.zone_id
            FROM WEEKLY_AUDIT_LOG w
            LEFT JOIN LOTTO_COMBINATIONS_POOL c ON w.combination_id = c.combination_id
            WHERE w.target_round_no = ? AND w.is_final_top10 = 1
            ORDER BY w.id ASC;
        """, (target_round,))
        
        rows = cursor.fetchall()
        
        if not rows:
            print(f"\n[알림] 제 {target_round}회차에 시스템이 생성한 추천 내역이 DB에 존재하지 않습니다.")
        else:
            print("\n" + "=" * 65)
            print(f" [제 {target_round}회차 시스템 최종 선별 추천 조합 복구 조회 (총 {len(rows)}게임)]")
            print("=" * 65)
            for idx, r in enumerate(rows):
                zone_id = r[7] if r[7] else 0
                label = chr(65 + (idx % 26)) + (str(idx // 26) if idx >= 26 else "")
                print(f" 게임 {label:<3} : [{r[0]:02d}, {r[1]:02d}, {r[2]:02d}, "
                      f"{r[3]:02d}, {r[4]:02d}, {r[5]:02d}] "
                      f"| Zone {zone_id:>2} | 점수: {float(r[6]):.1f}점")
            print("=" * 65)
            
        conn.close()
    except Exception as e:
        print(f"[시스템 예외] 과거 추천 내역 조회 중 오류 발생: {e}")


def main_menu():
    """메인 CLI 인터페이스 라우터"""
    user_budget = 10000            
    user_excluded_zones = []       
    user_bonus_score_zones = []    
    user_bonus_quota_zones = []    
    user_manual_nums = []          
    user_manual_count = 0          
    user_manual_mode = 0           
    user_exclude_nums = []         
    user_exclude_mode = 0          
    
    # 런타임 동적 하드필터 세션 상태 (메뉴 2번 연동)
    dynamic_filters = {
        "pair_ban": False,
        "start_num_limit": False,
        "end_num_limit": False,
        "adjacent_limit": False,
        "hyper_hot_ban": False,
        "hyper_hot_zone_ban": False
    }

    while True:
        output_count = max(1, user_budget // 1000)
        
        status_menu_9 = "없음"
        status_menu_10 = "없음"

        if user_manual_nums and user_manual_count > 0:
            if user_manual_mode == 1:
                status_menu_9 = f"{user_manual_nums} ({user_manual_count}게임 적용 중)"
                status_menu_10 = "비활성 (9번 모드 가동 중)"
            elif user_manual_mode == 2:
                status_menu_9 = "비활성 (10번 모드 가동 중)"
                status_menu_10 = f"{user_manual_nums} ({user_manual_count}게임 적용 중)"
                
        status_menu_11 = "없음"
        status_menu_12 = "없음"
        
        if user_exclude_nums:
            if user_exclude_mode == 1:
                status_menu_11 = f"{user_exclude_nums} (원천 차단 적용 중)"
                status_menu_12 = "비활성 (11번 모드 가동 중)"
            elif user_exclude_mode == 2:
                status_menu_11 = "비활성 (12번 모드 가동 중)"
                status_menu_12 = f"{user_exclude_nums} (원천 차단 적용 중)"
        
        active_dyn_count = sum(1 for v in dynamic_filters.values() if v)

        print("\n" + "#" * 78)
        print(" [LOTTO 6/45 AI 최적화 엔지니어링 시스템 v1.9]")
        print("#" * 78)
        print("  1. 최신 당첨 이력 동기화 (오프라인 CSV Bulk ETL)")
        print(f"  2. [분석] 당첨 패턴 정밀 진단 및 동적 하드필터 설정 (현재 활성: {active_dyn_count}개)")
        print("  3. 다음 회차 추천 조합 생성 (Hard Filter -> Soft Scoring 추출)")
        print("  4. 회차별 사후 감사 및 정밀도 검증 (Audit & Recall Test)")
        print("-" * 78)
        print(f"  5. [예산] 주간 구매 예산 설정            (현재: {user_budget:,}원 -> {output_count}게임)")
        print(f"  6. [제외] 배제 Zone (-100점)             (현재: {user_excluded_zones if user_excluded_zones else '없음'})")
        print(f"  7. [우대] 가점 Zone (+20점)              (현재: {user_bonus_score_zones if user_bonus_score_zones else '없음'})")
        print(f"  8. [할당] 우대 Zone 주입 (Max 30%)       (현재: {user_bonus_quota_zones if user_bonus_quota_zones else '없음'})")
        print(f"  9. [고정수 A] 수동 할당 (원칙: 70점컷)   (현재: {status_menu_9})")
        print(f" 10. [고정수 B] 수동 할당 (실용: 강제추출) (현재: {status_menu_10})")
        print(f" 11. [제외수 A] 수동 제외 (원칙: 70점컷)   (현재: {status_menu_11})")
        print(f" 12. [제외수 B] 수동 제외 (실용: 강제추출) (현재: {status_menu_12})")
        print("=" * 78)
        print(" 13. [초기화] 모든 수동 설정 및 예산 초기화 (Default 리셋)")
        print(" 14. [조회] 과거 회차 시스템 추천 조합 복구 조회")
        print(" 15. 시스템 안전 종료")
        print("#" * 78)

        choice = input("명령 번호를 선택하십시오 (1-15): ").strip()

        if choice == "1":
            sync_csv_to_db()
        elif choice == "2":
            pattern_analyzer_menu(dynamic_filters)
        elif choice == "3":
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lotto.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(round_no) FROM WINNING_HISTORY;")
            max_round = cursor.fetchone()[0]
            target_round = (max_round + 1) if max_round else 1
            
            cursor.execute("SELECT COUNT(*) FROM WEEKLY_AUDIT_LOG WHERE target_round_no = ? AND is_final_top10 = 1;", (target_round,))
            existing_count = cursor.fetchone()[0]
            conn.close()
            
            append_mode = False
            if existing_count > 0:
                print("\n" + "!" * 65)
                print(f" [시스템 알림] 주의: 제 {target_round}회차 추천 내역이 이미 존재합니다.")
                print(f" -> 현재 DB에 저장된 발급 대기 물량: [{existing_count} 게임]")
                print("!" * 65)
                print(" [1] 기존 내역 폐기 후 신규 추출")
                print(" [2] 기존 내역 유지 및 중복 방지 누적 추출 (Append)")
                print("-" * 65)
                sub_choice = input(" 진행 방식을 선택하십시오 (1 또는 2): ").strip()
                
                if sub_choice == "2":
                    append_mode = True
                    print(f"\n -> [설정] 기존 {existing_count}게임을 유지하고, 새로운 {output_count}게임을 중복 없이 추가 추출합니다.")
                else:
                    print("\n -> [설정] 기존 내역을 안전하게 삭제하고 새롭게 추출합니다.")
                    
            run_prediction_pipeline(
                user_excluded_zones, user_bonus_score_zones, user_bonus_quota_zones, 
                output_count, user_manual_nums, user_manual_count, user_manual_mode,
                user_exclude_nums, user_exclude_mode, append_mode, dynamic_filters
            )
        elif choice == "4":
            run_audit_pipeline()
        elif choice == "5":
            budget_input = input("\n구입할 예산 금액을 입력하십시오 (예: 5000, 20000): ").strip()
            try:
                parsed_budget = int(budget_input)
                if parsed_budget <= 0 or parsed_budget % 1000 != 0:
                    print("[오류] 예산은 1,000원 단위의 양의 정수로 입력해야 합니다.")
                else:
                    user_budget = parsed_budget
                    if user_manual_count > (user_budget // 1000):
                        print(f"[조정] 수동 할당 게임 수가 예산을 초과하여 {user_budget // 1000}게임으로 하향 조정됩니다.")
                        user_manual_count = user_budget // 1000
            except ValueError:
                print("[오류] 유효한 숫자를 입력하십시오.")
        elif choice == "6":
            zone_input = input("\n제외할 Zone ID(1~10)를 쉼표로 구분하여 입력 (초기화는 엔터): ").strip()
            if not zone_input:
                user_excluded_zones = []
            else:
                try:
                    user_excluded_zones = [z for z in [int(x.strip()) for x in zone_input.split(",")] if 1 <= z <= 10]
                except Exception as e:
                    print(f"[오류] 유효하지 않은 입력입니다. ({e})")
        elif choice == "7":
            zone_input = input("\n가점(+20점)을 부여할 Zone ID(1~10)를 쉼표로 구분하여 입력 (초기화는 엔터): ").strip()
            if not zone_input:
                user_bonus_score_zones = []
            else:
                try:
                    user_bonus_score_zones = [z for z in [int(x.strip()) for x in zone_input.split(",")] if 1 <= z <= 10]
                except Exception as e:
                    print(f"[오류] 유효하지 않은 입력입니다. ({e})")
        elif choice == "8":
            zone_input = input("\n할당량(Max 30%)을 주입할 Zone ID(1~10)를 쉼표로 구분하여 입력 (초기화는 엔터): ").strip()
            if not zone_input:
                user_bonus_quota_zones = []
            else:
                try:
                    user_bonus_quota_zones = [z for z in [int(x.strip()) for x in zone_input.split(",")] if 1 <= z <= 10]
                except Exception as e:
                    print(f"[오류] 유효하지 않은 입력입니다. ({e})")
        elif choice in ["9", "10"]:
            mode_name = "원칙주의(70점 미달 시 폐기)" if choice == "9" else "실용주의(점수 무시 상위 추출)"
            nums_input = input(f"\n[{mode_name}] 고정수 1~5개를 쉼표로 구분하여 입력 (초기화는 엔터): ").strip()
            if not nums_input:
                user_manual_nums = []
                user_manual_count = 0
                user_manual_mode = 0
                print("[설정] 수동 고정수 할당이 초기화되었습니다.")
            else:
                try:
                    parsed_nums = sorted(list(set([int(x.strip()) for x in nums_input.split(",")])))
                    if not all(1 <= n <= 45 for n in parsed_nums) or len(parsed_nums) < 1 or len(parsed_nums) > 5:
                        print("[오류] 번호는 1~45 사이여야 평준화되며, 중복 없이 1개에서 최대 5개까지만 입력 가능합니다.")
                        continue
                    
                    count_input = input(f"해당 고정수를 몇 게임에 할당하시겠습니까? (최대 {output_count}게임): ").strip()
                    parsed_count = int(count_input)
                    if parsed_count < 1 or parsed_count > output_count:
                        print(f"[오류] 할당 게임 수는 1 이상, 전체 예산({output_count}게임) 이하여야 합니다.")
                        continue
                    
                    user_manual_nums = parsed_nums
                    user_manual_count = parsed_count
                    user_manual_mode = 1 if choice == "9" else 2
                    print(f"[설정] 파이프라인 가동 시 {user_manual_nums} 고정수 조합이 {user_manual_count}게임 할당됩니다.")
                except Exception as e:
                    print(f"[오류] 유효하지 않은 입력입니다. ({e})")
        elif choice in ["11", "12"]:
            mode_name = "원칙주의(70점 미달 시 제외)" if choice == "11" else "실용주의(점수 미달 시 강제 추출 보충)"
            nums_input = input(f"\n[{mode_name}] 원천 배제할 제외수를 쉼표로 구분하여 입력 (초기화는 엔터): ").strip()
            if not nums_input:
                user_exclude_nums = []
                user_exclude_mode = 0
                print("[설정] 수동 제외수 차단이 초기화되었습니다.")
            else:
                try:
                    parsed_nums = sorted(list(set([int(x.strip()) for x in nums_input.split(",")])))
                    if not all(1 <= n <= 45 for n in parsed_nums) or len(parsed_nums) < 1 or len(parsed_nums) > 39:
                        print("[오류] 번호는 1~45 사이여야 하며, 최소 1개에서 최대 39개까지만 입력 가능합니다.")
                        continue
                    
                    user_exclude_nums = parsed_nums
                    user_exclude_mode = 1 if choice == "11" else 2
                    print(f"[설정] 파이프라인 가동 시 {user_exclude_nums} 번호가 포함된 조합은 전면 폐기됩니다.")
                except Exception as e:
                    print(f"[오류] 유효하지 않은 입력입니다. ({e})")
        elif choice == "13":
            user_budget = 10000
            user_excluded_zones = []
            user_bonus_score_zones = []
            user_bonus_quota_zones = []
            user_manual_nums = []
            user_manual_count = 0
            user_manual_mode = 0
            user_exclude_nums = []
            user_exclude_mode = 0
            for k in dynamic_filters.keys():
                dynamic_filters[k] = False
            print("\n[초기화 완료] 모든 사용자 정의 설정(예산/제외/우대/동적필터 등)이 기본 상태로 리셋되었습니다.")
        elif choice == "14":
            view_past_recommendations()
        elif choice == "15":
            print("\n시스템 자원을 반환하고 안전하게 종료합니다.")
            sys.exit(0)
        else:
            print("[경고] 올바른 메뉴 번호를 입력하십시오.")


if __name__ == "__main__":
    if os.name == 'nt':
        os.system('color')
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\n[시스템 인터럽트] 사용자에 의해 시스템이 강제 종료되었습니다.")
        sys.exit(0)