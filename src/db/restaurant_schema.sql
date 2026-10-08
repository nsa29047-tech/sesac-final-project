-- 맛집 RAG 챗봇 테이블 설계 v2 (PostgreSQL 16, restaurants_info.xlsx 실제 컬럼 기준)
-- 엑셀 1행 = "영상 1개 x 식당 1개" 가 비정규화된 형태 -> 아래처럼 분리한다.
--   정형(SQL search): videos / restaurants(region_1~3 포함) / restaurant_tags / restaurant_hours / michelin_* / menus
--   비정형(RAG search): video_restaurant_notes(원문) + restaurant_chunks(임베딩, restaurant_chunks.sql)

BEGIN;

-- 1. 영상 ------------------------------------------------------------------
CREATE TABLE videos (
    video_id    VARCHAR(20)  PRIMARY KEY,               -- video_url 의 v= 값
    url         VARCHAR(300) NOT NULL,
    title       VARCHAR(300) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 2. 식당 마스터 (Google Places 기준으로 중복 제거) -------------------------------
-- 주의: google_cid 는 부호없는 64bit 정수(예: 14137247191763917659)라 BIGINT 범위(9.22e18)를 넘는다.
--       -> 문자열로 저장. (가능하면 Places API 응답의 place id 도 함께 수집 권장)
CREATE TABLE restaurants (
    restaurant_id       BIGSERIAL     PRIMARY KEY,
    google_cid          VARCHAR(20)   NOT NULL UNIQUE,   -- google_maps_url 의 cid= 값
    google_maps_url     VARCHAR(500)  NOT NULL,
    google_place_id     VARCHAR(100),                    -- Places API place id (Details 재조회용)
    name_official       VARCHAR(200)  NOT NULL,          -- google_official_name
    name_ko             VARCHAR(200),                    -- 한국어 표기(영상 기준 추출명)
    country_code        CHAR(2)       NOT NULL,
    formatted_address   VARCHAR(500),                    -- google_formatted_address
    -- 지역(시 > 구/군 > 동·면, Places addressComponents 에서 추출). 없는 단계는 건너뛰고 앞에서부터 채운다
    -- (안동시는 region_1만, 도로명 주소라 동이 없으면 region_2까지). 도로명은 쓰지 않는다. 지역명은 Places 응답의 long_name(요청 언어).
    -- "OO역/OO 근처" 질문은 지역 컬럼이 아니라 latitude/longitude 로 반경 검색한다.
    region_1            VARCHAR(100),
    region_2            VARCHAR(100),
    region_3            VARCHAR(100),
    latitude            NUMERIC(9,6),                   -- Places API location ("근처 식당" 검색용)
    longitude           NUMERIC(9,6),
    rating              NUMERIC(2,1),
    rating_count        INT,
    business_status     VARCHAR(30),                     -- OPERATIONAL / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY
    phone               VARCHAR(40),
    website             VARCHAR(300),
    category_broad      VARCHAR(50),                     -- 대분류(한식/일식/중식/양식/동남아식/인도식/중동식/기타). 영상 추출값(load_notes.py)
    category_detail     VARCHAR(50),                     -- 세부 분류(오마카세/파인다이닝/라멘 ...). 영상 추출값(load_notes.py)
    chef_name           VARCHAR(300),                    -- 대표 셰프 이름(여러 명이면 쉼표로 연결). 자막 추출 + 근거 확인된 것만, 없으면 NULL (load_chefs.py)
    price_level         SMALLINT CHECK (price_level BETWEEN 0 AND 4),  -- Places priceLevel: 0 FREE ~ 4 VERY_EXPENSIVE. 없으면 NULL (load_prices.py)
    price_min           INTEGER,                         -- Places priceRange 하한(1인 기준, price_currency 단위)
    price_max           INTEGER,                         -- 상한. 파인다이닝은 하한만 있고 NULL인 경우가 많다
    price_currency      CHAR(3),                         -- ISO 4217 통화 코드. 통화가 섞여 있어 비교는 price_level 로
    chef_info           TEXT,                            -- 셰프 경력·수상·특징 요약. 영상에서 한 말이라 검증된 사실이 아니다
    places_fetched_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX idx_restaurants_country ON restaurants (country_code);
CREATE INDEX idx_restaurants_region ON restaurants (country_code, region_1, region_2, region_3);

-- 3. 영업시간 (google_opening_hours 파싱 결과) --------------------------------
-- 하루 여러 구간 가능(점심/저녁) -> seq. 휴무일은 open_time/close_time 이 NULL 인 행 1개로 표현한다
-- (행이 없는 요일은 "휴무"가 아니라 "정보 없음"이라 구분을 위해 휴무 행을 남긴다).
-- 자정을 넘기는 영업(5PM-1AM)은 close_time < open_time 으로 저장.
CREATE TABLE restaurant_hours (
    restaurant_id BIGINT   NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    day_of_week   SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),   -- 0=일 ... 6=토
    seq           SMALLINT NOT NULL DEFAULT 0,
    open_time     TIME,
    close_time    TIME,
    PRIMARY KEY (restaurant_id, day_of_week, seq),
    CONSTRAINT restaurant_hours_times_both_or_none CHECK ((open_time IS NULL) = (close_time IS NULL))   -- 둘 다 NULL 이면 휴무
);
-- google_open_now 는 조회 시점 값이라 저장하지 않는다. "지금 영업 중?"은 이 테이블 + 현지 시간으로 계산.

-- 4. 미슐랭 -----------------------------------------------------------------
-- 현재 상태(1:1). is_michelin=false 인 식당도 "확인했고 미등재"라는 사실을 남기기 위해 행을 만든다.
CREATE TABLE michelin_status (
    restaurant_id  BIGINT      PRIMARY KEY REFERENCES restaurants ON DELETE CASCADE,
    -- 등급은 Parse API(현재 등급)로 판별한다. 연도별 이력, 에디션 종류, 현재 등재 여부(is_michelin 과 같은 값)는 저장하지 않는다.
    is_michelin    BOOLEAN     NOT NULL,                 -- 현재 미슐랭 가이드에 등재됨
    latest_grade   VARCHAR(20) NOT NULL,                 -- NONE / BIB_GOURMAND / 1_STAR / 2_STARS / 3_STARS / SELECTED / UNKNOWN(등재는 확인, 등급 근거 없음)
    note          TEXT,
    checked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- 판별 근거 URL(source_urls)은 사람이 검증할 때만 쓰므로 DB에 적재하지 않고 CSV/xlsx에만 둔다.

-- 5. 영상 <-> 식당 (N:M) -----------------------------------------------------
-- 영상에서 LLM이 뽑은 원본(korean_name, address)은 여기 보관 -> 추출 품질 검증/재매칭에 사용
CREATE TABLE video_restaurant_mentions (
    video_id          VARCHAR(20) NOT NULL REFERENCES videos ON DELETE CASCADE,
    restaurant_id     BIGINT      NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    extracted_name    VARCHAR(200),
    extracted_address VARCHAR(500),
    PRIMARY KEY (video_id, restaurant_id)
);
CREATE INDEX idx_mentions_restaurant ON video_restaurant_mentions (restaurant_id);

-- 6. 영상에서 추출한 비정형 정보 (extract_video_notes.py 결과) --------------------------
-- 메뉴/음료. 재적재 시 (restaurant_id, video_id) 단위로 지우고 다시 넣는다.
CREATE TABLE menus (
    menu_id          BIGSERIAL    PRIMARY KEY,
    restaurant_id    BIGINT       NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    video_id         VARCHAR(20)  REFERENCES videos ON DELETE SET NULL,
    item_type        VARCHAR(10)  NOT NULL DEFAULT 'FOOD' CHECK (item_type IN ('FOOD', 'DRINK')),
    name             VARCHAR(200) NOT NULL,
    description      TEXT,                                -- 설명(음식은 조리 특징, 음료는 특징·페어링). 평가어·가격은 후처리로 뺀다
    evidence         TEXT,                                -- 근거 발언/화면 (시각 포함)
    first_appearance_sec INTEGER,                         -- 영상 분석: 음식이 화면에 처음 보이는 시각(초). 확인 안 되면 NULL
    mentioned_sec    INTEGER,                             -- 자막 분석: 서빙·시식 단서 또는 첫 언급 자막 시각(초). 화면 등장 시각이 아니라 추정치
    menu_order       SMALLINT,                            -- 영상에서 나온 순서(1부터). 코스는 서빙 순서
    course_stage     VARCHAR(20) CHECK (course_stage IN ('아뮤즈부쉬', '식전빵', '전채', '수프', '생선', '메인', '치즈', '프리디저트', '디저트', '프티푸르'))  -- 코스 식당의 음식만. 참고용(필터 금지)
);
CREATE INDEX idx_menus_restaurant ON menus (restaurant_id);
CREATE UNIQUE INDEX uq_menus_item ON menus (restaurant_id, video_id, item_type, name);

-- 음식 검색용 태그 (티본 스테이크, 스시, 양갈비 ...). 여러 영상의 태그가 합쳐진다.
CREATE TABLE restaurant_tags (
    restaurant_id BIGINT       NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    tag           VARCHAR(100) NOT NULL,
    PRIMARY KEY (restaurant_id, tag)
);
CREATE INDEX idx_restaurant_tags_tag ON restaurant_tags (tag);

-- (영상, 식당)별 컨셉/분위기/총평. raw 는 추출 원본 JSON 전체(프롬프트를 바꿔도 재조립 가능)
CREATE TABLE video_restaurant_notes (
    video_id       VARCHAR(20) NOT NULL REFERENCES videos ON DELETE CASCADE,
    restaurant_id  BIGINT      NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    concept        TEXT,
    atmosphere     TEXT,
    key_points     JSONB       NOT NULL DEFAULT '[]',
    final_review   TEXT,
    embedding_text TEXT,
    model          VARCHAR(60),
    extracted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw            JSONB,
    is_course      BOOLEAN     NOT NULL DEFAULT false,   -- 코스 요리 식당 여부(영상 자막 근거)
    course_name    VARCHAR(100),                          -- 코스 이름(오마카세 등)
    PRIMARY KEY (video_id, restaurant_id)
);

COMMIT;

-- 7. RAG 청크(pgvector)는 restaurant_chunks.sql 에서 별도로 만든다 (확장 설치가 필요해 이 파일과 분리).
