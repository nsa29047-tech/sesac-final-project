import { getToken } from "./auth";
import type { ChatEvent, ConversationSummary, RestaurantDetail, SavedMessage, User } from "./types";

// Spring 서버 주소(server/). 챗봇 서버는 Spring 이 대신 호출하므로 브라우저는 이 주소만 안다.
// 빌드할 때 값이 코드에 들어간다(NEXT_PUBLIC_). 바꾸려면 web/.env.local 에 설정하고 다시 빌드·재시작한다.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8081";

const UNREACHABLE = "서버에 연결할 수 없어요. Spring 서버(server/)가 켜져 있는지 확인해 주세요.";

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
    public code?: string,
  ) {
    super(message);
  }
}

// 토큰이 만료되거나 틀려서 401 을 받으면 앱이 로그인 화면으로 돌아가도록 알려 주는 연결 고리.
let unauthorizedHandler: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

/** 서버의 오류 응답({code, message})을 ApiError 로 바꾼다. */
async function toError(res: Response, fallback: string): Promise<ApiError> {
  let code: string | undefined;
  let message: string | undefined;
  try {
    const body = await res.json();
    code = body.code;
    message = body.message;
  } catch {}
  return new ApiError(message ?? `${fallback} (${res.status})`, res.status, code);
}

/** 토큰을 붙여 요청한다. auth=false 면 토큰 없이(로그인·가입). 연결 실패는 ApiError, abort 는 AbortError 로 던진다. */
async function call(path: string, init: RequestInit = {}, auth = true): Promise<Response> {
  const headers: Record<string, string> = {};
  if (init.body) headers["Content-Type"] = "application/json";
  const token = auth ? getToken() : null;
  if (token) headers.Authorization = `Bearer ${token}`;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers });
  } catch {
    if (init.signal?.aborted) throw new DOMException("aborted", "AbortError");
    throw new ApiError(UNREACHABLE);
  }
  if (res.status === 401 && auth) unauthorizedHandler?.();
  return res;
}

// ---- 계정 ----------------------------------------------------------------------------------

export async function signup(email: string, password: string, nickname: string): Promise<void> {
  const res = await call("/api/auth/signup", { method: "POST", body: JSON.stringify({ email, password, nickname }) }, false);
  if (!res.ok) throw await toError(res, "가입하지 못했어요");
}

/** 로그인해서 액세스 토큰을 돌려준다. */
export async function login(email: string, password: string): Promise<string> {
  const res = await call("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }, false);
  if (!res.ok) throw await toError(res, "로그인하지 못했어요");
  return (await res.json()).accessToken;
}

export async function fetchMe(): Promise<User> {
  const res = await call("/api/me");
  if (!res.ok) throw await toError(res, "사용자 정보를 불러오지 못했어요");
  return res.json();
}

// ---- 대화 ----------------------------------------------------------------------------------

export async function listConversations(): Promise<ConversationSummary[]> {
  const res = await call("/api/conversations");
  if (!res.ok) throw await toError(res, "대화 목록을 불러오지 못했어요");
  return res.json();
}

export async function fetchMessages(conversationId: string): Promise<SavedMessage[]> {
  const res = await call(`/api/conversations/${conversationId}/messages`);
  if (!res.ok) throw await toError(res, "대화를 불러오지 못했어요");
  return res.json();
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const res = await call(`/api/conversations/${conversationId}`, { method: "DELETE" });
  if (!res.ok) throw await toError(res, "대화를 지우지 못했어요");
}

// ---- 챗봇 ----------------------------------------------------------------------------------

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

/** 시작 전에 Spring 이 거절한 경우의 안내 문구. 그 밖에는 서버가 준 메시지를 쓴다. */
const CHAT_ERRORS: Record<string, string> = {
  CHAT_BUSY: "이 대화는 아직 답변 중이에요. 잠시 후 다시 보내 주세요.",
  CHAT_OVERLOADED: "지금 요청이 많아요. 잠시 후 다시 시도해 주세요.",
  CHAT_UNAVAILABLE: "챗봇 서버에 연결할 수 없어요. 서버(src/api/chat_server.py)가 켜져 있는지 확인해 주세요.",
};

/**
 * 질문을 보내고 SSE 이벤트를 하나씩 onEvent 로 전달한다. 연결을 못 하거나 서버가 오류 코드를 주면 ApiError 를 던진다.
 * signal 을 abort 하면 연결을 끊고(Spring 이 챗봇 서버 연결도 닫아 그래프 실행이 멈춘다) 조용히 끝난다.
 * conversationId 는 챗봇의 대화 기억 키(thread_id)와 같다.
 */
export async function streamChat(
  conversationId: string,
  message: string,
  onEvent: (e: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await call("/api/chat", { method: "POST", body: JSON.stringify({ conversationId, message }), signal });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return;
    throw e;
  }
  if (!res.ok || !res.body) {
    const err = await toError(res, "요청을 처리하지 못했어요");
    if (res.status === 401) throw new ApiError("로그인이 필요해요.", 401, err.code);
    throw new ApiError((err.code && CHAT_ERRORS[err.code]) || err.message, res.status, err.code);
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

/** 식당 상세. 없으면 ApiError(404), 서버에 닿지 않으면 ApiError. */
export async function fetchDetail(restaurantId: number, signal?: AbortSignal): Promise<RestaurantDetail> {
  const res = await call(`/api/restaurants/${restaurantId}`, { signal });
  if (res.status === 404) throw new ApiError("식당 정보를 찾지 못했어요.", 404);
  if (!res.ok) throw await toError(res, "식당 정보를 불러오지 못했어요");
  return res.json();
}
