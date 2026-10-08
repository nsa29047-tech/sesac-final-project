-- restaurant_chunks 에 CHEF 청크 종류를 추가한다. 여러 번 실행해도 안전하다.
-- CHEF 청크: 셰프 이름·경력만 담은 짧은 청크(식당당 1개, chunk_key='chef'). 개요 청크에 섞이면 경력 질문의 순위가 낮아져 분리했다.
-- 실행 후: uv run python src/db/embed_chunks.py (개요 청크의 셰프 문구를 빼고 CHEF 청크를 만든다)
BEGIN;
ALTER TABLE restaurant_chunks DROP CONSTRAINT IF EXISTS restaurant_chunks_chunk_type_check;
ALTER TABLE restaurant_chunks
    ADD CONSTRAINT restaurant_chunks_chunk_type_check CHECK (chunk_type IN ('OVERVIEW', 'FOOD', 'DRINK', 'CHEF'));
COMMIT;
