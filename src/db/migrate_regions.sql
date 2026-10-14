-- [폐기] regions 계층 테이블은 restaurants.region_1/2/3 컬럼으로 대체되었다. 새 DB는 restaurant_schema.sql 만 적용하고,
-- 이 파일을 이미 적용한 DB는 migrate_simplify_schema.sql 로 컬럼 방식으로 옮긴다. 아래는 과거 기록으로만 남긴다.
-- 이미 restaurant_schema.sql 을 적용한 DB에 지역(regions)을 추가하는 마이그레이션. 여러 번 실행해도 안전하다.
BEGIN;

CREATE TABLE IF NOT EXISTS regions (
    region_id    SERIAL       PRIMARY KEY,
    country_code CHAR(2)      NOT NULL,
    parent_id    INT          REFERENCES regions ON DELETE CASCADE,
    level        SMALLINT     NOT NULL CHECK (level BETWEEN 1 AND 3),   -- 1=시, 2=구/군, 3=동·면. 없는 단계는 건너뛰고 있는 데까지만
    name         VARCHAR(100) NOT NULL,
    CHECK ((level = 1 AND parent_id IS NULL) OR (level > 1 AND parent_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_regions ON regions (country_code, COALESCE(parent_id, 0), level, name);
CREATE INDEX IF NOT EXISTS idx_regions_parent ON regions (parent_id);

ALTER TABLE restaurants ADD COLUMN IF NOT EXISTS region_id INT REFERENCES regions;
CREATE INDEX IF NOT EXISTS idx_restaurants_region ON restaurants (region_id);

COMMIT;
