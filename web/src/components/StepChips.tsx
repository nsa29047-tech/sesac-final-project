import { Check, Loader2 } from "lucide-react";
import { stepLabel } from "@/lib/format";
import type { ToolCall } from "@/lib/types";

type State = "done" | "active" | "pending";

const STYLE: Record<State, string> = {
  done: "bg-[#eaf3de] text-[#27500a]",
  active: "bg-brand-soft text-[#993c1d]",
  pending: "bg-[#f1efe8] text-[#8a8279]",
};

function Chip({ state, children }: { state: State; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[12.5px] font-medium ${STYLE[state]}`}>
      {state === "done" && <Check size={14} strokeWidth={2.5} aria-hidden />}
      {state === "active" && <Loader2 size={14} className="animate-spin" aria-hidden />}
      {children}
    </span>
  );
}

/** 답변이 만들어지는 동안의 진행 단계: 조건 확인 -> (식당 찾는 중) -> 답변 작성. 도구를 부르지 않는 답변은 "답변 작성"만 거친다. */
export default function StepChips({ calls, hasText }: { calls: ToolCall[]; hasText: boolean }) {
  const searched = calls.length > 0;
  const tool = calls[calls.length - 1]?.tool;
  return (
    <div className="flex flex-wrap gap-2" role="status" aria-live="polite">
      <Chip state={searched || hasText ? "done" : "active"}>조건 확인</Chip>
      {searched && <Chip state={hasText ? "done" : "active"}>{stepLabel(tool)}</Chip>}
      <Chip state={hasText ? "active" : "pending"}>답변 작성</Chip>
    </div>
  );
}
