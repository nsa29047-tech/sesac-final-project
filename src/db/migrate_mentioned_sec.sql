-- 자막 분석으로 얻은 메뉴 시각 컬럼 추가. 여러 번 실행해도 안전하다(기존 데이터는 건드리지 않는다).
--   first_appearance_sec : 영상 분석 - 음식이 화면에 처음 보이는 시각(초)
--   mentioned_sec        : 자막 분석 - 서빙·시식 단서가 있는 자막 또는 메뉴가 처음 언급된 자막의 시각(초). 화면 등장 시각의 추정치
-- 챗봇은 COALESCE(first_appearance_sec, mentioned_sec) 로 쓰되, mentioned_sec 만 있으면 "영상 N분 근처에서 언급"이라고 안내한다.
ALTER TABLE menus ADD COLUMN IF NOT EXISTS mentioned_sec INTEGER;
