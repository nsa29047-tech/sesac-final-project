// 표시용 도우미. 서버의 도구 이름·인자를 사람이 읽는 문구로 바꾼다(Streamlit 의 TOOL_LABEL, ARG_LABEL 과 같은 문구).

const TOOL_LABEL: Record<string, string> = {
  find_restaurants: "조건에 맞는 식당 찾는 중",
  recommend_by_taste: "취향에 맞는 식당 찾는 중",
  get_restaurant_info: "식당 정보 확인 중",
};

const ARG_LABEL: Record<string, string> = {
  country_code: "국가",
  exclude_country_code: "제외 국가",
  region: "지역",
  city: "장소",
  michelin: "미슐랭",
  min_price_level: "가격대 하한",
  max_price_level: "가격대 상한",
  min_rating: "최소 평점",
  cuisine: "분류",
  has_chef: "셰프 정보",
  course_only: "코스",
  open_day: "요일",
  open_time: "시각",
  radius_km: "반경(km)",
  query: "검색어",
  chunk_type: "종류",
  name: "식당",
  sort_by: "정렬",
  limit: "개수",
};

export function describeCall(tool: string, args: Record<string, unknown>): string {
  const parts = Object.entries(args)
    .filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${ARG_LABEL[k] ?? k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`);
  return (TOOL_LABEL[tool] ?? tool) + (parts.length ? ` (${parts.join(", ")})` : "");
}

/** 답변 본문의 마크다운 링크를 없앤다. 링크는 카드의 버튼이 대신하므로 본문에 있으면 중복이다(서버 cards.strip_links 와 같은 규칙). */
export function stripLinks(text: string): string {
  return text.replace(/\s*\[[^\]]*\]\(https?:\/\/[^)]*\)/g, "");
}

/** 미슐랭 라벨을 카드 배지 문구로("미슐랭 1스타" -> "★ 1스타"). */
export function michelinBadge(label: string): string {
  return label.replace("미슐랭 ", "★ ");
}

/** 지역 배열의 뒤 두 단계만("서울특별시","강남구","역삼동" -> "강남구 역삼동"). */
export function shortRegion(region: string[]): string {
  return region.slice(-2).join(" ");
}

export function formatPrice(p: { min: number; max: number | null; currency: string | null }): string {
  const range = p.max ? `${p.min.toLocaleString()}~${p.max.toLocaleString()}` : `${p.min.toLocaleString()}~`;
  return `${p.currency ?? ""} ${range}`.trim();
}

const DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"];
const DAY_ORDER = [1, 2, 3, 4, 5, 6, 0]; // 월 ~ 일

/** 영업시간을 같은 시간이 이어지는 요일끼리 묶는다. [["월–금", "12:00 - 15:00, 18:00 - 22:30"], ["토", "휴무"]] */
export function hoursGroups(hours: { day: number; closed: boolean; intervals: { open: string; close: string }[] }[]): [string, string][] {
  const byDay = new Map<number, string>(
    hours.map((h) => [h.day, h.closed ? "휴무" : h.intervals.map((i) => `${i.open} - ${i.close}`).join(", ")]),
  );
  const groups: { days: number[]; text: string | null }[] = [];
  for (const d of DAY_ORDER) {
    const text = byDay.get(d) ?? null;
    const last = groups[groups.length - 1];
    if (last && last.text === text) last.days.push(d);
    else groups.push({ days: [d], text });
  }
  return groups.map((g) => [
    g.days.length === 1 ? DAY_NAMES[g.days[0]] : `${DAY_NAMES[g.days[0]]}–${DAY_NAMES[g.days[g.days.length - 1]]}`,
    g.text ?? "정보 없음",
  ]);
}

/** 초 -> "m:ss" 또는 "h:mm:ss". */
export function formatTime(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${String(m).padStart(2, "0")}:${ss}`;
}
