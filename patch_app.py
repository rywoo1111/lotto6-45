import os

def update_main_py():
    file_path = 'D:/lotto_project/lotto_project_github/main.py'
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. run_prediction_pipeline signature update
    content = content.replace(
        "def run_prediction_pipeline(user_excluded_zones: Optional[List[int]] = None,\n                            user_bonus_score_zones: Optional[List[int]] = None,\n                            user_bonus_quota_zones: Optional[List[int]] = None,",
        "def run_prediction_pipeline(user_excluded_zones: Optional[List[int]] = None,\n                            user_bonus_score_zones: Optional[List[int]] = None,"
    )
    
    content = content.replace(
        "user_bonus_quota_zones (Optional[List[int]]): 구조적 30% 할당 주입 Zone 목록.\n        output_count (int): 목표 추출 게임 수.",
        "output_count (int): 목표 추출 게임 수."
    )

    # 2. Extracting run_prediction_pipeline info logs
    content = content.replace(
        "if user_bonus_quota_zones:\n        print(f\" [수동 제어 8번] 우대 Zone 구조적 할당 (최대 30% 주입): \\033[1;93m{user_bonus_quota_zones}\\033[0m\")\n    if user_manual_nums and user_manual_count > 0:",
        "if user_manual_nums and user_manual_count > 0:"
    )

    content = content.replace(
        "top_combos, top_ids, top_zones, top_scores = scoring_engine.extract_top_recommendations(\n            scores, surv_combos, surv_ids, surv_zones,\n            user_bonus_quota_zones, output_count,",
        "top_combos, top_ids, top_zones, top_scores = scoring_engine.extract_top_recommendations(\n            scores, surv_combos, surv_ids, surv_zones,\n            output_count,"
    )

    # 3. main_menu initial variables
    content = content.replace(
        "user_bonus_score_zones = []\n    user_bonus_quota_zones = []\n    user_manual_nums = []",
        "user_bonus_score_zones = []\n    user_manual_nums = []"
    )

    # 4. main_menu prints
    content = content.replace(
        "print(\n            f\"  8. [할당] 우대 Zone 주입 (Max 30%)       (현재: {user_bonus_quota_zones if user_bonus_quota_zones else '없음'})\")\n        print(f\"  9. [고정수 A] 수동 할당 (원칙: 70점컷)   (현재: {status_menu_9})\")\n        print(f\" 10. [고정수 B] 수동 할당 (실용: 강제추출) (현재: {status_menu_10})\")\n        print(f\" 11. [제외수 A] 수동 제외 (원칙: 70점컷)   (현재: {status_menu_11})\")\n        print(f\" 12. [제외수 B] 수동 제외 (실용: 강제추출) (현재: {status_menu_12})\")\n        print(\"=\" * 78)\n        print(\" 13. [초기화] 모든 수동 설정 및 예산 초기화 (Default 리셋)\")\n        print(\" 14. [조회] 과거 회차 시스템 추천 조합 복구 조회\")\n        print(\" 15. 시스템 안전 종료\")",
        "print(f\"  8. [고정수 A] 수동 할당 (원칙: 70점컷)   (현재: {status_menu_9})\")\n        print(f\"  9. [고정수 B] 수동 할당 (실용: 강제추출) (현재: {status_menu_10})\")\n        print(f\" 10. [제외수 A] 수동 제외 (원칙: 70점컷)   (현재: {status_menu_11})\")\n        print(f\" 11. [제외수 B] 수동 제외 (실용: 강제추출) (현재: {status_menu_12})\")\n        print(\"=\" * 78)\n        print(\" 12. [초기화] 모든 수동 설정 및 예산 초기화 (Default 리셋)\")\n        print(\" 13. [조회] 과거 회차 시스템 추천 조합 복구 조회\")\n        print(\" 14. 시스템 안전 종료\")"
    )

    # 5. pipeline call in main_menu
    content = content.replace(
        "user_excluded_zones, user_bonus_score_zones, user_bonus_quota_zones,\n                output_count, user_manual_nums",
        "user_excluded_zones, user_bonus_score_zones,\n                output_count, user_manual_nums"
    )

    # 6. input loop updates
    content = content.replace("elif choice == \"8\":\n            zone_input = input(\"\\n할당량(Max 30%)을 주입할 Zone ID(1~10)를 쉼표로 구분하여 입력 (초기화는 엔터): \").strip()\n            if not zone_input:\n                user_bonus_quota_zones = []\n            else:\n                try:\n                    user_bonus_quota_zones = [z for z in [int(x.strip()) for x in zone_input.split(\",\")] if\n                                              1 <= z <= 10]\n                except Exception as e:\n                    print(f\"[오류] 유효하지 않은 입력입니다. ({e})\")\n        elif choice in [\"9\", \"10\"]:", "elif choice in [\"8\", \"9\"]:")
    
    content = content.replace("mode_name = \"원칙주의(70점 미달 시 폐기)\" if choice == \"9\" else \"실용주의(점수 무시 상위 추출)\"", "mode_name = \"원칙주의(70점 컷오프 유지)\" if choice == \"8\" else \"실용주의(점수 무시 상위 추출)\"")
    
    content = content.replace("user_manual_mode = 1 if choice == \"9\" else 2", "user_manual_mode = 1 if choice == \"8\" else 2")

    content = content.replace("elif choice in [\"11\", \"12\"]:", "elif choice in [\"10\", \"11\"]:")
    content = content.replace("mode_name = \"원칙주의(70점 미달 시 제외)\" if choice == \"11\" else \"실용주의(점수 미달 시 강제 추출 보충)\"", "mode_name = \"원칙주의(70점 컷오프 유지)\" if choice == \"10\" else \"실용주의(점수 미달 시 강제 추출 보충)\"")
    content = content.replace("user_exclude_mode = 1 if choice == \"11\" else 2", "user_exclude_mode = 1 if choice == \"10\" else 2")

    content = content.replace("elif choice == \"13\":\n            user_budget = 10000\n            user_excluded_zones = []\n            user_bonus_score_zones = []\n            user_bonus_quota_zones = []", "elif choice == \"12\":\n            user_budget = 10000\n            user_excluded_zones = []\n            user_bonus_score_zones = []")

    content = content.replace("elif choice == \"14\":\n            view_past_recommendations()\n        elif choice == \"15\":", "elif choice == \"13\":\n            view_past_recommendations()\n        elif choice == \"14\":")
    
    content = content.replace("초기화 (Default 리셋)\")\n        print(\" 14. [조회]", "초기화 (Default 리셋)\")\n        print(\" 13. [조회]")

    content = content.replace("명령 번호를 선택하십시오 (1-15):", "명령 번호를 선택하십시오 (1-14):")
    
    content = content.replace("비활성 (9번 모드 가동 중)", "비활성 (8번 모드 가동 중)")
    content = content.replace("비활성 (10번 모드 가동 중)", "비활성 (9번 모드 가동 중)")
    content = content.replace("비활성 (11번 모드 가동 중)", "비활성 (10번 모드 가동 중)")
    content = content.replace("비활성 (12번 모드 가동 중)", "비활성 (11번 모드 가동 중)")

    content = content.replace("수동 제어 9/10번", "수동 제어 8/9번")
    content = content.replace("수동 제어 11/12번", "수동 제어 10/11번")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)


def update_scoring_engine():
    file_path = 'D:/lotto_project/lotto_project_github/soft_scoring_engine.py'
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Remove user_bonus_quota_zones from extract_top_recommendations signature
    content = content.replace(
        "user_bonus_quota_zones: Optional[List[int]] = None, output_count: int = 10,",
        "output_count: int = 10,"
    )
    
    content = content.replace(
        "user_bonus_quota_zones (Optional[List[int]], optional): 구조적 30% 할당 대상 구역 (Menu 8).\n            output_count (int",
        "output_count (int"
    )

    # 2. Logic replacement for Pure Shuffle
    old_logic_part1 = """        # -------------------------------------------------------------
        # [우선순위 2순위] 우대 Zone 구조적 할당 (Stochastic 셔플링)
        # -------------------------------------------------------------
        rem_quota = output_count - len(selected_indices)
        if user_bonus_quota_zones and rem_quota > 0:
            max_bonus_quota = max(1, int(rem_quota * 0.3))
            print(f" - [2순위: 우대 할당] 잔여 예산 기준 우대 Zone 최대 {max_bonus_quota:,}게임 주입 시도...")

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
                print(f"   -> 성공: 우대 Zone에서 {len(sampled_bonus):,}게임 무작위 할당 완료")"""

    old_logic_part2 = """        # -------------------------------------------------------------
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
                print(f" - [3순위: AI 다각화] 잔여 {rem_quota:,}게임 -> 자연 우량 {len(unique_zones)}개 구역 무작위 균등 분산 배치 중...")

                num_zones = len(unique_zones)
                quota_per_zone = rem_quota // max(1, num_zones)
                remainder = rem_quota % max(1, num_zones)

                # [편향 방어] 잉여 할당(+1)을 부여받을 Zone 인덱스를 무작위 비복원 추출
                bonus_zone_indices = set(
                    np.random.choice(num_zones, size=remainder, replace=False)) if remainder > 0 else set()

                for i, z in enumerate(unique_zones):
                    z_indices = top_tier_indices[top_zones == z]
                    take_count = quota_per_zone + (1 if i in bonus_zone_indices else 0)
                    take_count = min(take_count, len(z_indices))

                    if len(z_indices) > 0 and take_count > 0:
                        sampled = np.random.choice(z_indices, size=take_count, replace=False)
                        selected_indices.extend(sampled)
                        used_mask[sampled] = True"""

    new_logic_pure_shuffle = """        # -------------------------------------------------------------
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
                    used_mask[top_tier_indices] = True"""

    # We need to replace part1 + part2 with new_logic_pure_shuffle.
    # Due to exact indent and spacing issues, let's use string find and slice.
    start_idx = content.find("        # -------------------------------------------------------------\n        # [우선순위 2순위]")
    end_idx = content.find("        # 3순위 분산 후에도 목표 게임 수가 부족한 경우 잔여 게임 무작위 채우기")

    if start_idx != -1 and end_idx != -1:
        content = content[:start_idx] + new_logic_pure_shuffle + "\n\n" + content[end_idx:]

    # Fixing the comments referring to "3순위" -> "2순위"
    content = content.replace(
        "        # 3순위 분산 후에도 목표 게임 수가 부족한 경우",
        "        # 2순위 셔플 후에도 목표 게임 수가 부족한 경우"
    )
    
    # Fixing module responsibility docstring
    content = content.replace(
        "- 상호 배타적 4단계 우선순위 큐(0순위: 마스킹 -> 1순위: 고정수 -> 2순위: 우대Zone -> 3순위: AI다각화) 파티셔닝.",
        "- 상호 배타적 3단계 우선순위 큐(0순위: 마스킹 원천차단 -> 1순위: 지정 고정수 -> 2순위: 우량 풀 통합 Pure Shuffle) 파티셔닝."
    )
    
    content = content.replace(
        "          2순위: 우대 Zone 구조적 할당 (잔여량의 최대 30%, max(1, int(rem * 0.3)))\n          3순위: 잔여 예산 자연 Cold Zone 무작위 균등 분산 (잉여 몫 비복원 추첨 배정)",
        "          2순위: 70점 이상 전체 우량 조합 풀 대상 확률적 완전 무작위 셔플 (Pure Shuffle)"
    )

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

update_main_py()
update_scoring_engine()
print("Menu and engine restructuring applied successfully.")
