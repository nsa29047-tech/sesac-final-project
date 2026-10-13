"""맛집 추천 챗봇 Streamlit UI. 실행: uv run streamlit run src/streamlit_app.py

LangGraph 그래프(chatbot.graph.build_graph)를 직접 호출한다. 답변은 토큰 단위로 스트리밍하고, 도구를 호출하는 동안에는
"검색 중" 상태 표시에 어떤 조건으로 찾는지 보여 준다. 추천된 식당은 도구 결과(구조화 데이터)로 카드를 그린다(chatbot.cards).
"""
import sys
import uuid
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")   # 라우터 구조화 출력이 스트림에 섞여 나오는 무해한 경고

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from chatbot.cards import CSS, card_html, collect, esc, pick, strip_links  # noqa: E402
from chatbot.detail import TL_CSS, drinks_html, fetch_detail, first_thumb, hours_html, player_html  # noqa: E402
from chatbot.graph import build_graph  # noqa: E402

EXAMPLES = [
    "서울에 있는 미슐랭 스타 식당 알려줘",
    "바삭하게 튀긴 음식이 맛있는 곳 추천해줘",
    "강남역 근처 일식집",
    "파리에 있는 미슐랭 식당",
    "밍글스 메뉴랑 영업시간 알려줘",
    "흑백요리사에 나온 셰프의 식당",
]
TOOL_LABEL = {"find_restaurants": "조건에 맞는 식당 찾는 중", "recommend_by_taste": "취향에 맞는 식당 찾는 중",
              "get_restaurant_info": "식당 정보 확인 중"}
ARG_LABEL = {"country_code": "국가", "exclude_country_code": "제외 국가", "region": "지역", "city": "장소", "michelin": "미슐랭",
             "min_price_level": "가격대 하한", "max_price_level": "가격대 상한", "min_rating": "최소 평점", "cuisine": "분류",
             "has_chef": "셰프 정보", "course_only": "코스", "open_day": "요일", "open_time": "시각", "radius_km": "반경(km)",
             "query": "검색어", "chunk_type": "종류", "name": "식당", "sort_by": "정렬", "limit": "개수"}

PAGE_CSS = """<style>
.block-container{max-width:920px;padding-top:2.2rem}
.hero{margin:0 0 .6rem}
.hero h1{font-size:1.9rem;font-weight:800;margin:0;color:#222}
.hero h1 span{color:#ff5a3c}
.hero p{color:#888;font-size:.9rem;margin:.2rem 0 0}
section[data-testid="stSidebar"] .stButton>button{border-radius:999px;border:1px solid #ffd2c5;background:#fff;color:#e0481f;font-size:.85rem;text-align:left}
section[data-testid="stSidebar"] .stButton>button:hover{background:#fff3ef;border-color:#ff5a3c}
div[data-testid="stChatInput"]{border-radius:999px}
</style>""".replace("\n", "")


@st.cache_resource
def get_graph():
    """대화 기억(MemorySaver)은 프로세스에 하나이고, 대화 구분은 thread_id로 한다. cards=True: 카드가 있으니 답변은 요약만."""
    return build_graph(MemorySaver(), cards=True)


def md(text: str) -> str:
    """본문의 중복 링크를 없애고(링크는 카드 버튼이 대신한다) 달러 기호를 이스케이프한다(Streamlit이 $...$를 수식으로 렌더링한다)."""
    return strip_links(text).replace("$", "\\$")


def describe_call(call) -> str:
    args = ", ".join(f"{ARG_LABEL.get(k, k)}: {v}" for k, v in call["args"].items() if v not in (None, "", []))
    return TOOL_LABEL.get(call["name"], call["name"]) + (f" ({args})" if args else "")


def stream_answer(question: str, thread_id: str, status, box):
    """그래프를 스트리밍하며 상태 표시와 답변 영역을 갱신한다. (최종 답변, 카드로 보여 줄 식당 목록)을 돌려준다."""
    text, found = "", {}
    for mode, data in get_graph().stream({"messages": [("user", question)]}, {"configurable": {"thread_id": thread_id}},
                                         stream_mode=["messages", "updates"]):
        if mode == "messages":
            chunk, meta = data
            # 라우터의 구조화 출력 같은 내부 호출은 거르고 답변 노드의 토큰만 쓴다
            if meta.get("langgraph_node") in ("agent", "chat") and isinstance(chunk, AIMessageChunk) and chunk.content:
                text += chunk.content
                box.markdown(md(text) + "▌")
        else:   # updates: 도구 호출 단계를 상태 표시에 남기고, 도구 결과에서 식당을 모은다
            for node, update in data.items():
                for m in (update or {}).get("messages", []):
                    if node == "agent" and isinstance(m, AIMessage) and m.tool_calls:
                        for call in m.tool_calls:
                            status.update(label=describe_call(call) + "…", state="running")
                            status.write(f"- {describe_call(call)}")
                    elif node == "tools" and isinstance(m, ToolMessage):
                        collect(m.content, found)
    box.markdown(md(text))
    return text, pick(found, text)



@st.cache_data(ttl=600, show_spinner=False)
def load_detail(restaurant_id: int) -> dict:
    return fetch_detail(restaurant_id)


def render_detail(d: dict):
    """식당 상세 팝업의 내용: 영상 속 메뉴 타임라인 -> 영상 소개·총평 -> 음료 -> 기본 정보(영업시간·주소·가격대)."""
    st.markdown(TL_CSS + CSS.replace("\n", ""), unsafe_allow_html=True)
    thumb = first_thumb(d)
    left, right = st.columns([2, 3]) if thumb else (None, st.container())
    if thumb:
        left.image(thumb, use_container_width=True)
    with right:
        parts = [p for p in (d.get("region") or "").split(" > ") if p]
        cat = d.get("category_detail") or d.get("category_broad") or ""
        st.markdown(f'<div class="rc-sub">{esc(" ".join(parts[-2:]))}{" · " if parts and cat else ""}{esc(cat)}</div>', unsafe_allow_html=True)
        chips = []
        if d.get("michelin") and not d["michelin"].startswith("미슐랭 아님"):
            chips.append(f'<span class="rc-badge" style="position:static">{esc(d["michelin"].replace("미슐랭 ", "★ "))}</span>')
        if d.get("rating"):
            n = f' <span class="rc-count">({d["rating_count"]:,})</span>' if d.get("rating_count") else ""
            chips.append(f'<span class="rc-star">★ {esc(d["rating"])}</span>{n}')
        if d.get("price_level"):
            chips.append(f'<span class="rc-price"><b>{"●" * int(d["price_level"])}</b>{"●" * (4 - int(d["price_level"]))}</span>')
        if d.get("chef_name"):
            chips.append(f'<span class="rc-chip">👨‍🍳 {esc(d["chef_name"])}</span>')
        st.markdown('<div class="rc-meta" style="gap:12px">' + "".join(chips) + "</div>", unsafe_allow_html=True)
        if d.get("tags"):
            st.markdown('<div class="kp">' + "".join(f"<span>#{esc(t)}</span>" for t in d["tags"][:8]) + "</div>", unsafe_allow_html=True)

    menus = d["menus"]
    st.markdown('<div class="sec">🎬 영상에 나온 메뉴 타임라인</div>', unsafe_allow_html=True)
    if any(m["item_type"] == "FOOD" for m in menus):
        for n in d["notes"]:
            mine = [m for m in menus if m["video_id"] == n["video_id"]]
            if len(d["notes"]) > 1:
                st.markdown(f'<div class="rc-sub" style="margin-top:8px">📺 {esc(n["title"])}</div>', unsafe_allow_html=True)
            if any(m['item_type'] == 'FOOD' for m in mine):
                components.html(player_html(n['video_id'], n['url'], mine), height=780)   # 영상 재생 + 타임라인(누르면 그 시각으로 이동)
        st.caption("메뉴를 누르면 그 장면부터 재생돼요. 시각은 자막에서 추정한 값이라 실제 화면과 몇 초 차이가 날 수 있어요. 메뉴별 맛 후기는 저장하지 않아 조리 특징 설명만 보여 줘요.")
    else:
        st.info("이 식당은 영상 속 메뉴 정보가 아직 없어요.")

    for n in d["notes"]:
        st.markdown('<div class="sec">📝 영상 속 소개와 총평</div>', unsafe_allow_html=True)
        link = f'<a href="{esc(n["url"])}" target="_blank">{esc(n["title"])}</a>'
        st.markdown(f'<div class="rc-sub">{link}' + (f' · 코스: {esc(n["course_name"])}' if n.get("course_name") and n["course_name"] != "코스" else (" · 코스 요리" if n.get("is_course") else "")) + "</div>", unsafe_allow_html=True)
        if n.get("concept"):
            st.markdown(f"**컨셉** {n['concept']}")
        if n.get("atmosphere"):
            st.markdown(f"**분위기** {n['atmosphere']}")
        if n.get("key_points"):
            st.markdown('<div class="kp">' + "".join(f"<span>{esc(k)}</span>" for k in n["key_points"]) + "</div>", unsafe_allow_html=True)
        if n.get("final_review"):
            st.markdown(f'<div class="rv">“{esc(n["final_review"])}”</div>', unsafe_allow_html=True)
    if d.get("chef_info"):
        st.caption(f"셰프 소개(영상에서 한 말이라 검증된 사실은 아니에요): {d['chef_info']}")

    drinks = drinks_html(menus)
    if drinks:
        st.markdown('<div class="sec">🍷 음료</div>', unsafe_allow_html=True)
        st.markdown(drinks, unsafe_allow_html=True)

    st.markdown('<div class="sec">📍 기본 정보</div>', unsafe_allow_html=True)
    info, hrs = st.columns(2)
    with info:
        rows = [("주소", d.get("formatted_address")), ("전화", d.get("phone"))]
        if d.get("price_min"):
            cur = d.get("price_currency") or ""
            rows.append(("가격", f'{cur} {d["price_min"]:,}~' + (f'{d["price_max"]:,}' if d.get("price_max") else "") + " (1인 기준)"))
        elif d.get("price_level"):
            rows.append(("가격대", f'{"●" * int(d["price_level"])}{"○" * (4 - int(d["price_level"]))} (Google 기준 단계)'))
        else:
            rows.append(("가격대", "정보 없음"))
        for k, v in rows:
            if v:
                st.markdown(f"**{k}**  \n{md(str(v))}")
        links = []
        if d.get("website"):
            links.append(f'<a href="{esc(d["website"])}" target="_blank">웹사이트</a>')
        if d.get("google_maps_url"):
            links.append(f'<a href="{esc(d["google_maps_url"])}" target="_blank">구글 지도</a>')
        if links:
            st.markdown(" · ".join(links), unsafe_allow_html=True)
    with hrs:
        st.markdown("**영업시간**")
        st.markdown(hours_html(d["hours"]), unsafe_allow_html=True)


def show_detail(restaurant_id: int, name: str):
    """식당 상세를 팝업(dialog)으로 연다. 채팅 화면은 그대로 유지된다."""
    @st.dialog(name, width="large")
    def _dialog():
        d = load_detail(restaurant_id)
        if not d:
            st.warning("식당 정보를 찾지 못했어요.")
            return
        render_detail(d)
    _dialog()


def render_cards(cards: list, idx: int):
    """카드를 2열로 보여 주고, 카드마다 상세 정보 보기 버튼을 둔다. idx는 대화 기록에서의 메시지 번호(버튼 key 구분용)."""
    for row in range(0, len(cards), 2):
        cols = st.columns(2)
        for col, r in zip(cols, cards[row:row + 2]):
            with col:
                st.markdown(CSS.replace("\n", "") + card_html(r), unsafe_allow_html=True)
                if st.button("상세 정보 보기", key=f"detail-{idx}-{r['restaurant_id']}", use_container_width=True):
                    show_detail(r["restaurant_id"], r.get("name") or r.get("name_official"))

st.set_page_config(page_title="맛집 노트", page_icon="🍽️", layout="centered")
st.markdown(PAGE_CSS, unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>🍽️ 맛집 <span>노트</span></h1>'
            '<p>미식 유튜브 영상에서 뽑은 식당 약 120곳으로 추천해 드려요. 미슐랭은 Michelin Guide 기준, 셰프 소개는 영상 속 이야기예요.</p></div>',
            unsafe_allow_html=True)

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.history = []

with st.sidebar:
    st.subheader("이런 걸 물어보세요")
    clicked = None
    for q in EXAMPLES:
        if st.button(q, use_container_width=True):
            clicked = q
    st.divider()
    if st.button("새 대화", type="primary", use_container_width=True):
        st.session_state.thread_id = str(uuid.uuid4())
        st.session_state.history = []
        st.rerun()

for i, m in enumerate(st.session_state.history):
    with st.chat_message(m["role"]):
        st.markdown(md(m["content"]))
        if m.get("cards"):
            render_cards(m["cards"], i)

question = st.chat_input("먹고 싶은 음식이나 조건을 말해 보세요") or clicked
if question:
    st.session_state.history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        status = st.status("생각 중…", expanded=False)
        box = st.empty()
        cards = []
        try:
            answer, cards = stream_answer(question, st.session_state.thread_id, status, box)
            status.update(label="완료", state="complete")
            if cards:
                render_cards(cards, len(st.session_state.history))
        except Exception as e:   # API 키·DB 접속 오류 등을 화면에 보여 주고 대화는 유지한다
            answer = f"답변을 만드는 중 오류가 났습니다: {type(e).__name__}"
            box.error(answer)
            status.update(label="오류", state="error")
    st.session_state.history.append({"role": "assistant", "content": answer, "cards": cards})
