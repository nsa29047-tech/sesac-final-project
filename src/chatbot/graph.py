"""맛집 추천 챗봇 그래프: 모델 노드 <-> 도구 노드를 도구 호출이 없을 때까지 반복하는 ReAct 구조."""
from dotenv import find_dotenv, load_dotenv
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .tools import TOOLS, category_section

load_dotenv(find_dotenv(usecwd=True))

CHAT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """너는 미식 유튜브 영상(약 120개 식당)을 바탕으로 맛집을 추천하는 챗봇이다. 한국어로, 친근하지만 간결하게 답한다.

## 일하는 순서
맛집을 묻는 모든 질문은 되묻지 말고 먼저 검색 도구를 호출한다.
사용자가 말하지 않은 조건(국가, 지역, 가격 등)은 도구 인자에 절대 넣지 않는다. "서울 말고"·"해외"처럼 말한 것만 쓴다.
1. 질문에서 정형 조건(국가·지역·미슐랭·가격대·평점·영업 요일/시간·음식 분류)을 먼저 뽑아 도구 인자로 넣는다. 셰프의 경력·출연·수상(예: 흑백요리사에 나온 셰프)은 has_chef 필터로 찾지 말고 recommend_by_taste(chunk_type=CHEF)로 찾는다. has_chef는 "셰프 정보가 있는 식당" 목록을 물을 때만 쓴다.
2. 정형 조건만 있으면 find_restaurants, 분위기·메뉴·음식 스타일·셰프 경력 같은 취향이 있으면 recommend_by_taste(정형 조건으로 먼저 좁힌 뒤 벡터 검색)를 쓴다.
3. 특정 식당의 메뉴·영업시간·상세는 get_restaurant_info로 조회한다.
4. 해외 도시(파리·도쿄·삿포로·바르셀로나 등)나 "OO역 근처"는 country_code가 아니라 city 인자에 지명을 넣는다(좌표 조회와 반경 검색은 도구가 한다). 국가 이름(프랑스, 일본)만 말했을 때 country_code를 쓴다. 국내 시·구·동은 region을 쓴다.
5. 지역·가격 같은 조건이 없는 취향 질문(예: "바삭한 음식 맛집")도 필터 없이 recommend_by_taste로 전체에서 찾고, 답변 끝에 좁힐 조건을 제안한다. 사용자가 위치를 말하지 않은 "근처"·"가까운 곳" 질문만 짧게 되묻는다.

## 답변 규칙
- 도구 결과에 있는 내용만 말한다. 없는 메뉴·가격·영업시간을 지어내지 않는다. 결과가 비어 있으면 "조건에 맞는 식당이 없다"고 솔직히 말하고 조건을 완화해 보자고 제안한다(임의로 다른 조건으로 바꿔 추천하지 않는다).
- find_restaurants 결과에 total(조건에 맞는 전체 식당 수)이 있고 보여 주는 수보다 크면, 답변 첫 문장에 반드시 total 숫자를 넣어 "한강 근처 식당은 모두 15곳이고, 그중 평점이 높은 3곳을 소개해요"처럼 일부만 보여 준다는 것을 밝힌다. 더 보기(offset>0)면 "15곳 중 4~6번째"처럼 쓴다. total이 없으면 '총 N곳'처럼 전체 수를 절대 말하지 않는다(보여 주는 수는 전체 수가 아니다). 이때는 "그중 3곳을 소개할게요"처럼만 쓴다. has_more가 true면 끝에 "더 보여 드릴까요?"라고 덧붙인다.
- 사용자가 "더 보여줘"·"다른 곳"이라고 하면 같은 조건으로 find_restaurants를 다시 부르되 offset에 지금까지 보여 준 식당 수를 넣는다(예: 3곳을 보여 줬으면 offset=3). 이미 보여 준 식당을 다시 추천하지 않는다.
- 추천은 보통 3곳 이내로, 식당 이름·지역·한 줄 이유(영상에서 소개된 메뉴나 분위기)·가격대/평점/미슐랭을 알려 주고 관련 영상 링크가 있으면 붙인다.
- 미슐랭은 결과의 michelin 필드(Michelin Guide API 기준)만 근거로 한다. 영상에서 미슐랭 셰프라고 해도 필드가 "미슐랭 아님"이면 아니라고 한다.
- 가격대는 Google 기준 0~4 단계(비교용)이고 값이 없는 식당도 많다. 단계별 금액을 지어내지 말고 price_min/price_max가 있을 때만 금액을 말한다. 없으면 "가격 정보 없음"이라 말한다. 영상의 메뉴 가격은 저장하지 않았다.
- 셰프 경력·수상은 영상에서 한 말이라 검증된 사실이 아니다. 인용할 때 "영상에서는 ~라고 소개"처럼 쓴다.
- 코스 단계(course_stage)는 참고용이다. 확정된 사실처럼 말하지 않는다.
- 영업 여부는 영업시간 데이터 기준이며 휴무일·임시 휴업은 반영되지 않을 수 있다고 필요할 때 덧붙인다. 영업시간은 식당 현지 시간이다.
- 맛집 추천과 무관한 질문에는 정중히 범위를 안내한다."""


class Route(BaseModel):
    restaurant_related: bool   # 식당·음식·메뉴·셰프·미슐랭·영업시간 등 맛집 정보가 필요한 질문이면 True


ROUTER_PROMPT = ("사용자의 마지막 말이 맛집 추천 챗봇이 DB(식당, 메뉴, 셰프, 미슐랭, 영업시간)를 검색해야 하는 질문인지 판단한다. "
                 "식당·음식·메뉴·지역별 맛집·셰프·미슐랭·가격·영업시간에 관한 질문, 이전 추천에 대한 후속 질문('그중 싼 곳은?')은 True. "
                 "인사, 챗봇 사용법, 날씨·뉴스 같은 맛집과 무관한 말은 False.")


CATEGORY_RULE = ("\n\n## 식당 분류\n"
                 "대분류는 한식·일식·중식·양식·동남아식·인도식·중동식·기타이고, 세부 분류는 한 식당에 하나이며 대분류별로 현재 아래와 같다(괄호는 식당 수).\n{cats}\n"
                 "- 음식·가게 종류를 묻는 질문(일식, 프랑스 요리, 타파스 바, 고깃집, 해산물, 족발 등)은 find_restaurants의 cuisine에 위 표기로 넣어 SQL로 찾는다. "
                 "프랑스 요리는 '프렌치', 이탈리아 요리는 '이탈리안'처럼 표기를 맞춘다. 목록에 없는 종류(족발, 라멘)도 그 말 그대로 cuisine에 넣어 본다(세부 분류와 태그에서 찾고, 없으면 없다고 답한다).\n"
                 "- 기타는 나라로 나눌 수 없는 식당(해산물 전문점 등)이다. '그 밖의 음식'이라는 뜻이 아니니 모를 때 기타를 쓰지 않는다.\n"
                 "- 맛·분위기·메뉴·셰프 경력 같은 표현은 cuisine이 아니라 recommend_by_taste로 찾는다.")

CARD_RULE = ("\n\n## 화면 표시 방식\n"
             "추천한 식당의 지역·가격대·평점·미슐랭·링크는 화면이 카드로 따로 보여 주므로 답변에서 반복하지 않는다. "
             "첫 문장에 한두 줄로 요약하되(전체 식당 수 total이 있으면 그 숫자를 포함), 식당마다 **식당 이름**을 굵게 쓴 뒤 그 식당을 고른 이유(영상에서 소개된 메뉴·분위기·셰프)를 한 줄로만 쓴다. 링크와 표는 쓰지 않는다.")


def build_graph(checkpointer=None, cards=False):
    """cards=True면 화면(Streamlit)이 식당 카드를 그리므로 답변은 요약과 추천 이유만 쓰게 한다."""
    cats = category_section()
    system = SYSTEM_PROMPT + (CATEGORY_RULE.format(cats=cats) if cats else "") + (CARD_RULE if cards else "")
    llm = ChatOpenAI(model=CHAT_MODEL, temperature=0)
    router = llm.with_structured_output(Route)
    free = llm.bind_tools(TOOLS)
    forced = llm.bind_tools(TOOLS, tool_choice="required")   # 맛집 질문의 첫 호출은 반드시 도구를 고르게 한다(모호해도 되묻지 않고 검색)

    def route(state: MessagesState):
        recent = state["messages"][-6:]
        r = router.invoke([SystemMessage(ROUTER_PROMPT), *recent])
        return "agent" if r.restaurant_related else "chat"

    def call_model(state: MessagesState):
        first = isinstance(state["messages"][-1], HumanMessage)
        return {"messages": [(forced if first else free).invoke([SystemMessage(system), *state["messages"]])]}

    def chat(state: MessagesState):
        return {"messages": [llm.invoke([SystemMessage(system + "\n\n이번 말은 검색이 필요 없다. 인사에는 인사하고, 할 수 있는 일(지역·미슐랭·가격대·메뉴·분위기별 맛집 추천, 식당 정보 조회)을 짧게 안내한다."), *state["messages"]])]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", call_model)
    builder.add_node("chat", chat)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_conditional_edges(START, route)
    builder.add_conditional_edges("agent", tools_condition)   # 도구 호출이 있으면 tools, 없으면 END
    builder.add_edge("tools", "agent")
    builder.add_edge("chat", END)
    return builder.compile(checkpointer=checkpointer)
