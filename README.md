# sesac-final-project

미식 유튜브 영상의 스크립트·소개란에서 식당 정보를 추출하고, 이를 기반으로 맛집을 추천해 주는 **RAG 챗봇** 프로젝트입니다.
국내뿐 아니라 해외 식당도 대상으로 하며, 영상 약 200개 규모의 데이터를 수집·정제합니다.

## 아키텍처

정형 데이터와 비정형 데이터를 나눠 저장하는 **하이브리드 구조**입니다.

| 구분 | 데이터 | 저장소 | 조회 방식 |
|---|---|---|---|
| 정형 | 영업시간, 위치, 전화번호, 평점, 가격대, 미슐랭 등급·이력 | PostgreSQL | SQL search |
| 비정형 | 분위기, 음식 스타일, 메뉴, 영상 속 특징 | Vector DB | RAG search |

챗봇은 LangGraph로 구성하며, 먼저 SQL로 조건(지역, 영업 여부, 미슐랭 등)을 좁힌 뒤 벡터 검색으로 취향에 맞는 식당을 찾는 흐름을 목표로 합니다.

## 기술 스택

- Python 3.14, [uv](https://docs.astral.sh/uv/)
- LangGraph / LangChain, LangSmith (트레이싱)
- OpenAI API (`gpt-4o-mini`): 소개란에서 식당명·주소 추출, 카테고리 분류, 미슐랭 판별
- Google Places API (New): 공식 상호명, 주소, 평점, 영업시간 등
- Tavily Search: 미슐랭 등재 정보 검색
- yt-dlp, Playwright, youtube-transcript-api: 영상 메타데이터·자막 수집
- PostgreSQL (+ pgvector 선택)

## 프로젝트 구조

```
sesac-final-project/
├── src/
│   ├── app.py                          # LangGraph 앱 (graph 엔트리포인트)
│   ├── pipeline/                       # 데이터 수집 파이프라인
│   │   ├── channel_video_filter.py     # 1. 영상 소개란 기준 필터링
│   │   ├── get_transcripts.py          # 2-a. 자막 수집 (Playwright)
│   │   ├── save_scripts.py             # 2-b. 자막 수집 (youtube-transcript-api, 이어받기 지원)
│   │   └── extract_restaurant_info.py  # 3. 식당 정보 추출 + Places/미슐랭 조회
│   └── db/
│       ├── restaurant_schema.sql       # PostgreSQL 테이블 정의
│       └── load_restaurants.py         # xlsx -> PostgreSQL 적재
├── data/                               # 산출물 (git 제외)
│   ├── urls/                           # 채널 영상 URL 목록 (원본 / 통과 / 제외)
│   ├── transcripts/                    # 영상 자막 원문
│   └── restaurants_info.xlsx           # 추출 결과
├── notebooks/
│   └── crawling.ipynb                  # 채널 영상 URL 추출 등 실험 노트북
├── langgraph.json                      # LangGraph 설정 (graph: ./src/app.py:graph)
├── pyproject.toml
└── .env                                # API 키 (git 제외)
```

## 데이터 파이프라인

```
채널 영상 URL 수집 ─▶ 필터링 ─▶ 자막 수집 ─▶ 식당 정보 추출 ─▶ DB 적재
 (crawling.ipynb)   (filter)  (transcripts)  (extract_*)      (load_restaurants)
```

1. **영상 URL 수집** (`notebooks/crawling.ipynb`): yt-dlp로 채널의 전체 영상 URL을 `channel_video_urls.txt`에 저장합니다.
2. **필터링** (`channel_video_filter.py`): 소개란에 '가게' 또는 '장소'가 언급된 영상만 남기고 `filtered_channel_video_urls.txt` / `excluded_channel_video_urls.txt`로 나눕니다. 중국 소재 식당 제외는 이후 Places API 주소로 판별할 예정이라 이 단계에서는 하지 않습니다.
3. **자막 수집** (`save_scripts.py`, `get_transcripts.py`): 영상별 자막을 `transcripts/`에 저장합니다. `save_scripts.py`는 `_progress.json`으로 진행 상황을 기록해 중단 후 재실행하면 이어서 처리합니다.
4. **식당 정보 추출** (`extract_restaurant_info.py`):
   - yt-dlp로 소개란을 가져와 OpenAI로 식당 한국어 상호명·주소·국가코드를 추출
   - Google Places API로 공식 상호명, 평점, 영업시간, 타입 등을 조회하고 카테고리(대분류/세부)를 분류
   - Tavily 검색과 OpenAI로 미슐랭 등재 여부·등급·연도별 이력·근거 URL을 판별 (근거가 없는 연도는 채우지 않도록 프롬프트에서 제한)
   - 건마다 CSV에 즉시 기록해 중간에 중단되어도 결과가 보존되며, 완료 후 xlsx로 변환
5. **DB 적재** (`load_restaurants.py`): 추출된 xlsx를 정규화된 테이블로 나눠 저장합니다. 여러 번 실행해도 중복되지 않습니다.

## DB 스키마

`src/db/restaurant_schema.sql` (PostgreSQL 16 기준)

| 테이블 | 설명 |
|---|---|
| `videos` | 영상 (video_id, url, title) |
| `restaurants` | 식당 마스터. `google_cid`(Google Maps URL의 cid)로 중복 제거 |
| `restaurant_types` | Google 타입 목록 (검색 필터용) |
| `restaurant_hours` | 요일별 영업시간 (하루 여러 구간, 휴무, 자정 넘김 지원) |
| `michelin_status` | 미슐랭 현재 상태 (등재 여부, 최신 등급, 현재 등재 중 여부) |
| `michelin_records` | 미슐랭 연도별 이력 |
| `michelin_sources` | 미슐랭 판별 근거 URL |
| `video_restaurant_mentions` | 영상 ↔ 식당 (N:M), LLM이 추출한 원본 이름·주소 보관 |
| `menus` | 메뉴 (예정) |

설계 시 참고한 점:
- `google_cid`는 부호 없는 64bit 정수라 BIGINT 범위를 넘을 수 있어 문자열로 저장합니다.
- `google_open_now`는 조회 시점 값이므로 저장하지 않고, 영업 여부는 `restaurant_hours`와 현지 시간으로 계산합니다.
- RAG용 `restaurant_chunks`(pgvector)는 스키마 파일 하단에 주석으로 준비되어 있습니다.

## 시작하기

### 1. 환경 설정

```bash
uv sync
uv add yt-dlp openai requests python-dotenv pydantic pandas playwright youtube-transcript-api
uv run playwright install chromium   # get_transcripts.py 사용 시
```

### 2. 환경변수 (`.env`)

| 변수 | 용도 |
|---|---|
| `OPENAI_API_KEY` | 식당 정보 추출·분류·미슐랭 판별 |
| `GOOGLE_PLACES_API_KEY` | Places API (New). 없으면 구글 조회를 건너뜀 |
| `TAVILY_API_KEY` | 미슐랭 검색. 없으면 미슐랭 정보는 '정보 없음' 처리 |
| `POSTGRES_URI` | PostgreSQL 접속 문자열 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | LangSmith 트레이싱 |

### 3. 파이프라인 실행

```bash
uv run python src/pipeline/channel_video_filter.py
uv run python src/pipeline/save_scripts.py
uv run python src/pipeline/extract_restaurant_info.py
```

### 4. DB 구축

```bash
createdb restaurants
psql -d restaurants -f src/db/restaurant_schema.sql          # 스키마는 한 번만 실행
uv run python src/db/load_restaurants.py data/restaurants_info.xlsx "$POSTGRES_URI"
```

### 5. LangGraph 실행

```bash
uv run langgraph dev
```

## 진행 상황

- [x] 채널 영상 URL 수집 및 필터링
- [x] 영상 자막 수집
- [x] 식당 정보 추출 (Google Places, 미슐랭)
- [x] DB 스키마 설계 및 적재 스크립트
- [ ] 메뉴·특징 등 비정형 정보 추출 및 벡터 스토어 적재
- [ ] LangGraph 챗봇 구현 (현재 `app.py`는 빈 그래프)
- [ ] n8n 자동화

## 알려진 이슈 / TODO

- 자막 수집 스크립트가 `get_transcripts.py`(`{video_id}.txt`)와 `save_scripts.py`(`{순번}. {제목}.txt`) 두 개라 파일명 규칙이 다릅니다. 하나로 통일이 필요합니다.
- `pyproject.toml`의 `dependencies`에 파이프라인용 패키지(yt-dlp, openai 등)가 아직 모두 포함되어 있지 않습니다.
- 미슐랭 판별 결과 검증이 필요합니다. `summary_badge`와 `history`가 서로 맞지 않는 건이 있고, 일부 `source_urls`는 다른 지역 식당의 페이지를 가리킵니다. 판정은 `michelin_records`를 기준으로 하는 것을 권장합니다.
- Places API 응답의 `location`(위·경도)을 아직 수집하지 않습니다.
- `extract_restaurant_info copy.py`(이전 버전)는 정리가 필요합니다.
