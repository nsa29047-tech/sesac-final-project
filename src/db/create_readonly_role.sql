-- 팀원 공유용 읽기 전용 DB 계정을 만드는 스크립트. 여러 번 실행해도 안전하다(역할이 있으면 비밀번호만 바꾼다).
-- 사용 전에 '<여기에_비밀번호>'를 길고 무작위인 값으로 바꾼다. 실제 비밀번호를 파일에 저장하거나 커밋하지 않는다.
-- 실행: 관리자 계정으로 접속한 psql 또는 Supabase SQL Editor에서 실행한다.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly_user') THEN
        CREATE ROLE readonly_user LOGIN PASSWORD '<여기에_비밀번호>'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    ELSE
        ALTER ROLE readonly_user PASSWORD '<여기에_비밀번호>';
    END IF;
END
$$;

-- 세션 기본값을 읽기 전용으로 두고, 오래 걸리는 쿼리가 DB를 붙잡지 않도록 제한한다.
ALTER ROLE readonly_user SET default_transaction_read_only = on;
ALTER ROLE readonly_user SET statement_timeout = '30s';

-- public 스키마의 조회 권한만 준다. 쓰기·DDL 권한은 주지 않는다.
GRANT USAGE ON SCHEMA public TO readonly_user;
REVOKE CREATE ON SCHEMA public FROM readonly_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_user;

-- 이후에 만들어지는 테이블도 조회할 수 있게 한다(테이블을 만드는 관리자 계정 기준).
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_user;
