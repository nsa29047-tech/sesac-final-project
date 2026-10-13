"""식당 카드 UI용 함수. 도구 결과(JSON)에서 식당을 모으고, 답변에 언급된 식당만 골라 HTML 카드로 만든다.

LLM이 쓴 글을 파싱하지 않고 도구가 돌려준 구조화 데이터(이름·지역·평점·미슐랭·영상 링크)로 카드를 그리므로 값이 틀리지 않는다.
"""
import html
import json
import re

CSS = """
<style>
.rc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin:10px 0 4px}
.rc{background:#fff;border:1px solid #eee;border-radius:16px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.06);display:flex;flex-direction:column}
.rc-img{height:128px;background:linear-gradient(135deg,#ffb199,#ff6a3c);background-size:cover;background-position:center;position:relative}
.rc-num{position:absolute;right:10px;top:10px;width:26px;height:26px;border-radius:50%;background:#ff5a3c;color:#fff;font-weight:800;font-size:13px;display:flex;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.3)}
.rc-badge{position:absolute;left:10px;top:10px;background:#fff;color:#d42a1b;font-size:12px;font-weight:700;padding:3px 9px;border-radius:999px;box-shadow:0 1px 4px rgba(0,0,0,.2)}
.rc-body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px;flex:1}
.rc-name{font-size:16px;font-weight:700;color:#222;line-height:1.3}
.rc-sub{font-size:12.5px;color:#777}
.rc-meta{font-size:13px;color:#333;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.rc-star{color:#ff5a3c;font-weight:700}
.rc-count{color:#999;font-weight:400}
.rc-price{color:#888;letter-spacing:1px}
.rc-price b{color:#ff5a3c}
.rc-chips{display:flex;gap:5px;flex-wrap:wrap}
.rc-chip{background:#fff3ef;color:#e0481f;font-size:11.5px;padding:2px 8px;border-radius:999px}
.rc-quote{font-size:12.5px;color:#555;background:#f7f5f2;border-radius:10px;padding:7px 9px;line-height:1.45}
.rc-links{margin-top:auto;display:flex;gap:8px;padding-top:6px}
.rc-links a{flex:1;text-align:center;font-size:12.5px;font-weight:600;padding:7px 0;border-radius:10px;text-decoration:none;border:1px solid #ffd2c5;color:#e0481f;background:#fff}
.rc-links a.pri{background:#ff5a3c;color:#fff;border-color:#ff5a3c}
</style>
"""


def collect(tool_content: str, into: dict):
    """도구 결과 JSON에서 식당을 모아 into(restaurant_id -> dict)에 넣는다. 같은 식당이 다시 나오면 matched를 유지한다."""
    try:
        data = json.loads(tool_content)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict):
        return
    for r in data.get("restaurants", []):
        into.setdefault(r["restaurant_id"], r)


def pick(restaurants: dict, answer: str, limit: int = 6):
    """답변에 이름이 나온 식당만, 처음 언급된 순서로 고른다."""
    low = answer.lower()
    found = []
    for r in restaurants.values():
        pos = [low.find(n.lower()) for n in (r.get("name"), r.get("name_official")) if n and len(n) >= 2]
        pos = [p for p in pos if p >= 0]
        if pos:
            found.append((min(pos), r))
    return [r for _, r in sorted(found, key=lambda x: x[0])][:limit]


def esc(text) -> str:
    """HTML 이스케이프. 달러 기호는 Streamlit 마크다운이 수식으로 읽지 않게 엔티티로 바꾼다."""
    return html.escape(str(text)).replace("$", "&#36;")


def first_video(videos):
    """'제목 url | 제목 url' 문자열에서 첫 영상의 (url, 영상 id)."""
    for part in (videos or "").split(" | "):
        m = re.search(r"https?://\S*v=([\w-]{11})\S*", part)
        if m:
            return m.group(0), m.group(1)
    return None, None


def card_html(r: dict, num=None) -> str:
    video_url, vid = first_video(r.get("videos"))
    img_style = f"background-image:url('https://img.youtube.com/vi/{vid}/mqdefault.jpg')" if vid else ""
    michelin = r.get("michelin") or ""
    badge = f'<div class="rc-badge">{esc(michelin.replace("미슐랭 ", "★ "))}</div>' if michelin and not michelin.startswith("미슐랭 아님") else ""

    parts = [p for p in (r.get("region") or "").split(" > ") if p]
    region = " ".join(parts[-2:]) if parts else ""
    cat = (r.get("category_detail") or r.get("category_broad") or "").strip()
    cat = cat if len(cat) <= 22 else cat[:21] + "…"
    sub = " · ".join(x for x in (region, cat) if x)

    meta = []
    if r.get("rating"):
        n = f' <span class="rc-count">({r["rating_count"]:,})</span>' if r.get("rating_count") else ""
        meta.append(f'<span class="rc-star">★ {esc(r["rating"])}</span>{n}')
    lvl = r.get("price_level")
    if lvl:
        meta.append(f'<span class="rc-price"><b>{"●" * int(lvl)}</b>{"●" * (4 - int(lvl))}</span>')
    if r.get("price_min"):
        cur = r.get("price_currency") or ""
        rng = f'{r["price_min"]:,}~{r["price_max"]:,}' if r.get("price_max") else f'{r["price_min"]:,}~'
        meta.append(f'<span class="rc-sub">{esc(cur)} {rng}</span>')

    chips = []
    if r.get("chef_name"):
        chips.append(f'👨‍🍳 {esc(r["chef_name"])}')
    chips_html = f'<div class="rc-chips">{"".join(f"<span class=rc-chip>{c}</span>" for c in chips)}</div>' if chips else ""

    quote = ""
    if r.get("matched"):
        m = r["matched"][0]
        text = (m.get("content") or "").replace("\n", " ")
        quote = f'<div class="rc-quote">“{esc(text[:90] + ("…" if len(text) > 90 else ""))}”</div>'

    links = []
    if video_url:
        links.append(f'<a class="pri" href="{esc(video_url)}" target="_blank">영상 보기</a>')
    if r.get("google_maps_url"):
        links.append(f'<a href="{esc(r["google_maps_url"])}" target="_blank">지도</a>')

    return (f'<div class="rc"><div class="rc-img" style="{img_style}">{badge}{f'<div class="rc-num">{num}</div>' if num else ""}</div><div class="rc-body">'
            f'<div class="rc-name">{esc(r.get("name") or r.get("name_official"))}</div><div class="rc-sub">{esc(sub)}</div>'
            f'<div class="rc-meta">{"".join(meta)}</div>{chips_html}{quote}<div class="rc-links">{"".join(links)}</div></div></div>')


def cards_html(restaurants: list) -> str:
    """카드 목록 전체 HTML. 줄바꿈이 있으면 마크다운이 HTML 블록을 끊으므로 한 줄로 합친다."""
    if not restaurants:
        return ""
    return CSS.replace("\n", "") + '<div class="rc-grid">' + "".join(card_html(r) for r in restaurants) + "</div>"


def strip_links(text: str) -> str:
    """답변 본문의 마크다운 링크를 없앤다. 링크는 카드의 버튼이 대신하므로 본문에 있으면 중복이다(링크 글자는 지운다)."""
    return re.sub(r"\s*\[[^\]]*\]\(https?://[^)]*\)", "", text)
