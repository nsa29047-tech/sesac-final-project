-- 챗봇 에이전트 전용 DB 계정. 범위 제한 뷰(create_scope_views.sql)와 검색용 테이블만 조회할 수 있고, 원본 restaurants /
-- restaurant_hours / michelin_status 는 조회할 수 없다(노트가 없는 식당이 SQL 전용 답변에 섞이는 것을 DB 권한으로 막는다).
-- 사용 전에 '<여기에_비밀번호>'를 길고 무작위인 값으로 바꾼다. 실제 비밀번호를 파일에 저장하거나 커밋하지 않는다.
-- 선행: create_scope_views.sql. 실행: 관리자 계정으로 접속한 psql 또는 Supabase SQL Editor. 여러 번 실행해도 안전하다.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'chatbot_user') THEN
        CREATE ROLE chatbot_user LOGIN PASSWORD '<여기에_비밀번호>'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    ELSE
        ALTER ROLE chatbot_user PASSWORD '<여기에_비밀번호>';
    END IF;
END
$$;
ALTER ROLE chatbot_user SET default_transaction_read_only = on;
ALTER ROLE chatbot_user SET statement_timeout = '30s';

GRANT USAGE ON SCHEMA public TO chatbot_user;
REVOKE CREATE ON SCHEMA public FROM chatbot_user;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM chatbot_user;
-- 식당 정보는 뷰로만
GRANT SELECT ON restaurants_in_scope, restaurant_hours_in_scope, michelin_status_in_scope TO chatbot_user;
-- 비정형 데이터(노트가 있는 식당만 들어 있음)
GRANT SELECT ON menus, restaurant_tags, video_restaurant_notes, restaurant_chunks, videos TO chatbot_user;
-- 주의: 뷰와 위 테이블은 restaurant_id 로 조인하므로, 챗봇은 restaurants 가 아니라 restaurants_in_scope 와 조인해야 한다.
