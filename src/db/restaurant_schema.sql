-- 맛집 RAG 챗봇 테이블 설계 v2 (PostgreSQL 16, restaurants_info.xlsx 실제 컬럼 기준)
-- 엑셀 1행 = "영상 1개 x 식당 1개" 가 비정규화된 형태 -> 아래처럼 분리한다.
--   정형(SQL search): videos / restaurants / restaurant_types / restaurant_tags / restaurant_hours / michelin_* / menus
--   비정형(RAG search): video_restaurant_notes(원문) + restaurant_chunks(임베딩, restaurant_chunks.sql)

BEGIN;

-- 1. 영상 ------------------------------------------------------------------
CREATE TABLE videos (
    video_id    VARCHAR(20)  PRIMARY KEY,               -- video_url 의 v= 값
    url         VARCHAR(300) NOT NULL,
    title       VARCHAR(300) NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 1-1. 지역 (시 -> 시 다음 단계, 최대 2단계) ---------------------------------------------------
-- Places API addressComponents 에서 뽑은 계층형 지역. 국내는 서울특별시 > 마포구, 성남시 > 분당구처럼 시와 구/군까지,
-- 구/군이 없는 곳(안동시)은 시만 저장한다. 도로명은 쓰지 않는다.
-- 식당은 가장 하위 지역 하나만 가리키고(restaurants.region_id), 상위 지역 검색은 parent_id 를 따라 올라가거나 내려가서 한다.
-- "OO역/OO 근처" 질문은 이 테이블이 아니라 restaurants.latitude/longitude 로 반경 검색한다.
CREATE TABLE regions (
    region_id    SERIAL       PRIMARY KEY,
    country_code CHAR(2)      NOT NULL,
    parent_id    INT          REFERENCES regions ON DELETE CASCADE,
    level        SMALLINT     NOT NULL CHECK (level BETWEEN 1 AND 2),   -- 1=시, 2=시 다음 단계(구/군 등). 없으면 1단계만
    name         VARCHAR(100) NOT NULL,                                 -- Places 응답의 long_name (languageCode 로 요청한 언어)
    CHECK ((level = 1 AND parent_id IS NULL) OR (level = 2 AND parent_id IS NOT NULL))
);
-- parent_id 가 NULL 인 최상위 행도 중복되지 않게 COALESCE 로 유니크 처리
CREATE UNIQUE INDEX uq_regions ON regions (country_code, COALESCE(parent_id, 0), level, name);
CREATE INDEX idx_regions_parent ON regions (parent_id);

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
    region_id           INT REFERENCES regions,          -- 가장 하위 지역(최대 2단계). 상위 지역은 regions.parent_id 로 조회
    latitude            NUMERIC(9,6),                   -- Places API location ("근처 식당" 검색용)
    longitude           NUMERIC(9,6),
    rating              NUMERIC(2,1),
    rating_count        INT,
    price_level         SMALLINT CHECK (price_level BETWEEN 0 AND 4),   -- FREE=0 ... VERY_EXPENSIVE=4, 없으면 NULL
    business_status     VARCHAR(30),                     -- OPERATIONAL / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY
    phone               VARCHAR(40),
    website             VARCHAR(300),
    editorial_summary   TEXT,                            -- Google 한줄 소개(영문). RAG 청크 후보
    primary_type        VARCHAR(60),                     -- google_category_code
    primary_type_label  VARCHAR(60),                     -- google_category ("Korean Restaurant")
    category_broad      VARCHAR(50),                     -- 대분류(한식/일식/중식/양식/동남아식/인도식/중동식/기타). Google 타입 매핑 우선, 없으면 영상 추출값
    category_detail     VARCHAR(50),                     -- 세부 분류(오마카세/파인다이닝/라멘 ...). 영상 추출값
    opening_hours_raw   TEXT,                            -- 원문 보관(파싱 실패/재파싱 대비)
    places_fetched_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX idx_restaurants_country ON restaurants (country_code);
CREATE INDEX idx_restaurants_primary_type ON restaurants (primary_type);
CREATE INDEX idx_restaurants_region ON restaurants (region_id);

-- google_types (쉼표 구분 문자열) -> 행으로 분리. 검색 필터용
CREATE TABLE restaurant_types (
    restaurant_id BIGINT      NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    type_code     VARCHAR(60) NOT NULL,
    position      SMALLINT    NOT NULL,                  -- 원본 순서(0 = 대표 타입)
    PRIMARY KEY (restaurant_id, type_code)
);
CREATE INDEX idx_restaurant_types_code ON restaurant_types (type_code);

-- 3. 영업시간 (google_opening_hours 파싱 결과) --------------------------------
-- 하루 여러 구간 가능(점심/저녁) -> seq. 휴무일은 행을 만들지 않고 is_closed 행 1개로 표현.
-- 자정을 넘기는 영업(5PM-1AM)은 close_time < open_time 으로 저장.
CREATE TABLE restaurant_hours (
    restaurant_id BIGINT   NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    day_of_week   SMALLINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),   -- 0=일 ... 6=토
    seq           SMALLINT NOT NULL DEFAULT 0,
    is_closed     BOOLEAN  NOT NULL DEFAULT FALSE,
    open_time     TIME,
    close_time    TIME,
    PRIMARY KEY (restaurant_id, day_of_week, seq),
    CHECK ((is_closed AND open_time IS NULL AND close_time IS NULL)
        OR (NOT is_closed AND open_time IS NOT NULL AND close_time IS NOT NULL))
);
-- google_open_now 는 조회 시점 값이라 저장하지 않는다. "지금 영업 중?"은 이 테이블 + 현지 시간으로 계산.

-- 4. 미슐랭 -----------------------------------------------------------------
-- 현재 상태(1:1). is_michelin=false 인 식당도 "확인했고 미등재"라는 사실을 남기기 위해 행을 만든다.
CREATE TABLE michelin_status (
    restaurant_id  BIGINT      PRIMARY KEY REFERENCES restaurants ON DELETE CASCADE,
    -- is_michelin / latest_grade / is_active 는 michelin_records(history)에서 계산한 값. 판정 기준은 michelin_records.
    is_michelin    BOOLEAN     NOT NULL,                 -- 한 번이라도 등재된 적 있음
    edition_type   VARCHAR(20) NOT NULL,                 -- NONE / REGULAR ...
    latest_grade   VARCHAR(20) NOT NULL,                 -- NONE / BIB_GOURMAND / 1_STAR / 2_STARS / 3_STARS / SELECTED / UNKNOWN(등재는 확인, 등급 근거 없음)
    is_active      BOOLEAN     NOT NULL,                 -- 최신 에디션에 현재 등재 중인가
    note           TEXT,
    checked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 연도별 이력 (history "2025:3_STARS; 2026:3_STARS" 를 행으로 분리)
CREATE TABLE michelin_records (
    restaurant_id BIGINT      NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    year          SMALLINT    NOT NULL,
    grade         VARCHAR(20) NOT NULL,
    PRIMARY KEY (restaurant_id, year)
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
    description      TEXT,
    cooking_features TEXT,                                -- 조리법/식재료 디테일
    taste_review     TEXT,                                -- 맛/식감 평가 (음료는 시음 평)
    tips             TEXT,                                -- 먹는 방법/추천 팁
    price_text       VARCHAR(100),                        -- 영상에서 확인된 가격 원문("1인 70,000원"). 통화/단위가 섞여 정형화하지 않음
    is_signature     BOOLEAN      NOT NULL DEFAULT FALSE,
    evidence         TEXT,                                -- 근거 발언/화면 (시각 포함)
    first_appearance_sec INTEGER                          -- 영상에서 처음 등장하는 시각(초). 확인 안 되면 NULL
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
    PRIMARY KEY (video_id, restaurant_id)
);

COMMIT;

-- 7. RAG 청크(pgvector)는 restaurant_chunks.sql 에서 별도로 만든다 (확장 설치가 필요해 이 파일과 분리).
