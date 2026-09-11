# 로또 6/45 조합 최적화 및 시스템 추천 엔진 (v1.9)

수학적 확률론, 통계학적 필터링, SQLite 기반 대용량 파이프라인을 활용한 데이터 기반 로또 조합 분석 및 추천 시스템입니다.

## 🛠️ 개발 환경 및 인터페이스 규격
- **개발 언어**: Python 3.x
- **핵심 라이브러리**: Pandas, NumPy (대용량 고속 필터링 및 배열 연산), SQLite3 (8,145,060건 인덱싱 및 트랜잭션)
- **메모리 캐싱**: `lotto_master_pool.npy` (8,145,060 x 6 uint8 바이너리 캐시, 0.15초 로드)
- **인터페이스 방식**: No-UI 통합 CLI 컨트롤러 (`python main.py`)
- **출력 규격**:
  - 추천 조합 생성 시 `predictions/` 디렉토리에 `lotto_recommendations_{target_round}회.txt` 및 `.csv` 파일로 자동 리빌드 저장.
  - 당첨 번호 발표 후 사후 검증 결과 리포트(Audit Report)를 ANSI 컬러 콘솔 텍스트로 출력 (당첨번호: 노란색 `[NN]`, 보너스: 청록색 `(NN)`).

---

## 📦 주요 모듈 구조 및 기능 명세

1. `main.py` (통합 CLI 라우터 & 오케스트레이터)
   - 시스템의 단일 진입점(Entry Point)으로 15-Way CLI 인터페이스 제공.
   - 런타임 세션 상태(제외/가점/할당/고정수/제외수/예산/동적 필터)를 유지하며 하위 엔진으로 전달.
   - Menu 1 동기화 서브메뉴(오프라인 CSV vs 온라인 실시간 벌크 크롤러) 분기 라우팅.
   - 누적 추출(Append) 시 시각적 경고 및 분기 처리, 다각화 결과 리빌드 저장.

2. `init_db.py` (마스터 DB 초기화 및 조합 풀 생성기)
   - `lotto.db` 생성 및 3개 핵심 테이블(`LOTTO_COMBINATIONS_POOL`, `WINNING_HISTORY`, `WEEKLY_AUDIT_LOG`) 스키마 구축.
   - 전체 8,145,060개 번호 조합과 구역(`zone_id`) 계산 배치 삽입 및 복합 인덱스(`idx_zone`, `idx_nums`) 생성.

3. `csv_to_db_etl.py` (오프라인 CSV 기반 당첨 이력 ETL)
   - 로컬 최신 `.csv` 파일을 자동 감지하여 당첨 이력 데이터를 DB에 동기화 (`INSERT OR REPLACE` 멱등적 적재).
   - 필수 CSV 헤더 규격: `회차, num1, num2, num3, num4, num5, num6, 보너스` (추첨일 누락 시 `"Unknown"` 대체).
   - CP949 / UTF-8 인코딩 자동 폴백 및 1,000건 단위 배치 트랜잭션 커밋.

4. `fetch_winning_history.py` (온라인 벌크 크롤러 ETL)
   - 동행복권 공식 웹 서버(`method=allWinExel`)로 단 1회의 HTTP 벌크 요청을 전송해 신규 회차 증분 일괄 수집.
   - EUC-KR HTML 20개 컬럼 고속 정규식 파싱 및 `YYYY-MM-DD` 표준 일자 규격 변환.
   - 마스터 풀 인덱스 스캔 후 `INSERT OR REPLACE` 기반 멱등적 적재.

5. `hard_filter_engine.py` (1단계 절대 탈락 지대 엔진)
   - 814만 개 마스터 조합 풀(`.npy` 바이너리 캐시)을 메모리에 로드하여 C-레벨 NumPy 벡터 연산 수행.
   - 기본 7대 Hard Filter 및 선택형 동적 패턴 필터 6종을 AND 조건으로 실행해 비정상 조합 전량 배제.
   - Filter 02 인접 블록 기본값: $\pm 10,000$ 범위 배제.

6. `soft_scoring_engine.py` (2/3단계 가중치 채점 및 동적 층화 추출 엔진)
   - 최근 10회차 통계 기반 장기 미출현수(Cold) 및 중앙값 이하 미출현 구역(Cold Zone) 동적 산출.
   - 1단계 생존 조합 대상 100점 만점 가중치 평가 및 70점 컷오프 적용.
   - 상호 배타적 4단계 우선순위 큐(0순위: 누적/제외수 마스킹 -> 1순위: 수동 고정수 -> 2순위: 우대 Zone 할당 -> 3순위: AI 자연 Cold Zone 분산) 파티셔닝.
   - Random Noise 기반 동점자 셔플링, `np.random.choice` 비복원 추출, 잔여 게임 무작위 보너스 Zone 배정 적용.

7. `audit_validator.py` (사후 감사 및 성능 검증기)
   - 회차 추첨 후 실제 1등 번호 대상 기본 7대 Hard Filter 생존율(Recall Audit) 역추적 검증.
   - 추천 포트폴리오 적중률(Precision Audit) 채점: 1~5등 등수 판정 및 `WEEKLY_AUDIT_LOG` 영구 업데이트.
   - ANSI 컬러 터미널 하이라이팅 및 26게임 초과 대응 포맷팅(`A, B ... Z, A1, B1 ...`).

8. `verify_and_query.py` (DB 정합성 검증 및 인덱스 조회 테스트기)
   - DB 물리 무결성, 경계값, Zone별 분포 검증 및 ID/번호 역추적 단건 조회 벤치마킹.

---

## 🖥️ 15-Way CLI 메뉴 구성

### [1~4] 핵심 파이프라인 블록
- **1 (데이터 동기화)**: DB 최신 당첨 이력 동기화 (오프라인 CSV / 온라인 실시간 벌크 크롤러 서브메뉴 선택).
- **2 (패턴 진단 및 필터 설정)**: 최근 5~10회차 당첨 통계 실시간 분석 리포트 출력 및 6대 동적 하드필터 토글(ON/OFF).
  - `pair_ban`: Filter 14 (3주 연속 이월 쌍 제외)
  - `start_num_limit`: Filter 15 (1구 16 이상 극단 이탈 배제)
  - `adjacent_limit`: Filter 16 (직전 이웃수 4개 이상 몰림 배제)
  - `hyper_hot_ban`: Filter 17 (최근 5회 중 3회 출현 번호 2개 이상 배제)
  - `end_num_limit`: Filter 18 (6구 30 이하 극단 이탈 배제)
  - `hyper_hot_zone_ban`: Filter 19 (10주 누적 3회 출현 Zone 배제)
- **3 (추천 생성)**: AI 조합 추천 생성 및 파일 출력 (당해 회차 추천 이력 감지 시 '기존 내역 폐기' vs '중복 방지 누적 추출(Append)' 분기).
- **4 (사후 감사)**: 당첨 번호 입력 후 Recall & Precision 백테스팅 리포트 출력 및 DB 피드백 저장.

### [5~12] 파라미터 튜닝 블록
- **5 (예산 설정)**: 주간 구매 예산 설정 (기본값: 10,000원 / 10게임).
- **6 (Zone 배제)**: 특정 Zone 원천 배제 패널티 부여 (-100점).
- **7 (Zone 가점)**: 특정 Zone 단순 가점 부여 (+20점).
- **8 (Zone 할당)**: 구조적 할당량 주입 (`max(1, int(rem_quota * 0.3))` 강제 할당, 권장 표준).
- **9 (원칙주의 고정수)**: 1~5개 고정수 포함 조합 중 70점 이상만 선별 (`user_manual_mode = 1`).
- **10 (실용주의 고정수)**: 고정수 포함 조합 중 70점 미만이더라도 최상위 점수 강제 선별 (`user_manual_mode = 2`).
- **11 (원칙주의 제외수)**: 지정 제외수 조합 원천 차단 후 70점 이상 유지 (`user_exclude_mode = 1`).
- **12 (실용주의 제외수)**: 지정 제외수 조합 차단 후 풀 고갈 시 70점 미만에서 강제 보충 (`user_exclude_mode = 2`).

### [13~15] 시스템 생명주기 블록
- **13 (전역 초기화)**: 모든 수동 파라미터 및 동적 필터 설정을 Default(10,000원 / 10게임 / AI 순수 모드)로 리셋.
- **14 (과거 조회)**: 과거 회차 추천 조합 복구 조회 및 콘솔 출력.
- **15 (시스템 종료)**: 시스템 자원 반환 및 안전 종료.

---

## ⚙️ 시스템 상수 및 계산 공식
- `GAME_UNIT_PRICE` = 1,000원 (게임당 단가 고정 상수)
- `SCORE_CUTOFF` = 70.0점 (우량 조합 고득점 컷오프 기준)
- 추출 게임 수 = `user_budget // GAME_UNIT_PRICE` (기본 10,000원 입력 시 10게임)
- 구조적 Zone 할당량(Menu 8) = `max(1, int(rem_quota * 0.3))`
- 결과 파일 경로: `predictions/lotto_recommendations_{target_round}회.txt` 및 `.csv`