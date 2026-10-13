# sesac-final-project

미식 유튜브 영상의 소개란과 영상 자막(스크립트 분석)에서 식당 정보를 추출하고, 이를 기반으로 맛집을 추천해 주는 **RAG 챗봇** 프로젝트입니다.
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
- OpenAI `gpt-4o-mini`: 영상 자막(스크립트)에서 메뉴·분위기·태그·셰프 추출 (현재 사용)
- Gemini API (Google AI Studio): 영상(시각·음성)을 직접 분석. 자동 생성 자막 영상(오타·누락이 많음)에만 사용(`extract_video_split.py`, 2026년 분 완료). 제작자 자막 영상은 자막 분석(gpt-4o-mini)
- yt-dlp: 영상 메타데이터와 자막(스크립트) 수집 (Playwright, youtube-transcript-api는 쓰지 않음)
- PostgreSQL (+ pgvector 선택)

## 프로젝트 구조

```
sesac-final-project/
├── src/
│   ├── app.py                          # LangGraph 엔트리포인트 (chatbot.graph.build_graph)
│   ├── chatbot/                        # 챗봇 에이전트: graph.py(ReAct 그래프+시스템 프롬프트), tools.py(find_restaurants·recommend_by_taste·get_restaurant_info·geocode_place), cli.py(터미널 대화), eval/(평가셋 cases.py + run_eval.py)
│   ├── pipeline/                       # 데이터 수집 파이프라인
│   │   ├── common.py                   # 자막 추출 단계의 공통 코드(셰프 추출 chefs_for_store 포함): 경로·모델·OpenAI 클라이언트, load_stores, 시각 보정(fix_times), 평가어 제거(strip_generic), 영상 구간 나누기(video_parts), 코스 판단(detect_course), 음식 분류(classify_cuisine)
│   │   ├── collect_video_urls.py       # 1. 채널 영상 URL 수집 (yt-dlp, 이분 탐색)
│   │   ├── channel_video_filter.py     # 2. 영상 소개란 기준 필터링
│   │   ├── extract_restaurant_info.py  # 3. 식당 정보 추출 + Places/미슐랭 조회, 중국(CN) 제외
│   │   ├── fill_michelin.py            # 3-b. 미슐랭 미조회 식당을 하루 한도만큼씩 채움
 (--only-notes 태그: 자막 분석 결과가 있는 식당만)
│   │   ├── export_review_list.py       # 3-c. 사람이 확인할 식당(미슐랭 후보·매칭 실패·숙소)을 xlsx로 추출
│   │   ├── enrich_places_details.py   # 4. Places Details 1회로 지역(시 > 구/군 > 동) 보강 + (--prices) 가격대 수집 -> data/restaurant_regions.csv, data/restaurant_prices_sample.csv. 예전 enrich_regions.py + fetch_price_level.py
│   │   ├── extract_script_split.py     # 5''''. A/B 분리 추출(A 가게 정보 / B 메뉴·음료·코스 단계 + 코스 판단·셰프 호출), 건별 저장 -> data/video_notes/gpt-4o-mini-script-split-v2/ (2026년), -2025/ (2025년), -2024/ (2024년). 2024년 영상은 환경변수 TARGETS_FILE=data/urls/transcript_targets_20240101.txt 로 대상 목록을 바꿔 실행
│   │   ├── extract_video_split.py      # 5-a'. 자동 생성 자막 영상용 Gemini 영상 분석(자막은 쓰지 않음). 자막 방식과 같은 필드·같은 JSON 구조로 저장, 결과 후처리는 postprocess_split.py --tag 로 같이 쓴다
│   │   ├── postprocess_split.py        # 5-b. A/B 분리 결과 후처리(음식 분류 다시 정하기(예전 reclassify_split_cuisine.py), 평가어·가격 문장, 코스 단계 보정, 음료 중복, 식당 이름 셰프 제거), 원본은 *_raw 폴더에 백업
│   │   ├── get_transcripts_since.py    # 자막 수집 (yt-dlp, 기본 2025-01-01 이후·적재 가능 영상만, --since 로 날짜 변경, 이어받기)
│   │   └── experiments/                # 실험·보류·미사용 코드. 지금 파이프라인은 import 하지 않음(sys.path로 pipeline 모듈만 가져다 씀)
│   │       ├── extract_script_notes.py / extract_script_course.py / extract_script_v7.py / extract_script_two_stage.py / compare_prompt_v3.py  # 자막 추출 프롬프트 실험(A/B 분리 이전 방식)
│   │       ├── compare_extraction_methods.py / extract_video_notes.py  # video / 자막 / 하이브리드 비교, 예전 Gemini 영상 분석 실험(extract_video_split.py로 대체됨)
│   │       ├── chunk_transcripts.py / extract_transcript_notes.py / check_course_gold.py  # 자막 원문 청킹 실험, 초기 스키마, 코스 점검표
│   │       ├── extract_chef_info.py  # 셰프 단독 추출(예전 방식, data/chef_notes). 지금은 extract_script_split.py 가 추출 때 함께 뽑음
│   │       └── probe_price.py / michelin_api_probe.py / save_scripts.py  # 가격대·Parse API 확인용, 옛 자막 수집
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
│       ├── load_notes.py               # 6-3. 자막 분석 결과 -> 카테고리·태그·메뉴·노트
│       ├── embed_chunks.py             # 6-4. 청크 생성 + 임베딩 -> restaurant_chunks
│       ├── compare_chunking.py         # 청킹 방식 비교 실험 (DB에 쓰지 않음)
│       ├── migrate_split_schema.sql    # 마이그레이션(10/8): A/B 분리 결과용. menus.cooking_features->description, taste_review/tips/price_text 삭제, menu_order·course_stage, video_restaurant_notes.is_course·course_name
│       ├── migrate_price_columns.sql   # 마이그레이션(10/8): restaurants.price_level/price_min/price_max/price_currency
│       ├── migrate_chef_chunk.sql      # 마이그레이션(10/8): restaurant_chunks에 CHEF 청크 종류 추가
│       ├── load_prices.py              # enrich_places_details.py --prices 결과 -> restaurants 가격 컬럼
│       ├── create_scope_views.sql      # 조회 범위 뷰: restaurants_in_scope 등 (자막 분석 노트가 있는 식당만). 챗봇은 현재 쓰지 않음
│       ├── create_chatbot_role.sql     # 챗봇 전용 DB 계정 스크립트. 현재 쓰지 않음(챗봇은 READONLY_POSTGRES_URI 사용)
│       ├── category_rules.py           # 분류 규칙: 세부 분류는 한 식당에 하나만(나머지는 태그), 수동 분류 MANUAL_CATEGORY
│       ├── pick_detail.py              # 세부 분류를 식당당 대표 하나로 정함(LLM) + 나머지는 태그, 미슐랭 태그 삭제. --limit 샘플, --apply 반영
│       ├── normalize_categories.py     # 이미 적재된 DB의 세부 분류 정리(--dry-run 가능). load_notes.py도 같은 규칙을 쓴다
│       ├── rag_search.py               # 검색 모듈: SQL 필터 -> 벡터 후보 -> LLM 충족 판단(맞는 결과 없으면 빈 목록)
│       └── test_rag.py                 # RAG 검색 테스트(12개 질문, 현재 범위 120곳)
├── parse_apis/                         # Parse SDK 스캐폴드. 생성 코드는 git 제외(`uv run parse add`로 재생성)
├── data/                               # 산출물 (git 제외)
│   ├── urls/                           # 채널 영상 URL 목록 (원본 / 통과 / 제외)
│   ├── restaurants_info.csv, .xlsx     # 3번 결과 (22개 컬럼, 건별 저장). *_before_*, test*, pilot* 등은 백업·실험본
│   ├── restaurant_regions.csv          # 4번 결과
│   ├── video_notes/{model}/            # 5번 결과 (영상×가게별 JSON)
│   ├── transcripts/, transcript_notes/ # 자막 방식 산출물 (현재 주 입력). Gemini 결과는 초기 비교용
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
영상 URL 수집 ─▶ 필터링 ─▶ 식당 정보 추출 ─▶ 미슐랭 채우기 ─▶ 지역 보강 ─▶ 자막 수집 ─▶ 자막 분석(GPT) ─▶ DB 적재
 (collect_*)     (filter)   (extract_restaurant)  (fill_michelin)  (enrich_places_details) (get_transcripts_since, extract_script_split)  (load_*)
```

1. **영상 URL 수집** (`collect_video_urls.py`): yt-dlp로 채널 영상 목록을 가져와, 이분 탐색으로 2023-01-01 이후 영상만 골라 `channel_video_urls.txt`에 저장합니다. `--since`, `--channel` 옵션으로 기준일과 채널을 바꿀 수 있습니다. (기존 `notebooks/crawling.ipynb` 첫 셀을 스크립트화)
2. **필터링** (`channel_video_filter.py`): 소개란에 '가게' 또는 '장소'가 언급된 영상만 남기고 `filtered_channel_video_urls.txt` / `excluded_channel_video_urls.txt`로 나눕니다. 중국 소재 식당 제외는 3번에서 합니다.
3. **식당 정보 추출** (`extract_restaurant_info.py`):
   - yt-dlp로 소개란을 가져와 OpenAI로 식당 한국어 상호명·주소·국가코드를 추출
   - 중국 본토(`country_code`가 `CN`)는 제외합니다(Places 호출 전에 건너뜀). 홍콩·마카오·대만은 유지합니다. 판별 기준이 LLM이 추정한 국가코드라 중국 식당을 다른 국가로 표기하면 걸러지지 않습니다(`EXCLUDED_COUNTRY`)
   - Google Places API(Text Search)로 공식 상호명, 주소, 좌표, 평점, 영업 상태, 연락처, 영업시간을 조회. 음식 분류(대분류/세부/태그)는 CSV에 저장하지 않고 자막 분석 결과를 씁니다. 가격대(`priceLevel`/`priceRange`)는 같은 Text Search 응답(이미 Enterprise 등급이라 추가 비용 없음)에서 받아 22컬럼 CSV가 아니라 `data/restaurant_prices_sample.csv`에 기록합니다(`enrich_places_details.py --prices`와 같은 컬럼이라 `load_prices.py`를 그대로 씁니다). 지역(`addressComponents`)은 합치지 않았습니다: 이 검색은 languageCode를 주지 않아 영문 지명(Seoul / Jung District)이 오는데, 공식 상호명도 같은 호출의 영문 표기를 쓰고 있어 언어를 바꿀 수 없고, 지역은 한국어(`languageCode=ko`)로 받아야 하므로 4번이 Details로 따로 받습니다. 숙소(호텔·료칸 등)로 매칭된 장소는 영상 제목으로 판단합니다: 제목에 식당·식사·요리 관련 단어(레스토랑, 식당, 식사, 요리, 밥, 디너, 맛집)가 있으면 가져오고(`note`에 '숙소로 매칭됨' 표시), 없으면 호텔 후기·료칸 소개가 주 소재라고 보고 제외합니다. 같은 영상에서 300m 이내에 다른 식당 행이 있는 숙소 행도 식당 정보에 딸려 적힌 것으로 보고 뺍니다
   - Parse(guide.michelin.com) API로 미슐랭 현재 등급을 판별: 검색 결과 중 이름과 도시(또는 거리 주소)가 일치하는 식당만 채택하고, 일치하는 식당이 없으면 `NONE`. API는 현재 등급만 주므로 연도별 이력은 저장하지 않습니다. 무료 플랜의 분당 5회 제한 때문에 호출 사이에 약 12.5초 간격을 둡니다. `--michelin-fallback`을 주면 일치 식당이 없을 때 기존 Tavily 검색으로 한 번 더 확인합니다(호출 증가).
   - Parse API는 **하루 100회 제한**이 있어서(식당 약 240곳이면 3일), 전체 실행은 `--skip-michelin`으로 미슐랭을 건너뛰고(`note`에 '미슐랭 미조회' 표시) 나중에 `fill_michelin.py`로 하루 한도(기본 90회)만큼씩 채웁니다. 같은 식당이 여러 영상에 나오면 한 번만 조회하고, 한도에 도달하면 멈췄다가 다음 날 이어서 처리합니다. `google_cid`가 없는 행은 건너뜁니다. 미슐랭을 못 채운 행은 `load_restaurants.py`가 `michelin_status`를 만들지 않습니다.
   - 건마다 CSV에 즉시 기록해 중간에 중단되어도 결과가 보존되며, 완료 후 xlsx로 변환
   - `export_review_list.py`: 사람이 확인할 행(미슐랭 후보, Google 매칭 실패, 숙소로 매칭된 행)만 뽑아 xlsx로 저장합니다
4. **지역·가격대 보강** (`enrich_places_details.py`, 가격대는 `--prices`일 때만): 3번 CSV의 `google_place_id`로 Places Details의 `addressComponents`에서 시 > 구/군 > 동·면(서울특별시 > 마포구 > 연남동, 성남시 > 분당구 > 정자동)을 뽑아 `data/restaurant_regions.csv`에 건별로 저장합니다. 없는 단계는 건너뛰고 있는 데까지만 저장하며(안동시는 시만, 도로명 주소라 동이 없으면 구까지), 도로명은 쓰지 않습니다. 식당당 Details 1회이고, 재실행하면 이어서 처리합니다. `--limit`으로 샘플을 먼저 검증하세요. "OO역/OO 근처" 질문은 지역 컬럼이 아니라 식당의 위경도로 반경 검색합니다(질문 시점에 지명의 좌표를 조회). DB 적재는 6번의 `load_regions.py`가 합니다.
5. **자막 수집·분석**: `get_transcripts_since.py`(yt-dlp)로 제작자 한국어 자막(없으면 자동 생성 자막)을 구간별 시각과 함께 받고(`data/transcripts/{video_id}.json`), `extract_script_split.py`가 3번의 CSV를 가게 목록으로 삼아 자막만으로 가게 정보(A)와 메뉴·음료(B)를 나눠 추출합니다(`gpt-4o-mini`, 가게당 3~4회 호출). 한 영상에 가게가 여러 곳이면 가게별 자막 구간만 입력으로 줍니다. 이어서 `postprocess_split.py`(음식 분류 다시 정하기, 평가어·코스 단계 보정)를 거치고, 결과는 `data/video_notes/{tag}/{video_id}__{google_cid}.json`에 저장됩니다. Google 매칭에 실패한 가게는 건너뜁니다. 제작자 자막 영상은 이 자막 분석을, **자동 생성 자막 영상(오타·누락이 많음)은 Gemini 영상 분석(`extract_video_split.py`, 자막은 쓰지 않음)을 씁니다.** 둘 다 같은 필드·JSON 구조를 만들어 후처리(`postprocess_split.py`)와 적재를 공유합니다. 2026년 자동 자막 영상은 처리했고 2025년 23개·2024년 11개가 남았습니다.
   - **후처리(`postprocess_split.py`)가 하는 일과 이유**: 추출 호출은 다시 하지 않고 결과 JSON만 정리합니다. (1) 음식 분류 다시 정하기: 영상 제목·가게 이름·주소·국가·컨셉·나온 메뉴를 보고 분류 전용 호출(`common.classify_cuisine`)로 대분류·세부 분류(대표 하나)·태그를 다시 정하고 표기 통일·미슐랭 제외(`category_rules.finalize_cuisine`)를 거칩니다. 이전 값은 `cuisine_before`에 남깁니다. (2) 설명의 평가어·가격: 근거 없이 평가만 있는 문장은 규칙으로 지우고, 구체적인 내용과 섞인 문장은 LLM이 평가·가격만 빼고 다시 씁니다. (3) 코스 단계 보정: '치즈'가 이름에 있으면 치즈, 고기보다 앞에 나온 해산물 메인은 생선, 단계가 순서대로 줄어들지 않게 맞춥니다. (4) 음료와 이름이 같은 메뉴 제거. (5) 식당 이름이 들어간 셰프 제거. 원본은 `data/video_notes/{tag}_raw`에 한 번만 복사하고 항상 원본에서 읽어 쓰므로 다시 실행해도 결과가 같습니다(수동 수정도 원본 쪽에 합니다). 바뀐 내용은 결과의 `postprocess` 키에 남습니다. 실행: `TARGETS_FILE=<대상 ID 목록> uv run python src/pipeline/postprocess_split.py --tag $TAG`(`--dry-run`은 규칙만, `--skip-cuisine`은 분류 건너뜀).
   - **왜 추출과 후처리를 나누는가**: 프롬프트에 같은 규칙(평가어 금지, 분류 기준, 코스 단계 순서, 음료는 메뉴 아님)이 들어 있어도 모델이 지키지 못하는 경우가 남습니다(영상 분석 테스트 4곳에서 분류 1곳, 코스 순서 2건, 평가어 3항목을 후처리가 고침). 순서 맞추기·음료 중복·셰프 이름 제거 같은 일은 규칙이라 코드가 항상 같은 결과를 내고 모델은 실행마다 달라집니다. 한 호출에 필드를 더하면 추출이 흔들렸고(과거 실험), 분류는 메뉴가 다 나온 뒤에 해야 정확합니다. 또 원본을 보존하므로 분류 표기나 평가어 규칙이 바뀌어도 영상 분석(Gemini, 하루 8시간 한도)을 다시 돌리지 않고 OpenAI 약 $0.01로 전체를 다시 정리할 수 있습니다. 추출 스크립트 끝에서 자동 실행하도록 합치는 것은 가능하나 아직 하지 않았습니다.
6. **DB 적재**: `load_restaurants.py`(식당·영상·영업시간·미슐랭) → `load_regions.py`(`restaurants.region_1/2/3`) → `load_notes.py`(카테고리·태그·메뉴·노트) → `embed_chunks.py`(청크 임베딩). 모두 여러 번 실행해도 중복되지 않습니다.

## DB 스키마

`src/db/restaurant_schema.sql` (PostgreSQL 16 기준)

| 테이블 | 설명 |
|---|---|
| `videos` | 영상 (video_id, url, title) |
| `restaurants` | 식당 마스터. `google_cid`(Google Maps URL의 cid)로 중복 제거. 분류는 `category_broad`(한식·일식·중식·양식·동남아식·인도식·중동식·기타)와 `category_detail`(식당당 대표 하나만, 나머지는 `restaurant_tags`. 프렌치·이탈리안·타파스 바·고깃집·해산물·회 등으로 표기 통일). 미슐랭은 태그에 넣지 않는다 |
| (`restaurants`의 `region_1~3`) | 지역(시 > 구/군 > 동·면, 최대 3단계, 없는 단계는 건너뛰고 앞에서부터 채움). 별도 테이블 없이 `restaurants` 컬럼으로 저장. 이전에 `regions` 테이블을 쓰던 DB는 `migrate_simplify_schema.sql` 실행(지역 컬럼 이전과 함께 `menus.description`·`is_signature`, `michelin_records`, `michelin_status.edition_type`·`is_active`, `restaurant_hours.is_closed` 제거), 안 쓰는 컬럼 정리는 `migrate_trim_columns.sql` |
| `restaurant_hours` | 요일별 영업시간 (하루 여러 구간, 자정 넘김 지원). 휴무는 `open_time`/`close_time`이 NULL인 행. |
| `michelin_status` | 미슐랭 현재 상태 (현재 등재 여부, 등급, 확인 시각) |
| `video_restaurant_mentions` | 영상 ↔ 식당 (N:M), LLM이 추출한 원본 이름·주소 보관 |
| `menus` | 영상에서 추출한 메뉴/음료 (`description` 설명, 근거 `evidence`, 영상 속 순서 `menu_order`, 코스 단계 `course_stage`(참고용, 필터 금지), 메뉴 시각 `mentioned_sec`(영상 분석은 화면 첫 등장, 자막 분석은 자막 시각 추정 — 한 칸으로 통합)). 맛 평가·팁·가격은 저장하지 않음 |
| `restaurant_tags` | 음식 검색용 태그 (티본 스테이크, 스시 등) |
| `video_restaurant_notes` | (영상, 식당)별 컨셉·분위기·총평·임베딩용 요약, 코스 식당 여부 `is_course`·코스 이름 `course_name`, 추출 원본 JSON |
| `restaurant_chunks` | RAG 청크와 임베딩 (pgvector, `restaurant_chunks.sql`로 별도 생성). 종류: OVERVIEW(식당 개요) / FOOD / DRINK(메뉴별) / CHEF(셰프 이름·경력만 담은 짧은 청크) |
| `restaurants_in_scope`, `restaurant_hours_in_scope`, `michelin_status_in_scope` | **뷰.** 자막 분석 노트가 있는 식당(현재 120곳)만 보이는 조회용(`create_scope_views.sql`). 챗봇은 현재 쓰지 않고 원본 테이블을 조회한다 |

설계 시 참고한 점:
- `google_cid`는 부호 없는 64bit 정수라 BIGINT 범위를 넘을 수 있어 문자열로 저장합니다.
- `google_open_now`는 조회 시점 값이므로 저장하지 않고, 영업 여부는 `restaurant_hours`와 현지 시간으로 계산합니다.
- 결과 CSV는 22개 컬럼입니다. 미슐랭 연도별 이력(`history`)은 저장하지 않습니다. 음식 분류·소개글은 쓰지 않고 자막 분석 결과를 씁니다. 가격대는 Places `priceLevel`/`priceRange`를 `restaurants.price_level`(0 무료 ~ 4 매우 비쌈)·`price_min`·`price_max`·`price_currency`에 저장합니다(영상의 메뉴 가격은 저장하지 않음). 현재는 자막 분석 노트가 있는 120곳만 호출했고(약 100곳에 값 있음), 통화가 섞여 있어 비교는 `price_level`로 합니다. `michelin_status`에는 `edition_type`·`is_active`를 두지 않고 `is_michelin`(현재 등재 여부)과 `latest_grade`만 저장합니다.
- **챗봇 조회 규칙**: 에이전트의 SQL 도구는 `restaurants`·`restaurant_hours`·`michelin_status` 등 원본 테이블을 읽기 전용 계정(`READONLY_POSTGRES_URI`, `create_readonly_role.sql`)으로 조회합니다(10/13 변경, 이전에는 `*_in_scope` 뷰만 조회). 노트가 없는 식당(약 100곳)은 메뉴·분위기가 없지만 `find_restaurants`(SQL 전용) 결과에는 나옵니다. 벡터 검색(`recommend_by_taste`)은 노트·청크와 조인하므로 노트가 있는 식당만 나옵니다. 조회 범위를 노트가 있는 식당으로 제한하려면 `create_scope_views.sql`의 뷰를 쓰도록 `src/chatbot/tools.py`·`rag_search.py`의 테이블 이름을 되돌립니다.
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
| `GEMINI_API_KEY` | Gemini API. 자동 생성 자막 영상을 직접 분석할 때 사용(`extract_video_split.py`). 무료 등급은 YouTube 영상을 하루 8시간까지만 받는다 |
| `POSTGRES_URI` | PostgreSQL 접속 문자열 |
| `READONLY_POSTGRES_URI` | 읽기 전용 계정(`create_readonly_role.sql`) 접속 문자열. 챗봇(`src/chatbot`)과 `rag_search.py`가 원본 테이블 조회에 쓴다. 없으면 `POSTGRES_URI`로 접속하며 경고한다 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | LangSmith 트레이싱 |

### 3. 파이프라인 실행

```bash
uv run python src/pipeline/collect_video_urls.py                          # 1. 영상 URL 수집 (--since, --channel)
uv run python src/pipeline/channel_video_filter.py                        # 2. 필터링
uv run python src/pipeline/extract_restaurant_info.py --skip-michelin   # Parse 하루 100회 제한 때문에 미슐랭은 건너뜀
uv run python src/pipeline/fill_michelin.py                               # 하루 한도(기본 90회)만큼씩 미슐랭 채움, 남으면 다음 날 다시 실행
uv run python src/pipeline/export_review_list.py                          # (선택) 사람이 확인할 식당 목록 xlsx
uv run python src/pipeline/enrich_places_details.py --limit 10 --regions-output data/restaurant_regions_sample.csv   # 샘플 검증 후 전체
```

### 4. DB 구축

```bash
createdb restaurants
psql -d restaurants -f src/db/restaurant_schema.sql          # 스키마는 한 번만 실행
uv run python src/db/load_restaurants.py data/restaurants_info.xlsx   # DSN을 생략하면 POSTGRES_URI(.env) 사용
uv run python src/db/load_regions.py                         # restaurants.region_1/2/3 적재 (먼저 --dry-run 권장)
psql -d restaurants -f src/db/restaurant_chunks.sql          # pgvector 필요 (이전 스키마 DB는 아래 마이그레이션 먼저)
# 비정형 정보 추출·적재는 아래 "A/B 분리 추출 결과를 DB에 반영하는 순서"를 따른다 (자막 수집 -> extract_script_split -> load_notes -> embed_chunks)
```

A/B 분리 추출 결과를 DB에 반영하는 순서입니다. 결과 폴더(태그)는 `gpt-4o-mini-script-split-v2`(2026년), `-2025`, `-2024` 세 개이고, 처음 한 번만 아래 준비(백업·비우기·마이그레이션)를 하고 태그마다 반복합니다(10/8 적용).

```bash
# [처음 한 번] 이미 비정형 테이블이 있는 DB를 새 스키마로 전환
uv run python src/db/backup_tables.py --tables restaurants menus restaurant_tags video_restaurant_notes restaurant_chunks   # 백업
# 비정형 4개 테이블 비우기: reset_notes_tables.sql 과 같은 내용 (restaurants.category_*, chef_* 도 초기화)
# migrate_split_schema.sql -> migrate_price_columns.sql -> migrate_chef_chunk.sql -> create_scope_views.sql 적용

# [태그마다 반복] TAG=gpt-4o-mini-script-split-v2 (또는 -2025, -2024)
uv run python src/db/load_notes.py --model $TAG
uv run python src/db/load_chefs.py --notes-tag $TAG
uv run python src/pipeline/fill_michelin.py --only-notes $TAG        # Parse 호출(하루 100회 한도, 결과는 CSV에 저장)
uv run python src/db/load_restaurants.py data/restaurants_info.xlsx   # 미슐랭 결과를 DB에 반영(가격대도 data/restaurant_prices_sample.csv가 있으면 같이 반영, 새로 매칭한 식당은 Text Search가 가격대를 같이 받아 별도 조회 불필요)
uv run python src/pipeline/enrich_places_details.py --prices --skip-regions --notes-tag $TAG      # (3번 이전에 매칭된 식당만) 가게 수만큼 Places Details 호출 (Enterprise SKU 필드, 호출당 약 $0.02)
uv run python src/db/load_prices.py                                 # Details로 따로 받은 가격대 반영(load_restaurants.py도 같은 CSV를 반영하므로 보통 불필요)
uv run python src/db/embed_chunks.py
uv run python src/db/test_rag.py                                      # 검색 확인
```

- 2024년 결과는 대상 목록이 달라 추출 때 `TARGETS_FILE=data/urls/transcript_targets_20240101.txt`가 필요합니다(적재 명령에는 필요 없음).
- 추출 후 `postprocess_split.py --tag $TAG`를 먼저 실행합니다(음식 분류 다시 정하기 포함). 영상 ID가 `-`로 시작하면 `--video-id=ID` 형태로 넘깁니다.
- `load_notes.py`·`load_chefs.py`·`embed_chunks.py`는 여러 번 실행해도 중복되지 않습니다. `fill_michelin.py`는 미조회 식당만 호출합니다.

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
- [x] 자막 추출 품질 개선 실험(A/B 분리 추출로 대체되어 DB에는 적용하지 않음): 현재 적재된 v2는 맛 표현의 40%가 자막 문장 그대로이고 팁을 지어내며, 자동 생성 자막 영상은 메뉴가 평균 3.5개(제작자 자막은 6.1개)로 누락이 많습니다. `compare_prompt_v3.py`로 3개 영상(르 퀸시, 레 프레 드 외제니, 비움)에서 비교한 결과 v4 프롬프트가 구어체를 정리하고 지어낸 팁을 없앴으며, 구간 분할(6분 단위)이 누락을 크게 줄이지만 중복을 만들어 v5(LLM 중복 제거+범주 이름·같은 시각 규칙)로 막았습니다. 제작자 자막으로 이미 잘 나오는 영상(비움)에서는 구간 분할이 반찬 같은 설명 없는 항목만 늘려서 자동 생성 자막 영상에만 적용하는 방안을 검토 중입니다. 3개 영상만 확인했고 전체 적용 전 추가 검증이 필요합니다. 2단계 추출(`extract_script_two_stage.py`)도 시험했으나 시각·누락 보완은 좋아지는 반면 요리별 설명이 많이 비고 같이 나온 요리의 평가가 섞여서, 자막만으로는 한계가 있어 하이브리드(영상+자막) 재검토가 필요합니다
- [x] A/B 분리 추출(10/7 개발, 10/8 DB 적용: 51곳, 메뉴 462, 청크 430): 한 번에 많은 필드를 뽑는 부담을 줄이려고 가게 정보(A: 컨셉·분류·분위기·키포인트)와 메뉴·음료(B)를 두 호출로 나눴습니다(`extract_script_split.py`). 코스 판단(`detect_course`)을 먼저 하고 B에 알려 코스면 요리마다 `course_stage`(아뮤즈부쉬, 식전빵, 전채, 수프, 생선, 메인, 치즈, 프리디저트, 디저트, 프티푸르)를 적으며, 셰프는 `chefs_for_store`를 별도 호출로 써서 대표 셰프(오너·총괄)만 남깁니다. 모든 호출은 가게별 자막 구간(`video_parts`)만 입력으로 받아 가게가 여러 곳인 영상에서 요리가 섞이는 문제를 막았습니다. 메뉴 설명 칸은 `cooking_features` 하나로 합쳤고(`taste_review`·`tips`·`price` 제거) 재료·조리법·맛 묘사·구체적인 평가를 요약해서 담습니다. 2026년 수동 자막 영상 34개(49곳, ID는 `data/urls/split_targets_2026_manual.txt`)를 실행했습니다(약 $0.15). 기존 v2와 비교(49곳)하면 메뉴가 5.5→7.4개, 설명 길이 23→48자, 키포인트 2.5→3.2개로 늘었고 비용은 가게당 $0.0019→$0.0031, 시간은 8.4→16.4초입니다. 이후 음식 분류만 별도 호출로 다시 정했고(`postprocess_split.py`, 한국 식당과 스페인 타파스 바가 중식으로 잘못 분류된 12곳 등 15곳이 바뀜, 이전 값은 `cuisine_before`) 후처리했습니다(`postprocess_split.py`: 평가어·가격 문장 정리, 코스 단계 보정, 음료가 메뉴로 들어간 1건과 식당 이름이 셰프로 들어간 2건 제거). 평가어는 근거 없이 평가만 있는 문장만 제거하는 기준이고, 남은 "식감이 좋다"·"맛있는 버터" 같은 표현(18개)은 확인 후 그대로 쓰기로 했다. 남은 문제: 코스 단계가 `메인`에 몰리고 일부 오분류라 필터가 아닌 참고용으로만 쓸 것, 설명이 없는 메뉴 66개(18%)는 실제로 먹었는지 미확인, 정답 기준이 없어 환각·누락은 판단하지 못했고 자동 자막 영상(55개)에는 적용해 보지 않았습니다. 기존 자막 방식 결과(165곳)는 DB에서 삭제하고 이 결과 51곳만 적재했습니다(백업 `data/backup_20261008_100934/`)
- [x] 가격대(Places priceLevel/priceRange): 51곳 샘플로 41곳(80%)에 값이 있는 것을 확인하고 `restaurants`에 적재. 노트가 없는 식당은 호출하지 않음
- [x] 수동 자막 영상 확대(10/8): 200개 영상 중 제작자 수동 한국어 자막은 126개(2024년 51·2025년 38·2026년 37), 자동 생성 72개, 자막 없음 2개입니다. 2025년 수동 자막 32개 영상 34곳(중국 본토 6개 영상 제외, ID `data/urls/manual_2025_ids.txt`)과 2024년 40개 영상 41곳(`data/urls/manual_2024_ready_ids.txt`, 가게 정보 없음 7·중국 4 제외)을 같은 A/B 분리 추출로 처리해 적재했습니다(결과 `gpt-4o-mini-script-split-v2-2025`·`-2024`, 각 약 $0.12). 미슐랭은 Parse 결과를 근거로 하고(영상 언급 아님) 철자 차이로 매칭이 실패한 아켈라레는 수동 반영했습니다. 식당 이름이 채널명("비밀이야")이나 일반 명칭으로 들어간 3건은 영상 제목 기준으로 고쳤고(라미띠에·뜨락·마틴 베라사테기), 소개란에 상호가 없으면 제목에서 찾도록 프롬프트에 규칙을 넣었습니다. Places가 건물로 잘못 매칭한 미츠이 2호관은 ACA(la cocina de acá)로 다시 매칭했습니다. 사용자가 제외한 4곳(파라다이스 X Les Amis, 순환수산, 광해호수산, 미가키쇼 나가야마)은 DB와 CSV에서 지웠습니다. 조회 범위는 51 → 120곳입니다. 아직 하지 않은 것: 자동 생성 자막 72개 영상, 소개란에 가게 정보가 없는 영상(꼼모아 강남 `nyKPQqu6674` 등), 환각·누락 검증
- [x] CHEF 청크: 셰프 경력 질문 순위 개선("흑백요리사에 나온 셰프" 질문에서 팔선 8위 -> 1위)
- [x] 검색 모듈 `rag_search.py`: LLM이 질문 충족 여부를 판단해 맞는 결과가 없으면 "없음", 홍콩처럼 음식 스타일로 쓰이는 지명은 country_code가 아니라 카테고리·태그로 판단
- [x] 조회 범위 뷰 `*_in_scope`(현재 120곳)와 챗봇 전용 계정 스크립트 `create_chatbot_role.sql`: 만들어 두었으나 챗봇은 10/13부터 쓰지 않고 원본 테이블을 읽기 전용 계정으로 조회
- [x] 자동 생성 자막 영상 Gemini 영상 분석(10/13): 2026년 자동 자막 영상 중 CSV에 Google 매칭 식당이 있는 30개 영상의 43곳을 `extract_video_split.py`로 추출·후처리·적재했습니다(태그 `gemini-3.5-flash-lite-split-v1`, 자막은 쓰지 않음, 입력 약 298만+검증 3개 영상 토큰, 영상 535분+검증 49분). 자막 방식(A/B 분리)과 같은 필드·같은 JSON 구조라 `postprocess_split.py --tag`, `load_notes.py --model`, `embed_chunks.py`, `load_chefs.py --notes-tag`를 그대로 썼습니다. 셰프도 같은 호출에서 영상으로 뽑고(확신도는 제목·소개란에 이름이 있으면 high, 아니면 medium) 밑반찬은 메뉴가 아니라 key_points에 넣으며 메뉴가 0개인 가게는 제외합니다(`_excluded`). 메뉴 시각은 `menus.mentioned_sec` 하나로 통합했습니다(`migrate_merge_menu_time.sql` 적용 완료, `first_appearance_sec` 삭제). 사용자 확인으로 제외한 3곳(MASSIMO MORDI e FUGGI·카페 길리·Perché no!)은 CSV/xlsx(백업 `restaurants_info_before_exclude_3stores.csv`)와 DB에서 지웠고, 포항물횟집·이방인횟집은 기타/해산물로 수동 통일했습니다. 적재 결과(DB 전체): 노트 164, 메뉴 음식 1,173·음료 272, 청크 OVERVIEW 164·FOOD 1,011·DRINK 190·CHEF 47, 이 43곳 중 셰프 13곳·가격대 32곳(Google에 값 없는 곳 제외)·미슐랭 전부 조회, 조회 범위 뷰 159곳. 한계: 메뉴 환각·누락을 전수 검증하지 못했고(메뉴 2개 이하 5곳은 확인해 큰 누락 없음), 은해갈치 왕특대조림은 영상 분석에서 빠지고 찌개와 시각이 뒤바뀌어 있어 해당 구간만 Gemini에 다시 질의해 고쳤고(조림 08:35, 찌개 09:44), 체리 가든 셰프 '페이'는 사용자가 맞다고 확인했으며, 후처리 분류에는 대분류-세부 분류 일관성 검사가 없습니다(포항물횟집이 중식/해산물로 나온 사례). 남은 자동 자막 영상은 2025년 23개·2024년 11개이고 아직 처리하지 않았습니다.
- [x] 챗봇 평가셋 재측정(10/13, 영상 분석 43곳 적재 후, `--no-geo`): 42/43(98%). 실패 `taste-french-fine`(프랑스 파인다이닝에 스테이크하우스·이탈리안이 섞임)은 실행마다 달라지는 편차(4번 중 2번 실패)이고, `none-jokbal`은 띵하우에 '족발' 태그가 생겨 낡은 케이스가 됐습니다(교체 필요).
- [ ] 남은 자동 생성 자막 영상(2025년 23개·2024년 11개) Gemini 영상 분석 (건수·비용 안내 후 샘플부터)
- [x] LangGraph 챗봇 1차 구현(10/13): gpt-4o-mini ReAct 에이전트 + 도구 4개(SQL 필터, SQL 후 벡터 검색, 식당 상세, 좌표 조회). `uv run python src/chatbot/cli.py --trace "질문"` 또는 `uv run langgraph dev`. 질문 8개로 수동 확인했고 자동 평가셋·대화 기억 검증은 아직 없음
- [x] 챗봇 평가셋(10/13): `src/chatbot/eval/` 41문항(정형 SQL 19·취향 RAG 12·없는 음식 5·식당 조회 3·잡담 2). 정답은 DB에서 SQL로 계산하고 도구 선택·결과 정확도·근거 없는 식당 언급을 점검한다. `uv run python src/chatbot/eval/run_eval.py [--ids ...] [--kind taste]`, 결과는 `data/eval/`. 첫 실행 51% -> 라우터·도구 강제 호출·국가 추측 금지·`city` 자동 좌표 조회·청크 종류별 후보·판단 프롬프트 보강으로 41/41(3회 연속, 고친 질문으로 잰 점수)
- [x] Streamlit 챗봇 UI(10/13): `uv run streamlit run src/streamlit_app.py`. 토큰 단위 스트리밍, 도구 호출 진행 표시, 대화 기억(thread_id), 예시 질문·새 대화 버튼. 추천 식당은 맛집 앱 스타일 카드로 표시(영상 썸네일, 미슐랭 배지, 평점·가격대, 셰프, 영상·지도 버튼, 검색에 걸린 메뉴 설명). 카드는 LLM 글이 아니라 도구 결과(JSON)로 그리고(`src/chatbot/cards.py`), 그래프는 `build_graph(cards=True)`로 본문을 요약·추천 이유만 쓰게 한다. 테마는 `.streamlit/config.toml`
- [x] 추천 결과 지도(10/13): 카드 위에 번호 마커 지도(Leaflet + OpenStreetMap, `src/chatbot/maps.py`), 마커 말풍선에 이름·평점·미슐랭·영상/지도 링크. 상세 팝업에도 그 식당 위치 지도
- [x] 식당 상세 팝업(10/13): 카드의 "상세 정보 보기"를 누르면 `st.dialog`로 한 식당의 정보를 한눈에 보여 준다(`src/chatbot/detail.py` + `streamlit_app.py`). 영상 속 메뉴 타임라인(앱 안에서 유튜브 영상을 바로 재생하고, 메뉴를 누르면 그 시각으로 이동해 재생하며 재생 중인 메뉴를 강조. 유튜브 IFrame API를 `components.html`로 임베드, 임베드가 막힌 영상은 "YouTube에서 보기" 링크로 대체. 코스 단계·조리 특징 설명), 영상 소개(컨셉·분위기·키포인트·총평), 음료, 기본 정보(주소·전화·가격·영업시간·웹사이트·지도). 메뉴별 맛 후기는 저장하지 않아 조리 특징만 보여 준다
- [ ] n8n 자동화

## 알려진 이슈 / TODO

- 자막 수집은 `get_transcripts_since.py`만 씁니다. 기존 Playwright 방식(`get_transcripts.py`)은 YouTube가 자막 패널 구조를 바꾸고 제작자가 영어 자막만 올린 영상은 패널이 영어로 열려서 삭제했습니다. `save_scripts.py`는 쓰지 않으며, `extract_transcript_notes.py`는 `extract_video_notes.py`가 `TranscriptNotes` 스키마를 import해서 남겨 둡니다.
- 추출 방식은 **자막 분석(스크립트)으로 정했습니다.** 초기에 영상 / 자막 / 하이브리드를 비교했고(5개 영상 10곳, `data/compare/report.md`) 영상 분석은 조리 특징·가격이 더 낫지만 비용과 시간이 커서, 지금은 제작자 자막 영상을 자막으로 처리합니다. 자동 생성 자막은 고유명사 오타와 메뉴 누락이 많아서 **이 영상들에 한해 Gemini 영상 분석(`extract_video_split.py`)을 씁니다**(2026년 분 완료, 2025·2024년 34개 남음). 정답 기준이 없어 환각·누락은 아직 판단하지 못했습니다.
- 자막 수집 대상은 `data/urls/transcript_targets_20250101.txt`(121개)입니다. 1단계 필터(소개란에 '가게'/'장소')는 이 채널 소개란 템플릿 때문에 호텔 후기도 통과시켜서, 가게와 `google_cid`가 없는 영상 14개는 `transcript_excluded_20250101.txt`로 뺐습니다. 그중 `G4gG5SqhfE8`(홍콩 광둥요리 2곳 비교)와 CSV에 행이 없는 9개 영상은 가게 추출이 빠진 것일 수 있어 확인이 필요합니다.
- 지역 매핑: `locality`가 구 단위인 나라(예: 일본 도쿄)는 "시" 자리에 구가 들어갑니다. 규칙을 바꾸면 Details를 다시 호출해 재생성합니다(식당당 1회, 223곳이면 약 3분). 원본 `addressComponents`는 용량 때문에 저장하지 않습니다. 해외 지역명은 Google이 한국어 이름을 주지 않아 영문·현지 표기(Paris, Sapporo, Bei Jing Shi 등)로 저장되고, 한국어 번역 컬럼은 만들지 않습니다(LLM 번역에 오류가 많았음). 해외 지명 질문("파리", "삿포로")은 챗봇이 질문 시점에 Places로 좌표를 조회해 반경 검색으로 처리합니다. 근처 역 정보는 저장하지 않으며, 좌표 반경 검색으로 대신합니다.
- 미슐랭: 판별은 Parse API가 기본입니다. 이름과 도시가 일치하는 후보만 채택하고 등급은 API의 `distinction`을 씁니다. 기존 Tavily 방식은 이름만 검색해 다른 도시 지점이 섞였고 실행마다 결과가 달라 `--michelin-fallback`일 때만 씁니다. API 결과가 없으면 `NONE`이라 이름 표기가 달라 놓치는 경우(특히 한국어 상호)가 있을 수 있습니다. `source_urls`는 사람이 검증할 때만 쓰므로 CSV/xlsx에만 남기고 DB에는 적재하지 않습니다. 판정은 `michelin_status`를 기준으로 합니다.
- 중국 제외는 LLM이 추정한 `country_code`가 `CN`인지로 판별하므로, 중국 식당을 다른 국가로 표기했거나 국가코드가 빈 행은 걸러지지 않습니다. 이미 DB에 적재된 중국 식당은 따로 삭제해야 합니다(적재 스크립트는 삭제하지 않음).
- Places 매칭은 후보 5개를 받아 LLM이 소개란의 이름·주소와 대조해 고르고, 외국 식당은 현지어 이름 추정으로 2차 검색합니다(식당당 Places 호출 최대 2회). 같은 주소의 다른 이름 등 일부는 여전히 틀릴 수 있어 미슐랭 후보는 매칭부터 사람이 확인합니다.
