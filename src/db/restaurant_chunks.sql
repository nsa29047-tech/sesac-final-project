-- RAG 청크 (pgvector). restaurant_schema.sql 적용 후 실행한다. 여러 번 실행해도 안전하다.
-- 임베딩은 OpenAI text-embedding-3-small (1536차원) 기준.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS restaurant_chunks (
    chunk_id      BIGSERIAL    PRIMARY KEY,
    restaurant_id BIGINT       NOT NULL REFERENCES restaurants ON DELETE CASCADE,
    video_id      VARCHAR(20)  NOT NULL REFERENCES videos ON DELETE CASCADE,
    chunk_type    VARCHAR(20)  NOT NULL CHECK (chunk_type IN ('OVERVIEW', 'FOOD', 'DRINK')),   -- FOOD/DRINK 는 menus.item_type 과 같은 값
    chunk_key     VARCHAR(200) NOT NULL,          -- OVERVIEW 는 'overview', FOOD/DRINK 는 메뉴명. 재적재 시 upsert 키
    content       TEXT         NOT NULL,          -- 임베딩한 텍스트 (검색 결과로 그대로 보여줄 수 있음)
    embedding     vector(1536) NOT NULL,
    model         VARCHAR(60)  NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (restaurant_id, video_id, chunk_type, chunk_key)
);
CREATE INDEX IF NOT EXISTS idx_chunks_restaurant ON restaurant_chunks (restaurant_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON restaurant_chunks USING hnsw (embedding vector_cosine_ops);
