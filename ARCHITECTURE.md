# 백엔드 아키텍처 및 필터링 알고리즘 규격 (v1.9)

## 1. 데이터베이스 스키마 및 물리 구조

### DB 초기화 성능 최적화 및 캐싱 (Bulk PRAGMA & Binary Caching)
- `PRAGMA synchronous = OFF;` (초기화) / `PRAGMA synchronous = NORMAL;` (런타임 WAL)
- `PRAGMA journal_mode = MEMORY;` (초기화) / `PRAGMA journal_mode = WAL;` (런타임 동시성)
- 배치 크기: 마스터 풀 100,000건, 당첨 이력 ETL 100~1,000건 단위 분할 커밋 (`executemany`)
- 바이너리 캐시: `lotto_master_pool.npy` (8,145,060 × 6 uint8 포맷 직렬화 캐싱, 디스크 I/O 0.15초 단축)
- 출력 경로: `predictions/lotto_recommendations_{target_round}회.txt` 및 `.csv`
- 인덱스 구축 전략: 814만 건 전체 벌크 적재 완료 후 인덱스 생성 (B-Tree 오버헤드 최소화)

### 테이블 상세 스키마

1. **`LOTTO_COMBINATIONS_POOL` (마스터 조합 풀)**
   - 규모: 총 8,145,060건
   - 컬럼:
     - `combination_id` (INTEGER PRIMARY KEY): 1 ~ 8,145,060 오름차순 시퀀스
     - `num1`, `num2`, `num3`, `num4`, `num5`, `num6` (INTEGER NOT NULL): 1~45 조합 번호
     - `zone_id` (INTEGER NOT NULL): 구역 ID (1~10)
   - **Zone ID 분할 공식**: `zone_id = min((idx - 1) // 814506 + 1, 10)` (구역당 814,506건 균등 분할)
   - 인덱스:
     - `idx_zone` ON `LOTTO_COMBINATIONS_POOL (zone_id)`
     - `idx_nums` ON `LOTTO_COMBINATIONS_POOL (num1, num2, num3, num4, num5, num6)`

2. **`WINNING_HISTORY` (역대 당첨 이력 테이블)**
   - 컬럼:
     - `round_no` (INTEGER PRIMARY KEY): 추첨 회차
     - `draw_date` (TEXT): 추첨 일자 (`YYYY-MM-DD` 표준 형식, 오프라인 CSV 결측 시 `"Unknown"`)
     - `num1`, `num2`, `num3`, `num4`, `num5`, `num6` (INTEGER NOT NULL): 1등 당첨 번호
     - `bonus` (INTEGER NOT NULL): 보너스 번호
     - `combination_id` (INTEGER): 마스터 풀 역추적 고유 ID
     - `zone_id` (INTEGER): 해당 조합의 구역 ID (1~10)
   - 동기화 전략: 
     - **오프라인 ETL**: CSV 파싱 (CP949/UTF-8 대응)
     - **온라인 크롤러 ETL**: 동행복권 `allWinExel` 엔드포인트 증분 벌크 수집 (`drwNoStart={start_round}`)
     - **트랜잭션 멱등성**: `INSERT OR REPLACE INTO WINNING_HISTORY` 멱등 적재

3. **`WEEKLY_AUDIT_LOG` (주간 검증 및 감사 로그 테이블)**
   - 컬럼:
     - `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
     - `target_round_no` (INTEGER NOT NULL): 대상 추첨 회차
     - `created_at` (DATETIME DEFAULT CURRENT_TIMESTAMP): 생성 일시
     - `combination_id` (INTEGER NOT NULL): 추천 조합 ID
     - `num1`, `num2`, `num3`, `num4`, `num5`, `num6` (INTEGER): 추천 번호 세트
     - `soft_score` (REAL): 2단계 Soft Scoring 가중치 점수
     - `is_final_top10` (BOOLEAN DEFAULT 0): 최종 포트폴리오(Top N) 선별 여부
     - `match_count` (INTEGER): 실제 당첨 번호 일치 개수 (사후 기록)
     - `bonus_matched` (BOOLEAN): 보너스 번호 일치 여부 (사후 기록)
     - `winning_rank` (INTEGER): 사후 판정 등수 (1~5등 / 0: 낙첨)

---

## 2. 시스템 이원화 구조 (Hard vs Soft)

### [1단계] 절대 탈락 지대 (Hard Filters = AND 조건)
단 하나의 조건이라도 위반 시 즉시 폐기하여 약 150만~250만 건의 '정상 조합 생존 풀' 형성.

> **과적합 방어 절대 규칙 (Hard Rule)**  
> 직전 회차 또는 최근 2회차의 `zone_id`를 1단계 Hard Filter에서 통째로 강제 탈락시키는 로직은 1등 당첨 확률을 선제적으로 파괴하는 치명적 과적합(Overfitting)이므로 절대 금지한다. Zone 제어는 2단계 Soft Scoring 가감점으로만 처리한다.

1. **[Filter 02] 인접 ID 블록 제외**: 직전 회차 당첨 ID 기준 $\pm N$ 범위 통째로 폐기 (기본값: $N = 10,000$).
2. **[Filter 03] 이월수 중복 제한**: 직전 회차 당첨 번호와 3개 이상 중복 배제.
3. **[Filter 04] 당첨 이력 제외**: 1회부터 직전 회차까지의 과거 1등 당첨 조합 영구 배제.
4. **[Filter 08] 연속 번호 제한**: 3연속 번호 이상 출현 배제 (정렬 배열 기준 `num[i+2] - num[i] == 2` 벡터 마스킹).
5. **[Filter 09] 동일 끝수 제한**: 일의 자리 동일 끝수 3개 이상 배제 (`mod 10` 빈도 $\ge 3$).
6. **[Filter 11] 한 구간 몰림 방지**: 10단위 구간에 4개 이상 몰림 배제 (`(num - 1) // 10` 빈도 $\ge 4$).
7. **[Filter 12] AC값 필터**: 산술 복잡도(AC) 6 이하(기하학적/등차수열) 배제 ($AC = \text{UniqueDiffs} - 5 \ge 7$ 통과).

#### 동적 패턴 하드필터 (Dynamic Hard Filters - Menu 2 연동)
최근 5~10회차 당첨 통계를 분석하여 런타임에 선택 토글 (`dynamic_filters` 딕셔너리):
8. **[Filter 14] 3주 연속 이월 쌍 제외 (`pair_ban`)**: 직전 2주 연속 동시 출현한 번호 쌍이 다시 동시에 2개 이상 포함된 조합 배제.
9. **[Filter 15] 시작 번호(1구) 이탈 배제 (`start_num_limit`)**: 1구 번호가 16 이상인 극단 패턴 배제.
10. **[Filter 18] 끝 번호(6구) 이탈 배제 (`end_num_limit`)**: 6구 번호가 30 이하인 극단 패턴 배제.
11. **[Filter 16] 직전 회차 이웃수 편중 방지 (`adjacent_limit`)**: 직전 회차 이웃수가 4개 이상 몰린 조합 배제.
12. **[Filter 17] 단기 초과열 번호 독점 배제 (`hyper_hot_ban`)**: 최근 5회차 중 3회 이상 출현한 번호가 2개 이상 포함된 조합 배제.
13. **[Filter 19] 10주 누적 초과열 Zone 배제 (`hyper_hot_zone_ban`)**: 최근 10회차 중 3회 이상 출현한 과열 Zone 조합 통째로 배제.

---

### [2단계] 점수 평가 지대 (Soft Scoring = 100점 만점 가중치 합산)

| 평가 항목 | 충족 조건 | 배점 |
| :--- | :--- | :---: |
| **[Filter 13] 장기 미출현수 포함** | 최근 10주 동안 출현하지 않은 미출현수 1~2개 포함 | **+25점** |
| **[Filter 05] 총합 구간** | 6개 번호의 총합이 100 ~ 175 사이 | **+20점** |
| **[Filter 01] ID 구역 균등 분산** | 최근 10주 출현 빈도가 중앙값(Median) 이하인 Cold Zone에 속함 | **+20점** |
| **[Filter 10] 10단위 멸 구간** | 5개 구간 중 번호가 출현하지 않은 멸구간 1~2개 존재 | **+15점** |
| **[Filter 06] 홀:짝 비율** | 홀짝 비율이 3:3, 4:2, 2:4 중 하나 (홀수 개수 2~4개) | **+10점** |
| **[Filter 07] 고:저 비율** | 저번호(1~22)와 고번호(23~45) 비율이 3:3, 4:2, 2:4 중 하나 (고번호 개수 2~4개) | **+10점** |

- **고득점 컷오프**: 100점 만점 중 **70점 이상**(`SCORE_CUTOFF = 70.0`) 획득 조합만 최종 후보 승격.

---

## 3. 우선순위 큐 및 수동 제어 알고리즘

### 우선순위 큐(Priority Queue) 파티셔닝 순서
1. **0순위 (마스킹)**: 누적 추출(Append) 시 당해 회차 기추출 ID 영구 배제 및 수동 제외수 마스킹
2. **1순위 (고정수)**: 수동 지정 1~5개 고정수 포함 부분공간 무작위 선별
3. **2순위 (우대 Zone)**: 지정 우대 Zone 70점 이상 조합 강제 주입 (`max(1, int(rem_quota * 0.3))`)
4. **3순위 (AI 다각화)**: 잔여 예산 분량 대상 최고 득점 및 70점 이상 자연 Cold Zone 비복원 균등 분산

### 편향 방어 기제
- **Random Noise 벡터 연산**: 동점자 정렬 시 결정론적 인덱스 고착화를 막기 위해 점수에 미세 난수(`np.random.rand() * 1e-5`)를 가산(Tie-Breaking Noise).
- **Stochastic Remainder Allocation**: 균등 분할 시 발생하는 잉여 잔여량(+1 게임) 배정 대상을 무작위 비복원 추첨(`np.random.choice`)하여 저번호 Zone의 독점 편향을 원천 차단.
- **Stochastic Stratified Sampling**: 기계적 `[::step]` 슬라이싱을 영구 폐기하고 `np.random.choice(..., replace=False)` 비복원 무작위 추출 적용.

### 수동 제어 및 예외 처리 규격 (Menu 6~12)
- **Menu 6 (원천 배제 패널티)**: 지정 Zone 조합에 **-100점** 패널티를 부여하여 컷오프(70점) 미달 탈락 유도. (Hard Filter에서 데이터 삭제 시 발생하는 사후 감사 Recall 0% 붕괴 오류 방지)
- **Menu 7 (단순 가점)**: 지정 Zone 조합에 **+20점** 가중치 강제 부여.
- **Menu 8 (구조적 할당량 주입 - 권장)**: 70점 이상 통과 조합 중 지정 Zone 조합을 잔여 출력량의 **최대 30%**까지만 강제 할당 (`max(1, int(rem_quota * 0.3))`). 잔여 분량은 Cold Zone 균등 분산 원칙 유지.
- **Menu 9 (원칙주의 고정수)**: 1~5개 고정수 부분공간 내에서 70점 이상 조합만 선별 (`user_manual_mode = 1`, 기준 미달 시 추출 거절).
- **Menu 10 (실용주의 고정수)**: 70점 미만이더라도 해당 부분공간 내 최고 득점 조합을 사용자 지정 게임 수만큼 강제 보충 (`user_manual_mode = 2`).
- **Menu 11 (원칙주의 제외수)**: 제외수 포함 조합 영구 차단 후 70점 이상 우량 풀에서만 선별 (`user_exclude_mode = 1`).
- **Menu 12 (실용주의 제외수)**: 제외수 차단으로 70점 이상 우량 풀 고갈 시, 70점 미만 차상위 풀에서 강제 보충 (`user_exclude_mode = 2`).

---

## 4. 사후 감사 모듈 (Audit Logic)

### Recall Audit (Hard Filter 안전성 역추적 검증)
- 실제 1등 조합을 대상으로 기본 7대 Hard Filter의 위반 여부를 역검사하여 필터망의 과적합(Overfitting) 발생 여부를 판정.
- **검증 항목**:
  1. Filter 04: 과거 1등 당첨 이력 여부 (`SELECT COUNT(*) WHERE combination_id = ? AND round_no < ?`)
  2. Filter 02: 직전 1등 당첨 ID 기준 $\pm 10,000$ 범위 포함 여부
  3. Filter 03: 직전 당첨 번호와의 일치 개수 3개 이상 여부
  4. Filter 08: 3연속 번호 포함 여부
  5. Filter 09: 동일 끝수 3개 이상 포함 여부
  6. Filter 11: 10단위 구간 4개 이상 몰림 여부
  7. Filter 12: AC값 6 이하 여부

### Precision Audit (추천 포트폴리오 적중률 채점)
- 해당 회차 추첨 전 발행된 추천 세트(`WEEKLY_AUDIT_LOG` 내 `is_final_top10 = 1`)와 실제 당첨 번호/보너스 번호를 비교.
- **등수 판정 기준**:
  - 1등: 번호 6개 일치 (`match_count = 6`)
  - 2등: 번호 5개 일치 + 보너스 번호 일치 (`match_count = 5`, `bonus_matched = 1`)
  - 3등: 번호 5개 일치 (`match_count = 5`, `bonus_matched = 0`)
  - 4등: 번호 4개 일치 (`match_count = 4`)
  - 5등: 번호 3개 일치 (`match_count = 3`)
  - 낙첨: 번호 2개 이하 일치 (`match_count <= 2`, `winning_rank = 0`)
- **DB 업데이트**: 판정된 `match_count`, `bonus_matched`, `winning_rank`를 해당 로그 레코드에 영구 갱신 (`UPDATE`).