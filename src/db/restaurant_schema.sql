-- 맛집 RAG 챗봇 테이블 설계 v2 (PostgreSQL 16, restaurants_info.xlsx 실제 컬럼 기준)
-- 엑셀 1행 = "영상 1개 x 식당 1개" 가 비정규화된 형태 -> 아래처럼 분리한다.
--   정형(SQL search): videos / restaurants / restaurant_types / restaurant_hours / michelin_*
--   비정형(RAG search): restaurant_chunks (파일 하단, 메뉴/특징 추출 후 사용)

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
    name_official       VARCHAR(200)  NOT NULL,          -- google_official_name
    name_ko             VARCHAR(200),                    -- 한국어 표기(영상 기준 추출명)
    country_code        CHAR(2)       NOT NULL,
    formatted_address   VARCHAR(500),                    -- google_formatted_address
    latitude            NUMERIC(9,6),                    -- 현재 데이터에 없음: Places API location 으로 추가 수집 권장
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
    category_broad      VARCHAR(50),                     -- 자체 분류(대분류). 현재 전부 NULL
    category_detail     VARCHAR(50),                     -- 자체 분류(세부). 현재 전부 NULL
    opening_hours_raw   TEXT,                            -- 원문 보관(파싱 실패/재파싱 대비)
    places_fetched_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
);
CREATE INDEX idx_restaurants_country ON restaurants (country_code);
CREATE INDEX idx_restaurants_primary_type ON restaurants (primary_type);

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
    is_michelin    BOOLEAN     NOT NULL,                 -- 한 번이라도 등재된 적 있음
    edition_type   VARCHAR(20) NOT NULL,                 -- NONE / REGULAR ...
    latest_grade   VARCHAR(20) NOT NULL,                 -- NONE / BIB_GOURMAND / 1_STAR / 2_STARS / 3_STARS / SELECTED
    is_active      BOOLEAN     NOT NULL,                 -- 최신 에디션에 현재 등재 중인가
    summary_badge  VARCHAR(100),                         -- "2021-2025 5년 연속 3스타" / "미등재"
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

-- 판별 근거 URL (source_urls 를 " | " 로 분리)
CREATE TABLE michelin_sources (
    restaurant_id BIGINT       NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    url           VARCHAR(600) NOT NULL,
    PRIMARY KEY (restaurant_id, url)
);

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

-- 6. (예정) 메뉴 / 특징 : 현재 엑셀에는 없음. 추출하면 사용 -------------------------
CREATE TABLE menus (
    menu_id       BIGSERIAL    PRIMARY KEY,
    restaurant_id BIGINT       NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    video_id      VARCHAR(20)  REFERENCES videos ON DELETE SET NULL,
    name          VARCHAR(200) NOT NULL,
    description   TEXT,
    is_signature  BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX idx_menus_restaurant ON menus (restaurant_id);

COMMIT;

-- 7. (예정) RAG 청크 : pgvector 를 쓸 때만 실행 (별도 벡터DB를 쓰면 생략) ------------
-- CREATE EXTENSION IF NOT EXISTS vector;
-- CREATE TABLE restaurant_chunks (
--     chunk_id      BIGSERIAL PRIMARY KEY,
--     restaurant_id BIGINT NOT NULL REFERENCES restaurants ON DELETE CASCADE,
--     video_id      VARCHAR(20) REFERENCES videos ON DELETE SET NULL,
--     chunk_type    VARCHAR(20) NOT NULL,    -- ATMOSPHERE / FOOD_STYLE / MENU / SUMMARY
--     content       TEXT NOT NULL,
--     embedding     vector(1536)             -- text-embedding-3-small 기준
-- );
-- CREATE INDEX ON restaurant_chunks USING hnsw (embedding vector_cosine_ops);