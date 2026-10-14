// 챗봇 API 응답 형식. 서버의 src/chatbot/schemas.py 와 맞춘다(CHATBOT_API_DESIGN.md 4절).

export interface Video {
  video_id: string;
  title: string;
  url: string;
  thumbnail: string;
}

export interface Michelin {
  grade: string; // 1_STAR, 2_STARS, 3_STARS, BIB_GOURMAND, SELECTED, UNKNOWN
  label: string; // 미슐랭 1스타
}

export interface Match {
  type: string; // OVERVIEW | FOOD | DRINK | CHEF
  item: string | null;
  content: string;
}

export interface Restaurant {
  restaurant_id: number;
  name: string;
  name_official: string | null;
  country_code: string | null;
  region: string[];
  address: string | null;
  category_broad: string | null;
  category_detail: string | null;
  tags: string[];
  price_level: number | null; // 0~4
  price: { min: number; max: number | null; currency: string | null } | null;
  rating: number | null;
  rating_count: number | null;
  michelin: Michelin | null;
  chef_name: string | null;
  latitude: number | null;
  longitude: number | null;
  google_maps_url: string | null;
  videos: Video[];
  matched: Match[];
}

// POST /api/chat 의 SSE 이벤트
export type ChatEvent =
  | { event: "thread"; data: { thread_id: string } }
  | { event: "status"; data: { tool: string; args: Record<string, unknown> } }
  | { event: "token"; data: { text: string } }
  | { event: "restaurants"; data: { restaurants: Restaurant[] } }
  | { event: "done"; data: { answer: string } }
  | { event: "error"; data: { code: string; message: string } };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  cards: Restaurant[];
  statuses: string[]; // 도구 호출 진행 표시
  streaming: boolean;
  error?: string;
}
