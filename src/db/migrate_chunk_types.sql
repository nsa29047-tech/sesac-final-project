-- restaurant_chunks 의 청크 종류를 OVERVIEW / MENU 에서 OVERVIEW / FOOD / DRINK 로 바꾼다.
--   MENU -> FOOD 로 이름만 바꾼다(content, embedding 은 그대로라 재임베딩이 필요 없다).
--   DRINK 는 이 마이그레이션 후 embed_chunks.py 를 실행하면 새로 만들어진다.
--   설명(조리 특징·맛 평가·팁)이 전혀 없는 음식 청크는 "식당명의 메뉴명" 한 줄뿐이라 삭제한다(embed_chunks.py 도 이제 만들지 않는다).
--   메뉴 원본은 menus 테이블에 그대로 있다. 삭제된 청크는 embed_chunks.py --include-empty 로 다시 만들 수 있다.
--
-- 사용자가 직접 실행한다(DELETE 포함). 트랜잭션이라 한 문장이라도 실패하면 전체가 취소된다. 여러 번 실행해도 안전하다.
-- 실행 순서: 이 SQL -> uv run python src/db/embed_chunks.py
BEGIN;

-- 1. 설명 없는 음식 청크 삭제 (삭제 대상 수를 먼저 확인하려면 DELETE 를 SELECT count(*) 로 바꿔 실행)
DELETE FROM restaurant_chunks c
USING menus m
WHERE c.chunk_type = 'MENU'
  AND m.restaurant_id = c.restaurant_id
  AND m.video_id = c.video_id
  AND m.item_type = 'FOOD'
  AND m.name = c.chunk_key
  AND COALESCE(m.cooking_features, m.taste_review, m.tips) IS NULL;

-- 2. 허용 값 변경 후 MENU -> FOOD
ALTER TABLE restaurant_chunks DROP CONSTRAINT IF EXISTS restaurant_chunks_chunk_type_check;
UPDATE restaurant_chunks SET chunk_type = 'FOOD' WHERE chunk_type = 'MENU';
ALTER TABLE restaurant_chunks
    ADD CONSTRAINT restaurant_chunks_chunk_type_check CHECK (chunk_type IN ('OVERVIEW', 'FOOD', 'DRINK'));

-- 3. 확인: OVERVIEW 165, FOOD 681 안팎이어야 하고 MENU 는 0이어야 한다
SELECT chunk_type, count(*) FROM restaurant_chunks GROUP BY 1 ORDER BY 1;

COMMIT;
