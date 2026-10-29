"use client";

import { ArrowUp, History, LogOut, MapPinned, Plus, Square, UtensilsCrossed } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, deleteConversation, fetchMessages, listConversations, streamChat } from "@/lib/api";
import { argChips } from "@/lib/format";
import type { ChatEvent, ChatMessage, ConversationSummary, Restaurant, SavedMessage, User } from "@/lib/types";
import AnswerMarkdown from "./AnswerMarkdown";
import ConversationDrawer from "./ConversationDrawer";
import DetailDialog from "./DetailDialog";
import ResultsList from "./ResultsList";
import StepChips from "./StepChips";
import Welcome from "./Welcome";

/** 저장된 메시지를 화면용 메시지로 바꾼다. 카드·지도는 그때 저장한 restaurants 이벤트로 복원하고, 도구 호출 칩(calls)은 저장하지 않아 비어 있다. */
function toChatMessage(m: SavedMessage): ChatMessage {
  return {
    id: `s${m.id}`,
    role: m.role,
    text: m.content,
    cards: m.restaurants?.restaurants ?? [],
    mapExtra: m.restaurants?.map_extra ?? [],
    calls: [],
    streaming: false,
  };
}

export default function Chat({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null); // 지금 열려 있는 대화
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [loadingConv, setLoadingConv] = useState(false);
  const [notice, setNotice] = useState(""); // 대화 목록·불러오기 오류 안내
  const [detail, setDetail] = useState<Restaurant | null>(null); // 상세 팝업으로 보고 있는 식당
  const [activeId, setActiveId] = useState<string | null>(null); // 넓은 화면의 결과 패널에 보여 줄 답변
  // 렌더링 중에 난수를 만들면 안 되므로(cacheComponents) 대화 ID 는 첫 질문을 보낼 때 만든다.
  const threadRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const counterRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const refreshList = useCallback(async () => {
    try {
      setConversations(await listConversations());
    } catch {} // 목록을 못 불러와도 채팅은 계속 쓸 수 있다
  }, []);

  useEffect(() => {
    refreshList();
  }, [refreshList]);

  const nextId = () => `m${++counterRef.current}`;

  async function send(raw: string) {
    const question = raw.trim();
    if (!question || busy) return;
    threadRef.current ??= crypto.randomUUID();
    setCurrentId(threadRef.current);
    setNotice("");
    const botId = nextId();
    setMessages((ms) => [
      ...ms,
      { id: nextId(), role: "user", text: question, cards: [], mapExtra: [], calls: [], streaming: false },
      { id: botId, role: "assistant", text: "", cards: [], mapExtra: [], calls: [], streaming: true },
    ]);
    setInput("");
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    const patch = (fn: (m: ChatMessage) => ChatMessage) => setMessages((ms) => ms.map((m) => (m.id === botId ? fn(m) : m)));

    const onEvent = (e: ChatEvent) => {
      switch (e.event) {
        case "status":
          patch((m) => ({ ...m, calls: [...m.calls, { tool: e.data.tool, args: e.data.args }] }));
          break;
        case "token":
          patch((m) => ({ ...m, text: m.text + e.data.text }));
          break;
        case "restaurants":
          patch((m) => ({ ...m, cards: e.data.restaurants, mapExtra: e.data.map_extra ?? [] }));
          if (e.data.restaurants.length > 0) setActiveId(botId); // 새 결과가 오면 결과 패널이 그것을 보여 준다
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
      refreshList(); // 새 대화가 목록에 생기고 제목이 정해졌다
    }
  }

  function newChat() {
    abortRef.current?.abort();
    setDetail(null);
    setActiveId(null);
    threadRef.current = null;
    setCurrentId(null);
    setMessages([]);
    setInput("");
    setBusy(false);
    setNotice("");
    setDrawerOpen(false);
  }

  /** 저장된 대화를 열어 이어서 질문할 수 있게 한다(대화 id 가 챗봇의 thread_id 라 챗봇도 앞 내용을 기억한다). */
  async function openConversation(id: string) {
    if (id === currentId && messages.length > 0) {
      setDrawerOpen(false);
      return;
    }
    abortRef.current?.abort();
    setDrawerOpen(false);
    setDetail(null);
    setBusy(false);
    setNotice("");
    setLoadingConv(true);
    try {
      const saved = (await fetchMessages(id)).map(toChatMessage);
      threadRef.current = id;
      setCurrentId(id);
      setMessages(saved);
      const lastWithCards = [...saved].reverse().find((m) => m.cards.length > 0);
      setActiveId(lastWithCards?.id ?? null);
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "대화를 불러오지 못했어요.");
    } finally {
      setLoadingConv(false);
    }
  }

  async function removeConversation(id: string) {
    const title = conversations.find((c) => c.id === id)?.title ?? "이 대화";
    if (!window.confirm(`"${title}" 대화를 지울까요? 되돌릴 수 없어요.`)) return;
    try {
      await deleteConversation(id);
      if (id === currentId) newChat();
      await refreshList();
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : "대화를 지우지 못했어요.");
    }
  }

  const withCards = messages.filter((m) => m.cards.length > 0);
  const active = withCards.find((m) => m.id === activeId) ?? withCards[withCards.length - 1];
  const activeQuestion = active ? messages[messages.indexOf(active) - 1]?.text : "";

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-neutral-200 bg-white px-4 sm:px-6">
        <div className="flex items-center gap-2.5">
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            aria-label="대화 기록 열기"
            className="flex h-8 w-8 items-center justify-center rounded-[10px] text-neutral-600 hover:bg-neutral-100"
          >
            <History size={18} aria-hidden />
          </button>
          <span className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-brand text-white">
            <UtensilsCrossed size={17} aria-hidden />
          </span>
          <span className="text-[17px] font-extrabold tracking-tight text-neutral-900">맛집 노트</span>
          <span className="ml-1 hidden text-[13px] text-neutral-400 md:inline">미식 유튜브 속 식당 추천</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden max-w-[10rem] truncate text-[13px] font-semibold text-neutral-600 sm:inline">{user.nickname}</span>
          <button
            type="button"
            onClick={newChat}
            className="flex items-center gap-1.5 rounded-full border border-neutral-200 bg-white px-3.5 py-1.5 text-[13px] font-semibold text-neutral-700 hover:border-brand hover:text-brand-ink"
          >
            <Plus size={15} aria-hidden />새 대화
          </button>
          <button
            type="button"
            onClick={onLogout}
            aria-label="로그아웃"
            title="로그아웃"
            className="flex h-8 w-8 items-center justify-center rounded-full text-neutral-500 hover:bg-neutral-100 hover:text-neutral-800"
          >
            <LogOut size={16} aria-hidden />
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6 sm:px-6">
              {notice && (
                <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {notice}
                </p>
              )}
              {loadingConv && <p className="py-10 text-center text-sm text-neutral-400">대화를 불러오는 중…</p>}
              {messages.length === 0 && !loadingConv && <Welcome onPick={send} />}

              {messages.map((m) =>
                m.role === "user" ? (
                  <div key={m.id} className="flex justify-end">
                    <p className="max-w-[85%] whitespace-pre-wrap rounded-[20px] rounded-br-md bg-brand px-4 py-2.5 text-[15px] font-medium text-white">{m.text}</p>
                  </div>
                ) : (
                  <div key={m.id} className="space-y-3">
                    <article className="rounded-[20px] rounded-tl-md border border-[#e9e4dd] bg-white px-5 py-4 shadow-[0_1px_2px_rgba(60,40,20,0.04)]">
                      <div className="mb-3 flex items-center gap-2">
                        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-brand-soft text-brand-dark">
                          <UtensilsCrossed size={13} aria-hidden />
                        </span>
                        <span className="text-[13px] font-semibold text-neutral-500">맛집 노트</span>
                      </div>

                      {m.streaming && !m.error && <StepChips calls={m.calls} hasText={m.text !== ""} />}

                      {!m.streaming && m.calls.length > 0 && argChips(m.calls[m.calls.length - 1].args).length > 0 && (
                        <div className="mb-3 flex flex-wrap items-center gap-1.5">
                          <span className="text-xs font-semibold text-neutral-400">검색 조건</span>
                          {argChips(m.calls[m.calls.length - 1].args).map((c) => (
                            <span key={c} className="rounded-full bg-brand-soft px-2.5 py-0.5 text-xs font-medium text-brand-ink">
                              {c}
                            </span>
                          ))}
                        </div>
                      )}

                      {m.text && (
                        <div className={m.streaming ? "mt-3" : ""}>
                          <AnswerMarkdown text={m.text} cards={m.cards} onDetail={setDetail} />
                          {m.streaming && <span className="ml-0.5 inline-block animate-pulse text-brand">▌</span>}
                        </div>
                      )}

                      {m.error && (
                        <p role="alert" className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                          {m.error}
                        </p>
                      )}

                      {m.cards.length > 0 && (
                        <button
                          type="button"
                          onClick={() => setActiveId(m.id)}
                          className={`mt-4 hidden items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-[13px] font-semibold lg:inline-flex ${
                            active?.id === m.id ? "border-brand bg-brand-soft text-brand-ink" : "border-neutral-200 text-neutral-600 hover:border-brand"
                          }`}
                        >
                          <MapPinned size={15} aria-hidden />
                          추천 식당 {m.cards.length}곳 {active?.id === m.id ? "보는 중" : "보기"}
                        </button>
                      )}
                    </article>

                    {/* 좁은 화면에서는 결과를 답변 아래에 그대로 보여 준다(넓은 화면은 오른쪽 패널) */}
                    {m.cards.length > 0 && (
                      <div className="lg:hidden">
                        <ResultsList cards={m.cards} mapExtra={m.mapExtra} onDetail={setDetail} />
                      </div>
                    )}
                  </div>
                ),
              )}
              <div ref={bottomRef} />
            </div>
          </div>

          <div className="shrink-0 border-t border-neutral-200 bg-background/95 px-4 pb-3 pt-3 sm:px-6">
            <form
              className="mx-auto flex w-full max-w-3xl items-center gap-2 rounded-[22px] border border-neutral-300 bg-white py-2 pl-5 pr-2 focus-within:border-brand focus-within:ring-4 focus-within:ring-brand-soft"
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
                autoFocus
                className="min-w-0 flex-1 bg-transparent py-1 text-[15px] outline-none placeholder:text-neutral-400"
              />
              {busy ? (
                <button
                  type="button"
                  onClick={() => abortRef.current?.abort()}
                  aria-label="답변 중지"
                  className="flex h-10 w-10 items-center justify-center rounded-full border border-neutral-300 bg-white text-neutral-700 hover:bg-neutral-100"
                >
                  <Square size={14} fill="currentColor" aria-hidden />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  aria-label="보내기"
                  className="flex h-10 w-10 items-center justify-center rounded-full bg-brand text-white hover:bg-brand-dark disabled:bg-neutral-200 disabled:text-neutral-400"
                >
                  <ArrowUp size={20} strokeWidth={2.5} aria-hidden />
                </button>
              )}
            </form>
            <p className="mx-auto mt-2 max-w-3xl text-center text-[11.5px] text-neutral-400">
              미슐랭은 Michelin Guide 기준이에요. 셰프 소개와 메뉴 설명은 영상 속 이야기라 검증된 사실은 아니에요.
            </p>
          </div>
        </section>

        {active && (
          <aside className="hidden w-[52%] max-w-[780px] shrink-0 flex-col border-l border-neutral-200 bg-panel lg:flex">
            <div className="shrink-0 border-b border-neutral-200 bg-white/70 px-6 py-3.5">
              <p className="text-[15px] font-bold text-neutral-900">추천 식당 {active.cards.length}곳</p>
              {activeQuestion && <p className="mt-0.5 truncate text-[13px] text-neutral-500">“{activeQuestion}”</p>}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-6">
              <ResultsList cards={active.cards} mapExtra={active.mapExtra} onDetail={setDetail} />
            </div>
          </aside>
        )}
      </div>

      <ConversationDrawer
        open={drawerOpen}
        items={conversations}
        currentId={currentId}
        onClose={() => setDrawerOpen(false)}
        onSelect={openConversation}
        onNew={newChat}
        onDelete={removeConversation}
      />

      <DetailDialog restaurantId={detail?.restaurant_id ?? null} name={detail?.name ?? ""} onClose={() => setDetail(null)} />
    </div>
  );
}
