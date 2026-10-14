import type { ChatEvent } from "./types";

// 챗봇 서버 주소. 빌드할 때 값이 코드에 들어간다(NEXT_PUBLIC_). 바꾸려면 web/.env.local 에 설정하고 다시 빌드·재시작한다.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
  ) {
    super(message);
  }
}

/** SSE 텍스트를 이벤트로 쪼갠다. 이벤트는 빈 줄로 구분되고 `event:` 와 `data:` 줄로 이루어진다. */
function parseBlock(block: string): ChatEvent | null {
  let event = "";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!event || data.length === 0) return null;
  return { event, data: JSON.parse(data.join("\n")) } as ChatEvent;
}

/**
 * 질문을 보내고 SSE 이벤트를 하나씩 onEvent 로 전달한다. 연결을 못 하거나 서버가 오류 코드를 주면 ApiError 를 던진다.
 * signal 을 abort 하면 연결을 끊고(서버도 그래프 실행을 멈춘다) 조용히 끝난다.
 */
export async function streamChat(
  threadId: string,
  message: string,
  onEvent: (e: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, message }),
      signal,
    });
  } catch {
    if (signal.aborted) return;
    throw new ApiError("챗봇 서버에 연결할 수 없어요. 서버(src/api/chat_server.py)가 켜져 있는지 확인해 주세요.");
  }
  if (!res.ok || !res.body) {
    let detail = "";
    try {
      detail = (await res.json()).detail ?? "";
    } catch {}
    const msg = res.status === 409 ? "이 대화는 아직 답변 중이에요. 잠시 후 다시 보내 주세요." : `요청을 처리하지 못했어요 (${res.status}) ${typeof detail === "string" ? detail : ""}`;
    throw new ApiError(msg, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) >= 0) {
        const parsed = parseBlock(buffer.slice(0, sep));
        buffer = buffer.slice(sep + 2);
        if (parsed) onEvent(parsed);
      }
    }
  } catch {
    if (signal.aborted) return;
    throw new ApiError("답변을 받는 도중 연결이 끊겼어요.");
  }
}
