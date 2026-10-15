# 챗봇 API 설계 (Next.js 전환용)

> 상태: 설계안(미구현, 10/14). 로컬 데모로 쓰기로 정했다(6절 참고). 아래 본문은 공개 배포까지 가정해 쓴 것이고, 로컬 데모에서 생략·단순화하는 부분은 6절에 표시했다.
> 목적: 지금 Streamlit이 LangGraph를 직접 호출하고 파이썬이 HTML 카드를 만드는 구조를, Next.js가 HTTP API로 쓸 수 있게 나눈다.

## 1. 지금 구조와 문제

```
Streamlit(streamlit_app.py)
  ├─ build_graph(MemorySaver, cards=True).stream(...)   # LangGraph를 같은 프로세스에서 직접 호출
  ├─ cards.py / detail.py / maps.py                      # 식당 데이터를 HTML 문자열로 변환
  └─ fetch_detail(restaurant_id)                         # 상세 팝업용 DB 조회
```

Next.js로 옮길 때 걸리는 점:

| 문제 | 현재 | 필요한 것 |
|---|---|---|
| 호출 방식 | 같은 프로세스의 함수 호출 | HTTP API, 토큰 스트리밍 |
| 표현과 데이터가 섞임 | `card_html`, `info_html`, `player_html`이 데이터를 HTML로 바꿈 | API는 JSON만, 화면은 React가 그림 |
| 영상 정보가 문자열 | `videos` = `"제목 url \| 제목 url"`. 제목에 `\|`가 있으면(예: `... \| Epicure`) 쪼개기가 깨진다 | 영상 목록을 구조화한 필드 |
| 대화 기억 | 프로세스 메모리의 `MemorySaver` | 서버 재시작·여러 프로세스에서도 일관된 기억(또는 한계를 명시) |
| DB 연결 | `run_query`가 호출마다 새 연결을 연다 | 연결 풀(동시 요청 대응) |
| 지도 마커 | iframe이라 클릭을 앱에 못 돌려줌 → 마커로 상세를 못 연다 | React 지도 컴포넌트면 가능 |

## 2. 전체 구조

```
브라우저
  └─ Next.js (web/)  ── 페이지, 채팅 UI, 카드, 지도, 상세 팝업
        └─ Route Handler (BFF)  ── /api/chat, /api/restaurants/[id]   (서버에서만 키를 가진다)
              └─ chat_server.py (FastAPI, src/api/)  ── LangGraph, 읽기 전용 DB
                    ├─ OpenAI (gpt-4o-mini)
                    ├─ Google Places (지명 좌표, 하루 100회 한도)
                    └─ PostgreSQL (READONLY_POSTGRES_URI 만)
```

- **`pipeline_server.py`와 합치지 않는다.** 그쪽은 쓰기 권한(`POSTGRES_URI`)과 파이프라인 키를 가진 내부용이고, 챗봇 API는 읽기 전용 계정만 가진 공개용이다. 프로세스와 환경변수를 분리해 챗봇 쪽이 뚫려도 쓰기 권한이 없게 한다.
- 챗봇 서버 환경변수: `OPENAI_API_KEY`, `READONLY_POSTGRES_URI`, `GOOGLE_PLACES_API_KEY`, `CHAT_API_KEY`(BFF가 보내는 키). `POSTGRES_URI`는 넣지 않는다.
- **BFF를 두는 이유**: 브라우저에 API 키를 넣을 수 없다. Next.js의 서버 코드(Route Handler)가 키를 붙여 챗봇 서버로 중계하고, 브라우저는 키를 모른다.

## 3. 엔드포인트

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/chat` | 질문을 보내고 **SSE로** 답변을 받는다 |
| GET | `/api/restaurants/{id}` | 식당 상세(팝업용) JSON |
| GET | `/api/health` | 서버, DB 연결 확인 |

대화 목록·삭제 API는 두지 않는다. 대화는 클라이언트가 만든 `thread_id`(UUID)로 구분하고, 새 대화 = 새 `thread_id`다. 화면에 보이는 기록은 브라우저(localStorage)가 가진다.

### POST /api/chat

요청:

```json
{ "thread_id": "uuid", "message": "강남역 근처 일식집" }
```

응답은 `text/event-stream`이고 이벤트는 다음과 같다. **한 요청에 아래 순서로 나온다.**

| event | data | 의미 |
|---|---|---|
| `status` | `{"tool": "find_restaurants", "args": {"city": "강남역", "cuisine": "일식"}}` | 도구를 부를 때마다(0회 이상). 화면의 "검색 중" 표시용. 라벨 문구는 프론트가 가진다 |
| `token` | `{"text": "강남역 근처에는 "}` | 답변의 일부(여러 번). 이어 붙여 보여 준다 |
| `restaurants` | `{"restaurants": [Restaurant, ...], "map_extra": [Restaurant, ...]}` | 답변이 끝난 뒤 한 번. `restaurants`는 카드와 번호 마커에 쓴다(답변에 이름이 나온 식당만, 처음 언급된 순서로, 최대 6곳). `map_extra`는 카드로 소개하지 않았지만 조건에 맞는 식당으로 지도에만 번호 없는 점으로 찍는다(없으면 빈 배열, `find_restaurants` 결과일 때만) |
| `done` | `{"answer": "전체 답변 텍스트"}` | 정상 종료 |
| `error` | `{"code": "...", "message": "..."}` | 오류. 이후 `done`은 오지 않는다 |

`restaurants`의 번호(1, 2, …)는 배열 순서이고, 카드 번호와 지도 마커 번호가 같다.

예:

```
event: status
data: {"tool":"find_restaurants","args":{"city":"강남역","cuisine":"일식"}}

event: token
data: {"text":"강남역 근처 일식집을 "}
...
event: restaurants
data: {"restaurants":[{"restaurant_id":120, ...}]}

event: done
data: {"answer":"강남역 근처 일식집을 ..."}
```

- 취소: 브라우저가 연결을 끊으면(새 질문, 페이지 이동) 서버가 `request.is_disconnected()`로 감지해 그래프 실행을 멈춘다(LLM 호출 비용 절약).
- 서버는 `cards=True` 프롬프트(링크·가격 반복 금지)를 쓴다. 답변 본문의 마크다운 링크 제거(`strip_links`)는 프론트에서 같은 정규식으로 한다(스트리밍 중에는 서버가 못 지운다).

### GET /api/restaurants/{id}

`fetch_detail`의 결과를 JSON으로 낸다. 없으면 404.

## 4. 데이터 형식

`Restaurant`(카드·지도·상세 공통의 기본 필드, `summarize()`가 이미 가진 값):

```ts
interface Restaurant {
  restaurant_id: number
  name: string                    // name_ko 우선
  name_official: string | null
  country_code: string
  region: string[]                // ["서울특별시", "강남구"]  (지금은 " > " 로 이은 문자열)
  address: string | null
  category_broad: string | null
  category_detail: string | null
  tags: string[]                  // 지금은 ", " 로 이은 문자열
  price_level: number | null      // 0~4
  price: { min: number, max: number | null, currency: string } | null
  rating: number | null
  rating_count: number | null
  michelin: { grade: string, label: string } | null   // grade: "1_STAR" 등, 미슐랭 아님이면 null
  chef_name: string | null
  latitude: number | null
  longitude: number | null
  google_maps_url: string | null
  videos: Video[]                 // 구조화된 영상 목록 (아래)
  matched?: { type: string, item: string | null, content: string }[]   // 취향 검색이 맞춘 근거(최대 3). 카드의 인용문
}

interface Video { video_id: string, title: string, url: string, thumbnail: string }   // thumbnail = https://img.youtube.com/vi/{id}/mqdefault.jpg
```

`RestaurantDetail` = `Restaurant` + 다음.

```ts
{
  phone: string | null, website: string | null, chef_info: string | null,
  hours: { day: number /*0=일*/, closed: boolean, intervals: { open: "HH:MM", close: "HH:MM" }[] }[],
  notes: { video_id, title, url, concept, atmosphere, key_points: string[], final_review, is_course, course_name }[],
  menus: { video_id, item_type: "FOOD" | "DRINK", name, description, course_stage, menu_order, mentioned_sec: number | null }[]
}
```

### 변환 계층 (`src/chatbot/serialize.py`, 새로 만든다)

DB 행과 도구 결과(LLM이 보는 JSON)를 위 형식으로 바꾸는 곳이다. **LLM에게 가는 도구 결과는 건드리지 않는다**(평가셋 점수가 바뀔 수 있다). API용 필드만 변환 계층에서 만든다.

- `videos`: 도구 결과의 문자열을 쪼개지 않고, 변환 계층이 **영상 목록을 DB에서 따로 조회**한다(`serialize.videos_of`, 식당 여러 곳을 쿼리 한 번으로). `summarize()`와 LLM이 보는 도구 결과는 전혀 바꾸지 않는다(처음에는 `summarize()` SQL에 JSON 컬럼을 추가하려 했으나, 그러면 LLM 입력에도 필드가 늘어 평가 점수가 흔들릴 수 있어 따로 조회하는 방식으로 바꿨다). 제목에 ` | `가 있어도 안전하다.
- `region`, `tags`: 문자열을 배열로 바꾼다.
- `michelin`: 라벨 문자열("미슐랭 1스타")을 `{grade, label}`로. "미슐랭 아님"은 `null`.
- `hours`: `datetime.time`을 `"HH:MM"` 문자열로(JSON 직렬화 오류 방지).
- 스키마는 Pydantic 모델로 정의해(`src/chatbot/schemas.py`) FastAPI가 검증·문서화(`/docs`)하게 한다. 프론트의 TypeScript 타입은 이 모델에서 생성하거나(openapi-typescript) 손으로 맞춘다.

## 5. 서버 내부(`src/api/chat_server.py`)

- `build_graph(checkpointer, cards=True)`를 서버 시작 시 한 번 만들어 재사용한다. 지금 Streamlit이 쓰는 `get_graph()`와 같다.
- 스트리밍은 Streamlit의 `stream_answer`와 같은 로직이다(`stream_mode=["messages", "updates"]`).
  - `messages` 모드에서 `agent`·`chat` 노드의 토큰 → `token` 이벤트
  - `updates`의 `agent` 노드 도구 호출 → `status` 이벤트
  - `updates`의 `tools` 노드 결과를 `cards.collect()`로 모으고, 끝에서 `cards.pick()`으로 답변에 나온 식당만 골라 `restaurants` 이벤트
- `collect`·`pick`은 HTML이 아니라 순수 데이터 로직이라 그대로 재사용한다(HTML을 만드는 `card_html` 등은 쓰지 않는다).
- **DB 연결 풀**: `tools.run_query`가 호출마다 `rs.connect()`로 새 연결을 연다. 동시 요청이 생기므로 `psycopg2.pool.ThreadedConnectionPool`로 바꾼다(읽기 전용 설정은 유지).
- 그래프 실행은 동기 코드(LangChain 호출)이므로 스레드풀에서 돌리거나 `graph.astream`을 쓴다. 한 요청당 LLM 호출이 여러 번(라우터, 에이전트, 후보 판단, 최종 답변)이라 응답이 수 초 걸리고, 그동안 `status`/`token`으로 진행을 보여 준다.

### 대화 기억

| 선택 | 설명 | 판단 |
|---|---|---|
| **A. `MemorySaver`(프로세스 메모리)** | 지금과 같다. 서버를 재시작하면 기억이 사라지고, 서버 프로세스를 여러 개 띄우면 같은 대화가 다른 프로세스에 가서 기억이 끊긴다 | **v1로 충분**. 서버 1개(워커 1개)로 운영, 한계를 문서에 명시 |
| B. PostgreSQL 체크포인터 | 재시작·다중 프로세스에서도 유지 | 체크포인트 테이블에 **쓰기**가 필요하다. 읽기 전용 계정 원칙과 충돌하므로 별도 스키마·별도 계정(그 스키마에만 권한)이 필요. 나중 단계 |

- 메모리가 무한히 늘지 않게 오래된 `thread_id`를 정리하는 규칙(예: 마지막 사용 후 일정 시간 지나면 삭제)이 필요하다.

### 보호 장치 (공개하면 필요)

- **요청 제한**: IP(또는 BFF가 전달하는 클라이언트 식별자)별 분당 요청 수 제한. 한 질문이 OpenAI 호출 여러 번과 경우에 따라 Places 호출을 쓰므로 비용 보호용이다. Places 지명 좌표는 하루 100회 한도라 소진되면 지명 질문이 실패한다(캐시 `data/geocode_cache.json`이 있어 같은 지명은 호출하지 않는다).
- 입력 길이 제한(메시지 500자 정도), 응답 시간 제한(약 60초).
- CORS는 BFF만 허용하거나, 서버를 같은 호스트의 내부망에 두고 외부에 열지 않는다.
- 오류 응답에는 내부 정보(키, 스택)를 넣지 않는다.

## 6. 결정 (10/14)

**1. 로컬 데모로 쓴다(사용자 결정).** 인터넷에 공개하지 않고 내 PC에서 챗봇 서버(`127.0.0.1`)와 Next.js(`localhost:3000`)를 띄워 쓴다. 이에 따라 정한 것:

| 항목 | 공개 배포였다면 | 로컬 데모에서는 |
|---|---|---|
| BFF(Next.js Route Handler) | 필요(키를 서버에 숨기기) | **생략**. 브라우저가 챗봇 서버를 직접 호출한다. OpenAI·Places 키는 어차피 챗봇 서버 쪽에만 있고, 브라우저에 키를 넣지 않는다 |
| 접근 제어 | `X-API-Key` + 요청 제한 | 서버를 `127.0.0.1`에만 바인딩하고 CORS는 `http://localhost:3000`만 허용. 키 인증과 요청 제한은 **만들지 않는다**. 단, 서버를 `0.0.0.0`으로 열거나 외부에 노출하게 되면 그때 추가한다 |
| 입력 길이·응답 시간 제한 | 필요 | 간단한 제한(메시지 500자, 60초)만 둔다 |
| 대화 기억 | 영속 저장 검토 | **`MemorySaver`(서버 메모리)**. 서버를 재시작하면 대화 기억이 사라지고, 서버는 프로세스 1개로 띄운다. 오래된 `thread_id` 정리는 하지 않는다(데모 규모) |
| 배포·도메인 | 필요 | 불필요 |
| 읽기 전용 DB 계정 | 필수 | **그대로 필수**(챗봇 서버에 쓰기 권한 계정을 주지 않는다). 로컬이어도 지키는 원칙이다 |

로컬이라도 하는 것: 읽기 전용 계정만 사용, 서버 환경변수에 `POSTGRES_URI`를 넣지 않기, 오류 응답에 키·스택을 넣지 않기, 연결 풀 적용.

**2~5. 기본값으로 진행한다(바꾸고 싶으면 알려 준다).**
- 대화 기억은 `MemorySaver`.
- Streamlit은 당분간 병행한다(같은 `build_graph`를 쓰므로 부담이 없다). Next.js가 안정되면 제거할지 다시 정한다.
- 직접 FastAPI로 감싼다(`langgraph dev`는 쓰지 않는다).

## 7. 단계별 계획

| 단계 | 내용 | 확인 방법 |
|---|---|---|
| 0 (완료 10/14) | `schemas.py`, `serialize.py` 작성, `test_serialize.py`로 시험(단위 4 + 실제 데이터 1). `tools.py`·`summarize()`는 수정하지 않아 LLM 입력이 그대로이므로 평가셋은 다시 돌리지 않았다 | `uv run python src/chatbot/test_serialize.py` 5/5 통과 |
| 1 (완료 10/14) | `chat_server.py`: `/api/chat`(SSE), `/api/restaurants/{id}`, `/api/health`, 연결 풀(`rag_search.enable_pool`), 연결 끊김 취소, 같은 대화 동시 요청 409, CORS, 입력·시간 제한 | `smoke_chat_api.py` 13/13, 서로 다른 대화 4개 동시 요청 시험 통과 |
| 2 (완료 10/14) | Next.js(`web/`): 채팅 화면, SSE 스트리밍 표시, 도구 호출 진행, 카드, 지도(react-leaflet). BFF는 로컬 데모라 생략 | 브라우저에서 예시 질문, 후속 질문(대화 기억), 서버 꺼짐 오류 화면 확인 |
| 3 (완료 10/14) | 상세 팝업: 유튜브 IFrame API 플레이어 + 메뉴 타임라인(누르면 `seekTo`, 재생 중 강조), 영업시간, 지도 마커 말풍선에서 상세 열기 | Streamlit 팝업과 같은 정보가 나오는지, 타임라인 클릭 이동·강조, ESC 닫기, 마커로 열기, 모바일 폭 |
| 4 | (로컬 데모에서는 생략) 요청 제한, 기억 정리, PostgreSQL 체크포인터, 배포. 대신 오류 화면(서버 꺼짐, 시간 초과)과 `127.0.0.1`·CORS 설정만 확인 | 서버를 끄고 화면이 오류를 보여 주는지 |

각 단계가 끝나면 이 문서와 README, CLAUDE.md를 갱신한다.

## 8. 위험 요소

- **응답 속도**: 한 질문에 LLM 호출이 여러 번이라 수 초~십수 초다. 스트리밍과 `status` 이벤트로 체감을 줄이되, 근본적으로는 호출 수를 줄이는 별도 작업이 필요할 수 있다.
- **비용**: 공개 시 OpenAI와 Places 비용이 요청 수에 비례한다. 요청 제한이 필수다.
- **평가셋 영향**: 변환 계층은 LLM이 보는 도구 결과를 건드리지 않는다(`tools.py` 무수정). 이후 단계에서 `tools.py`·`graph.py`를 고치게 되면 평가셋을 다시 돌려 확인한다(Places 한도 때문에 `--no-geo`).
- **프론트 재구현 범위**: 상세 팝업의 타임라인·플레이어 동기화(재생 중 메뉴 강조)가 가장 손이 많이 간다. 단계 3에서 별도로 다룬다.
