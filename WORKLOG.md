# 작업 일지 (프로젝트 시작 ~ 10/7)

> **작성 근거**: git 커밋(9/22, 9/30), 파일 수정 시각, 산출물(`data/`), 코드·README·CLAUDE.md.
> 일일 메모나 대화 기록이 없어서 **9/30까지의 실제 작업 시간과 막힌 이유는 코드·산출물에서 추정**했습니다. 10/1은 작업 대화 기록을 바탕으로 정리했습니다.
> 추정인 항목은 `(추정)`으로 표시했으니 기억과 다르면 고쳐 주세요.
> 9/23~9/27은 남은 파일이 없어 기록을 만들지 않았습니다.

## 한눈에 보기

| 일자 | 핵심 |
|---|---|
| 9/22 (화) | 저장소 생성 (Initial commit) |
| 9/23~9/27 | 기록 없음 (확인 필요) |
| 9/28 (월) | 환경 세팅, LangGraph 뼈대, 자막 수집 |
| 9/29 (화) | 자막 수집 이어받기, 식당 정보 추출 1차 결과 |
| 9/30 (수) | 추출 개선·검증, Gemini 영상 분석 실험, 노트 적재 스크립트, 커밋 |
| 10/1 (목) | 7개 영상으로 3~5번 파이프라인 테스트, 미슐랭 판별을 Parse API로 교체, 지역(시>구/군) 보강 설계·구현 |
| 10/2 (금) | DB 스키마 정리(미사용 컬럼·테이블 삭제, `regions`를 `region_1~3` 컬럼으로 대체), 백업 후 Supabase 마이그레이션 적용 |
| 10/3~10/4 | 기록 없음 |
| 10/5 (월) | 중국(CN) 식당 제외(CSV 19행 삭제·코드 반영), README 프로젝트 구조 최신화, 미슐랭 채우기 재개 방식·임베딩·청킹 구조 확인, 청킹 비교 실행 |
| 10/6 (화) | 자막 수집을 yt-dlp로 교체(121개), 자막 기반 추출·DB 적재(v2), 청크를 FOOD/DRINK로 분리, 셰프 정보 추출·적재, 추출 품질 개선 실험(v3~v7, DB 미적용) |
| 10/7 (수) | 자막 추출을 A/B로 나눈 실험(코스 단계·대표 셰프 포함), 2026년 수동 자막 34개 영상 49곳 실행, 음식 분류 재호출·후처리, 문서 갱신 (DB 미적용) |

---

## 9/22 (화) — 프로젝트 시작
**오늘 한 일**
- GitHub 저장소 생성, `.gitignore`(Python 템플릿)와 빈 README 커밋 (`Initial commit`)

**해결해야 할 일**
- 주제(미식 유튜브 → 맛집 추천 RAG 챗봇) 구체화, 데이터 수집 대상 채널 확정

---

## 9/23 ~ 9/27 — 기록 없음
- 커밋과 파일이 남아 있지 않습니다. 기획·자료 조사 등 코드 외 작업을 했다면 직접 채워 넣어 주세요.
- `data/urls/`의 URL 수집은 노트북(`notebooks/crawling.ipynb`)에서 먼저 했을 가능성이 있으나 날짜는 확인되지 않습니다. (추정)

---

## 9/28 (월)
**오늘 한 일**
- Python 버전 고정(`.python-version`), `langgraph.json` 작성, `src/app.py` 빈 그래프 생성 (13:07~13:17)
- `langgraph dev`를 실행해 로컬 체크포인트 파일이 생성됨 (13:19) → 개발 서버 기동 확인
- 영상 자막 53개를 `data/transcripts/`에 저장 (14:06~14:07, 약 1분 사이 일괄 수집)

**막히는 부분 & 해결 방법** (추정)
- 자막 수집: Playwright 방식(`get_transcripts.py`)과 `youtube-transcript-api` 방식(`save_scripts.py`)을 둘 다 만듦. 둘 다 파일명 규칙이 달라 이후 TODO로 남음.
- 짧은 시간에 대량 요청하면 차단될 수 있어 `save_scripts.py`에 `_progress.json` 이어받기 기능을 둠.

**해결해야 할 일**
- 전체 영상(필터 통과 313개)의 자막 수집
- 자막 스크립트 하나로 통일

---

## 9/29 (화)
**오늘 한 일**
- 자막 진행 상황 갱신 (`_progress.json`, 10:27)
- 필터링(`channel_video_filter.py`), 자막 수집 스크립트 정리 (19:03)
- 식당 정보 추출 첫 결과 `restaurants_info.csv/xlsx` 생성 (19:38)
  - 소개란에서 식당 정보 추출 → Google Places 조회 → 미슐랭 판별 흐름

**막히는 부분 & 해결 방법**
- 소개란에 가게·장소가 없는 영상이 있음 → 소개란 키워드 필터로 통과 313개 / 제외 30개 분리 (URL 수는 파일 기준)
- 추출 중 중단되는 문제 대비 → 건별로 CSV에 즉시 기록해 재실행 시 이어서 처리 (추정)

**해결해야 할 일**
- 외국 식당 Places 매칭 정확도 검증
- 미슐랭 판별의 신뢰도 (LLM이 근거 없이 연도를 채우는 문제)

---

## 9/30 (수)
**오늘 한 일**
- 오전: 첫 커밋 `Add RAG chatbot source, notebooks, and project config` (10:15), Python 3.14 환경으로 실행 확인 (10:50)
- DB 적재 스크립트 `load_restaurants.py` 동작 확인 (11:04)
- 식당 정보 추출 검증을 단계적으로 진행
  - sample(11:05) → pilot(12:25) → pilot2(13:17) → 사람이 검토할 목록 `pilot2_review.xlsx` 생성(`export_review_list.py`, 13:18)
  - 추출 로직 수정(`extract_restaurant_info.py` 13:13, 17:10), 4개 영상 최종 검증(`verify4`, 17:57)
- 비정형 정보 추출 실험
  - 자막 기반(`extract_transcript_notes`, 14:08)
  - Gemini 영상 직접 분석(`gemini-3.5-flash-lite`, 14:47~)
  - 프롬프트·방식을 바꿔 비교: `v1` → 소개란 없음(`nodesc`) → 단일 호출(`v2_single`) → 가게 단위 호출(최종, 17:11)
- 적재 스크립트·스키마 추가: `load_notes.py`, `embed_chunks.py`, `migrate_notes.sql`, `restaurant_chunks.sql` (17:12~17:13)
- 검증 결과를 반영해 `load_restaurants.py` 수정 (18:00)

**막히는 부분 & 해결 방법**
| 문제 | 해결/현재 상태 |
|---|---|
| 영상 1개에 가게가 여러 곳이라 결과가 섞임 | Gemini 호출을 **가게 단위**로 분리, 파일명에 `{video_id}__{google_cid}` 사용 |
| 소개란을 넣을 때와 뺄 때 결과 차이 | `nodesc` 실험으로 비교. 최종적으로 가게 단위 방식을 선택 (추정) |
| Places가 같은 주소의 다른 가게, 외국 식당을 잘못 매칭 | 후보 5개를 받아 LLM이 선택, 외국 식당은 현지어 이름으로 2차 검색. **일부는 여전히 오류** → 사람이 확인 |
| 미슐랭 등급·연도를 LLM이 근거 없이 채움 | `history`는 연도 근거 필수, `current_grade`는 근거 문장 인용 필수. 근거 없으면 `UNKNOWN` |
| `google_cid`가 BIGINT 범위를 넘음 | 문자열로 저장, 중복 제거 기준으로 사용 |
| `open_now`는 조회 시점 값 | 저장하지 않고 `restaurant_hours` + 현지 시간으로 계산 |
| 실행할 때마다 검색·LLM 결과가 달라 미슐랭 등재를 놓침(false negative) | **미해결** |

**해결해야 할 일**
- DB 실제 연결 확인과 적재 (스크립트만 작성, 미실행)
- 이전 방식으로 만든 CSV/DB의 미슐랭 재추출
- 전체 영상 대상 Gemini 추출 (실행 전 건수·비용 안내 후 소수 샘플로 검증)
- LangGraph 챗봇 구현 (`app.py`는 빈 그래프)
- n8n 자동화

---

## 10/1 (목)
**오늘 한 일**
- **자막 수집 불필요 확인**: Gemini 영상 분석(`extract_video_notes.py`)은 자막 파일을 읽지 않고 `TranscriptNotes` 스키마만 import함. 자막 스크립트 3개(`get_transcripts.py`, `save_scripts.py`, `extract_transcript_notes.py`)는 지우지 않고 README·CLAUDE.md에 "미사용"으로 표시
- **7개 영상(여러 식당이 나오는 영상)으로 3~5번 파이프라인 테스트** (식당 24곳)
  - 3번(식당 정보 추출): Places 매칭 24/24 성공, 영상 1개(`G4gG5SqhfE8`)는 소개란에 가게 정보가 없어 추출 실패
  - 5번(Gemini 영상 분석): 24곳 처리(23곳 성공 + 1곳 재시도 성공). 입력 약 283만 토큰, **비용 약 $0.91**, 가게당 평균 55초
  - 결과 파일: `data/test7_*` (git 제외)
- **미슐랭 판별을 Parse(guide.michelin.com) API로 교체** (`check_michelin_status_api`)
  - 기존 Tavily 방식이 이름만 검색해 다른 도시 지점이 섞이고 실행마다 결과가 달랐음. 본앤브레드 해운대가 3스타(실제 SELECTED), 썸머팰리스(홍콩)가 UNKNOWN/NONE(실제 1스타)으로 오판
  - 검색 결과 중 이름과 도시(또는 거리 주소)가 일치하는 후보만 채택, 등급은 API의 `distinction` 사용. 호출 사이 12.5초 간격(무료 플랜 분당 5회)
  - 24곳 재검증: 1_STAR 1곳, SELECTED 1곳, NONE 22곳(후보 0건 16곳, 이름·도시 불일치 6곳 모두 다른 식당 확인)
  - 테스트 CSV에 썸머팰리스(1스타, 샹그릴라 공식 페이지 기준 2008~2026 이력)와 본앤브레드(SELECTED) 정정. 기존 Tavily 방식은 `--michelin-fallback`으로만 사용
- **지역 보강(시 > 구/군)** (`enrich_regions.py`, `regions` 스키마, `migrate_regions.sql`)
  - 처음에는 시 > 구 > 동 3단계와 가까운 역(Nearby Search)을 설계·구현해 24곳으로 실행. 결과를 보고 방향을 바꿔 **지역은 시 + 구/군 2단계(구/군이 없으면 시만, 도로명 제외)로 줄이고 근처 역 저장은 제거**
  - 근처 질문("성수역 근처", "트레비 분수 근처")은 질문 시점에 지명 좌표를 조회해 `restaurants`의 위경도로 반경 검색하기로 결정
  - 저장된 `address_components`로 API 호출 없이 재계산(`--rebuild-from`)해 24곳 검증: 2단계 14곳, 시만 10곳
- **챗봇 구조 정리**: 파이프라인은 검색할 재료를 만드는 오프라인 단계, 챗봇 에이전트가 좌표 조회·DB 검색(SQL)·RAG 검색·Tavily 검색을 도구로 사용. SQL 도구는 LLM이 SQL을 쓰지 않고 파라미터가 정해진 함수(`search_restaurants` 등)로 만들기로 함
- README, CLAUDE.md를 위 변경에 맞게 갱신 (미슐랭 Parse API, 환경변수 `MICHELIN_GUIDE_API_KEY`, 지역 단계, 자막 단계 제거)

**막히는 부분 & 해결 방법**
| 문제 | 해결/현재 상태 |
|---|---|
| 로그를 파일로 리다이렉트하면 영상 제목의 이모지 때문에 인코딩 오류(cp949)로 중단 | `PYTHONUTF8=1`로 재실행. 이어받기로 처리된 영상은 건너뜀 |
| 영상 `G4gG5SqhfE8`은 소개란에 가게 정보가 없어 추출 실패 (홍콩 광둥요리 2곳 누락) | **미해결.** 가게 정보가 없는 영상만 Gemini로 가게 이름·주소를 찾아 기존 흐름에 넘기는 방안. 전체 313개 중 몇 개인지 먼저 확인 필요 |
| Gemini 429(할당량 초과)·503(과부하) 반복, 가게당 평균 55초 | 30초 대기 후 재시도로 완료. 전체 실행은 유료 키나 호출 간격 조정 필요 |
| Tavily 미슐랭이 실행마다 달라지고 다른 도시 지점이 섞임 | Parse API로 교체 |
| Parse API는 현재 등급만 주고 연도별 이력이 없음. 이력은 다른 근거 필요 | 이력은 근거가 확인된 경우에만 채움(썸머팰리스는 샹그릴라 공식 페이지). `guide_year` 필드가 에디션 연도인지는 **미검증** |
| Google이 한국 도로명 주소를 줘서 `addressComponents`에 동이 없음 | 요구사항을 시 + 구/군 2단계로 변경 |
| 가까운 역 조회에서 "해운대경찰서"가 지하철역으로 나옴(Google 데이터 잡음) | 근처 역 저장 자체를 제거하고 좌표 반경 검색으로 대체 |
| 결과 CSV가 엑셀에서 열려 있어 쓰기 실패 | 파일을 닫고 재실행. 파일 내용은 손상되지 않음 |
| README의 일부 변경(자막·Gemini 관련)이 원래대로 돌아가 있었음 | 원인 불명. 다시 반영 |

**주의할 점**
- Parse 크레딧(월 200개)을 오늘 약 34회 사용. 호출 후 남은 크레딧 표시가 줄지 않아 실제 차감량은 Parse 대시보드에서 확인 필요. 전체 약 390곳을 조회하기에는 부족함
- Michelin API는 이름이 영문이라 한국어 상호는 매칭을 놓칠 수 있음(이번 샘플에는 해당 사례가 없어 **미검증**)
- 한국어 이름이 없는 해외 지역은 지역명이 현지 표기로 저장됨(Barcelona, Milano 등). 일본 등 `locality`가 구 단위인 나라는 "시" 자리에 구가 들어갈 수 있음
- 오늘 작업물은 아직 커밋하지 않음

**해결해야 할 일**
- 3번(식당 정보 추출) 전체 313개 실행 (건수·비용 안내 후 소수 샘플 검증, 미슐랭 단계만 약 80분)
- 소개란 누락 영상 보완, 한국어 상호 매칭 검증(밍글스 등)
- 지역 보강 전체 실행과 `load_regions.py` 작성
- Gemini 영상 분석 전체 실행. 가게마다 영상을 반복 분석해 비용이 커서 영상 1회 분석·캐싱 검토. 전체 실행 전에 자막 방식 결과와 샘플 비교
- DB 연결 확인과 실제 적재
- 챗봇 에이전트 구현 (도구: 좌표 조회, 파라미터 고정형 `search_restaurants`, RAG 검색, Tavily 검색)

---

## 10/2 (금)
> 이 절은 DB 스키마 정리 세션만 기록합니다. 같은 날 앞서 한 작업(200개 영상 223곳 실행, DB 적재 등)은 CLAUDE.md의 "현재 진행 상황"을 참고하세요.

**오늘 한 일**
- **파이프라인/챗봇 브랜치 합치는 방법 정리**: 두 작업의 접점은 코드가 아니라 DB 스키마. 충돌 가능 지점은 `README.md`, `CLAUDE.md`, `restaurant_schema.sql`, `uv.lock`
- **DB 스키마 정리** — 쓰지 않거나 정보가 중복인 컬럼·테이블을 코드에서 참조 위치를 확인한 뒤 삭제하고, 마이그레이션 `migrate_simplify_schema.sql` 하나로 통합해 Supabase에 적용(적용 후 검증 완료)

| 대상 | 조치 | 이유 |
|---|---|---|
| `menus.description` | 삭제 | 적재하지 않아 항상 NULL |
| `menus.is_signature` | 삭제 | 저장만 하고 읽는 곳 없음. 챗봇에서도 안 쓰기로 함 |
| `michelin_records`, CSV `history` | 삭제 | Parse API는 현재 등급만 줘서 항상 비어 있었음 |
| `michelin_status.edition_type`, `is_active` | 삭제 | `is_michelin`에서 유도되는 값이라 정보 없음. `checked_at`은 재조회 기준으로 유지 |
| `regions` 테이블, `restaurants.region_id` | 삭제 후 `restaurants.region_1~3` 컬럼으로 대체 | 깊이가 3으로 고정이라 계층 테이블의 이점이 작고 조회가 복잡 |
| `restaurant_hours.is_closed` | 삭제 | `open_time`/`close_time`이 NULL인지와 항상 같은 값. 휴무는 시간이 NULL인 행으로 유지 |
| `restaurants.opening_hours_raw` | 삭제 | 읽는 곳 없음. 원문은 CSV에 있고 `data/` CSV 12개의 영업시간 문자열은 파싱 실패 0건 |

- 연관 코드(`load_restaurants.py`, `load_notes.py`, `load_regions.py`, `extract_restaurant_info.py`, `extract_transcript_notes.py`, `extract_video_notes.py`, `fill_michelin.py`)와 README, CLAUDE.md 갱신. 결과 CSV는 23개에서 22개 컬럼
- **적용 전 백업**: `backup_tables.py`를 만들어 6개 테이블(`restaurants` 224, `regions` 165, `restaurant_hours` 1,803, `menus` 297, `michelin_status` 89, `michelin_records` 0)을 `data/backup_20261002_192433/`에 CSV로 저장
- **적용 결과 검증**: 삭제 대상 컬럼·테이블 0개 남음, 식당 224곳 모두 `region_1` 채워짐(`region_2` 143곳, `region_3` 19곳), 영업시간 1,803행·메뉴 297행 유지
- 커밋 `934f7be`를 `feature/pipeline`으로 push (마이그레이션 수정분과 `backup_tables.py`는 아직 미커밋)

**막히는 부분 & 해결 방법**
| 문제 | 해결/현재 상태 |
|---|---|
| 문서·스키마 주석이 "미슐랭 판정 기준은 `michelin_records`"라고 적었는데 실제 데이터는 `michelin_status`에만 있었음 | 기준을 `michelin_status`로 정정하고 `michelin_records` 삭제 |
| `pg_dump`가 이 PC에 없어 덤프 불가 | 설치 대신 `psycopg2`로 테이블별 CSV 백업 스크립트 작성 |
| 마이그레이션 첫 실행 오류 `recursive query "path" column 5 has type ...` | `regions.name`이 `VARCHAR(100)`이라 재귀 쿼리의 배열 타입이 맞지 않았음. `::text` 캐스트로 수정, 읽기 전용 쿼리로 먼저 검증한 뒤 재실행. 트랜잭션이라 첫 실패 때 DB는 그대로였음 |
| SQL Editor에서 검증 쿼리 결과가 마지막 것만 보임 | `UNION ALL`로 한 표에 모으는 검증 쿼리로 대체 |

**주의할 점**
- 삭제한 컬럼·테이블의 데이터는 DB에서 복구할 수 없음. 필요하면 위 백업 CSV와 `data/` 원본(CSV, 영상 분석 JSON)에서 되살림
- 기존 `data/` CSV에는 아직 `history` 컬럼이 있어 `extract_restaurant_info.py`로 이어받기를 하면 컬럼 불일치 오류가 남
- `--michelin-fallback`(Tavily) 경로는 `history`를 내부 계산에만 쓰고 저장하지 않는 상태로 남아 있음
- `compare_chunking.py`(청킹 비교 실험)는 untracked 상태로 둠
- 챗봇 브랜치에서 삭제된 컬럼·테이블(`regions`, `region_id`, `is_closed` 등)을 참조하는지 아직 확인하지 않음

**해결해야 할 일**
1. 미커밋 변경(마이그레이션 수정분, `backup_tables.py`, 문서) 커밋·push, `compare_chunking.py` 처리 결정
2. 미슐랭 135곳을 `fill_michelin.py`로 채우기(하루 한도 100회, 기본 90회씩) 후 `load_restaurants.py` 재실행
3. 나머지 169개 영상 Gemini 분석 → `load_notes.py` → `embed_chunks.py`. 전체 실행 전에 자막 방식 결과와 샘플 비교, 건수·예상 비용 공지(영상당 약 $0.027). `video_restaurant_notes`의 이전 실험 데이터 38건 정리 여부 결정
4. Tavily 폴백 경로를 계속 쓸지 결정, 안 쓰면 `history` 관련 코드 정리
5. 챗봇 브랜치에서 삭제 항목 참조 확인, 지역 질의는 `country_code`와 `region_1`을 함께 거는 방식으로 반영("중구"처럼 같은 이름이 여러 곳에 있음)
6. `feature/pipeline` PR → main, 챗봇 브랜치에서 main merge(README·CLAUDE.md·스키마 충돌 해결)
7. LangGraph 챗봇 구현(`src/app.py`는 현재 빈 그래프), n8n 자동화

---

## 10/5 (월)
> 이 절은 코드·문서 점검 세션만 기록합니다. 대부분 확인(읽기) 작업이고, 파일을 바꾼 것은 아래 "바꾼 것" 표뿐입니다. 이 세션에서는 커밋하지 않았습니다.

**오늘 한 일**
- **미슐랭(Parse API) 진행 상태 점검**: 구현은 끝났고, `restaurants_info.csv`에서 `is_michelin`이 비어 있는(미조회) 행이 138개(True 24, False 90). `fill_michelin.py`로 하루 90회씩 채우는 일이 남아 있음
- **`fill_michelin.py` 재개 방식 확인**: 식당 1곳마다 CSV를 임시 파일로 저장 후 교체하므로 중단돼도 원본은 안 깨지고, 재실행하면 `latest_grade`가 비었거나 note에 `미슐랭 미조회/조회 실패`가 남은 행부터 이어감. 같은 `google_cid`는 1회만 호출하고 결과를 복사
- **중국(CN) 식당 제외**: 현재 CSV에서 `country_code=CN`은 19행·15곳(주소가 있는 14행, Google 매칭 실패로 주소가 없는 5행). 홍콩(HK 22행)·마카오·대만은 유지하기로 하고 CN만 삭제. 판별은 주소 문자열이 아니라 `country_code`로 함(주소에 "China"가 들어간 이탈리아 식당 울리아씨가 오탐으로 잡혔음)
- **README 점검·갱신**: 프로젝트 구조에 빠져 있던 파일(`collect_video_urls.py`, `export_review_list.py`, `michelin_api_probe.py`, `load_regions.py`, `load_notes.py`, `embed_chunks.py`, `compare_chunking.py`, `backup_tables.py`, 마이그레이션 SQL, `restaurant_chunks.sql`, `parse_apis/`, `WORKLOG.md`)을 추가. 오래된 설명(지역 적재 스크립트 "없음", Tavily 연도별 이력, 옛 ALTER 안내)을 고침
- **임베딩 구조 확인**: `restaurant_chunks.embedding`은 같은 행 `content`를 `text-embedding-3-small`로 임베딩한 값으로, pgvector(`vector(1536)`, HNSW 코사인 인덱스)에 저장됨
  - OVERVIEW 청크: `video_restaurant_notes`(`concept`, `atmosphere`, `final_review`, `key_points`, `embedding_text`) + `restaurants`(이름, `category_broad/detail`)
  - MENU 청크: `menus`의 FOOD 행(`name`, `cooking_features`, `taste_review`, `tips`) + 식당명. 음료와 `price_text`, `evidence`, `first_appearance_sec`는 임베딩에 쓰지 않음
- **DB 현황 조회(읽기 전용)**: `restaurant_chunks` MENU 211·OVERVIEW 44. `menus` 297행 중 `cooking_features` 230, `taste_review` 204, `tips` 55행에만 값이 있음. 값이 없는 메뉴는 청크가 "식당명의 메뉴명" 10자 안팎으로 남음
- **청킹 비교 실행** (`compare_chunking.py`, 식당 46곳·질문 6개, A=식당당 통합 46청크 / B=개요+메뉴 290청크, 유사도는 코사인):

| 질문 유형 | 결과 |
|---|---|
| 특정 음식(랍스터, 캐비아) | B가 해당 메뉴가 있는 밍글스를 1위로 올림. 캐비아는 0.563 대 0.480 |
| 분위기·식당 유형(파인다이닝, 돼지고기 숯불구이) | A와 B가 상위 식당이 같고 점수 차이 0.01 이내 |
| 점수가 낮은 질문(회식 고깃집, 트러플 면) | B에서 무관한 메뉴 청크(김밥, Piatto di Lampredotto)가 상위에 올라옴 |

  표본이 작고 정답 채점이 없어 경향 수준. 46곳에 이전 실험 데이터가 섞여 있음
- **RAG 흐름·검색 노드 요건 정리**: 질문 임베딩 → `embedding`으로 유사 청크 검색 → `content`를 LLM에 넣어 답변 생성. SQL 선필터링과 벡터 검색은 같은 Postgres에서 한 쿼리로 가능. 필요한 것은 `POSTGRES_URI`, `OPENAI_API_KEY`, 청크 데이터(+해외 지명 질문은 `GOOGLE_PLACES_API_KEY`)

**바꾼 것**
| 파일 | 변경 |
|---|---|
| `data/restaurants_info.csv`, `.xlsx` | CN 19행 삭제 (252→233행). 원본은 `data/restaurants_info_before_cn_drop.csv`에 백업 |
| `src/pipeline/extract_restaurant_info.py` | `EXCLUDED_COUNTRY="CN"` 추가. Places 호출 전에 건너뛰고, 영상의 가게가 전부 제외되면 안내 행의 `note`에 사유를 남김. 문법 검사만 했고 실제 실행은 안 함 |
| `README.md`, `CLAUDE.md` | 위 내용 반영 |

**막히는 부분 & 해결 방법**
| 문제 | 해결/현재 상태 |
|---|---|
| `compare_chunking.py`가 120초 안에 끝나지 않아 백그라운드로 넘어갔고, `\| tail`에 연결해서 끝날 때까지 출력이 안 보였음 | 완료 후 출력 파일에서 결과 확인. 다음엔 `tail` 없이 실행 |
| `load_dotenv()`를 `python -`(stdin)으로 실행하면 `find_dotenv` 오류 | 스크립트 파일로 저장해 `load_dotenv('.env')`로 실행 |
| Bash에서 `python`이 uv 환경이 아닌 다른 인터프리터로 잡혀 편집이 적용되지 않았음 | `uv run python`으로 다시 실행 |

**주의할 점**
- CN 판별 기준이 LLM이 추정한 `country_code`라서, 중국 식당을 다른 국가로 표기했거나 `country_code`가 빈 행(11행, 주소도 없음)은 걸러지지 않음
- CSV에서만 지웠으므로 **이미 DB에 적재된 중국 식당은 남아 있을 수 있음**. `load_restaurants.py`는 삭제를 하지 않음. DB 쪽은 아직 확인하지 않음
- `fill_michelin.py`는 크레딧 소진 오류 문구가 `_CAP_HINTS`(`Daily request cap`, `호출 한도`)와 다르면 개별 실패로만 처리하고, 연속 실패 자동 중단 장치가 없음
- README의 마이그레이션 적용 순서와 `uv run parse init` 로그인 필요 설명은 파일 이름·일반 가정에서 추정한 것이라 확인이 필요함

**해결해야 할 일**
1. 변경분 커밋 (코드, 문서, 일지. `data/`는 git 제외)
2. DB에서 중국 식당 적재 여부 확인 후 삭제
3. `fill_michelin.py`로 미슐랭 미조회 약 138행 채우기(하루 90회씩) → `load_restaurants.py` 재실행. 연속 실패 자동 중단 장치 추가 여부 결정
4. `migrate_simplify_schema.sql`이 DB에 적용됐는지 확인(10/2 일지 기록상 적용·검증됨, 현재 상태는 미확인)
5. 빈 메뉴 청크(내용 없는 MENU)를 제외할지 결정하고 필요하면 재비교. 청킹 B를 고른 근거는 일지에 없음
6. 나머지 영상 Gemini 분석 → `load_notes.py` → `embed_chunks.py`, 이후 LangGraph 검색 노드 구현
7. 위 "전체 미해결 목록"의 나머지 항목

---

## 10/6 (화)
**오늘 한 일**
- **추출 방식 비교** (`compare_extraction_methods.py`): 영상(Gemini) / 자막(gpt-4o-mini) / 하이브리드(영상+자막)를 5개 영상·10곳에서 비교. 자막 v1은 분류가 틀리고(10곳 중 6곳이 "일식") 분위기·태그가 비었음. 자막 프롬프트를 고친 v2와 하이브리드 v2에서 분류 오류 0곳, 분위기·태그 채움. 하이브리드가 가장 고르게 채워졌고 메뉴 수는 세 방식이 비슷했음(영상 55, 자막 57, 하이브리드 60). 정답 대조는 안 함
- **자막 수집**: Playwright 방식(`get_transcripts.py`)이 YouTube 자막 패널 구조 변경(`transcript-segment-view-model`)과 영어 패널 문제로 동작하지 않아 yt-dlp 방식(`get_transcripts_since.py`)으로 교체하고 기존 파일은 삭제. 2025-01-01 이후 영상 중 가게와 `google_cid`가 있는 121개 수집(제외 14개는 `data/urls/transcript_excluded_20250101.txt`). 영상마다 `.txt`(시간 없음)와 `.json`(구간별 시각) 저장, 출처(제작자/자동 생성)는 `_sources.csv`. 제작자 한국어 자막 66개, 자동 생성 55개. 처음에 "기존 53개가 모두 제작자 자막"이라고 한 것은 틀린 판단이었고 자동 생성이 24개 섞여 있었음(문서 정정)
- **자막 추출 v2 → DB 적재** (`extract_script_notes.py`): 165곳(159개 식당)을 약 $0.31에 추출. 비정형 4개 테이블을 백업(`data/backup_20261006_151428/`) 후 비우고 `load_notes.py`, `embed_chunks.py`로 적재. 메뉴별 자막 시각을 `menus.mentioned_sec`(`migrate_mentioned_sec.sql`)에 저장. 영상 분석 결과는 JSON으로 남아 있어 복원 가능
- **메뉴·청크 정리**: 청크 종류를 `MENU` → `FOOD`로 바꾸고 `DRINK` 추가(`migrate_chunk_types.sql`), 설명 없는 음식 청크 제외(117개), 음식으로 잘못 들어간 음료 5개를 DRINK로 수정(`fix_misclassified_drinks.sql`), FOOD/DRINK 청크 content에서 식당 이름 제거(786개 재임베딩)
- **셰프 정보**: `restaurants.chef_name`(여러 명은 쉼표 연결)·`chef_info` 추가(`migrate_chef_columns.sql`), 96곳 추출(`extract_chef_info.py`, 약 $0.13). 모델 결과의 약 30%가 틀려(직함, 채널 운영자, 식당 이름, 같은 영상의 다른 가게 셰프) 확신도를 코드로 다시 계산하고 `load_chefs.py`에서 규칙으로 거른 뒤 27곳 적재. 규칙으로 걸러지지 않은 3건은 `MANUAL_EXCLUDE`에 사유와 함께 남김. OVERVIEW 청크에도 반영(28개 재임베딩)
- **추출 품질 진단**: 적재된 v2는 맛 표현의 40%가 자막 문장 그대로, 팁을 지어냄(비움 영상 포함), 자동 자막 영상은 메뉴가 평균 3.5개(제작자 자막 6.1개)로 누락이 많음. 자막만으로 시각이 "음식이 화면에 보이는 시점"이 아니라 "말한 시점"이라는 한계도 확인(영상 분석 `first_appearance`와 11개 중 10개가 30초 이내)
- **추출 품질 개선 실험** (DB에는 적용 안 함, 결과는 `data/video_notes/gpt-4o-mini-script-v3~v7`과 `data/compare/prompt_v3_*.md`)

| 버전 | 내용 | 결과 |
|---|---|---|
| v3 | 구어체 정리·메뉴 누락 방지 규칙 | 구어체는 정리되나 팁을 지어내고 음료가 사라짐 |
| v3w/v4w | 6분 구간 분할 추출 추가(v4는 팁·음료·범주 이름 규칙 보완) | 누락은 크게 줄지만 같은 요리가 한글/외국어 이름으로 중복 |
| v5 | LLM 중복 제거 + 범주 이름·같은 시각 합침 규칙 | 자동 자막 영상에서 효과가 큼. 제작자 자막으로 잘 나오는 영상(비움)은 반찬 같은 설명 없는 항목만 늘어남 |
| v6(2단계) | 요리·먹는 장면 줄 번호 찾기 → 요리별 발췌만 보고 정리 → 가게 정보 | 시각·"메뉴판만 읽음" 구분은 좋아지나 설명이 많이 비고 같이 나온 요리의 평가가 섞임. 영상당 $0.02~0.035. 시험 중 예시를 시험 영상 자막에서 가져와 결과가 오염돼 폐기하고 무관한 예시로 다시 시험 |
| v7 | v5 + 구어체 정리 + 의미 없는 평가 제거(정리 패스) + 자막 근거 확인 + 이름 병기 | 아래 "진행 상황" 참고 |

- **v7 진행 상황** (요청: ① 구어체·감탄사 정리, ② "맛있다"류 무의미한 평가는 비움, ③ 메뉴 이름에 자막의 이름과 한국어 풀이 병기): ①은 5개 영상에서 의심 문장 1건만 남음. ②는 "좋은 술이다", "죽인다" 같은 평가형을 규칙으로 걷어내고 이름 되풀이 조리 문장("푸아그라를 제공")도 비움. 미라주르(제작자 자막)는 요청대로 나옴. ③은 모델이 지시를 따르지 않아 **요리가 나오는 장면의 자막 줄에서 한글 표기를 고르고 코드가 그 줄에 실제로 있는지 확인하는 방식**으로 바꿔 `푸아그라 드 카나드(오리 푸아그라)`, `테트 드 보(송아지 머릿고기)`까지 확인. 자동 자막 영상은 자막 표기의 오타가 이름으로 들어가(`랍스타(랍스터)`) 병기를 제작자 자막에만 적용하도록 마지막에 수정했으나 **그 수정 후 검증 실행은 하지 못함**
- **첫 버전(v2) 재추출 시험**: 5개 영상(`6vYMBhJOneU`, `gYR7g7aKFag`, `3vYwR8V8IaU`, `XWG0rcLvFck`, `_1GWHHzAE0E`)을 같은 v2 프롬프트로 다시 뽑아(`--tag gpt-4o-mini-script-v2-rerun`) 일관성을 확인. 제작자 자막 3개는 메뉴 이름이 거의 같고, 자동 자막 2개는 실행마다 크게 달라짐(온도 0.1)
- **문서**: README, CLAUDE.md를 위 결과에 맞춰 갱신

**바꾼 것**
| 파일 | 변경 |
|---|---|
| `src/pipeline/get_transcripts_since.py` (신규) | yt-dlp 자막 수집, 날짜 필터(이분 탐색), 적재 가능 영상만, `.txt`+`.json`, 이어받기. 기존 `get_transcripts.py`는 삭제 |
| `src/pipeline/compare_extraction_methods.py` (신규) | 영상/자막/하이브리드 비교, 프롬프트 v2 변형 |
| `src/pipeline/extract_script_notes.py` (신규) | 시각 자막 추출(v2, DB 적재본), `--tag` 옵션 |
| `src/pipeline/extract_chef_info.py`, `src/db/load_chefs.py` (신규) | 셰프 추출·규칙 필터·적재 |
| `src/pipeline/compare_prompt_v3.py`, `extract_script_two_stage.py`, `extract_script_v7.py` (신규) | 프롬프트 개선 실험(v3~v7) |
| `src/db/load_notes.py`, `embed_chunks.py`, `compare_chunking.py` | `mentioned_sec` 적재, FOOD/DRINK 청크, 설명 없는 청크 제외, 셰프를 OVERVIEW에 포함, 메뉴 청크에서 식당 이름 제거 |
| `src/db/restaurant_schema.sql`, `restaurant_chunks.sql` | `mentioned_sec`, `chef_name`, `chef_info`, 청크 종류 변경 반영 |
| `src/db/*.sql` (신규) | `migrate_mentioned_sec`, `reset_notes_tables`, `migrate_chunk_types`, `fix_misclassified_drinks`, `migrate_chef_columns` |
| `README.md`, `CLAUDE.md` | 위 내용 반영 |

**DB 현황 (10/6 기준)**
- 정형: `videos` 185, `restaurants` 224(`chef_name` 27, `chef_info` 21), `restaurant_hours` 1,803, `michelin_status` 89, 영상-식당 연결 235
- 비정형(자막 v2): `video_restaurant_notes` 165, `menus` 904(음식 793·음료 111, 음식의 `mentioned_sec` 790), `restaurant_tags` 798, `restaurant_chunks` 951(OVERVIEW 165·FOOD 677·DRINK 109). 분류된 식당 159
- **v3~v7 결과는 파일로만 있고 DB에는 없음**

**막히는 부분 & 해결 방법**
| 문제 | 해결/현재 상태 |
|---|---|
| Playwright 자막 수집 실패 | yt-dlp로 교체 |
| 모델의 확신도를 믿을 수 없음(셰프, 검수) | 코드로 재계산, 규칙 필터 |
| 직접 실행하신 SQL이 반영되지 않음(원인 불명) | 제가 같은 SQL을 대신 실행해 해결. 실행 시 `COMMIT`까지 됐는지 확인 필요 |
| 자동 자막 영상은 메뉴 누락·오타·이름 깨짐 | **미해결**(자막만으로는 한계, 하이브리드 검토 필요) |
| 같이 나온 요리의 평가가 섞임(2단계 추출에서도 남음) | **미해결** |
| 구간 분할로 메뉴가 늘면서 같은 요리가 중복(한글/외국어 이름) | v5의 LLM 중복 제거와 규칙으로 완화 |
| 근거 확인 패스가 정상 정보까지 지움(검수 모델 기준이 "애매하면 근거 없음") | 기준을 "명백히 없는 것만"으로 완화. 이름-설명 불일치 판정은 신뢰 불가라 기록만 남김 |
| 모델이 이름 병기 지시를 따르지 않음 | 장면 줄 기반 방식으로 일부 해결, 자동 자막에는 적용 안 함 |
| 영어 "에스카르고"로 검색하면 달팽이 청크가 750위 | **미해결**(청크에 별칭이 없음) |
| 셰프 경력 질문의 검색 순위가 낮음(OVERVIEW가 길어 희석: 팔선 12위, 베수비오 20위) | **미해결**(CHEF 청크 또는 키워드 검색 병행) |

**제 판단이 틀렸던 부분 (재발 방지)**
- "처음 버전"을 영상 분석으로 오해해 진행하려다 중단됨(자막 v2가 맞았음). 모호한 지시는 먼저 확인
- 2단계 추출 시험에서 프롬프트 예시를 시험 영상 자막에서 가져와 결과가 오염됨. 예시는 시험 데이터와 무관한 문장으로 쓸 것
- 한 영상에 맞춰 프롬프트를 반복 수정하면 과적합 위험이 커서 최소 3개 영상으로 확인할 것

**주의할 점**
- v7 마지막 수정(자막 줄 검색 창을 3초 → 10초 전으로 확대, 병기를 제작자 자막에만 적용)은 적용했지만 **검증 실행을 못 해서 결과가 확인되지 않음**
- 모든 개선 실험은 3~5개 영상 기준이고 **정답 대조는 하지 않음**. 개수가 늘었다고 정확한 것은 아님(구간 분할로 늘어난 메뉴 중 일부는 설명 없는 반찬 같은 항목)
- 메뉴 시각 `mentioned_sec`는 "말한 시점"의 추정치이고 화면 등장 시각이 아님. 챗봇에서는 "영상 N분 근처에서 언급"으로 안내해야 함
- 셰프 `chef_info`의 경력·수상은 유튜버가 영상에서 한 말이라 검증된 사실이 아님. 식당 이름에 들어 있는 셰프 이름(알랭 뒤카스)과 다중 가게 영상의 셰프(우동카덴 정호영)는 규칙상 빠짐
- DB의 `CN`(중국 본토) 식당 14곳이 아직 남아 있음(10/5 일지의 확인 항목을 오늘 확인함, 삭제는 안 함)
- 외부 API 비용은 오늘 합쳐 약 $1.5 안팎으로 추정(개별 실행의 출력값을 합산한 대략값)
- **오늘 변경분(코드·SQL·문서)은 모두 커밋하지 않음**

**해결해야 할 일**
1. v7 최종 수정 검증: 5개 영상으로 다시 추출해 이름 병기(`에스카르고(달팽이)`)와 설명 정리를 확인(약 $0.06)
2. 추출 방식 결정: 제작자 자막 83곳은 자막(v7), 자동 자막 82곳은 하이브리드(영상+자막)를 검토. 3개 영상으로 하이브리드를 v7과 비교(약 $0.1)
3. 메뉴 시각의 정확도 검증: 영상 링크 5개를 직접 확인해 정답 기준 만들기
4. 전체 적용 시 건수·예상 비용 안내 → 비정형 테이블 비우기(`reset_notes_tables.sql`, 직접 실행) → `load_notes.py` → `embed_chunks.py` → 셰프 재계산
5. 별칭 검색 문제(에스카르고/달팽이)와 셰프 경력 검색(CHEF 청크 또는 `chef_info ILIKE` 병행)
6. DB의 중국 본토 식당 14곳 삭제, 가게 추출이 누락된 영상 확인(`G4gG5SqhfE8`와 CSV에 행이 없는 9개)
7. 변경분 커밋, LangGraph 챗봇 구현(질문 분석 → 지역 변환 → SQL 필터 + 벡터 검색), 미슐랭 135곳 채우기, n8n

---

## 10/7 (수)
**오늘 한 일**
- **A/B 분리 설계**: 한 번에 가게·메뉴·셰프 정보를 모두 뽑는 부담이 누락·부정확의 원인인지 따져 보고, 필드를 나누는 방식(A 가게 정보 / B 메뉴·음료)을 택했습니다. 앞서 해 본 2단계 추출은 요리별 발췌만 보여 주느라 소개 단계의 설명이 빠졌으므로, 분리해도 입력은 자막 전체(가게별 구간)를 줍니다. 비용은 입력이 두 배라 약 1.3~1.9배로 추정했고 실제로는 1.6배였습니다
- **`extract_script_split.py` (신규)**: 코스 판단(`detect_course`) → A·B·셰프(`chefs_for_store`) 호출. B는 코스면 요리마다 `course_stage`(아뮤즈부쉬~프티푸르)를 적고, 셰프는 대표(owner·head)만 남깁니다. 메뉴 설명은 `cooking_features` 한 칸으로 합쳤습니다(`taste_review`·`tips`·`price` 제거). 프롬프트 캐시를 위해 자막을 맨 앞에 두고, 호출별 토큰·시간·비용을 `usage`에 남깁니다
- **3개 영상 시험(2회)**: 1회차에서 가게가 여러 곳인 영상(`z5sRczuZcQ8`)의 요리 섞임·시각 전부 탈락, 범주 이름(`디저트`) 메뉴, 코스 단계 누락을 찾았고, 2회차에서 가게별 구간 입력·코스 선판단·범주 이름 제거로 고쳤습니다
- **34개 영상 실행**: 2026년 업로드·수동 한국어 자막 영상 34개(49곳, 4곳은 시험에서 처리, 신규 45곳, 약 $0.15). 첫 실행은 `-y3kWfSZlyg`처럼 `-`로 시작하는 영상 ID가 인자로 파싱돼 실패해서(처리 0건) `--video-id=ID` 형태로 다시 실행
- **음식 분류 재호출** (`reclassify_split_cuisine.py`): A 호출이 한국 식당 9곳과 스페인 타파스 바 3곳을 중식으로 분류. `classify_cuisine`을 별도 호출해 51곳 중 15곳을 고침(이전 값은 `cuisine_before`, 약 $0.01). 중식 19곳 중 7곳만 실제 중식이었는데 처음에는 8곳·11곳으로 잘못 세어 알렸다가 정정
- **후처리** (`postprocess_split.py`): 평가어·가격 문장(규칙 38건 + LLM 재작성 57건, 약 $0.007), 코스 단계 보정(치즈 2, 생선 14, 순서 맞춤 10), 음료가 메뉴로 들어간 1건, 식당 이름이 셰프로 들어간 2건. 원본은 `*_raw`에 백업하고 항상 원본에서 읽습니다
- **문서**: README, CLAUDE.md, WORKLOG 갱신

**결과 (49곳, 기존 v2 대비)**
| 지표 | 기존 v2 | A/B 분리 |
|---|---|---|
| 메뉴 수(평균) | 5.5 | 7.4 |
| 음료 수(평균) | 0.8 | 1.4 |
| 설명 길이(평균) | 23자 | 48자 |
| 키포인트(평균) | 2.5 | 3.2 |
| 가게당 비용 | $0.0019 | $0.0031 |
| 가게당 시간 | 8.4초 | 16.4초 |

**바꾼 것**
| 파일 | 변경 |
|---|---|
| `src/pipeline/extract_script_split.py` (신규) | A/B 분리 추출, 코스 단계, 대표 셰프, 비용·시간 기록, 기존 v2와 비교 출력 |
| `src/pipeline/reclassify_split_cuisine.py` (신규) | 음식 분류만 별도 호출로 다시 정함 |
| `src/pipeline/postprocess_split.py` (신규) | 평가어·코스 단계·음료 중복·셰프 후처리 |
| `README.md`, `CLAUDE.md`, `WORKLOG.md` | 위 내용 반영 |

**막히는 부분 & 해결 방법**
| 문제 | 해결/현재 상태 |
|---|---|
| 영상 전체 자막을 주면 가게가 여러 곳인 영상에서 요리가 섞임 | 가게별 구간(`video_parts`)만 입력으로 줘서 해결 |
| 범주 이름(`디저트`)이 메뉴로 들어감 | 설명 없는 범주 이름 메뉴를 코드로 제거 |
| A 호출의 음식 분류 오류(한국 식당·타파스 바가 중식) | 분류만 별도 호출로 재분류 |
| 코스 단계가 대부분 `메인`으로 몰림 | 규칙으로 보정했으나 **일부만 해결**(참고용으로만 쓸 것, 필터 금지) |
| 평가어가 구체적인 내용과 붙어 있으면 못 거름 | 규칙 + LLM 재작성으로 99→50(넓은 기준), 핵심 평가어 8개 **미해결** |
| 영상 ID가 `-`로 시작하면 인자 오류 | `--video-id=ID` 형태 사용 |

**주의할 점**
- **DB에는 적용하지 않음**: 기존 DB의 자막 방식 결과(165곳)와 겹치는 가게를 어떻게 다룰지 아직 정하지 않았음
- 정답 기준이 없어 환각·누락은 검증하지 못했고, 34개 영상은 모두 수동 자막이라 자동 자막(55개)에서도 같은 효과가 나는지는 모름
- 기존 v2 결과의 가게 간 섞임(`z5sRczuZcQ8`) 때문에 그 4곳은 비교 기준으로 믿기 어려움
- 설명이 없는 메뉴 66개(18%)가 실제로 먹은 요리인지 미확인. 프릳츠 장충점과 밍글스에 같은 셰프(강민구)가 붙은 것도 미확인
- 식당 이름이 셰프로 들어간 2건은 뺐지만 `load_chefs.py`의 규칙은 적재 때 따로 적용해야 함

**해결해야 할 일**
1. 남은 평가어 8건과 강민구 셰프 건 확인, 후처리 규칙 보완
2. 코스 단계를 어디에 쓸지 결정(표시용인지 검색 조건인지)
3. 설명 없는 메뉴 확인
4. DB 적재 방침 결정(`load_notes.py --model gpt-4o-mini-script-split-v2`, `embed_chunks.py`, `load_chefs.py` 규칙)
5. 자동 자막 영상(55개)에 시험하고, 전체 121개로 넓히기 전에 건수·비용 안내

---

## 전체 미해결 목록 (10/1 기준, 최신은 위 10/2·10/5·10/6·10/7 절 참고)

**우선순위 높음**
1. 3번 전체 실행(약 313개 영상): 호출 건수·비용 사전 안내, 소수 샘플 검증, 이어받기 지원. Parse 크레딧 한도 확인
2. 소개란에 가게 정보가 없는 영상 보완 (Gemini로 가게 이름·주소 추출)
3. PostgreSQL 연결 확인 → `load_restaurants.py` → `load_notes.py` → `embed_chunks.py` 순으로 실제 적재, `load_regions.py` 작성
4. Gemini 영상 분석 전체 확대 (429 대응, 비용 절감 방안 검토)
5. LangGraph 챗봇: 좌표 조회 / SQL(반경·지역·영업 여부·미슐랭·메뉴) / RAG / Tavily 도구로 구성, SQL로 조건 좁히기 → 벡터 검색 순서

**우선순위 중간**
6. 한국어 상호 등재 식당으로 Michelin API 매칭 검증, `guide_year` 의미 확인
7. 미슐랭 후보의 Places 매칭을 사람이 확인하고, 이전 방식 결과를 재추출
8. 자막 관련 스크립트 삭제 여부 결정 (`TranscriptNotes`를 먼저 옮겨야 함)

**낮음**
9. n8n 자동화
10. 기존 DB가 있다면 `summary_badge` 컬럼과 `michelin_sources` 테이블 제거

---

## 참고: 10/1 중 세션 이전에 이미 있던 파일
`collect_video_urls.py`, `michelin_api_probe.py`는 오늘 대화 시작 시점에 이미 작성돼 있던 파일입니다(이번 정리 범위 밖).
