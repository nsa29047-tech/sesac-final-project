"""LangGraph 엔트리포인트 (langgraph.json: ./src/app.py:graph). 대화 기억은 langgraph dev가 제공하는 저장소를 쓴다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chatbot.graph import build_graph  # noqa: E402

graph = build_graph()
