# 백엔드 아키텍처 및 필터링 알고리즘 규격 (v1.9)

## 1. 데이터베이스 스키마
- **DB 01 (`LOTTO_COMBINATIONS_POOL`)**: 8,145,060건 마스터 조합 풀 (1~8,145,060 ID 시퀀스).
- **DB 02 (`WINNING_HISTORY`)**: 역대 당첨 이력 (`round_no`, `draw_date`, 번호 6개, 보너스, `combination_id`, `zone_id` 1~10).
- **DB 03 (`WEEKLY_AUDIT_LOG`)**: 회차별 1단계 생존 풀 및 최종 추천 ID 기록.

## 2. 시스템 이원화 구조 (Hard Filter vs Soft Scoring)

### [1단계] Hard Filters (AND 조건 - 절대 탈락)
1. **Filter 02**: 직전 회차 당첨 ID 기준 ±N 범위 통째로 폐기.
2. **Filter 03**: 직전 회차 당첨 번호와 3개 이상 중복 배제.
3. **Filter 04**: 과거 1등 당첨 이력 영구 배제.
4. **Filter 08**: 3연속 번호 이상 출현 배제.
5. **Filter 09**: 동일 끝수 3개 이상 배제.
6. **Filter 11**: 10단위 구간에 4개 이상 몰림 배제.
7. **Filter 12**: AC값 6 이하(기하학적/등차수열) 배제 (7 이상 통과).

#### 동적 패턴 하드필터 (Menu 2 연동 선택 항목)
8. **Filter 14**: 3주 연속 이월 쌍 배제.
9. **Filter 15**: 시작 번호(1구) 16 이상 극단 이탈 배제.
10. **Filter 18**: 끝 번호(6구) 30 이하 극단 이탈 배제.
11. **Filter 16**: 직전 회차 이웃수 4개 이상 몰림 배제.
12. **Filter 17**: 최근 5회차 중 3회 이상 출현 초과열 번호 2개 이상 배제.
13. **Filter 19**: 최근 10회차 중 3회 이상 출현 초과열 Zone 통째로 배제.

---

### [2단계] Soft Scoring (100점 만점 가중치 평가 & 70점 컷오프)
- **Filter 13 (장기 미출현수 1~2개 포함)**: +25점
- **Filter 05 (총합 구간 100~175)**: +20점
- **Filter 01 (ID 구역 Cold Zone 속함)**: +20점
- **Filter 10 (10단위 멸구간 1~2개 존재)**: +15점
- **Filter 06 (홀짝 비율 3:3, 4:2, 2:4)**: +10점
- **Filter 07 (고저 비율 3:3, 4:2, 2:4)**: +10점

---

## 3. 우선순위 큐 & 편향 방어 알고리즘
- **우선순위 순서**: 0순위(누적 및 수동 제외 마스킹) → 1순위(수동 고정수) → 2순위(우대 Zone) → 3순위(자연 Cold Zone 균등 분산).
- **Random Noise 벡터 연산**: 점수에 미세 난수(`np.random.rand() * 1e-5`)를加算하여 동점자 정렬 시 결정론적 결함 제거.
- **Stochastic Remainder Allocation**: 잉여 잔여량(+1 게임) 배정 대상을 무작위 비복원 추첨하여 저번호 구역의 독점 방지.
- **Stochastic Stratified Sampling**: 기계적 `[::step]` 슬라이싱을 폐기하고 `np.random.choice(..., replace=False)` 비복원 무작위 추출 및 반환 직전 `np.random.shuffle` 실행.

---

## 4. 사후 감사 모듈 (Audit Logic)
- **Recall Audit**: 실제 1등 번호가 1단계 생존 풀(약 150만~250만 건) 내에 포함되어 있는지 검증.
- **Precision Audit**: 실제 1등 번호가 최종 추천 풀(`WEEKLY_AUDIT_LOG`) 상위권에 안착했는지 적중률 평가.