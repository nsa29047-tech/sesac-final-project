-- 새 영상 자동 처리(process_video.py / n8n)의 진행 상태 테이블. 여러 번 실행해도 안전하다.
-- 영상 1개가 어느 단계까지 끝났는지 기록해서, 실패하거나 중단돼도 그 다음 단계부터 이어서 처리한다.
--   status : pending(대기) / running(처리 중) / loaded(적재 완료) / skipped(대상 아님) / needs_review(사람 확인 필요) / failed(오류, 재시도 가능)
--   stage  : 마지막으로 끝난 단계 (gate -> places -> captions -> extract -> postprocess -> load)
--   caption_type : manual(제작자 자막 -> 자막 분석) / auto(자동 생성 자막 -> Gemini 영상 분석) / none(자막 없음 -> Gemini 영상 분석)
CREATE TABLE IF NOT EXISTS video_queue (
    video_id         VARCHAR(20)  PRIMARY KEY,
    url              VARCHAR(300) NOT NULL,
    title            VARCHAR(300),
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'loaded', 'skipped', 'needs_review', 'failed')),
    stage            VARCHAR(20),
    caption_type     VARCHAR(10)  CHECK (caption_type IN ('manual', 'auto', 'none')),
    skip_reason      TEXT,                     -- skipped / needs_review 사유
    error            TEXT,                     -- failed 일 때 마지막 오류
    note             TEXT,                     -- 처리는 끝났지만 알아 둘 점 (일부 가게 매칭 실패 등)
    gate             JSONB,                    -- 제목·소개란 판단 결과
    store_count      INT,
    michelin_pending BOOLEAN      NOT NULL DEFAULT FALSE,   -- Parse 하루 한도 등으로 미슐랭을 아직 못 채움
    attempts         INT          NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_video_queue_status ON video_queue (status);
