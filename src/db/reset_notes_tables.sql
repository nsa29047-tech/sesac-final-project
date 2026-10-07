-- 자막 방식 테스트 전에 영상 분석 결과로 채워진 비정형 데이터를 비운다. 정형 데이터(videos, restaurants의 Places·지역 정보,
-- restaurant_hours, michelin_status, video_restaurant_mentions)는 건드리지 않는다.
--
-- 실행 전 확인: data/backup_20261006_151428/ 에 restaurants / menus / restaurant_tags / video_restaurant_notes / restaurant_chunks CSV가 있어야 한다.
-- 복원: 영상 분석 JSON(data/video_notes/gemini-3.5-flash-lite/)에서 load_notes.py, embed_chunks.py 를 다시 실행한다.
--
-- 사용자가 직접 실행한다(DB 삭제 작업). 트랜잭션이라 한 문장이라도 실패하면 전체가 취소된다.
BEGIN;

TRUNCATE restaurant_chunks, menus, restaurant_tags, video_restaurant_notes RESTART IDENTITY;

-- load_notes.py 가 영상 추출값으로 채운 분류. 자막 결과로 다시 채워진다.
UPDATE restaurants SET category_broad = NULL, category_detail = NULL, updated_at = now();

-- 비워졌는지 확인(모두 0이어야 한다)
SELECT (SELECT count(*) FROM restaurant_chunks)        AS chunks,
       (SELECT count(*) FROM menus)                    AS menus,
       (SELECT count(*) FROM restaurant_tags)          AS tags,
       (SELECT count(*) FROM video_restaurant_notes)   AS notes,
       (SELECT count(*) FROM restaurants WHERE category_broad IS NOT NULL) AS categorized;

COMMIT;
