-- 이미 restaurant_schema.sql(이전 버전)을 적용한 DB를 새 버전으로 올리는 마이그레이션. 여러 번 실행해도 안전하다.
BEGIN;

ALTER TABLE menus
    ADD COLUMN IF NOT EXISTS item_type        VARCHAR(10) NOT NULL DEFAULT 'FOOD' CHECK (item_type IN ('FOOD', 'DRINK')),
    ADD COLUMN IF NOT EXISTS cooking_features TEXT,
    ADD COLUMN IF NOT EXISTS taste_review     TEXT,
    ADD COLUMN IF NOT EXISTS tips             TEXT,
    ADD COLUMN IF NOT EXISTS price_text       VARCHAR(100),
    ADD COLUMN IF NOT EXISTS evidence         TEXT,
    ADD COLUMN IF NOT EXISTS first_appearance_sec INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS uq_menus_item ON menus (restaurant_id, video_id, item_type, name);

CREATE TABLE IF NOT EXISTS restaurant_tags (
    restaurant_id BIGINT       NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    tag           VARCHAR(100) NOT NULL,
    PRIMARY KEY (restaurant_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_restaurant_tags_tag ON restaurant_tags (tag);

CREATE TABLE IF NOT EXISTS video_restaurant_notes (
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
-- 이어서 restaurant_chunks.sql 을 실행한다.
