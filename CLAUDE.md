# sesac-final-project

미식 유튜브 영상(약 200개)의 소개란과 영상 본편(Gemini 분석)에서 식당 정보를 추출해 DB에 저장하고, 이를 기반으로 맛집을 추천하는 RAG 챗봇. 국내·해외 식당 모두 대상.

프로젝트 구조, 파이프라인 단계, DB 스키마, 실행 방법은 `README.md`에 정리되어 있으니 작업 전에 먼저 읽는다. 이 파일에는 README에 없는 작업 규칙만 적는다.

## 스택
Python 3.14 + uv, LangGraph/LangChain, LangSmith, OpenAI(`gpt-4o-mini`), Google Places API (New), Tavily, PostgreSQL(+pgvector 선택), n8n(예정).
패키지는 `uv add`로 추가하고 실행은 `uv run python ...`을 쓴다.

## 아키텍처 원칙
- 정형 데이터(영업시간, 위치, 평점, 미슐랭 등)는 PostgreSQL + SQL search, 비정형 데이터(분위기, 음식 스타일, 메뉴, 영상 속 특징)는 Vector DB + RAG search로 분리한다.
- 챗봇 흐름은 SQL로 조건(지역, 영업 여부, 미슐랭 등)을 먼저 좁히고, 벡터 검색으로 취향에 맞는 식당을 고르는 순서다.

## 지켜야 할 규칙
- `.env`는 열어 보거나 출력하지 않는다. API 키를 코드, 로그, 커밋, 채팅에 노출하지 않는다. 새 환경변수가 필요하면 README의 환경변수 표에 이름만 추가한다.
- `data/`는 git 제외 대상이다. 산출물을 커밋하지 않는다.
- 외부 API(OpenAI, Places, Tavily)를 대량 호출하기 전에 대상 건수와 예상 비용을 먼저 알려 주고, 소수 샘플로 검증한 뒤 전체를 돌린다.
- 배치 작업은 건별로 결과를 저장하고, 중단 후 재실행하면 이어서 처리되게 만든다. (기존 방식: `save_scripts.py`의 `_progress.json`, `extract_restaurant_info.py`의 건별 CSV 기록)
- DB 적재는 여러 번 실행해도 중복되지 않아야 한다. 식당 중복 제거 기준은 `google_cid`이며, 부호 없는 64bit 정수라 BIGINT 범위를 넘을 수 있으므로 문자열로 저장한다.
- `google_open_now`는 저장하지 않는다. 영업 여부는 `restaurant_hours`와 현지 시간으로 계산한다.
- 결과 CSV는 22개 컬럼이다(미슐랭 연도별 이력 `history`는 저장하지 않는다). 음식 분류(Google 카테고리·타입·소개글)와 가격대는 저장하지 않고 Gemini 영상 분석 결과를 쓴다. `michelin_status`에는 `edition_type`·`is_active`를 두지 않는다(Parse API에서는 `is_michelin`과 같은 값이라 정보가 없다). Places 요청에서는 유형(primaryType)을 계속 받는다(`choose_place`의 후보 선택과 숙소 판별에 필요). 숙소(호텔·료칸)로 매칭된 장소는 영상 제목에 식당·식사·요리 관련 단어가 없으면 제외하고, 같은 영상의 식당 행과 300m 이내인 숙소 행도 제외한다(`has_food_title`, `drop_redundant_lodging`).
- 미슐랭 판별은 근거가 없는 연도를 채우지 않는다. 결과 검증 시 판정 기준은 `michelin_status`이다.
- 코드와 식별자는 영어, 설명과 주석은 한국어로 작성한다.

## Parse (Michelin Guide API)
- `parse-sdk`로 guide.michelin.com API를 호출한다. 키는 `.env`의 `MICHELIN_GUIDE_API_KEY`이고, SDK가 읽는 `PARSE_API_KEY`로는 코드에서 옮겨 준다(`src/pipeline/michelin_api_probe.py` 참고).
- 사용: `from parse_apis.guide_michelin_com_api import MichelinGuide` 후 `client.restaurants.search(query=..., limit=...)`. 호출마다 크레딧을 쓰므로 `limit`을 항상 지정한다. 무료 플랜은 월 200크레딧, 분당 5회, **하루 100회**(`Daily request cap reached`)다. 그래서 식당 정보 추출은 `--skip-michelin`으로 돌리고 `fill_michelin.py`로 하루 한도만큼씩 채운다(기본 90회, 같은 식당은 1회만 호출).
- `parse_apis/`는 `parse add`로 생성한 코드이고 생성물은 `.gitignore` 대상이다. 새로 받았으면 `uv run parse init`, `uv run parse add --marketplace guide-michelin-com-api`를 다시 실행한다.
- `guide_michelin_com_api_fork`와 `guide_michelin_com_api` 두 모듈이 생겼으나 `guide_michelin_com_api`만 쓴다.
- 반환되는 distinction은 현재 등급뿐이고 연도별 이력은 확인되지 않아 `michelin_records` 테이블은 삭제했다(`migrate_simplify_schema.sql`).

## 현재 진행 상황
완료: 영상 URL 수집·필터링, 식당 정보 추출(Places, 미슐랭), DB 스키마와 적재 스크립트. 자막 수집은 끝났으나 Gemini 영상 분석으로 대체되어 더 이상 쓰지 않는다.
완료(200개 영상 223곳 실행, `data/restaurant_regions.csv`): 지역(`restaurants.region_1~3`, 시>구/군>동·면 최대 3단계, 없는 단계는 건너뛰고 있는 데까지, 도로명 제외) 스키마와 `enrich_regions.py`(Places Details, 건별 CSV 저장). DB(Supabase 추정)에 `migrate_regions.sql`, `migrate_trim_columns.sql`을 적용하고(10/2) 200개 영상 결과를 적재했다: 식당 224, 영상 185, 영업시간 1,803행, regions 165(식당 224곳 연결). 이후 `regions` 테이블을 `restaurants.region_1~3` 컬럼으로 바꿨으니 DB에 `migrate_simplify_schema.sql`(regions 컬럼화, menus·michelin·restaurant_hours 불필요 컬럼 삭제)을 적용한다. 미슐랭은 89곳만 적재됐으니 `fill_michelin.py`로 135곳을 채운 뒤 `load_restaurants.py`를 다시 실행한다. 메뉴 21행·청크 19행은 이전 테스트 데이터이고, `load_notes.py`/`embed_chunks.py`(영상 분석 결과 적재)는 아직 실행하지 않았다. 해외 지역명은 영문·현지 표기로 저장되고 한국어 번역 컬럼은 만들지 않는다(LLM 번역 오류가 많았음). 해외 지명 질문은 챗봇이 질문 시점에 좌표를 조회해 반경 검색으로 처리한다. 근처 역은 저장하지 않는다("OO역/OO 근처" 질문은 질문 시점에 지명 좌표를 조회해 `restaurants`의 위경도로 반경 검색).
완료(4개 영상으로 검증 단계): Gemini 영상 분석으로 메뉴·분위기·분류·태그 추출(`extract_video_notes.py`, 가게 단위 호출), 적재 스크립트(`load_notes.py`, `embed_chunks.py`) 작성. 영상 1개를 한 번 호출로 분석하며 `--limit N`은 처리 안 된 영상 N개씩 이어서 처리한다(연속 3번 실패하면 중단). 200개 영상 중 5개를 새 프롬프트로 분석해 DB에 적재·임베딩하고 RAG 검색을 확인했다(영상당 약 $0.027, 20초). 나머지 169개 영상은 아직이다. DB의 `video_restaurant_notes` 44건 중 38건은 이전 실험(예전 프롬프트) 결과다.
진행 예정: 전체 영상 추출·적재, LangGraph 챗봇 구현(`src/app.py`는 현재 빈 그래프), n8n 자동화.

## 알려진 이슈 (README의 TODO와 동일)
- 자막 수집 스크립트(`get_transcripts.py`, `save_scripts.py`)와 `extract_transcript_notes.py`는 Gemini 방식으로 대체되어 쓰지 않는다. 삭제하려면 `extract_video_notes.py`가 import하는 `TranscriptNotes`를 먼저 옮긴다. Gemini 추출은 4개 영상으로만 검증했으니 전체 실행 전에 자막 방식 결과와 샘플 비교가 필요하다.
- 지역 매핑: `locality`가 구 단위인 나라(일본 등)는 "시" 자리에 구가 들어간다. 규칙을 고치면 `enrich_regions.py`로 Details를 다시 호출해 재생성한다(원본 addressComponents는 저장하지 않는다).
- 미슐랭 판별은 이제 Parse API(`check_michelin_status_api`)가 기본이다. 이름과 도시가 일치하는 후보만 채택하고 등급은 API의 `distinction`을 쓴다. 기존 Tavily 방식은 이름만 검색해 다른 도시 지점이 섞였고(본앤브레드 부산을 3스타로, 썸머팰리스 홍콩 1스타를 UNKNOWN/NONE으로 오판) 실행마다 결과가 달랐다. `check_michelin_status_tavily`는 `--michelin-fallback`일 때만 쓴다. API 결과가 없으면 `NONE`이라 이름 표기가 달라 놓치는 경우(특히 한국어 상호)가 있을 수 있다. 호출 사이 약 12.5초 간격을 둔다(무료 플랜 분당 5회).
- (이전 Tavily 방식 설명) 미슐랭: `summary_badge`는 제거했고 `is_michelin`/`latest_grade`/`is_active`는 `history`와 근거 검증을 거친 `current_grade`에서 계산한다(`build_michelin_info`). 등급 근거가 없으면 `UNKNOWN`이며 사람이 확인해야 한다. 이전 방식으로 만든 기존 CSV/DB는 재추출 또는 재계산이 필요하다. `source_urls`는 사람이 검증할 때만 쓰므로 CSV/xlsx에만 남기고 DB(`michelin_sources`)에는 적재하지 않는다. 다른 지역 식당을 가리키는 건이 있을 수 있다.
- Places 매칭: 후보 5개를 받아 LLM이 고르고(`choose_place`), 외국 식당은 현지어 이름 추정(`guess_local_name`)으로 2차 검색한다. 그래도 같은 주소의 다른 이름 등은 틀릴 수 있어 미슐랭 후보는 매칭부터 사람이 확인한다. 검색 호출이 식당당 최대 2회로 늘었다.

진행 상황이나 결정이 바뀌면 이 파일과 README를 함께 갱신한다.
