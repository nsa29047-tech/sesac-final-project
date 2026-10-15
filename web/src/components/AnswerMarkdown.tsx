"use client";

import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { stripLinks } from "@/lib/format";
import type { Restaurant } from "@/lib/types";

const norm = (s: string) => s.replace(/\s+/g, "").toLowerCase();

function textOf(node: ReactNode): string {
  return Children.toArray(node)
    .map((c) => (typeof c === "string" ? c : isValidElement(c) ? textOf((c.props as { children?: ReactNode }).children) : ""))
    .join("");
}

/** 식당 이름이 이 답변의 카드 중 하나와 같으면 그 식당을 돌려준다(이름을 눌러 상세를 열기 위해). */
function matchCard(name: string, cards: Restaurant[]): Restaurant | undefined {
  const n = norm(name);
  if (n.length < 2) return undefined;
  return cards.find((c) => {
    const k = norm(c.name);
    return k.length >= 2 && (n === k || n.includes(k) || k.includes(n));
  });
}

/**
 * 답변 본문. 목록 항목이 "**이름** - 설명" 형태이면 이름과 설명을 따로 그려서(글꼴·색 구분) 한눈에 구분되게 한다.
 * 이름이 카드의 식당과 같으면 이름을 눌러 상세 팝업을 열 수 있다.
 */
export default function AnswerMarkdown({ text, cards, onDetail }: { text: string; cards: Restaurant[]; onDetail?: (r: Restaurant) => void }) {
  return (
    <div className="answer text-[15.5px] leading-[1.75]">
      <ReactMarkdown
        components={{
          ol: ({ children }) => <ol className="md-ol">{children}</ol>,
          ul: ({ children }) => <ul className="md-ul">{children}</ul>,
          li: ({ children }) => {
            // 줄바꿈만 있는 글자는 버린다(항목 안에 섞여 들어온다)
            const blank = (c: ReactNode) => typeof c === "string" && c.trim() === "";
            let parts = Children.toArray(children).filter((c) => !blank(c));
            // 항목 사이에 빈 줄이 있으면 내용이 <p> 하나로 감싸져 온다
            if (parts.length === 1 && isValidElement(parts[0]) && parts[0].type === "p") {
              parts = Children.toArray((parts[0].props as { children?: ReactNode }).children).filter((c) => !blank(c));
            }
            const [first, ...rest] = parts;
            if (!isValidElement(first) || first.type !== "strong") return <li>{children}</li>;

            const nameNode = (first.props as { children?: ReactNode }).children;
            const card = onDetail ? matchCard(textOf(nameNode), cards) : undefined;
            if (typeof rest[0] === "string") rest[0] = rest[0].replace(/^\s*[-–—:·]+\s*/, ""); // 이름과 설명 사이의 구분 기호는 지운다
            const hasDesc = rest.some((r) => (typeof r === "string" ? r.trim() !== "" : true));
            return (
              <li>
                <span className="li-name">
                  {card ? (
                    <button type="button" onClick={() => onDetail?.(card)} title="상세 정보 보기">
                      {nameNode}
                    </button>
                  ) : (
                    nameNode
                  )}
                </span>
                {hasDesc && <span className="li-desc">{rest}</span>}
              </li>
            );
          },
        }}
      >
        {stripLinks(text)}
      </ReactMarkdown>
    </div>
  );
}
