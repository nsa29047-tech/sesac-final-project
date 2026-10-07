# sesac-final-project

미식 유튜브 영상의 소개란과 영상 본편(Gemini 분석)에서 식당 정보를 추출하고, 이를 기반으로 맛집을 추천해 주는 **RAG 챗봇** 프로젝트입니다.
국내뿐 아니라 해외 식당도 대상으로 하며, 영상 약 200개 규모의 데이터를 수집·정제합니다.

## 아키텍처

정형 데이터와 비정형 데이터를 나눠 저장하는 **하이브리드 구조**입니다.

| 구분 | 데이터 | 저장소 | 조회 방식 |
|---|---|---|---|
| 정형 | 영업시간, 위치, 전화번호, 평점, 가격대, 미슐랭 등급 | PostgreSQL | SQL search |
| 비정형 | 분위기, 음식 스타일, 메뉴, 영상 속 특징 | Vector DB | RAG search |

챗봇은 LangGraph로 구성하며, 먼저 SQL로 조건(지역, 영업 여부, 미슐랭 등)을 좁힌 뒤 벡터 검색으로 취향에 맞는 식당을 찾는 흐름을 목표로 합니다.

## 기술 스택

- Python 3.14, [uv](https://docs.astral.sh/uv/)
- LangGraph / LangChain, LangSmith (트레이싱)
- OpenAI API: `gpt-4o-mini`(소개란에서 식당명·주소 추출, Places 후보 선택), `text-embedding-3-small`(RAG 청크 임베딩)
- Google Places API (New): 공식 상호명, 주소, 평점, 영업시간 등
- Parse SDK (guide.michelin.com API): 미슐랭 현재 등급 판별 / Tavily Search: 폴백(`--michelin-fallback`) 전용
- Gemini API (Google AI Studio): 영상(시각·음성)을 직접 분석해 메뉴·분위기·태그 추출
- yt-dlp: 영상 메타데이터와 자막(스크립트) 수집 (Playwright, youtube-transcript-api는 쓰지 않음)
- PostgreSQL (+ pgvector 선택)

## 프로젝트 구조

```
sesac-final-project/
├── src/
│   ├── app.py                          # LangGraph 앱 (graph 엔트리포인트, 현재 빈 그래프)
│   ├── pipeline/                       # 데이터 수집 파이프라인
│   │   ├── collect_video_urls.py       # 1. 채널 영상 URL 수집 (yt-dlp, 이분 탐색)
│   │   ├── channel_video_filter.py     # 2. 영상 소개란 기준 필터링
│   │   ├── extract_restaurant_info.py  # 3. 식당 정보 추출 + Places/미슐랭 조회, 중국(CN) 제외
│   │   ├── fill_michelin.py            # 3-b. 미슐랭 미조회 식당을 하루 한도만큼씩 채움
│   │   ├── export_review_list.py       # 3-c. 사람이 확인할 식당(미슐랭 후보·매칭 실패·숙소)을 xlsx로 추출
│   │   ├── michelin_api_probe.py       # Parse(Michelin) API 호출 확인용
│   │   ├── enrich_regions.py           # 4. 지역(시 > 구/군 > 동) 보강
│   │   ├── extract_video_notes.py      # 5. Gemini 영상 분석 (메뉴·분위기·태그)
│   │   ├── extract_script_notes.py     # 5'. 자막 기반 추출 (시각 자막 입력, 메뉴별 mentioned_at 포함, gpt-4o-mini)
│   │   ├── compare_prompt_v3.py        # 5'''. 자막 추출 프롬프트 실험(v2/v3/v4, 구간 분할, v5=구간 분할+LLM 중복 제거) -> data/compare/prompt_v3_*.md
│   │   ├── extract_chef_info.py        # 5''. 자막에서 가게별 셰프(이름·역할·경력·근거) 추출, 확신도는 코드로 계산 -> data/chef_notes/
│   │   ├── extract_script_split.py     # 5''''. A/B 분리 추출(A 가게 정보 / B 메뉴·음료·코스 단계 + 코스 판단·셰프 호출), 건별 저장 -> data/video_notes/gpt-4o-mini-script-split-v2/
│   │   ├── reclassify_split_cuisine.py # 5-b. A/B 분리 결과의 음식 분류(대분류·소분류·태그)만 별도 호출로 다시 정함
│   │   ├── postprocess_split.py        # 5-c. A/B 분리 결과 후처리(평가어·가격 문장, 코스 단계 보정, 음료 중복, 식당 이름 셰프 제거), 원본은 *_raw 폴더에 백업
│   │   ├── get_transcripts_since.py    # 자막 수집 (yt-dlp, 2025-01-01 이후·적재 가능 영상만, 이어받기)
│   │   ├── compare_extraction_methods.py # 추출 방식 비교 실험: video / 자막 / 하이브리드 (data/compare/report.md)
│   │   ├── save_scripts.py             # (미사용) 자막 수집 - youtube-transcript-api
│   │   └── extract_transcript_notes.py # (미사용) 자막 기반 추출. TranscriptNotes 스키마는 5번이 import
│   └── db/
│       ├── restaurant_schema.sql       # PostgreSQL 테이블 정의
│       ├── restaurant_chunks.sql       # RAG 청크 테이블 (pgvector)
│       ├── migrate_notes.sql           # 마이그레이션: 메뉴·태그·노트 테이블 추가
│       ├── migrate_regions.sql         # 마이그레이션: 지역 (이후 migrate_simplify_schema.sql로 컬럼화)
│       ├── migrate_trim_columns.sql    # 마이그레이션: 안 쓰는 컬럼 정리
│       ├── migrate_simplify_schema.sql # 마이그레이션: regions 컬럼화, 불필요 테이블·컬럼 삭제
│       ├── migrate_mentioned_sec.sql   # 마이그레이션: menus.mentioned_sec(자막 추정 시각) 추가
│       ├── reset_notes_tables.sql      # 비정형 4개 테이블 비우기(방식 비교용, 백업 후 직접 실행)
│       ├── migrate_chunk_types.sql     # 마이그레이션: 청크 종류 MENU -> FOOD, DRINK 추가, 설명 없는 음식 청크 삭제
│       ├── fix_misclassified_drinks.sql # 일회성 보정: 음식으로 잘못 들어간 음료 5개를 DRINK로 (menus, 청크)
│       ├── migrate_chef_columns.sql    # 마이그레이션: restaurants.chef_name / chef_info 추가
│       ├── load_chefs.py               # 셰프 추출 결과 -> restaurants.chef_name / chef_info (규칙으로 한 번 더 거름)
│       ├── backup_tables.py            # 마이그레이션 전 테이블 CSV 백업 (DB는 읽기만)
│       ├── load_restaurants.py         # 6-1. xlsx -> PostgreSQL (식당·영상·영업시간·미슐랭)
│       ├── load_regions.py             # 6-2. restaurants.region_1~3 적재
│       ├── load_notes.py               # 6-3. 영상 분석 결과 -> 카테고리·태그·메뉴·노트
│       ├── embed_chunks.py             # 6-4. 청크 생성 + 임베딩 -> restaurant_chunks
│       └── compare_chunking.py         # 청킹 방식 비교 실험 (DB에 쓰지 않음)
├── parse_apis/                         # Parse SDK 스캐폴드. 생성 코드는 git 제외(`uv run parse add`로 재생성)
├── data/                               # 산출물 (git 제외)
│   ├── urls/                           # 채널 영상 URL 목록 (원본 / 통과 / 제외)
│   ├── restaurants_info.csv, .xlsx     # 3번 결과 (22개 컬럼, 건별 저장). *_before_*, test*, pilot* 등은 백업·실험본
│   ├── restaurant_regions.csv          # 4번 결과
│   ├── video_notes/{model}/            # 5번 결과 (영상×가게별 JSON)
│   ├── transcripts/, transcript_notes/ # (미사용) 자막 방식 산출물. Gemini 결과 비교용
│   ├── experiments/                    # 실험 결과 JSON
│   └── backup_YYYYMMDD_HHMMSS/         # backup_tables.py 백업
├── notebooks/
│   └── crawling.ipynb                  # 채널 영상 URL 추출 등 실험 노트북
├── WORKLOG.md                          # 작업 일지
├── CLAUDE.md                           # Claude Code용 작업 규칙·진행 상황
├── langgraph.json                      # LangGraph 설정 (graph: ./src/app.py:graph)
├── pyproject.toml
└── .env                                # API 키 (git 제외)
```

## 데이터 파이프라인

```
영상 URL 수집 ─▶ 필터링 ─▶ 식당 정보 추출 ─▶ 미슐랭 채우기 ─▶ 지역 보강 ─▶ 영상 분석(Gemini) ─▶ DB 적재
 (collect_*)     (filter)   (extract_restaurant)  (fill_michelin)  (enrich_regions) (extract_video_notes)  (load_*)
```

1. **영상 URL 수집** (`collect_video_urls.py`): yt-dlp로 채널 영상 목록을 가져와, 이분 탐색으로 2023-01-01 이후 영상만 골라 `channel_video_urls.txt`에 저장합니다. `--since`, `--channel` 옵션으로 기준일과 채널을 바꿀 수 있습니다. (기존 `notebooks/crawling.ipynb` 첫 셀을 스크립트화)
2. **필터링** (`channel_video_filter.py`): 소개란에 '가게' 또는 '장소'가 언급된 영상만 남기고 `filtered_channel_video_urls.txt` / `excluded_channel_video_urls.txt`로 나눕니다. 중국 소재 식당 제외는 3번에서 합니다.
3. **식당 정보 추출** (`extract_restaurant_info.py`):
   - yt-dlp로 소개란을 가져와 OpenAI로 식당 한국어 상호명·주소·국가코드를 추출
   - 중국 본토(`country_code`가 `CN`)는 제외합니다(Places 호출 전에 건너뜀). 홍콩·마카오·대만은 유지합니다. 판별 기준이 LLM이 추정한 국가코드라 중국 식당을 다른 국가로 표기하면 걸러지지 않습니다(`EXCLUDED_COUNTRY`)
   - Google Places API로 공식 상호명, 주소, 좌표, 평점, 영업 상태, 연락처, 영업시간을 조회. 음식 분류(대분류/세부/태그)와 가격 정보는 CSV에 저장하지 않고 Gemini 영상 분석 결과를 씁니다. 숙소(호텔·료칸 등)로 매칭된 장소는 영상 제목으로 판단합니다: 제목에 식당·식사·요리 관련 단어(레스토랑, 식당, 식사, 요리, 밥, 디너, 맛집)가 있으면 가져오고(`note`에 '숙소로 매칭됨' 표시), 없으면 호텔 후기·료칸 소개가 주 소재라고 보고 제외합니다. 같은 영상에서 300m 이내에 다른 식당 행이 있는 숙소 행도 식당 정보에 딸려 적힌 것으로 보고 뺍니다
   - Parse(guide.michelin.com) API로 미슐랭 현재 등급을 판별: 검색 결과 중 이름과 도시(또는 거리 주소)가 일치하는 식당만 채택하고, 일치하는 식당이 없으면 `NONE`. API는 현재 등급만 주므로 연도별 이력은 저장하지 않습니다. 무료 플랜의 분당 5회 제한 때문에 호출 사이에 약 12.5초 간격을 둡니다. `--michelin-fallback`을 주면 일치 식당이 없을 때 기존 Tavily 검색으로 한 번 더 확인합니다(호출 증가).
   - Parse API는 **하루 100회 제한**이 있어서(식당 약 240곳이면 3일), 전체 실행은 `--skip-michelin`으로 미슐랭을 건너뛰고(`note`에 '미슐랭 미조회' 표시) 나중에 `fill_michelin.py`로 하루 한도(기본 90회)만큼씩 채웁니다. 같은 식당이 여러 영상에 나오면 한 번만 조회하고, 한도에 도달하면 멈췄다가 다음 날 이어서 처리합니다. `google_cid`가 없는 행은 건너뜁니다. 미슐랭을 못 채운 행은 `load_restaurants.py`가 `michelin_status`를 만들지 않습니다.
   - 건마다 CSV에 즉시 기록해 중간에 중단되어도 결과가 보존되며, 완료 후 xlsx로 변환
   - `export_review_list.py`: 사람이 확인할 행(미슐랭 후보, Google 매칭 실패, 숙소로 매칭된 행)만 뽑아 xlsx로 저장합니다
4. **지역 보강** (`enrich_regions.py`): 3번 CSV의 `google_place_id`로 Places Details의 `addressComponents`에서 시 > 구/군 > 동·면(서울특별시 > 마포구 > 연남동, 성남시 > 분당구 > 정자동)을 뽑아 `data/restaurant_regions.csv`에 건별로 저장합니다. 없는 단계는 건너뛰고 있는 데까지만 저장하며(안동시는 시만, 도로명 주소라 동이 없으면 구까지), 도로명은 쓰지 않습니다. 식당당 Details 1회이고, 재실행하면 이어서 처리합니다. `--limit`으로 샘플을 먼저 검증하세요. "OO역/OO 근처" 질문은 지역 컬럼이 아니라 식당의 위경도로 반경 검색합니다(질문 시점에 지명의 좌표를 조회). DB 적재는 6번의 `load_regions.py`가 합니다.
5. **영상 분석** (`extract_video_notes.py`): 3번의 CSV를 가게 목록으로 삼아, Gemini API에 영상 URL을 직접 넘겨 메뉴·분위기·대분류/세부 분류·태그를 추출합니다. 한 영상에 가게가 여러 곳이면 가게마다 따로 호출하며, 결과는 `data/video_notes/{model}/{video_id}__{google_cid}.json`에 저장됩니다. Google 매칭에 실패한 가게는 건너뜁니다. 자막은 더 이상 수집하지 않습니다.
6. **DB 적재**: `load_restaurants.py`(식당·영상·영업시간·미슐랭) → `load_regions.py`(`restaurants.region_1/2/3`) → `load_notes.py`(카테고리·태그·메뉴·노트) → `embed_chunks.py`(청크 임베딩). 모두 여러 번 실행해도 중복되지 않습니다.

## DB 스키마

`src/db/restaurant_schema.sql` (PostgreSQL 16 기준)

| 테이블 | 설명 |
|---|---|
| `videos` | 영상 (video_id, url, title) |
| `restaurants` | 식당 마스터. `google_cid`(Google Maps URL의 cid)로 중복 제거 |
| (`restaurants`의 `region_1~3`) | 지역(시 > 구/군 > 동·면, 최대 3단계, 없는 단계는 건너뛰고 앞에서부터 채움). 별도 테이블 없이 `restaurants` 컬럼으로 저장. 이전에 `regions` 테이블을 쓰던 DB는 `migrate_simplify_schema.sql` 실행(지역 컬럼 이전과 함께 `menus.description`·`is_signature`, `michelin_records`, `michelin_status.edition_type`·`is_active`, `restaurant_hours.is_closed` 제거), 안 쓰는 컬럼 정리는 `migrate_trim_columns.sql` |
| `restaurant_hours` | 요일별 영업시간 (하루 여러 구간, 자정 넘김 지원). 휴무는 `open_time`/`close_time`이 NULL인 행. |
| `michelin_status` | 미슐랭 현재 상태 (현재 등재 여부, 등급, 확인 시각) |
| `video_restaurant_mentions` | 영상 ↔ 식당 (N:M), LLM이 추출한 원본 이름·주소 보관 |
| `menus` | 영상에서 추출한 메뉴/음료 (조리 특징, 맛 평가, 팁, 가격 원문, 근거) |
| `restaurant_tags` | 음식 검색용 태그 (티본 스테이크, 스시 등) |
| `video_restaurant_notes` | (영상, 식당)별 컨셉·분위기·총평·임베딩용 요약과 추출 원본 JSON |
| `restaurant_chunks` | RAG 청크와 임베딩 (pgvector, `restaurant_chunks.sql`로 별도 생성) |

설계 시 참고한 점:
- `google_cid`는 부호 없는 64bit 정수라 BIGINT 범위를 넘을 수 있어 문자열로 저장합니다.
- `google_open_now`는 조회 시점 값이므로 저장하지 않고, 영업 여부는 `restaurant_hours`와 현지 시간으로 계산합니다.
- 결과 CSV는 22개 컬럼입니다. 미슐랭 연도별 이력(`history`)은 저장하지 않습니다. 음식 분류·가격대(Google `priceLevel`)·소개글은 쓰지 않습니다. `michelin_status`에는 `edition_type`·`is_active`를 두지 않고 `is_michelin`(현재 등재 여부)과 `latest_grade`만 저장합니다.
- RAG용 `restaurant_chunks`는 pgvector 확장이 필요해 `restaurant_chunks.sql`로 분리했습니다. 이미 이전 스키마를 적용한 DB는 `migrate_notes.sql`을 실행하세요.

## 시작하기

### 1. 환경 설정

```bash
uv sync   # 의존성은 pyproject.toml에 모두 정의되어 있음
# parse_apis/ 의 생성 코드는 git 제외이므로 새로 받았다면 다시 만든다 (Parse 계정 로그인 필요)
uv run parse init
uv run parse add --marketplace guide-michelin-com-api
```

### 2. 환경변수 (`.env`)

| 변수 | 용도 |
|---|---|
| `OPENAI_API_KEY` | 식당 정보 추출·분류·미슐랭 판별 |
| `GOOGLE_PLACES_API_KEY` | Places API (New). 없으면 구글 조회를 건너뜀 |
| `MICHELIN_GUIDE_API_KEY` | Parse의 guide.michelin.com API 키. 미슐랭 현재 등급 판별에 사용(코드에서 `PARSE_API_KEY`로 옮겨 줌). 없으면 미슐랭 조회가 실패로 기록됨 |
| `TAVILY_API_KEY` | 미슐랭 폴백 검색(`--michelin-fallback`)과 이력 근거 검색 |
| `GEMINI_API_KEY` | Gemini API. 영상(시각·음성)을 직접 분석해 비정형 정보를 추출할 때 사용 |
| `POSTGRES_URI` | PostgreSQL 접속 문자열 |
| `READONLY_POSTGRES_URI` | 읽기 전용 계정 접속 문자열. `src/db/test_readonly_role.py`에서 권한 확인용으로만 사용 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | LangSmith 트레이싱 |

### 3. 파이프라인 실행

```bash
uv run python src/pipeline/collect_video_urls.py                          # 1. 영상 URL 수집 (--since, --channel)
uv run python src/pipeline/channel_video_filter.py                        # 2. 필터링
uv run python src/pipeline/extract_restaurant_info.py --skip-michelin   # Parse 하루 100회 제한 때문에 미슐랭은 건너뜀
uv run python src/pipeline/fill_michelin.py                               # 하루 한도(기본 90회)만큼씩 미슐랭 채움, 남으면 다음 날 다시 실행
uv run python src/pipeline/export_review_list.py                          # (선택) 사람이 확인할 식당 목록 xlsx
uv run python src/pipeline/enrich_regions.py --limit 10 --output data/restaurant_regions_sample.csv   # 샘플 검증 후 전체
```

### 4. DB 구축

```bash
createdb restaurants
psql -d restaurants -f src/db/restaurant_schema.sql          # 스키마는 한 번만 실행
uv run python src/db/load_restaurants.py data/restaurants_info.xlsx   # DSN을 생략하면 POSTGRES_URI(.env) 사용
uv run python src/db/load_regions.py                         # restaurants.region_1/2/3 적재 (먼저 --dry-run 권장)
psql -d restaurants -f src/db/restaurant_chunks.sql          # pgvector 필요 (이전 스키마 DB는 아래 마이그레이션 먼저)
uv run python src/pipeline/extract_video_notes.py --stores-csv data/restaurants_info.csv --limit 5   # 처리 안 된 영상 N개씩 이어서
uv run python src/db/load_notes.py
uv run python src/db/embed_chunks.py
```

이미 이전 스키마를 적용한 DB는 `uv run python src/db/backup_tables.py`로 백업한 뒤 `migrate_notes.sql` → `migrate_regions.sql` → `migrate_trim_columns.sql` → `migrate_simplify_schema.sql` 순으로 필요한 것만 적용합니다.

### 5. LangGraph 실행

```bash
uv run langgraph dev
```

## 진행 상황

- [x] 채널 영상 URL 수집 및 필터링
- [x] 영상 자막 수집: 2025-01-01 이후 영상 중 DB 적재 가능한 121개 수집 완료(`get_transcripts_since.py`). 영상마다 `{video_id}.txt`(시간 없음)와 구간별 시각이 담긴 `{video_id}.json`을 저장하고, 모델 입력용 `[00:03:52] 텍스트` 문자열은 `load_timed_transcript()`가 만듭니다(메뉴가 영상의 어느 시점에 나오는지 찾는 용도). 제작자 한국어 자막은 66개이고, 나머지 55개는 자동 생성 자막(`data/transcripts/_sources.csv`의 `auto_ko`)이라 고유명사 인식 오류가 많습니다. 예전 Playwright로 받은 `.txt`는 새 자막과 문장이 다른 영상이 있습니다(자동 생성 54개 중 23개가 일치율 100% 미만). 자막/영상/하이브리드 중 어느 방식으로 추출할지는 비교 중입니다(`compare_extraction_methods.py`)
- [x] 식당 정보 추출 (Google Places, 미슐랭)
- [x] DB 스키마 설계 및 적재 스크립트
- [x] 지역(시 > 구/군 > 동): 200개 영상의 223곳 보강 후 DB 적재 완료(식당 224곳에 `region_1~3` 적재)
- [x] 200개 영상 식당 정보 DB 적재: 식당 224, 영상 185, 영업시간 1,803행. 미슐랭은 89곳만 적재됨 — `fill_michelin.py`로 나머지를 채운 뒤 `load_restaurants.py`를 다시 실행하면 `michelin_status`가 채워집니다
- [x] 비정형 정보 추출·적재(자막 방식 테스트): 2025-01-01 이후 영상 121개의 165곳(159개 식당)을 `extract_script_notes.py`로 추출해 `load_notes.py --model gpt-4o-mini-script-v2`, `embed_chunks.py`로 적재했습니다(메뉴 904: 음식 793·음료 111, 태그 798, 청크 951: OVERVIEW 165·FOOD 677·DRINK 109. 설명이 없는 메뉴는 청크로 만들지 않으며 메뉴 원본은 `menus`에서 조회합니다. FOOD/DRINK 청크 content에는 식당 이름을 넣지 않고 "메뉴명: 설명"만 두며, 식당 정보는 `restaurant_id`로 조인합니다). 메뉴 시각은 음식에만 있고(793개 중 790개) `menus.mentioned_sec`(자막 추정치)에 저장되며 음료는 시각을 저장하지 않습니다. 음식으로 잘못 분류된 음료 5개(음식 798개를 gpt-4o-mini로 재분류해 확인)는 원본 JSON과 DB를 모두 DRINK로 고쳤습니다(`fix_misclassified_drinks.sql`). 영상 분석 방식(`extract_video_notes.py`)은 5개 영상만 실행했고 결과 JSON은 `data/video_notes/gemini-3.5-flash-lite/`에 있습니다. 두 방식의 품질 비교와 환각·누락 검증은 아직 하지 않았습니다
- [x] 셰프 정보(자막 방식): 96곳을 `extract_chef_info.py`로 추출하고 `load_chefs.py`로 `restaurants.chef_name`(여러 명은 쉼표로 연결)·`chef_info`에 27곳 적재했습니다(159개 식당 중 17%). 모델 판단을 그대로 믿으면 약 30%가 틀려서("사장님", 채널 운영자, 식당 이름, 같은 영상의 다른 가게 셰프 등) 적재 단계에서 규칙으로 거르고, 확신도 low(자동 자막에서 한 번만 나온 이름 등)는 넣지 않습니다. 규칙으로 걸러지지 않은 3건은 사람이 확인해 `MANUAL_EXCLUDE`에 사유와 함께 남겼습니다. `chef_info`의 경력·수상은 유튜버가 영상에서 한 말이라 검증된 사실이 아니고, 정답 기준으로 적재 결과 전체를 검증하지는 않았습니다. 셰프 이름·경력은 OVERVIEW 청크에도 넣었습니다(28개 재임베딩). 다만 개요 청크가 길어 경력 질문의 검색 순위가 낮아서("흑백요리사 출연 셰프" 질문에서 팔선 12위, 베수비오 20위) 짧은 CHEF 청크나 키워드 검색 보완이 필요합니다. 셰프 이름이 식당 이름에 들어 있는 경우(예: 알랭 뒤카스)와 한 영상의 여러 가게에 걸친 셰프(예: 우동카덴의 정호영)는 규칙상 빠집니다
- [ ] 자막 추출 품질 개선(실험 중, 아직 DB에 적용 안 함): 현재 적재된 v2는 맛 표현의 40%가 자막 문장 그대로이고 팁을 지어내며, 자동 생성 자막 영상은 메뉴가 평균 3.5개(제작자 자막은 6.1개)로 누락이 많습니다. `compare_prompt_v3.py`로 3개 영상(르 퀸시, 레 프레 드 외제니, 비움)에서 비교한 결과 v4 프롬프트가 구어체를 정리하고 지어낸 팁을 없앴으며, 구간 분할(6분 단위)이 누락을 크게 줄이지만 중복을 만들어 v5(LLM 중복 제거+범주 이름·같은 시각 규칙)로 막았습니다. 제작자 자막으로 이미 잘 나오는 영상(비움)에서는 구간 분할이 반찬 같은 설명 없는 항목만 늘려서 자동 생성 자막 영상에만 적용하는 방안을 검토 중입니다. 3개 영상만 확인했고 전체 적용 전 추가 검증이 필요합니다. 2단계 추출(`extract_script_two_stage.py`)도 시험했으나 시각·누락 보완은 좋아지는 반면 요리별 설명이 많이 비고 같이 나온 요리의 평가가 섞여서, 자막만으로는 한계가 있어 하이브리드(영상+자막) 재검토가 필요합니다
- [ ] A/B 분리 추출(실험 중, 아직 DB에 적용 안 함, 10/7): 한 번에 많은 필드를 뽑는 부담을 줄이려고 가게 정보(A: 컨셉·분류·분위기·키포인트)와 메뉴·음료(B)를 두 호출로 나눴습니다(`extract_script_split.py`). 코스 판단(`detect_course`)을 먼저 하고 B에 알려 코스면 요리마다 `course_stage`(아뮤즈부쉬, 식전빵, 전채, 수프, 생선, 메인, 치즈, 프리디저트, 디저트, 프티푸르)를 적으며, 셰프는 `chefs_for_store`를 별도 호출로 써서 대표 셰프(오너·총괄)만 남깁니다. 모든 호출은 가게별 자막 구간(`video_parts`)만 입력으로 받아 가게가 여러 곳인 영상에서 요리가 섞이는 문제를 막았습니다. 메뉴 설명 칸은 `cooking_features` 하나로 합쳤고(`taste_review`·`tips`·`price` 제거) 재료·조리법·맛 묘사·구체적인 평가를 요약해서 담습니다. 2026년 수동 자막 영상 34개(49곳, ID는 `data/urls/split_targets_2026_manual.txt`)를 실행했습니다(약 $0.15). 기존 v2와 비교(49곳)하면 메뉴가 5.5→7.4개, 설명 길이 23→48자, 키포인트 2.5→3.2개로 늘었고 비용은 가게당 $0.0019→$0.0031, 시간은 8.4→16.4초입니다. 이후 음식 분류만 별도 호출로 다시 정했고(`reclassify_split_cuisine.py`, 한국 식당과 스페인 타파스 바가 중식으로 잘못 분류된 12곳 등 15곳이 바뀜, 이전 값은 `cuisine_before`) 후처리했습니다(`postprocess_split.py`: 평가어·가격 문장 정리, 코스 단계 보정, 음료가 메뉴로 들어간 1건과 식당 이름이 셰프로 들어간 2건 제거). 남은 문제: 핵심 평가어 8개("맛있는 버터" 등 구체적인 내용과 붙은 표현), 코스 단계가 `메인`에 몰리고 일부 오분류라 필터가 아닌 참고용으로만 쓸 것, 설명이 없는 메뉴 66개(18%)는 실제로 먹었는지 미확인, 정답 기준이 없어 환각·누락은 판단하지 못했고 자동 자막 영상(55개)에는 적용해 보지 않았습니다. 기존 DB의 자막 방식 결과(165곳)와 겹치는 가게를 어떻게 다룰지 정해야 합니다
- [ ] LangGraph 챗봇 구현 (현재 `app.py`는 빈 그래프)
- [ ] n8n 자동화

## 알려진 이슈 / TODO

- 자막 수집은 `get_transcripts_since.py`만 씁니다. 기존 Playwright 방식(`get_transcripts.py`)은 YouTube가 자막 패널 구조를 바꾸고 제작자가 영어 자막만 올린 영상은 패널이 영어로 열려서 삭제했습니다. `save_scripts.py`는 쓰지 않으며, `extract_transcript_notes.py`는 `extract_video_notes.py`가 `TranscriptNotes` 스키마를 import해서 남겨 둡니다.
- 추출 방식(영상 / 자막 / 하이브리드)은 아직 정하지 않았습니다. 5개 영상 10곳 비교(`data/compare/report.md`)에서 자막 v2 프롬프트는 분류·분위기·태그가 영상 수준으로 올라왔지만 조리 특징과 가격은 영상이 더 낫고, 하이브리드 v2가 가장 고르게 채워졌습니다. 정답 기준이 없어 환각·누락은 판단하지 못했고, video 결과는 이전 프롬프트로 만든 것이라 같은 조건이 아닙니다. 자동 생성 자막 영상으로는 아직 비교하지 않았고, 비교에 쓴 자막은 예전 `.txt`라 새 `.json`과 다를 수 있습니다(예: `7L_UMisgqrw`는 문장 일치율 10%).
- 자막 수집 대상은 `data/urls/transcript_targets_20250101.txt`(121개)입니다. 1단계 필터(소개란에 '가게'/'장소')는 이 채널 소개란 템플릿 때문에 호텔 후기도 통과시켜서, 가게와 `google_cid`가 없는 영상 14개는 `transcript_excluded_20250101.txt`로 뺐습니다. 그중 `G4gG5SqhfE8`(홍콩 광둥요리 2곳 비교)와 CSV에 행이 없는 9개 영상은 가게 추출이 빠진 것일 수 있어 확인이 필요합니다.
- 지역 매핑: `locality`가 구 단위인 나라(예: 일본 도쿄)는 "시" 자리에 구가 들어갑니다. 규칙을 바꾸면 Details를 다시 호출해 재생성합니다(식당당 1회, 223곳이면 약 3분). 원본 `addressComponents`는 용량 때문에 저장하지 않습니다. 해외 지역명은 Google이 한국어 이름을 주지 않아 영문·현지 표기(Paris, Sapporo, Bei Jing Shi 등)로 저장되고, 한국어 번역 컬럼은 만들지 않습니다(LLM 번역에 오류가 많았음). 해외 지명 질문("파리", "삿포로")은 챗봇이 질문 시점에 Places로 좌표를 조회해 반경 검색으로 처리합니다. 근처 역 정보는 저장하지 않으며, 좌표 반경 검색으로 대신합니다.
- 미슐랭: 판별은 Parse API가 기본입니다. 이름과 도시가 일치하는 후보만 채택하고 등급은 API의 `distinction`을 씁니다. 기존 Tavily 방식은 이름만 검색해 다른 도시 지점이 섞였고 실행마다 결과가 달라 `--michelin-fallback`일 때만 씁니다. API 결과가 없으면 `NONE`이라 이름 표기가 달라 놓치는 경우(특히 한국어 상호)가 있을 수 있습니다. `source_urls`는 사람이 검증할 때만 쓰므로 CSV/xlsx에만 남기고 DB에는 적재하지 않습니다. 판정은 `michelin_status`를 기준으로 합니다.
- 중국 제외는 LLM이 추정한 `country_code`가 `CN`인지로 판별하므로, 중국 식당을 다른 국가로 표기했거나 국가코드가 빈 행은 걸러지지 않습니다. 이미 DB에 적재된 중국 식당은 따로 삭제해야 합니다(적재 스크립트는 삭제하지 않음).
- Places 매칭은 후보 5개를 받아 LLM이 소개란의 이름·주소와 대조해 고르고, 외국 식당은 현지어 이름 추정으로 2차 검색합니다(식당당 Places 호출 최대 2회). 같은 주소의 다른 이름 등 일부는 여전히 틀릴 수 있어 미슐랭 후보는 매칭부터 사람이 확인합니다.
