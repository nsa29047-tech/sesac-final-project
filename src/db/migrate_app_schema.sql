-- Spring Boot 서버(server/)가 쓰는 사용자 데이터. 파이프라인이 쓰는 public 스키마와 분리한 app 스키마에 둔다.
-- 관계: users 1:N conversations 1:N messages, users N:M restaurants (favorites).
-- favorites 가 public.restaurants 를 FK 로 참조하므로 같은 DB 에 둔다. 식당 행이 지워지면 즐겨찾기도 CASCADE 로 지운다.
-- 실행: 관리자 계정으로 접속한 psql 또는 Supabase SQL Editor. 여러 번 실행해도 안전하다.
-- 사전: restaurant_schema.sql (public.restaurants). 사용 전에 '<여기에 비밀번호>'를 길고 무작위인 값으로 바꾼다(파일에 저장·커밋하지 않는다).

CREATE SCHEMA IF NOT EXISTS app;

-- 1. 사용자 ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.users (
    user_id        BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          VARCHAR(255) NOT NULL UNIQUE,
    password_hash  VARCHAR(100) NOT NULL,                 -- BCrypt(60자) 등 해시. 평문은 저장하지 않는다
    nickname       VARCHAR(50)  NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- 2. 대화 (챗봇 LangGraph thread_id 와 같은 UUID 를 쓴다) --------------------------
CREATE TABLE IF NOT EXISTS app.conversations (
    conversation_id UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT       NOT NULL REFERENCES app.users(user_id) ON DELETE CASCADE,
    title           VARCHAR(200) NOT NULL DEFAULT '새 대화',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()    -- 마지막 메시지 시각, 목록 정렬용
);
-- 내 대화 목록(최근 순)
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
    ON app.conversations (user_id, updated_at DESC);

-- 3. 메시지 ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.messages (
    message_id       BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id  UUID        NOT NULL REFERENCES app.conversations(conversation_id) ON DELETE CASCADE,
    role             VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    content          TEXT        NOT NULL,
    restaurant_ids   BIGINT[],                            -- assistant 답변에 카드로 나온 restaurant_id 배열(없으면 NULL)
    restaurants_json TEXT,                                -- 그 답변의 restaurants 이벤트 원문(카드·지도 복원용 스냅샷, 없으면 NULL)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 대화 하나의 메시지를 시간순으로
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON app.messages (conversation_id, created_at);

-- 이미 만든 DB 에는 위 CREATE TABLE 이 건너뛰어지므로 나중에 더한 컬럼은 따로 추가한다
ALTER TABLE app.messages ADD COLUMN IF NOT EXISTS restaurants_json TEXT;

-- 4. 즐겨찾기 (users N:M restaurants 의 연결 테이블) -----------------------------
CREATE TABLE IF NOT EXISTS app.favorites (
    user_id        BIGINT      NOT NULL REFERENCES app.users(user_id) ON DELETE CASCADE,
    restaurant_id  BIGINT      NOT NULL REFERENCES public.restaurants(restaurant_id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, restaurant_id)                  -- 같은 식당 중복 즐겨찾기 방지
);
-- 복합 PK 는 user_id 로 시작하는 조회만 돕는다. 식당 기준 조회(인기 식당 등)용
CREATE INDEX IF NOT EXISTS idx_favorites_restaurant ON app.favorites (restaurant_id);

-- 5. 서버 전용 계정: app 스키마는 읽기·쓰기, public 은 식당 조회만 ----------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN PASSWORD '<여기에 비밀번호>'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
    END IF;
END
$$;
GRANT USAGE ON SCHEMA app TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO app_user;
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT ON public.restaurants TO app_user;
