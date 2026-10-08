# 챗봇 에이전트 안내

미식 유튜브 영상에서 뽑은 식당 정보를 기반으로 맛집을 추천하는 챗봇 에이전트가 알아야 할 내용이다. 먼저 `README.md`, `src/db/restaurant_schema.sql`, `src/db/restaurant_chunks.sql`, `src/db/rag_search.py`를 읽는다. 설계 배경은 `RAG_DESIGN.md`에 있으며, 문서와 DB가 다르면 **DB가 맞다**.

## 1. 현재 데이터 범위

- 영상 분석(메뉴·분위기·셰프·코스 등)이 있는 식당은 **51곳**이다. 영상 36개(2026년, 수동 자막 35개와 자동 생성 자막 1개)에서 뽑았다.
- DB의 `restaurants`에는 224곳이 있지만 나머지 173곳은 정형 정보(Places)만 있고 영상 분석이 없다. **이 식당은 추천하지 않는다.**
- 한국 식당과 해외 식당(홍콩, 일본, 프랑스, 스페인 등)이 섞여 있다. 중국 본토는 제외했다.

## 2. 조회 규칙 (꼭 지킨다)

1. 식당 정보는 원본 테이블이 아니라 **뷰**로만 조회한다.
   - `restaurants_in_scope` (원본 `restaurants`)
   - `restaurant_hours_in_scope` (원본 `restaurant_hours`)
   - `michelin_status_in_scope` (원본 `michelin_status`)
2. 챗봇 전용 계정 `chatbot_user`(`src/db/create_chatbot_role.sql`)로 접속한다. 이 계정은 원본 3개 테이블을 조회할 수 없고, 읽기 전용이다. `menus`, `restaurant_tags`, `video_restaurant_notes`, `restaurant_chunks`, `videos`를 조인할 때는 `restaurants_in_scope`와 `restaurant_id`로 조인한다.
3. 접속 문자열은 환경변수 `POSTGRES_URI`, OpenAI 키는 `OPENAI_API_KEY`로 읽는다. `.env`를 열어 보거나 출력하지 않고, 키를 코드·로그·답변에 쓰지 않는다.
4. 쓰기·DDL은 하지 않는다. SELECT만 쓴다.
5. SQL을 문자열로 만들 때 `LIKE '서울%'`처럼 `%`를 직접 쓰면 psycopg2 파라미터와 충돌한다. 값은 파라미터로 넘기거나 `starts_with(col, '서울')`를 쓴다.

## 3. 검색 흐름

조건은 SQL로 먼저 좁히고, 취향은 벡터 검색으로 고른다.

1. 질문에서 **지역·영업 여부·미슐랭·가격대·코스 여부**를 뽑아 SQL로 식당 후보를 좁힌다.
2. 좁힌 식당 안에서 `rag_search.search(query, where, params, chunk_type, cur, client)`로 청크를 찾는다.
   - `where`: `restaurants_in_scope`(별칭 `r`), `video_restaurant_notes`(별칭 `n`)에 대한 SQL 조건. 없으면 `"TRUE"`.
   - `chunk_type`: 메뉴·음식 질문은 `"FOOD"`, 음료는 `"DRINK"`, 셰프는 `"CHEF"`, 분위기·컨셉·식당 전반은 `"OVERVIEW"`, 모르면 `None`.
   - 반환: 가까운 순 청크 목록(`restaurant_id`, `name`, `chunk_type`, `key`, `content`, `distance`). **빈 목록이면 질문에 맞는 식당이 없다는 뜻이다.**
3. 빈 목록이면 비슷한 다른 식당을 억지로 추천하지 말고 "조건에 맞는 식당이 없다"고 답한다. 조건을 완화해 다시 제안하는 것은 좋다.
4. "OO 식당 메뉴 알려줘"는 `menus`를 조회한다(청크는 설명이 있는 메뉴만 있다).
5. 답변에는 식당 이름, 위치(`region_1~3`, 주소), 영업시간·평점·가격대·미슐랭 같은 정형 정보와, 총평·컨셉·분위기(`video_restaurant_notes`), 검색된 청크 내용을 쓴다. 청크 content에는 식당 이름이 없으니 `restaurant_id`로 `restaurants_in_scope`와 조인해서 붙인다.

### 질문 유형별 처리

| 질문 | 처리 |
|---|---|
| "지금 영업 중인 곳" | `restaurant_hours_in_scope`와 현지 시간으로 계산한다. `google_open_now`는 저장하지 않는다. 휴무는 `open_time`/`close_time`이 NULL이다. 하루 여러 구간은 `seq`로 구분하고, 자정을 넘기는 구간이 있다 |
| "서울/부산 …" | `region_1~3`(시 > 구/군 > 동·면). 국내는 한국어 지명이다 |
| "도쿄/파리/삿포로 …" | 해외 지역명은 영문·현지 표기로 저장돼 있어 한국어 지명으로 못 찾는다. 질문 시점에 지명 좌표를 조회해 `latitude`/`longitude` 반경으로 검색한다. "OO역 근처"도 같다 |
| "홍콩 …" 같은 음식 스타일 지명 | `country_code`가 아니라 카테고리·태그로 판단한다(`rag_search`가 자동 처리: `STYLE_PLACES`). 미국에 있는 홍콩식 식당도 포함된다 |
| "미슐랭 …" | `michelin_status_in_scope.latest_grade`(BIB_GOURMAND, 1_STAR, 2_STARS, 3_STARS, SELECTED, NONE, UNKNOWN). 51곳은 모두 조회했다 |
| "저렴한/비싼 곳" | `restaurants_in_scope.price_level`(0 무료 ~ 4 매우 비쌈). **NULL인 식당이 있다(51곳 중 10곳).** 통화가 섞여 있어 `price_min`/`price_max`는 답변에 "대략 2~3만원대"처럼 보여 줄 때만 쓴다 |
| "코스 요리" | `video_restaurant_notes.is_course = true`(17곳). `menus.course_stage`·`menu_order`는 코스 흐름을 보여 줄 때 참고만 한다 |
| "셰프 …" | `restaurants_in_scope.chef_name`(SQL 키워드)과 `CHEF` 청크(벡터)를 함께 쓴다. 경력(`chef_info`)은 영상에서 한 말이라 검증된 사실이 아니다. "영상에서는 …라고 소개했다"로 말한다 |
| "OO 메뉴/음식" | 설명 검색은 `FOOD` 청크, 정확한 이름은 `menus.name ILIKE`를 병행한다 |
| "와인/음료" | `DRINK` 청크 |

## 4. 테이블 요약

- `restaurants_in_scope`: `restaurant_id`, `google_cid`, `name_ko`, `name_official`, `country_code`, `region_1~3`, `formatted_address`, `latitude`, `longitude`, `rating`, `rating_count`, `business_status`, `phone`, `website`, `google_maps_url`, `category_broad`(양식/한식/중식/일식), `category_detail`, `chef_name`, `chef_info`, `price_level`, `price_min`, `price_max`, `price_currency`
- `restaurant_hours_in_scope`: `restaurant_id`, `day_of_week`(0=일~6=토), `seq`, `open_time`, `close_time`
- `michelin_status_in_scope`: `restaurant_id`, `is_michelin`, `latest_grade`
- `menus`: `restaurant_id`, `video_id`, `item_type`(FOOD/DRINK), `name`, `description`, `evidence`, `menu_order`, `course_stage`, `mentioned_sec`(자막에서 추정한 시각), `first_appearance_sec`
- `video_restaurant_notes`: `video_id`, `restaurant_id`, `concept`, `atmosphere`, `key_points`(JSON 배열), `final_review`, `is_course`, `course_name`
- `restaurant_tags`: `restaurant_id`, `tag`
- `videos`: `video_id`, `url`, `title` (답변에 영상 링크를 붙일 때: `url`, 메뉴 시각은 `url`에 `&t=초`)
- `restaurant_chunks`: `restaurant_id`, `video_id`, `chunk_type`(OVERVIEW/FOOD/DRINK/CHEF), `chunk_key`, `content`, `embedding`

## 5. 답변 원칙

- 데이터에 없는 것은 없다고 말한다. 추측으로 메뉴·가격·영업시간을 만들지 않는다.
- 추천 근거는 DB의 내용(영상에서 소개된 메뉴·분위기)에서 가져오고, 평가는 "영상에서는 …라고 소개했다"처럼 출처를 밝힌다.
- 설명이 비어 있는 메뉴(예: 반찬)는 맛이나 특징을 지어내지 않는다.
- 추출은 자막만으로 했다. 자막에 나오지 않은 메뉴는 빠져 있을 수 있고, 메뉴 이름에 자막 오타가 섞였을 수 있다(자동 생성 자막이 1개 영상 포함).

## 6. 알려진 한계

- **51곳뿐이다.** 질문에 맞는 식당이 없을 가능성이 높으니 "없다"는 답변이 정상이다.
- **짧은 질문**("딤섬", "와인")은 벡터 거리가 크게 나온다. 거리 숫자로 직접 자르지 말고 `rag_search`의 판단을 따른다.
- **모호한 질문**("가볍게 먹을 저렴한 한 끼", "분위기 좋은 곳")은 `price_level`·`region` 같은 필터가 핵심이고 검색 판단은 느슨하다. 필터를 먼저 건다.
- **코스 단계**(`course_stage`)는 정확도가 낮다. 필터로 쓰지 않는다.
- **홍콩 해산물집 Wai Lung Seafood** 같은 식당은 카테고리·태그에 홍콩 단서가 없어 "홍콩" 질문에 잡히지 않을 수 있다.
- **셰프 경력**은 검증되지 않았다.
- 미슐랭은 이름 표기 차이로 놓친 식당이 있을 수 있다(특히 한국어 상호).

## 7. 로컬에서 확인하는 방법

```bash
uv run python src/db/test_rag.py     # 12개 질문으로 검색을 확인한다(없는 음식은 "조건에 맞는 식당이 없습니다"가 나와야 한다)
```
