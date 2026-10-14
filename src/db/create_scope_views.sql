-- 챗봇 에이전트가 조회할 범위를 "영상 분석 노트가 있는 식당"으로 제한하는 뷰. 여러 번 실행해도 안전하다.
-- 노트가 없는 식당(정형 정보만 있는 식당)이 SQL 로만 조회되는 답변에 섞이지 않게 하려는 것이다.
-- 에이전트의 SQL 도구는 restaurants / restaurant_hours / michelin_status 대신 아래 뷰만 조회한다.
-- 범위를 넓히려면 노트를 더 적재하면 되고(뷰는 자동으로 따라간다), 전체로 풀려면 restaurants_in_scope 정의에서 EXISTS 조건을 뺀다.
BEGIN;

CREATE OR REPLACE VIEW restaurants_in_scope AS
SELECT r.*
FROM restaurants r
WHERE EXISTS (SELECT 1 FROM video_restaurant_notes n WHERE n.restaurant_id = r.restaurant_id);

CREATE OR REPLACE VIEW restaurant_hours_in_scope AS
SELECT h.* FROM restaurant_hours h JOIN restaurants_in_scope r USING (restaurant_id);

CREATE OR REPLACE VIEW michelin_status_in_scope AS
SELECT m.* FROM michelin_status m JOIN restaurants_in_scope r USING (restaurant_id);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly_user') THEN
        GRANT SELECT ON restaurants_in_scope, restaurant_hours_in_scope, michelin_status_in_scope TO readonly_user;
    END IF;
END $$;

COMMIT;
