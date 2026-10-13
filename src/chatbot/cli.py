"""터미널 챗봇: uv run python src/chatbot/cli.py ("질문" 인자를 주면 한 번만 답하고 종료, 없으면 대화 모드). --trace 는 도구 호출 표시."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from chatbot.graph import build_graph  # noqa: E402


def ask(app, text, thread, trace):
    final = None
    for step in app.stream({"messages": [("user", text)]}, {"configurable": {"thread_id": thread}}, stream_mode="updates"):
        for node, update in step.items():
            for m in update["messages"]:
                if trace and getattr(m, "tool_calls", None):
                    for t in m.tool_calls:
                        print(f"  [도구] {t['name']} {t['args']}")
                if node == "agent" and not getattr(m, "tool_calls", None):
                    final = m.content
    print(f"\n{final}\n")


def main():
    args = [a for a in sys.argv[1:] if a != "--trace"]
    trace = "--trace" in sys.argv
    app = build_graph(MemorySaver())
    if args:
        ask(app, " ".join(args), "cli", trace)
        return
    print("맛집 챗봇 (종료: 빈 줄 또는 Ctrl+C)")
    while (q := input("> ").strip()):
        ask(app, q, "cli", trace)


if __name__ == "__main__":
    main()
