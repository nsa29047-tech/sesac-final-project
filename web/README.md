# 맛집 노트 웹 (Next.js)

챗봇 HTTP API(`src/api/chat_server.py`)를 쓰는 화면이다. 설계는 루트의 `CHATBOT_API_DESIGN.md`.
Next.js 16(App Router, `cacheComponents`), React 19, Tailwind CSS 4, Leaflet(react-leaflet), react-markdown.

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
| `src/components/Chat.tsx` | 채팅 화면(클라이언트). 질문 전송, SSE 스트리밍 표시, 도구 호출 진행, 중지, 새 대화, 오류 표시 |
| `src/components/RestaurantCard.tsx` | 추천 식당 카드(썸네일, 미슐랭 배지, 평점, 가격대, 셰프, 상세 정보 보기, 영상·지도 링크) |
| `src/components/DetailDialog.tsx` | 식당 상세 팝업(브라우저 `<dialog>`): 메뉴 타임라인, 영상 속 소개·총평, 음료, 기본 정보(주소·가격·영업시간), 위치 지도. 카드 버튼이나 지도 마커 말풍선의 버튼으로 연다 |
| `src/components/VideoPlayer.tsx` | 유튜브 IFrame API 플레이어와 메뉴 타임라인. 메뉴를 누르면 그 시각으로 이동해 재생하고 재생 중 현재 메뉴를 강조한다. 플레이어는 스크롤해도 위에 고정 |
| `src/components/RestaurantMap.tsx`, `MapSection.tsx` | 번호 마커 지도(Leaflet + OpenStreetMap). Leaflet 은 서버 렌더링에서 빼려고 `ssr:false` 로 불러온다 |
| `src/lib/api.ts` | `streamChat`(fetch + SSE 파서), API 주소 |
| `src/lib/types.ts` | API 응답 형식(서버 `src/chatbot/schemas.py` 와 맞춤) |
| `src/lib/format.ts` | 도구·인자 라벨, 답변 본문의 마크다운 링크 제거 등 표시용 도우미 |

## 알려진 점

- 대화 ID 는 첫 질문을 보낼 때 만든다(`cacheComponents` 에서는 렌더링 중 난수를 쓸 수 없다). 대화 기록은 저장하지 않아 새로고침하면 사라진다.
- 상세 팝업의 식당 정보는 한 번 받으면 새로고침 전까지 캐시한다. 영상 화면은 유튜브가 그리므로 외부 영상을 막는 환경(일부 사내망·브라우저 창)에서는 검게 보일 수 있다(재생이 안 되면 `YouTube에서 보기` 링크를 보여 준다).
