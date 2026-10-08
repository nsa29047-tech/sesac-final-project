-- Google Places priceLevel / priceRange 컬럼 추가. 여러 번 실행해도 안전하다.
-- 채우기: uv run python src/db/load_prices.py (data/restaurant_prices_sample.csv, fetch_price_level.py 결과)
BEGIN;
ALTER TABLE restaurants
    ADD COLUMN IF NOT EXISTS price_level    SMALLINT CHECK (price_level BETWEEN 0 AND 4),
    ADD COLUMN IF NOT EXISTS price_min      INTEGER,
    ADD COLUMN IF NOT EXISTS price_max      INTEGER,
    ADD COLUMN IF NOT EXISTS price_currency CHAR(3);
COMMENT ON COLUMN restaurants.price_level    IS 'Places priceLevel: 0 FREE, 1 INEXPENSIVE, 2 MODERATE, 3 EXPENSIVE, 4 VERY_EXPENSIVE. 없으면 NULL';
COMMENT ON COLUMN restaurants.price_min      IS 'Places priceRange 하한(1인 기준, price_currency 단위). 없으면 NULL';
COMMENT ON COLUMN restaurants.price_max      IS 'Places priceRange 상한. 파인다이닝은 하한만 있고 NULL인 경우가 많다';
COMMENT ON COLUMN restaurants.price_currency IS 'ISO 4217 통화 코드(KRW, JPY, EUR ...). 통화가 섞여 있어 price_level 로 비교한다';
COMMIT;
