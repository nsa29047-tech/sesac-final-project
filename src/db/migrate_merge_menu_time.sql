-- 메뉴 시각 컬럼을 mentioned_sec 하나로 합친다(영상 분석 first_appearance_sec 도 같은 칸에 저장). 여러 번 실행해도 안전하다.
--   영상 분석: 음식이 화면에 처음 보이는 시각 / 자막 분석: 서빙·시식 단서 또는 첫 언급 자막 시각(추정치)
UPDATE menus SET mentioned_sec = first_appearance_sec WHERE mentioned_sec IS NULL AND first_appearance_sec IS NOT NULL;
ALTER TABLE menus DROP COLUMN IF EXISTS first_appearance_sec;
