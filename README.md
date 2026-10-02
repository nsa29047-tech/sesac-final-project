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
- OpenAI API (`gpt-4o-mini`): 소개란에서 식당명·주소 추출, Places 후보 선택
- Google Places API (New): 공식 상호명, 주소, 평점, 영업시간 등
- Parse SDK (guide.michelin.com API): 미슐랭 현재 등급 판별 / Tavily Search: 폴백(`--michelin-fallback`)과 연도별 이력 근거 검색
- Gemini API (Google AI Studio): 영상(시각·음성)을 직접 분석해 메뉴·분위기·태그 추출
- yt-dlp: 영상 메타데이터 수집 (자막 수집용 Playwright, youtube-transcript-api는 더 이상 쓰지 않음)
- PostgreSQL (+ pgvector 선택)

## 프로젝트 구조

```
sesac-final-project/
├── src/
│   ├── app.py                          # LangGraph 앱 (graph 엔트리포인트)
│   ├── pipeline/                       # 데이터 수집 파이프라인
│   │   ├── channel_video_filter.py     # 2. 영상 소개란 기준 필터링
│   │   ├── extract_restaurant_info.py  # 3. 식당 정보 추출 + Places/미슐랭 조회
│   │   ├── fill_michelin.py            # 3-b. 미슐랭 미조회 식당을 하루 한도만큼씩 채움
│   │   ├── enrich_regions.py           # 4. 지역(시 > 구/군 > 동) 보강
│   │   ├── extract_video_notes.py      # 5. Gemini 영상 분석 (메뉴·분위기·태그)
│   │   ├── get_transcripts.py          # (미사용) 자막 수집 - Playwright
│   │   ├── save_scripts.py             # (미사용) 자막 수집 - youtube-transcript-api
│   │   └── extract_transcript_notes.py # (미사용) 자막 기반 추출. TranscriptNotes 스키마는 5번이 import
│   └── db/
│       ├── restaurant_schema.sql       # PostgreSQL 테이블 정의
│       └── load_restaurants.py         # xlsx -> PostgreSQL 적재
├── data/                               # 산출물 (git 제외)
│   ├── urls/                           # 채널 영상 URL 목록 (원본 / 통과 / 제외)
│   ├── transcripts/                    # (미사용) 기존에 받아 둔 자막. Gemini 결과 비교용
│   └── restaurants_info.xlsx           # 추출 결과
├── notebooks/
│   └── crawling.ipynb                  # 채널 영상 URL 추출 등 실험 노트북
├── langgraph.json                      # LangGraph 설정 (graph: ./src/app.py:graph)
├── pyproject.toml
└── .env                                # API 키 (git 제외)
```

## 데이터 파이프라인

```
영상 URL 수집 ─▶ 필터링 ─▶ 식당 정보 추출 ─▶ 지역 보강 ─▶ 영상 분석(Gemini) ─▶ DB 적재
 (collect_*)     (filter)   (extract_restaurant)  (enrich_regions) (extract_video_notes)  (load_*)
```

1. **영상 URL 수집** (`collect_video_urls.py`): yt-dlp로 채널 영상 목록을 가져와, 이분 탐색으로 2023-01-01 이후 영상만 골라 `channel_video_urls.txt`에 저장합니다. `--since`, `--channel` 옵션으로 기준일과 채널을 바꿀 수 있습니다. (기존 `notebooks/crawling.ipynb` 첫 셀을 스크립트화)
2. **필터링** (`channel_video_filter.py`): 소개란에 '가게' 또는 '장소'가 언급된 영상만 남기고 `filtered_channel_video_urls.txt` / `excluded_channel_video_urls.txt`로 나눕니다. 중국 소재 식당 제외는 이후 Places API 주소로 판별할 예정이라 이 단계에서는 하지 않습니다.
3. **식당 정보 추출** (`extract_restaurant_info.py`):
   - yt-dlp로 소개란을 가져와 OpenAI로 식당 한국어 상호명·주소·국가코드를 추출
   - Google Places API로 공식 상호명, 주소, 좌표, 평점, 영업 상태, 연락처, 영업시간을 조회. 음식 분류(대분류/세부/태그)와 가격 정보는 CSV에 저장하지 않고 Gemini 영상 분석 결과를 씁니다. 숙소(호텔·료칸 등)로 매칭된 장소는 영상 제목으로 판단합니다: 제목에 식당·식사·요리 관련 단어(레스토랑, 식당, 식사, 요리, 밥, 디너, 맛집)가 있으면 가져오고(`note`에 '숙소로 매칭됨' 표시), 없으면 호텔 후기·료칸 소개가 주 소재라고 보고 제외합니다. 같은 영상에서 300m 이내에 다른 식당 행이 있는 숙소 행도 식당 정보에 딸려 적힌 것으로 보고 뺍니다
   - Parse(guide.michelin.com) API로 미슐랭 현재 등급을 판별: 검색 결과 중 이름과 도시(또는 거리 주소)가 일치하는 식당만 채택하고, 일치하는 식당이 없으면 `NONE`. API는 현재 등급만 주므로 연도별 이력은 저장하지 않습니다. 무료 플랜의 분당 5회 제한 때문에 호출 사이에 약 12.5초 간격을 둡니다. `--michelin-fallback`을 주면 일치 식당이 없을 때 기존 Tavily 검색으로 한 번 더 확인합니다(호출 증가).
   - Parse API는 **하루 100회 제한**이 있어서(식당 약 240곳이면 3일), 전체 실행은 `--skip-michelin`으로 미슐랭을 건너뛰고(`note`에 '미슐랭 미조회' 표시) 나중에 `fill_michelin.py`로 하루 한도(기본 90회)만큼씩 채웁니다. 같은 식당이 여러 영상에 나오면 한 번만 조회하고, 한도에 도달하면 멈췄다가 다음 날 이어서 처리합니다. `google_cid`가 없는 행은 건너뜁니다. 미슐랭을 못 채운 행은 `load_restaurants.py`가 `michelin_status`를 만들지 않습니다.
   - 건마다 CSV에 즉시 기록해 중간에 중단되어도 결과가 보존되며, 완료 후 xlsx로 변환
4. **지역 보강** (`enrich_regions.py`): 3번 CSV의 `google_place_id`로 Places Details의 `addressComponents`에서 시 > 구/군 > 동·면(서울특별시 > 마포구 > 연남동, 성남시 > 분당구 > 정자동)을 뽑아 `data/restaurant_regions.csv`에 건별로 저장합니다. 없는 단계는 건너뛰고 있는 데까지만 저장하며(안동시는 시만, 도로명 주소라 동이 없으면 구까지), 도로명은 쓰지 않습니다. 식당당 Details 1회이고, 재실행하면 이어서 처리합니다. `--limit`으로 샘플을 먼저 검증하세요. "OO역/OO 근처" 질문은 지역 컬럼이 아니라 식당의 위경도로 반경 검색합니다(질문 시점에 지명의 좌표를 조회). DB 적재 스크립트는 아직 없습니다.
5. **영상 분석** (`extract_video_notes.py`): 3번의 CSV를 가게 목록으로 삼아, Gemini API에 영상 URL을 직접 넘겨 메뉴·분위기·대분류/세부 분류·태그를 추출합니다. 한 영상에 가게가 여러 곳이면 가게마다 따로 호출하며, 결과는 `data/video_notes/{model}/{video_id}__{google_cid}.json`에 저장됩니다. Google 매칭에 실패한 가게는 건너뜁니다. 자막은 더 이상 수집하지 않습니다.
6. **DB 적재**: `load_restaurants.py`(식당·영업시간·미슐랭) → `load_regions.py`(`restaurants.region_1/2/3`) → `load_notes.py`(카테고리·태그·메뉴·노트) → `embed_chunks.py`(청크 임베딩). 모두 여러 번 실행해도 중복되지 않습니다.

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
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | LangSmith 트레이싱 |

### 3. 파이프라인 실행

```bash
uv run python src/pipeline/channel_video_filter.py
uv run python src/pipeline/extract_restaurant_info.py --skip-michelin   # Parse 하루 100회 제한 때문에 미슐랭은 건너뜀
uv run python src/pipeline/fill_michelin.py                               # 하루 한도(기본 90회)만큼씩 미슐랭 채움, 남으면 다음 날 다시 실행
uv run python src/pipeline/enrich_regions.py --limit 10 --output data/restaurant_regions_sample.csv   # 샘플 검증 후 전체
```

### 4. DB 구축

```bash
createdb restaurants
psql -d restaurants -f src/db/restaurant_schema.sql          # 스키마는 한 번만 실행
uv run python src/db/load_restaurants.py data/restaurants_info.xlsx "$POSTGRES_URI"
uv run python src/db/load_regions.py                         # restaurants.region_1/2/3 적재 (먼저 --dry-run 권장)
psql -d restaurants -f src/db/restaurant_chunks.sql          # pgvector 필요 (이전 스키마 DB는 migrate_notes.sql 먼저)
uv run python src/pipeline/extract_video_notes.py --stores-csv data/restaurants_info.csv
uv run python src/db/load_notes.py
uv run python src/db/embed_chunks.py
```

### 5. LangGraph 실행

```bash
uv run langgraph dev
```

## 진행 상황

- [x] 채널 영상 URL 수집 및 필터링
- [x] 영상 자막 수집 (Gemini 영상 분석으로 대체되어 더 이상 쓰지 않음)
- [x] 식당 정보 추출 (Google Places, 미슐랭)
- [x] DB 스키마 설계 및 적재 스크립트
- [x] 지역(시 > 구/군 > 동): 200개 영상의 223곳 보강 후 DB 적재 완료(식당 224곳에 `region_1~3` 적재)
- [x] 200개 영상 식당 정보 DB 적재: 식당 224, 영상 185, 영업시간 1,803행. 미슐랭은 89곳만 적재됨 — `fill_michelin.py`로 나머지를 채운 뒤 `load_restaurants.py`를 다시 실행하면 `michelin_status`가 채워집니다
- [ ] 메뉴·특징 등 비정형 정보 추출 및 벡터 스토어 적재: 영상 5개 분석·적재·임베딩 검증 완료(`extract_video_notes.py --limit N`을 반복 실행해 나머지 169개를 처리하고, `load_notes.py`·`embed_chunks.py`로 적재)
- [ ] LangGraph 챗봇 구현 (현재 `app.py`는 빈 그래프)
- [ ] n8n 자동화

## 알려진 이슈 / TODO

- 자막 수집 스크립트(`get_transcripts.py`, `save_scripts.py`)와 `extract_transcript_notes.py`는 Gemini 영상 분석으로 대체되어 쓰지 않습니다. 삭제하려면 `extract_video_notes.py`가 import하는 `TranscriptNotes`를 먼저 옮겨야 합니다. Gemini 추출은 4개 영상으로만 검증했으므로 전체 실행 전에 기존 자막 방식 결과와 샘플을 비교합니다.
- 지역 매핑: `locality`가 구 단위인 나라(예: 일본 도쿄)는 "시" 자리에 구가 들어갑니다. 규칙을 바꾸면 Details를 다시 호출해 재생성합니다(식당당 1회, 223곳이면 약 3분). 원본 `addressComponents`는 용량 때문에 저장하지 않습니다. 해외 지역명은 Google이 한국어 이름을 주지 않아 영문·현지 표기(Paris, Sapporo, Bei Jing Shi 등)로 저장되고, 한국어 번역 컬럼은 만들지 않습니다(LLM 번역에 오류가 많았음). 해외 지명 질문("파리", "삿포로")은 챗봇이 질문 시점에 Places로 좌표를 조회해 반경 검색으로 처리합니다. 근처 역 정보는 저장하지 않으며, 좌표 반경 검색으로 대신합니다.
- 미슐랭: `summary_badge`는 제거했고, `is_michelin`/`latest_grade`/`is_active`는 LLM이 준 `history`(연도 근거 필수, 계산에만 쓰고 저장하지 않음)와 `current_grade`(근거 문장 인용 필수)에서 코드로 계산합니다(`--michelin-fallback` 경로). 등급은 근거 문장·공식 등급별 목록 페이지로 검증하고, 등재는 확인했지만 등급 근거가 없으면 `latest_grade=UNKNOWN`으로 저장합니다(사람이 확인 필요). `ACTIVE_FROM_YEAR`는 새 가이드 발표 시 갱신합니다. 같은 식당도 검색·LLM 결과가 실행마다 달라 등재를 놓치는 경우(false negative)가 있습니다. 이전 방식으로 만든 기존 결과는 재추출이 필요합니다. `source_urls`(판별 근거 링크)는 사람이 검증할 때만 쓰므로 CSV/xlsx에만 남기고 DB에는 적재하지 않습니다. 일부는 다른 지역 식당 페이지를 가리킬 수 있습니다. 판정은 `michelin_status`를 기준으로 합니다.
- 기존 DB가 있다면 `ALTER TABLE michelin_status DROP COLUMN summary_badge; DROP TABLE michelin_sources;`를 실행해야 합니다.
- Places 매칭은 후보 5개를 받아 LLM이 소개란의 이름·주소와 대조해 고르고, 외국 식당은 현지어 이름 추정으로 2차 검색합니다(식당당 Places 호출 최대 2회). 같은 주소의 다른 이름 등 일부는 여전히 틀릴 수 있어 미슐랭 후보는 매칭부터 사람이 확인합니다.
