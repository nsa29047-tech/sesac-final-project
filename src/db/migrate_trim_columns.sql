-- 이전 스키마로 만든 DB에서 더 이상 쓰지 않는 컬럼·테이블을 정리하는 마이그레이션. 여러 번 실행해도 안전하다.
-- 음식 분류(Google 카테고리·타입·소개글)와 가격대는 저장하지 않고 Gemini 영상 분석 결과(category_broad/detail, restaurant_tags)를 쓴다.
-- 주의: 삭제하는 컬럼의 값은 사라진다. 먼저 migrate_regions.sql 을 적용하고, 이 파일은 그 뒤에 실행한다.
BEGIN;

DROP INDEX IF EXISTS idx_restaurants_primary_type;
ALTER TABLE restaurants
    DROP COLUMN IF EXISTS price_level,
    DROP COLUMN IF EXISTS editorial_summary,
    DROP COLUMN IF EXISTS primary_type,
    DROP COLUMN IF EXISTS primary_type_label;

DROP TABLE IF EXISTS restaurant_types;

COMMIT;
