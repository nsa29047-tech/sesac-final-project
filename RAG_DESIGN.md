# RAG 설계 (현재 구현 기준, 2026-10-08)

2026-10-07 초안(자막 원문 + OVERVIEW 하이브리드)을 실제로 구현한 내용에 맞게 고친 문서다. 초안과 달라진 점은 5장에 정리했고, DB 정의는 `src/db/restaurant_schema.sql`·`restaurant_chunks.sql`이 기준이다(문서와 다르면 DB가 맞다). 에이전트가 읽을 안내는 `AGENT_GUIDE.md`에 있다.

## 1. 결정 사항

| 항목 | 현재 상태 | 근거·비고 |
|---|---|---|
| 대상 | 영상 분석 결과가 있는 **51곳**(영상 36개: 수동 자막 35·자동 생성 자막 1, 2026년)만 비정형 데이터가 있다 | 기존 자막 방식 결과(165곳)는 DB에서 삭제(백업 `data/backup_20261008_100934/`). 자동 생성 자막 영상은 대부분 보류(1개만 포함) |
| 추출 방식 | A/B 분리 추출(`extract_script_split.py`): 가게 정보(A)와 메뉴·음료(B)를 따로 호출, 코스 판단·셰프는 별도 호출 | 한 번에 많은 필드를 뽑는 부담을 줄이고 설명 품질을 높임(메뉴 5.5→7.4개, 설명 23→48자) |
| 정형 데이터 | `restaurants`(Places 정보·지역·셰프·**가격대**), `restaurant_hours`, `michelin_status` | 가격대는 Places `priceLevel`/`priceRange`를 51곳 샘플로 확인(80% 채워짐) 후 적재 |
| 미슐랭 | 51곳은 모두 조회 완료(`fill_michelin.py --only-notes`). 나머지 123곳은 미조회 | 하루 100회, 월 200크레딧 한도 |
| 벡터 저장 | `restaurant_chunks` 4종: **OVERVIEW**(식당당 1) / **FOOD** / **DRINK**(메뉴별) / **CHEF**(셰프 이름·경력만) | 임베딩 `text-embedding-3-small`(1536차원), 청크 430개 |
| 검색 | SQL 필터 -> 벡터 후보 8개 -> LLM(gpt-4o-mini)이 질문 충족 여부 판단. 맞는 결과가 없으면 "없음" | 거리 임계값만으로는 갈리지 않았다("딤섬" 0.74, 없는 "일본 오마카세 스시" 0.62). `src/db/rag_search.py` |
| 조회 범위 | 챗봇은 원본 대신 뷰 `*_in_scope`(51곳)만 조회 | 원본에는 영상 분석이 없는 식당 173곳이 남아 있다 |

## 2. 파이프라인 (실제 스크립트)

1. **식당 추출** `extract_restaurant_info.py --skip-michelin`: 소개란 -> 상호·주소 -> Places. 결과는 `data/restaurants_info.csv`.
2. **미슐랭** `fill_michelin.py [--only-notes 태그]`: Parse API로 하루 한도만큼씩.
3. **자막 수집** `get_transcripts_since.py`: 시각이 붙은 자막 `data/transcripts/{video_id}.json`.
4. **정보 추출** `extract_script_split.py`: 가게별 자막 구간만 입력으로 A(컨셉·분류·분위기·키포인트·총평), B(메뉴·음료·코스 단계), 코스 판단, 대표 셰프를 뽑는다. 결과는 `data/video_notes/gpt-4o-mini-script-split-v2/`.
5. **후처리** `reclassify_split_cuisine.py`(음식 분류 재판정), `postprocess_split.py`(근거 없는 평가어·가격 문장 제거, 코스 단계 보정, 음료 중복·식당 이름 셰프 제거). 원본은 `*_raw` 폴더에 보관.
6. **가격대** `fetch_price_level.py` -> `load_prices.py`.
7. **적재** `load_restaurants.py` -> `load_notes.py --model gpt-4o-mini-script-split-v2` -> `load_chefs.py --notes-tag ...` -> `embed_chunks.py`. 모두 upsert라 여러 번 실행해도 중복되지 않는다(`google_cid` 기준).
8. **조회 범위** `create_scope_views.sql`(뷰), `create_chatbot_role.sql`(챗봇 전용 계정, 비밀번호를 채워 직접 실행).

## 3. 스키마 (현재)

핵심만 정리한다. 전체 정의는 `restaurant_schema.sql`.

- `restaurants`: Places 정보(`name_ko`, `name_official`, `formatted_address`, `latitude`, `longitude`, `rating`, `rating_count`, `business_status`, `country_code`), 지역 `region_1~3`, 음식 분류 `category_broad/detail`(영상 분석 결과), 셰프 `chef_name`/`chef_info`, **가격** `price_level`(0 무료 ~ 4 매우 비쌈, 없으면 NULL)·`price_min`·`price_max`·`price_currency`.
- `restaurant_hours`: 요일(0=일~6=토)별 영업 구간. 휴무는 시간이 NULL.
- `michelin_status`: `is_michelin`, `latest_grade`(NONE/BIB_GOURMAND/1_STAR/2_STARS/3_STARS/SELECTED/UNKNOWN).
- `menus`: `item_type`(FOOD/DRINK), `name`, **`description`**, `evidence`, `menu_order`(영상에서 나온 순서), `course_stage`(코스 식당의 음식만), `mentioned_sec`(자막에서 추정한 시각), `first_appearance_sec`. 맛 평가·팁·가격은 저장하지 않는다.
- `video_restaurant_notes`: `concept`, `atmosphere`, `key_points`, `final_review`, `embedding_text`, **`is_course`**, **`course_name`**, 추출 원본 `raw`.
- `restaurant_tags`: 음식 태그. `videos`, `video_restaurant_mentions`.
- `restaurant_chunks`: `chunk_type`(OVERVIEW/FOOD/DRINK/CHEF), `chunk_key`('overview'/'chef'/메뉴명), `content`, `embedding`. FOOD/DRINK/CHEF 청크 content에는 식당 이름을 넣지 않는다(식당은 `restaurant_id`로 정한다).
- 뷰: `restaurants_in_scope`, `restaurant_hours_in_scope`, `michelin_status_in_scope`.

## 4. 챗봇 검색 흐름

1. 질문에서 지역·영업 여부·미슐랭·가격대 조건을 뽑아 SQL로 식당 후보를 좁힌다(뷰만 사용).
2. 좁힌 식당의 청크를 벡터 검색하고 `rag_search.search()`가 LLM으로 질문 충족 여부를 확인한다. 청크 종류는 질문에 맞게 고른다(메뉴·음식 질문은 FOOD, 음료는 DRINK, 셰프 질문은 CHEF, 분위기·컨셉은 OVERVIEW).
3. 결과가 하나도 없으면 "조건에 맞는 식당이 없다"고 답한다.
4. 답변은 DB 정형 정보(영업시간·평점·가격대·미슐랭), `video_restaurant_notes`의 총평·컨셉·분위기·키포인트, 검색된 청크로 쓴다. "OO 식당 메뉴" 질문은 `menus`를 조회한다.
5. 지명이 음식 스타일로 쓰이는 말(홍콩)은 `country_code`가 아니라 카테고리·태그로 거른다(`rag_search.STYLE_PLACES`). 해외 지명 질문은 질문 시점에 좌표를 조회해 `latitude`/`longitude` 반경으로 검색한다.
6. 코스 식당은 `video_restaurant_notes.is_course`로 거른다. `menus.course_stage`는 정확도가 낮아 표시용으로만 쓴다.
7. "지금 영업 중?"은 `restaurant_hours`와 현지 시간으로 계산한다(`google_open_now`는 저장하지 않는다).

## 5. 초안(10/7)과 달라진 점

| 초안 | 현재 | 이유 |
|---|---|---|
| 자막 원문 500자 `TRANSCRIPT` 청크 + OVERVIEW만 임베딩, FOOD/DRINK 청크는 만들지 않음 | **TRANSCRIPT 청크는 구현하지 않았다.** OVERVIEW + FOOD + DRINK + CHEF를 임베딩 | 초안의 비교 실험(21개 질문)은 v2 결과 기준이었고, 메뉴 설명을 개선한 A/B 결과에서는 FOOD/DRINK 청크가 메뉴 질문에 잘 맞았다. 원문 청크는 필요하면 추가할 선택지로 남겨 둔다 |
| 검색 합치기: 식당 단위 RRF(k=60) | 청크 종류별 검색 후 LLM 충족 판단 | 점수 분포가 달라 합치기 어렵고, "없음" 판단이 필요했다 |
| 가격 컬럼: `price_level VARCHAR`, `price_range_min/max` | **`price_level SMALLINT`(0~4), `price_min`, `price_max`, `price_currency`** | SQL 비교(`price_level <= 2`)를 위해 숫자로 저장. 통화가 섞여 있어 비교는 `price_level`로 한다 |
| 코스: `menus.course_label`, `serve_order` | **`menus.course_stage`, `menus.menu_order`, `video_restaurant_notes.is_course`/`course_name`** | 코스 묶음 이름 대신 단계(아뮤즈부쉬~프티푸르)와 순서를 저장 |
| 정보 추출: `extract_script_course.py` | `extract_script_split.py` + 후처리 | A/B 분리가 설명 품질이 좋았다 |
| 셰프: OVERVIEW 청크 끝에 합침 | **짧은 CHEF 청크로 분리** | 개요가 길어 경력 질문 순위가 낮았다("흑백요리사에 나온 셰프"에서 팔선 8위 -> 1위) |
| 챗봇이 원본 테이블 조회 | **뷰만 조회** | 노트 없는 식당 173곳이 섞이지 않게 |

초안의 비교 결과(21개 질문, 식당 49곳, 1위 / 3위 이내 / 평균순위): 원문 11/15/4.0, OVERVIEW 13/19/3.0, 원문+OVERVIEW 16/19/2.4, 원문+OVERVIEW+FOOD·DRINK 16/19/1.7. 질문은 직접 만든 소규모 시험이다.

## 6. 위험과 한계

- **대상이 51곳뿐**이다. 자동 생성 자막 영상과 나머지 영상은 아직 추출하지 않았다.
- **환각·누락**은 검증하지 못했다(정답 기준 없음). 추출 결과는 영상에서 한 말이라 검증된 사실이 아니다(셰프 경력 포함).
- **모호한 질문**("가볍고 저렴한 한 끼", "분위기 좋은 곳")은 필터가 핵심이고 LLM 충족 판단이 느슨하다. 가격·영업 조건은 SQL로 처리한다.
- **짧은 질문**의 벡터 거리는 길게 나온다("딤섬" 0.74). 거리로 자르지 말고 LLM 충족 판단을 쓴다(`MAX_DISTANCE` 0.70은 아주 먼 후보만 버린다).
- **코스 단계** `course_stage`는 정확도가 낮아 참고용이다(필터 금지).
- **`price_level`**은 Places 값이라 일부 식당은 NULL이다(51곳 중 10곳).
- **미슐랭**은 51곳만 채웠다. 이름 표기 차이로 놓치는 경우가 있을 수 있다(특히 한국어 상호).
- **홍콩 같은 스타일 키워드**는 카테고리·태그에 키워드가 있는 식당만 잡는다(Wai Lung Seafood처럼 "해산물 전문점"만 있으면 빠진다).
- 모델을 `text-embedding-3-small`(1536차원)로 통일한다. 청크 크기나 모델을 바꾸면 전체를 다시 임베딩한다.

## 7. 다음 단계

1. 챗봇 에이전트(LangGraph) 구현: SQL 도구(뷰), 벡터 검색 도구(`rag_search.search`), 지명 좌표 조회.
2. 챗봇 전용 DB 계정(`create_chatbot_role.sql`) 생성.
3. 자동 생성 자막 영상과 나머지 영상으로 확대(건수·비용 안내 후, 소수 샘플로 검증).
4. 필요하면 TRANSCRIPT 원문 청크와 키워드 검색(`chef_info ILIKE`, `menus.name ILIKE`)을 병행.
5. 나머지 식당의 가격대·미슐랭을 채울 때 범위를 넓힌다(뷰 정의의 `EXISTS` 조건을 빼면 전체가 보인다).
