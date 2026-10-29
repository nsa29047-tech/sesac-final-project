"use client";

import { MessageSquare, Plus, Trash2, X } from "lucide-react";
import type { ConversationSummary } from "@/lib/types";

interface Props {
  open: boolean;
  items: ConversationSummary[];
  currentId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

/** 내 대화 기록. 화면 왼쪽에서 겹쳐 열리는 서랍이다(채팅과 결과 패널의 너비를 줄이지 않으려고 자리를 차지하지 않는다). */
export default function ConversationDrawer({ open, items, currentId, onClose, onSelect, onNew, onDelete }: Props) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex" role="dialog" aria-label="대화 기록">
      <aside className="flex h-full w-80 max-w-[85vw] flex-col border-r border-neutral-200 bg-white shadow-xl">
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-neutral-200 px-4">
          <span className="text-[15px] font-bold text-neutral-900">대화 기록</span>
          <button type="button" onClick={onClose} aria-label="닫기" className="rounded-full p-1.5 text-neutral-500 hover:bg-neutral-100">
            <X size={18} aria-hidden />
          </button>
        </div>
        <div className="shrink-0 p-3">
          <button
            type="button"
            onClick={onNew}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-neutral-200 py-2 text-[14px] font-semibold text-neutral-700 hover:border-brand hover:text-brand-ink"
          >
            <Plus size={16} aria-hidden />새 대화
          </button>
        </div>
        <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {items.length === 0 && <li className="px-3 py-6 text-center text-[13px] text-neutral-400">아직 대화가 없어요.</li>}
          {items.map((c) => (
            <li key={c.id} className="group relative">
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                className={`flex w-full items-center gap-2 rounded-xl px-3 py-2.5 pr-10 text-left text-[14px] ${
                  c.id === currentId ? "bg-brand-soft font-semibold text-brand-ink" : "text-neutral-700 hover:bg-neutral-100"
                }`}
              >
                <MessageSquare size={15} className="shrink-0 opacity-60" aria-hidden />
                <span className="truncate">{c.title}</span>
              </button>
              <button
                type="button"
                onClick={() => onDelete(c.id)}
                aria-label={`"${c.title}" 대화 삭제`}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-full p-1.5 text-neutral-400 opacity-60 hover:bg-neutral-200 hover:text-red-600 hover:opacity-100 focus:opacity-100"
              >
                <Trash2 size={15} aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <button type="button" aria-label="닫기" onClick={onClose} className="flex-1 bg-black/30" />
    </div>
  );
}
