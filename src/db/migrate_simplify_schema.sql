-- 쓰지 않거나 중복인 컬럼·테이블을 정리하는 마이그레이션 (이미 restaurant_schema.sql 을 적용한 DB용). 여러 번 실행해도 안전하다.
-- 한 트랜잭션이라 중간에 실패하면 전부 되돌아간다. 새 DB는 restaurant_schema.sql 만 적용하면 되고 이 파일은 필요 없다.
--   1) regions 계층 테이블 -> restaurants.region_1/2/3 컬럼 (기존 값은 컬럼으로 옮긴 뒤 삭제)
--   2) menus.description, menus.is_signature 삭제 (적재만 하고 읽지 않거나 항상 NULL)
--   3) michelin_records 삭제 (Parse API는 현재 등급만 줘서 항상 비어 있었다)
--      michelin_status.edition_type, is_active 삭제 (is_michelin 에서 유도되는 값이라 정보가 없었다)
--   4) restaurant_hours.is_closed 삭제 (open_time/close_time 이 NULL 인지와 항상 같은 값이다. 휴무는 시간이 NULL 인 행)
--   5) restaurants.opening_hours_raw 삭제 (저장만 하고 읽지 않았다. 원문은 CSV의 google_opening_hours 에 남아 있어 재파싱할 수 있다)
-- 주의: 삭제하는 테이블·컬럼의 데이터는 사라진다.
BEGIN;

-- 1) 지역 --------------------------------------------------------------------
ALTER TABLE restaurants
    ADD COLUMN IF NOT EXISTS region_1 VARCHAR(100),
    ADD COLUMN IF NOT EXISTS region_2 VARCHAR(100),
    ADD COLUMN IF NOT EXISTS region_3 VARCHAR(100);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'restaurants' AND column_name = 'region_id') THEN
        -- 각 지역의 조상 경로(위에서부터)를 구해 식당 컬럼에 채운다. 단계가 건너뛰어 저장된 지역은 앞에서부터 채워진 형태 그대로다.
        WITH RECURSIVE path AS (
            SELECT region_id, parent_id, name, 1 AS depth, ARRAY[name::text] AS names
            FROM regions WHERE parent_id IS NULL
            UNION ALL
            SELECT c.region_id, c.parent_id, c.name, p.depth + 1, p.names || c.name::text
            FROM regions c JOIN path p ON c.parent_id = p.region_id
        )
        UPDATE restaurants r
           SET region_1 = p.names[1], region_2 = p.names[2], region_3 = p.names[3]
          FROM path p
         WHERE r.region_id = p.region_id;
    END IF;
END $$;

DROP INDEX IF EXISTS idx_restaurants_region;
ALTER TABLE restaurants DROP COLUMN IF EXISTS region_id;
DROP TABLE IF EXISTS regions;
CREATE INDEX IF NOT EXISTS idx_restaurants_region ON restaurants (country_code, region_1, region_2, region_3);

-- 2) 메뉴 --------------------------------------------------------------------
ALTER TABLE menus
    DROP COLUMN IF EXISTS description,
    DROP COLUMN IF EXISTS is_signature;

-- 3) 미슐랭 ------------------------------------------------------------------
DROP TABLE IF EXISTS michelin_records;

ALTER TABLE michelin_status
    DROP COLUMN IF EXISTS edition_type,
    DROP COLUMN IF EXISTS is_active;

-- 4) 영업시간 ----------------------------------------------------------------
-- 컬럼을 지우면 이를 참조하던 CHECK 제약도 함께 사라지므로 새 제약을 추가한다. 기존 행은 그대로 유효하다.
ALTER TABLE restaurant_hours DROP COLUMN IF EXISTS is_closed;
ALTER TABLE restaurant_hours DROP CONSTRAINT IF EXISTS restaurant_hours_times_both_or_none;
ALTER TABLE restaurant_hours
    ADD CONSTRAINT restaurant_hours_times_both_or_none CHECK ((open_time IS NULL) = (close_time IS NULL));

-- 5) 영업시간 원문 ----------------------------------------------------------
ALTER TABLE restaurants DROP COLUMN IF EXISTS opening_hours_raw;

COMMIT;
