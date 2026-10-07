# 자막 원문 + 식당 개요 하이브리드 RAG 설계안 (초안, 2026-10-07)

수동 자막 34개 영상(2026-01-01 이후) 비교 결과를 근거로 정리한 설계다. 구현 전 초안이며, 확정되면 CLAUDE.md·README에 반영한다.

## 1. 결정 사항

| 항목 | 결정 | 근거 |
|---|---|---|
| 식당 정보 | 소개란 가게명·주소 → Places 검색. **가격대 필드 추가** | 기존 규칙(가격대 미저장)을 바꾼다. 채워지는 비율은 샘플로 확인 필요 |
| 미슐랭 | 보류. `--skip-michelin` 유지, 나중에 `fill_michelin.py` | 일일 100회 한도 |
| 자막 수집 | 수동 자막(`manual_ko`) 34개로 먼저 진행. 자동 생성 자막은 보류 | 자동 자막은 오타가 많아 추출·검색 품질이 낮다 |
| 벡터 저장 ① | 자막 원문을 **500자 청크**로 잘라 저장(`TRANSCRIPT`) | 1000자는 의미·키워드 질문에서 모두 나빴다(1위 9개 대 7개) |
| 벡터 저장 ② | 추출 결과 중 **식당당 OVERVIEW 청크 1개**만 임베딩 | OVERVIEW 49개만으로 추출 전체(298개)와 비슷. FOOD/DRINK 청크는 원문과 합치면 오히려 나빠졌다 |
| 추출 결과 | DB에만 적재(메뉴·코스·타임라인·후기·특징·키 포인트·셰프·총평) | 검색은 ①②가 맡고, 추출 결과는 SQL 조회와 답변 표시에 쓴다 |
| 검색 합치기 | 식당 단위 RRF(k=60) | 점수 분포가 달라 점수를 직접 합칠 수 없다 |

비교 결과(21개 질문, 식당 49곳, 정답 식당 1위 / 3위 이내 / 평균순위):
원문 11/15/4.0, OVERVIEW 13/19/3.0, 원문+OVERVIEW 16/19/2.4, 원문+OVERVIEW+FOOD·DRINK 16/19/1.7.
질문은 직접 만든 소규모 시험이고 추출 데이터는 v2 결과라, 구현 후 같은 질문으로 다시 확인한다(`data/transcript_chunks/_eval_types.py`).

## 2. 파이프라인

1. **식당 추출** `extract_restaurant_info.py --skip-michelin`: 소개란 → 상호·주소 → Places. `priceLevel`, `priceRange`를 요청 필드에 추가한다.
2. **구간 나누기** `chunk_transcripts.py`: 식당 1곳이면 영상 전체, 여러 곳이면 소개란 챕터("MM:SS 가게명")의 시각을 그대로 쓴다. 챕터에 가게 이름이 다 없으면 모델이 나눈다. 모델이 나눈 영상은 경계를 사람이 확인한다.
3. **원문 청크** 같은 스크립트: 구간별로 500자(겹침 100자)로 자르고 메타데이터를 붙여 `TRANSCRIPT` 청크로 임베딩한다.
4. **정보 추출** `extract_script_course.py`(+ v7 후처리): 식당별 구간의 자막만 입력으로 메뉴(음식·음료)·코스 순서·등장 시각·후기·특징·키 포인트·셰프·총평을 뽑는다. 다른 가게 내용이 섞이지 않게 2번의 구간을 입력 단위로 쓴다.
5. **적재** `load_notes.py`(메뉴·노트), `load_chefs.py`, 신규 `load_transcript_chunks.py`, `embed_chunks.py`(OVERVIEW만).

## 3. 스키마 변경(안)

```sql
-- 식당: Places 가격대 (대부분 NULL일 수 있다)
ALTER TABLE restaurants ADD COLUMN price_level VARCHAR(30);        -- PRICE_LEVEL_MODERATE 등 Places enum 그대로
ALTER TABLE restaurants ADD COLUMN price_range_min INTEGER;        -- priceRange.startPrice
ALTER TABLE restaurants ADD COLUMN price_range_max INTEGER;
ALTER TABLE restaurants ADD COLUMN price_currency CHAR(3);

-- 메뉴: 코스 묶음과 순서. 단품 주문이면 course_label NULL
ALTER TABLE menus ADD COLUMN course_label VARCHAR(100);            -- 예: "런치 코스", "디너 코스"
ALTER TABLE menus ADD COLUMN serve_order SMALLINT;                 -- 이 가게에서 나온 순서(1부터)

-- 청크: 원문 청크 추가. FOOD/DRINK 는 더 만들지 않는다(기존 행은 유지하거나 정리)
ALTER TABLE restaurant_chunks DROP CONSTRAINT restaurant_chunks_chunk_type_check;
ALTER TABLE restaurant_chunks ADD CONSTRAINT restaurant_chunks_chunk_type_check
    CHECK (chunk_type IN ('OVERVIEW', 'FOOD', 'DRINK', 'TRANSCRIPT'));
ALTER TABLE restaurant_chunks ADD COLUMN start_sec INTEGER;        -- TRANSCRIPT 만 사용
ALTER TABLE restaurant_chunks ADD COLUMN end_sec INTEGER;
-- chunk_key: TRANSCRIPT 는 'seg-0', 'seg-1', ... (영상·식당 안에서 순번)
```

- 타임라인은 기존 `menus.mentioned_sec`를 쓴다(화면 등장 시각은 `first_appearance_sec`).
- 총평은 `video_restaurant_notes.final_review`, 특징은 `concept`·`atmosphere`, 키 포인트는 `key_points`, 셰프는 `restaurants.chef_name`·`chef_info`를 그대로 쓴다.
- 청크 content: TRANSCRIPT는 자막 텍스트만(식당 이름 없음, 식당은 `restaurant_id`와 SQL로 먼저 좁힌다). OVERVIEW는 기존대로 식당 이름·분류·컨셉·분위기·총평·셰프를 합친 문장이다.
- 모든 적재는 upsert로 여러 번 실행해도 중복되지 않게 한다(`google_cid` 기준).

## 4. 챗봇 검색 흐름

1. 질문에서 지역·영업 여부·미슐랭·가격대 조건을 뽑아 SQL로 식당 후보를 좁힌다.
2. 후보 식당의 `OVERVIEW`와 `TRANSCRIPT` 청크를 각각 벡터 검색하고, 식당 단위 최고 유사도로 순위를 매긴 뒤 RRF로 합친다.
3. 상위 식당마다 `TRANSCRIPT`에서 질문과 가까운 청크를 근거로 가져온다(시각 포함, 앞뒤 청크 합치기 가능).
4. 답변은 DB의 정형 정보(영업시간·평점·가격대), 총평·특징, 근거 청크로 쓴다. "OO 식당 메뉴" 질문은 `menus`를 조회해 답한다.
5. 정확한 메뉴 이름 검색은 `menus.name`/태그로 SQL(`ILIKE`, 필요하면 `pg_trgm`)을 병행한다.

## 5. 위험과 확인할 것

- **가격대 채워지는 비율**: 한국·해외 식당에서 많이 비어 있을 수 있다. 5~10곳 샘플로 먼저 확인하고, 낮으면 보조 필터로만 쓴다.
- **구간 경계**: 챕터가 없는 영상은 모델이 나눠서 틀릴 수 있다. 34개 중 챕터로 나눈 영상은 현재 1개뿐이다. 식당이 여러 곳인 5개 영상은 구현 전에 경계를 확인한다.
- **코스 추출**: `extract_script_course.py`가 순서(`order`)를 이미 뽑지만 `course_label`(코스 묶음)은 아직 없다. 프롬프트와 후처리를 확장하고 샘플로 검증한다.
- **OVERVIEW 이름 포함**: 이름이 들어가서 이름과 겹치는 질문에 유리하다. 비교 수치에도 반영돼 있으니 이름 없는 개요 문장으로 한 번 더 확인한다.
- **추출 환각·누락**: 아직 검증하지 못했다. 원문 청크가 근거와 교차 확인 수단이 된다.
- **임베딩 일관성**: 모델은 `text-embedding-3-small`(1536차원)로 통일한다. 청크 크기나 모델을 바꾸면 전체를 다시 임베딩한다.

## 6. 진행 순서(각 단계는 소수 샘플 확인 후 전체)

1. Places 가격대 샘플 5~10곳 → 채워지는 비율 확인.
2. 식당이 여러 곳인 5개 영상의 구간 경계 확인.
3. 34개 영상 추출 시험(`extract_script_course.py` + 코스 확장) → 건수·비용 안내 후 실행.
4. 스키마 마이그레이션 → 적재 스크립트 작성 → 34개 영상 적재.
5. 같은 질문 21개로 DB 검색 재평가(원문+OVERVIEW, 필요하면 SQL 키워드 검색 추가).
6. 결과를 보고 자동 생성 자막 영상과 나머지 영상으로 확대.

## 7. 기존 자산

- `extract_script_v7.py`의 후처리(시각 보정, 평가어 제거, 구어체 정리, 근거 검수)와 `extract_script_course.py`는 그대로 재사용한다.
- 기존 DB의 v2 추출 결과(165곳)와 FOOD/DRINK 청크는 수동 자막 34개 영상 범위에서 새 결과로 교체한다. 교체 전 `backup_tables.py`로 백업한다.
- `chunk_transcripts.py`와 구간 경계(`data/transcript_chunks/_boundaries/`)는 실험용 산출물이다. DB 적재 스크립트가 같은 로직을 쓰도록 맞춘다.
