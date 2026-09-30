from langgraph.graph import StateGraph, MessagesState, START, END


builder = StateGraph(MessagesState)
builder.add_edge(START, END)
graph = builder.compile()