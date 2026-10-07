-- 자막 추출에서 음식(FOOD)으로 잘못 들어간 음료 5개를 DRINK 로 바로잡는다. 삭제 없이 UPDATE 만 한다. 여러 번 실행해도 안전하다.
--   menu_id 와 이름이 둘 다 맞는 행만 바꾼다(재적재 등으로 menu_id 가 달라졌을 때 엉뚱한 행을 바꾸지 않기 위함).
--   음료는 시각을 저장하지 않으므로(load_notes.py 와 동일) mentioned_sec 를 비운다.
--   청크는 FOOD -> DRINK 로 종류만 바꾼다. content 형식이 같아서 임베딩은 그대로 쓴다. (볼린저 RD 07 은 설명이 없어 청크가 원래 없다)
-- 원본 JSON(data/video_notes/gpt-4o-mini-script-v2/)도 같은 기준으로 고쳐 두었으므로, load_notes.py 로 다시 적재해도 같은 결과가 된다.
--
-- 사용자가 직접 실행한다. 트랜잭션이라 한 문장이라도 실패하면 전체가 취소된다.
BEGIN;

CREATE TEMP TABLE fix_drinks (menu_id BIGINT, name TEXT) ON COMMIT DROP;
INSERT INTO fix_drinks VALUES
    (142, '레드와인(생태 스태프)'),
    (310, '볼린저 RD 07'),
    (375, '샴페인 자크셀로스 로제'),
    (680, '프로세코'),
    (829, 'Homemade forest herb tea');

-- 청크를 먼저 옮긴다(menus 의 item_type 을 바꾸기 전에 FOOD 행을 기준으로 찾는다)
UPDATE restaurant_chunks c SET chunk_type = 'DRINK'
FROM menus m JOIN fix_drinks f ON f.menu_id = m.menu_id AND f.name = m.name
WHERE m.item_type = 'FOOD'
  AND c.chunk_type = 'FOOD' AND c.restaurant_id = m.restaurant_id AND c.video_id = m.video_id AND c.chunk_key = m.name;

UPDATE menus m SET item_type = 'DRINK', mentioned_sec = NULL, first_appearance_sec = NULL
FROM fix_drinks f
WHERE m.menu_id = f.menu_id AND m.name = f.name AND m.item_type = 'FOOD';

-- 확인: DRINK 메뉴 111개(106 + 5), DRINK 청크 109개(105 + 4) 가 되어야 한다
SELECT (SELECT count(*) FROM menus WHERE item_type = 'DRINK')              AS drink_menus,
       (SELECT count(*) FROM restaurant_chunks WHERE chunk_type = 'DRINK') AS drink_chunks,
       (SELECT count(*) FROM restaurant_chunks WHERE chunk_type = 'FOOD')  AS food_chunks;

COMMIT;
