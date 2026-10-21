# 맛집 노트 웹 (Next.js)

챗봇 HTTP API(`src/api/chat_server.py`)를 쓰는 화면이다. 설계는 `docs/CHATBOT_API_DESIGN.md`.
Next.js 16(App Router, `cacheComponents`), React 19, Tailwind CSS 4, Leaflet(react-leaflet), react-markdown, lucide-react(아이콘), Pretendard(글꼴, npm 패키지로 자체 호스팅).

> 이 Next.js 버전은 기존 지식과 다를 수 있다. 코드를 고치기 전에 `node_modules/next/dist/docs/` 의 해당 문서를 읽는다(`AGENTS.md`).

## 실행 (로컬 데모)

```bash
uv run python src/api/chat_server.py      # 1) 챗봇 API  http://127.0.0.1:8000  (프로젝트 루트에서)
npm --prefix web install                   # 2) 처음 한 번
npm --prefix web run dev                   # 3) 화면       http://localhost:3000
```

- API 주소를 바꾸려면 `web/.env.local` 에 `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` 처럼 적고 개발 서버를 다시 시작한다(빌드 때 코드에 들어가는 값이다).
- API 서버의 CORS 는 `http://localhost:3000` 만 허용한다. 다른 주소로 열면 서버의 `CHAT_ALLOWED_ORIGINS` 도 맞춘다.
- 확인: `npm --prefix web run lint`, `npx --prefix web tsc --noEmit -p web`, `npm --prefix web run build`.

## 구조

| 파일 | 설명 |
|---|---|
| `src/app/page.tsx`, `layout.tsx` | 서버 컴포넌트. 페이지는 `<Chat />` 만 그린다 |
| `src/components/Chat.tsx` | 화면 전체(클라이언트). 상단 바 + 채팅 + (넓은 화면) 오른쪽 결과 패널. 질문 전송, SSE 스트리밍, 중지, 새 대화, 오류 표시. 결과가 있으면 넓은 화면(lg 이상)에서는 채팅 옆 패널에 지도·카드를 보여 주고, 좁은 화면에서는 답변 아래에 둔다 |
| `src/components/AnswerMarkdown.tsx` | 답변 본문. `**이름** - 설명` 목록 항목을 번호 배지 + 이름(굵은 주황) + 설명(연한 회색)으로 나눠 그린다. 이름이 카드의 식당과 같으면 눌러서 상세를 연다 |
| `src/components/StepChips.tsx` | 답변이 만들어지는 동안의 진행 단계 칩(조건 확인 → 식당 찾는 중 → 답변 작성) |
| `src/components/Welcome.tsx` | 첫 화면: 인사와 아이콘이 있는 예시 질문 카드 6개 |
| `src/components/ResultsList.tsx` | 추천 결과(지도 + 카드 목록). 결과 패널과 좁은 화면의 답변 아래에서 같이 쓴다 |
| `src/components/RestaurantCard.tsx` | 추천 식당 카드(썸네일, 미슐랭 배지, 평점, 가격대, 셰프, 상세 정보 보기, 영상·지도 링크) |
| `src/components/DetailDialog.tsx` | 식당 상세 팝업(브라우저 `<dialog>`): 메뉴 타임라인, 영상 속 소개·총평, 음료, 기본 정보(주소·가격·영업시간), 위치 지도. 카드 버튼이나 지도 마커 말풍선의 버튼으로 연다 |
| `src/components/VideoPlayer.tsx` | 유튜브 IFrame API 플레이어와 메뉴 타임라인. 메뉴를 누르면 그 시각으로 이동해 재생하고 재생 중 현재 메뉴를 강조한다. 플레이어는 스크롤해도 위에 고정 |
| `src/components/RestaurantMap.tsx`, `MapSection.tsx` | 번호 마커 지도(Leaflet + OpenStreetMap). 카드에 없는 나머지 후보(`map_extra`)는 번호 없는 작은 주황 점으로 함께 찍고 지도 범위에 포함한다. Leaflet 은 서버 렌더링에서 빼려고 `ssr:false` 로 불러온다 |
| `src/lib/api.ts` | `streamChat`(fetch + SSE 파서), API 주소 |
| `src/lib/types.ts` | API 응답 형식(서버 `src/chatbot/schemas.py` 와 맞춤) |
| `src/lib/format.ts` | 도구·인자 라벨, 답변 본문의 마크다운 링크 제거 등 표시용 도우미 |

## 알려진 점

- 글꼴·CSS 패키지를 새로 설치하거나 `globals.css` 의 `@import` 를 바꾼 뒤에는 개발 서버를 다시 시작해야 반영된다(핫 리로드가 새 CSS 를 못 가져오는 경우가 있었다).
- 대화 ID 는 첫 질문을 보낼 때 만든다(`cacheComponents` 에서는 렌더링 중 난수를 쓸 수 없다). 대화 기록은 저장하지 않아 새로고침하면 사라진다.
- 상세 팝업의 식당 정보는 한 번 받으면 새로고침 전까지 캐시한다. 영상 화면은 유튜브가 그리므로 외부 영상을 막는 환경(일부 사내망·브라우저 창)에서는 검게 보일 수 있다(재생이 안 되면 `YouTube에서 보기` 링크를 보여 준다).
