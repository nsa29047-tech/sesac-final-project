-- A/B 분리 추출(extract_script_split.py) 결과에 맞춰 스키마를 바꾼다. 여러 번 실행해도 안전하다.
--   menus: 메뉴 설명 칸을 description 하나로 합치고(cooking_features 이름 변경, 음료 설명 포함),
--          taste_review / tips / price_text 는 삭제한다. 코스 순서(menu_order)와 코스 단계(course_stage)를 추가한다.
--   video_restaurant_notes: 코스 요리 여부(is_course)와 코스 이름(course_name)을 추가한다.
-- 셰프 컬럼(restaurants.chef_name / chef_info)은 이미 있다(migrate_chef_columns.sql).
-- 삭제되는 컬럼 값은 새 결과에서는 모두 비어 있고, 이전 값은 data/backup_*/ 와 결과 JSON 에 남아 있다.
-- 실행 후: load_notes.py --model gpt-4o-mini-script-split-v2 -> load_chefs.py --notes-tag ... -> embed_chunks.py
BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'menus' AND column_name = 'taste_review') THEN
        UPDATE menus SET cooking_features = COALESCE(cooking_features, taste_review);  -- 음료 설명(시음 평) 보존
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'menus' AND column_name = 'cooking_features') THEN
        ALTER TABLE menus RENAME COLUMN cooking_features TO description;
    END IF;
END $$;

ALTER TABLE menus
    DROP COLUMN IF EXISTS taste_review,
    DROP COLUMN IF EXISTS tips,
    DROP COLUMN IF EXISTS price_text,
    ADD COLUMN IF NOT EXISTS menu_order   SMALLINT,
    ADD COLUMN IF NOT EXISTS course_stage VARCHAR(20);
ALTER TABLE menus DROP CONSTRAINT IF EXISTS menus_course_stage_check;
ALTER TABLE menus ADD CONSTRAINT menus_course_stage_check CHECK (course_stage IN
    ('아뮤즈부쉬', '식전빵', '전채', '수프', '생선', '메인', '치즈', '프리디저트', '디저트', '프티푸르'));
COMMENT ON COLUMN menus.description  IS '메뉴 설명(음식은 조리 특징, 음료는 특징·페어링). 평가어·가격은 후처리로 뺀다';
COMMENT ON COLUMN menus.menu_order   IS '영상에서 나온 순서(1부터). 코스는 서빙 순서';
COMMENT ON COLUMN menus.course_stage IS '코스 단계(코스 식당의 음식만). 정확도가 낮아 참고용이며 필터로 쓰지 않는다';

ALTER TABLE video_restaurant_notes
    ADD COLUMN IF NOT EXISTS is_course   BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS course_name VARCHAR(100);
COMMENT ON COLUMN video_restaurant_notes.is_course   IS '코스 요리 식당 여부(영상 자막 근거)';
COMMENT ON COLUMN video_restaurant_notes.course_name IS '코스 이름(오마카세 등)';

COMMIT;
