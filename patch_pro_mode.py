import os

file_soft = 'D:/lotto_project/lotto_project_github/soft_scoring_engine.py'
with open(file_soft, 'r', encoding='utf-8') as f:
    text = f.read()

# Fix the fallback logic so that budget is always fulfilled unless explicit mode 1 is enabled
text = text.replace(
    "if need > 0 and (user_exclude_mode == 2 or user_manual_mode == 2):",
    "if need > 0 and user_exclude_mode != 1 and user_manual_mode != 1:"
)
text = text.replace(
    "print(f\"   -> [주의] '실용(강제추출)' 모드 규정에 따라 70점 미만 풀에서 {need:,}게임 무작위 보충합니다.\")",
    "print(f\"   -> [주의] 예산 달성({output_count:,}게임)을 위해 70점 미만 풀에서 {need:,}게임 무작위 추출하여 보충합니다.\")"
)

# Fix label alignment formatting
text = text.replace("f\" 게임 {label:<4} :", "f\" 게임 {label:<5} :")
text = text.replace("f\"게임 {label:<4} :", "f\"게임 {label:<5} :")

with open(file_soft, 'w', encoding='utf-8') as f:
    f.write(text)

file_main = 'D:/lotto_project/lotto_project_github/main.py'
with open(file_main, 'r', encoding='utf-8') as f:
    text2 = f.read()

text2 = text2.replace("f\" 게임 {label:<4} :", "f\" 게임 {label:<5} :")
with open(file_main, 'w', encoding='utf-8') as f:
    f.write(text2)

file_audit = 'D:/lotto_project/lotto_project_github/audit_validator.py'
with open(file_audit, 'r', encoding='utf-8') as f:
    text3 = f.read()

text3 = text3.replace("f\" 게임 {label:<4} :", "f\" 게임 {label:<5} :")
text3 = text3.replace("f\"  게임 {r['game_label']:<4} :", "f\"  게임 {r['game_label']:<5} :")
with open(file_audit, 'w', encoding='utf-8') as f:
    f.write(text3)

print("Patch applied successfully.")
