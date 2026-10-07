-- 식당의 셰프 정보 컬럼 추가. 여러 번 실행해도 안전하다(기존 데이터는 건드리지 않는다).
--   chef_name : 대표 셰프 이름. 여러 명이면 쉼표로 이어서 적는다("박정현, 박정은"). 근거가 약한 이름은 넣지 않고 NULL 로 둔다.
--   chef_info : 셰프 경력·수상·특징 요약. 영상에서 한 말을 옮긴 것이라 검증된 사실이 아니다.
-- 값은 src/db/load_chefs.py 가 data/chef_notes/ 의 추출 결과를 합쳐 채운다.
ALTER TABLE restaurants
    ADD COLUMN IF NOT EXISTS chef_name VARCHAR(300),
    ADD COLUMN IF NOT EXISTS chef_info TEXT;
