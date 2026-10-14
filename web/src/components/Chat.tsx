"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ApiError, streamChat } from "@/lib/api";
import { describeCall, stripLinks } from "@/lib/format";
import type { ChatEvent, ChatMessage, Restaurant } from "@/lib/types";
import DetailDialog from "./DetailDialog";
import MapSection from "./MapSection";
import RestaurantCard from "./RestaurantCard";

const EXAMPLES = [
  "서울에 있는 미슐랭 스타 식당 알려줘",
  "바삭하게 튀긴 음식이 맛있는 곳 추천해줘",
  "강남역 근처 일식집",
  "파리에 있는 미슐랭 식당",
  "밍글스 메뉴랑 영업시간 알려줘",
  "흑백요리사에 나온 셰프의 식당",
];

export default function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Restaurant | null>(null); // 상세 팝업으로 보고 있는 식당
  // 렌더링 중에 난수를 만들면 안 되므로(cacheComponents) 대화 ID 는 첫 질문을 보낼 때 만든다.
  const threadRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const counterRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const nextId = () => `m${++counterRef.current}`;

  async function send(raw: string) {
    const question = raw.trim();
    if (!question || busy) return;
    threadRef.current ??= crypto.randomUUID();
    const botId = nextId();
    setMessages((ms) => [
      ...ms,
      { id: nextId(), role: "user", text: question, cards: [], statuses: [], streaming: false },
      { id: botId, role: "assistant", text: "", cards: [], statuses: [], streaming: true },
    ]);
    setInput("");
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const patch = (fn: (m: ChatMessage) => ChatMessage) => setMessages((ms) => ms.map((m) => (m.id === botId ? fn(m) : m)));

    const onEvent = (e: ChatEvent) => {
      switch (e.event) {
        case "status":
          patch((m) => ({ ...m, statuses: [...m.statuses, describeCall(e.data.tool, e.data.args)] }));
          break;
        case "token":
          patch((m) => ({ ...m, text: m.text + e.data.text }));
          break;
        case "restaurants":
          patch((m) => ({ ...m, cards: e.data.restaurants }));
          break;
        case "done":
          patch((m) => ({ ...m, text: e.data.answer }));
          break;
        case "error":
          patch((m) => ({ ...m, error: e.data.message }));
          break;
      }
    };

    try {
      await streamChat(threadRef.current, question, onEvent, ac.signal);
    } catch (err) {
      patch((m) => ({ ...m, error: err instanceof ApiError ? err.message : "알 수 없는 오류가 났어요." }));
    } finally {
      patch((m) => ({ ...m, streaming: false }));
      setBusy(false);
      abortRef.current = null;
    }
  }

  function newChat() {
    abortRef.current?.abort();
    setDetail(null);
    threadRef.current = null;
    setMessages([]);
    setInput("");
    setBusy(false);
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-4">
      <header className="flex items-end justify-between pb-2 pt-8">
        <div>
          <h1 className="text-3xl font-extrabold text-neutral-900">
            🍽️ 맛집 <span className="text-brand">노트</span>
          </h1>
          <p className="mt-1 text-sm text-neutral-500">
            미식 유튜브 영상에서 뽑은 식당 약 120곳으로 추천해 드려요. 미슐랭은 Michelin Guide 기준, 셰프 소개는 영상 속 이야기예요.
          </p>
        </div>
        {messages.length > 0 && (
          <button
            type="button"
            onClick={newChat}
            className="shrink-0 rounded-full border border-brand-line bg-white px-3.5 py-1.5 text-sm font-semibold text-brand-dark hover:bg-brand-soft"
          >
            새 대화
          </button>
        )}
      </header>

      <main className="flex-1 space-y-6 py-4">
        {messages.length === 0 && (
          <section className="pt-6">
            <p className="mb-3 text-sm font-semibold text-neutral-700">이런 걸 물어보세요</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => send(q)}
                  className="rounded-full border border-brand-line bg-white px-3.5 py-2 text-left text-sm text-brand-dark hover:border-brand hover:bg-brand-soft"
                >
                  {q}
                </button>
              ))}
            </div>
          </section>
        )}

        {messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-[15px] text-white">{m.text}</p>
            </div>
          ) : (
            <div key={m.id} className="space-y-3">
              {m.streaming && m.text === "" && !m.error && (
                <p className="flex items-center gap-2 text-sm text-neutral-500" role="status">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-brand" />
                  {m.statuses.length ? m.statuses[m.statuses.length - 1] + "…" : "생각 중…"}
                </p>
              )}
              {m.text && (
                <div className="md rounded-2xl rounded-bl-md bg-white px-4 py-3 text-[15px] leading-relaxed text-neutral-800 shadow-sm">
                  <ReactMarkdown>{stripLinks(m.text)}</ReactMarkdown>
                  {m.streaming && <span className="ml-0.5 inline-block animate-pulse text-brand">▌</span>}
                </div>
              )}
              {m.error && (
                <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {m.error}
                </p>
              )}
              {m.cards.length > 0 && (
                <>
                  <MapSection restaurants={m.cards} onDetail={setDetail} />
                  <div className="grid gap-3.5 sm:grid-cols-2">
                    {m.cards.map((r, i) => (
                      <RestaurantCard key={r.restaurant_id} r={r} num={i + 1} onDetail={setDetail} />
                    ))}
                  </div>
                </>
              )}
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </main>

      <form
        className="sticky bottom-0 -mx-4 flex gap-2 border-t border-neutral-200 bg-background/95 px-4 py-3 backdrop-blur"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          maxLength={500}
          placeholder="먹고 싶은 음식이나 조건을 말해 보세요"
          aria-label="질문 입력"
          className="flex-1 rounded-full border border-neutral-300 bg-white px-5 py-2.5 text-[15px] outline-none focus:border-brand"
        />
        {busy ? (
          <button
            type="button"
            onClick={() => abortRef.current?.abort()}
            className="rounded-full border border-neutral-300 bg-white px-5 py-2.5 text-sm font-semibold text-neutral-700 hover:bg-neutral-100"
          >
            중지
          </button>
        ) : (
          <button
            type="submit"
            disabled={!input.trim()}
            className="rounded-full bg-brand px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-40"
          >
            보내기
          </button>
        )}
      </form>
      <DetailDialog restaurantId={detail?.restaurant_id ?? null} name={detail?.name ?? ""} onClose={() => setDetail(null)} />
    </div>
  );
}
