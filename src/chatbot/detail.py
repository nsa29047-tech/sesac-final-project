"""식당 상세 화면용 데이터와 HTML. 식당 한 곳의 영상 속 메뉴 타임라인, 영상 소개·총평, 영업시간·주소·가격대 등을 모은다.

메뉴별 맛 후기는 저장하지 않는다(평가어는 후처리에서 제거). 메뉴에는 조리 특징 설명(description)만 있고,
후기는 식당 단위의 총평(final_review)·키포인트만 있다.
"""
from .cards import esc, first_video
from .tools import DAY_NAMES, run_query, summarize

DAY_ORDER = [1, 2, 3, 4, 5, 6, 0]   # 월 ~ 일


def fmt_time(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fetch_detail(restaurant_id: int) -> dict:
    """식당 한 곳의 모든 정보를 모아 돌려준다."""
    summary = summarize([restaurant_id])
    if not summary:
        return {}
    d = summary[0]
    base = run_query("SELECT phone, website, formatted_address, chef_info FROM restaurants WHERE restaurant_id = %s", (restaurant_id,))[0]
    d.update(base)
    d["notes"] = run_query(
        """SELECT n.video_id, v.title, v.url, n.concept, n.atmosphere, n.key_points, n.final_review, n.is_course, n.course_name
           FROM video_restaurant_notes n JOIN videos v USING (video_id) WHERE n.restaurant_id = %s ORDER BY n.video_id""", (restaurant_id,))
    d["menus"] = run_query(
        """SELECT video_id, item_type, name, description, course_stage, menu_order, mentioned_sec
           FROM menus WHERE restaurant_id = %s ORDER BY video_id, item_type, menu_order NULLS LAST, mentioned_sec NULLS LAST, menu_id""", (restaurant_id,))
    d["hours"] = run_query("SELECT day_of_week, open_time, close_time FROM restaurant_hours WHERE restaurant_id = %s ORDER BY day_of_week, seq", (restaurant_id,))
    d["tags"] = [r["tag"] for r in run_query("SELECT tag FROM restaurant_tags WHERE restaurant_id = %s ORDER BY tag", (restaurant_id,))]
    return d


TL_CSS = """<style>
.tl{border-left:2px solid #ffd2c5;margin:6px 0 4px 8px;padding-left:0}
.tl-item{position:relative;padding:0 0 14px 18px}
.tl-item:before{content:"";position:absolute;left:-6px;top:5px;width:10px;height:10px;border-radius:50%;background:#ff5a3c;border:2px solid #fff}
.tl-time a{font-size:12.5px;font-weight:700;color:#fff;background:#ff5a3c;padding:2px 9px;border-radius:999px;text-decoration:none}
.tl-time span{font-size:12.5px;color:#999}
.tl-name{font-size:15px;font-weight:700;color:#222;margin:5px 0 2px}
.tl-stage{font-size:11px;color:#a66;background:#fff3ef;padding:1px 7px;border-radius:999px;margin-left:6px;font-weight:500}
.tl-desc{font-size:13.5px;color:#555;line-height:1.55}
.dr{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #f1ece8}
.dr b{font-size:14px;color:#222}.dr div{font-size:13px;color:#666;line-height:1.5}
.hrs{display:grid;grid-template-columns:48px 1fr;gap:4px 8px;font-size:13.5px;color:#333}
.hrs .off{color:#bbb}
.sec{font-size:17px;font-weight:800;color:#222;margin:22px 0 6px}
.kp{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
.kp span{background:#fff3ef;color:#e0481f;font-size:12.5px;padding:3px 10px;border-radius:999px}
.rv{background:#f7f5f2;border-radius:12px;padding:12px 14px;font-size:14px;color:#444;line-height:1.6;margin:6px 0}
</style>""".replace("\n", "")


def timeline_html(menus: list, video_urls: dict) -> str:
    """영상 속 음식 타임라인. 시각이 있는 음식은 시각순으로, 해당 시각부터 재생하는 링크를 건다."""
    foods = [m for m in menus if m["item_type"] == "FOOD"]
    foods.sort(key=lambda m: (m["mentioned_sec"] is None, m["mentioned_sec"] or 0, m["menu_order"] or 0))
    rows = []
    for m in foods:
        url = video_urls.get(m["video_id"])
        if m["mentioned_sec"] is not None and url:
            when = f'<a href="{esc(url)}&t={int(m["mentioned_sec"])}s" target="_blank">▶ {fmt_time(m["mentioned_sec"])}</a>'
        else:
            when = "<span>시각 정보 없음</span>"
        stage = f'<span class="tl-stage">{esc(m["course_stage"])}</span>' if m.get("course_stage") else ""
        desc = f'<div class="tl-desc">{esc(m["description"])}</div>' if m.get("description") else ""
        rows.append(f'<div class="tl-item"><div class="tl-time">{when}</div><div class="tl-name">{esc(m["name"])}{stage}</div>{desc}</div>')
    return '<div class="tl">' + "".join(rows) + "</div>" if rows else ""


def drinks_html(menus: list) -> str:
    rows = [f'<div class="dr"><div>🍷</div><div><b>{esc(m["name"])}</b>' + (f'<div>{esc(m["description"])}</div>' if m.get("description") else "") + "</div></div>"
            for m in menus if m["item_type"] == "DRINK"]
    return "".join(rows)


def hours_html(hours: list) -> str:
    by_day = {}
    for h in hours:
        by_day.setdefault(h["day_of_week"], []).append("휴무" if h["open_time"] is None else f"{h['open_time']:%H:%M} - {h['close_time']:%H:%M}")
    if not by_day:
        return '<div class="tl-desc">영업시간 정보가 없습니다.</div>'
    cells = []
    for d in DAY_ORDER:
        v = by_day.get(d)
        cells.append(f'<div><b>{DAY_NAMES[d]}</b></div><div class="{"off" if v == ["휴무"] else ""}">{esc(", ".join(v)) if v else "-"}</div>')
    return '<div class="hrs">' + "".join(cells) + "</div>"


def video_urls_of(d: dict) -> dict:
    return {n["video_id"]: n["url"] for n in d["notes"]}


def first_thumb(d: dict):
    _, vid = first_video(d.get("videos"))
    return f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if vid else None


PLAYER_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
html,body{margin:0;height:100%;font-family:-apple-system,"Segoe UI","Malgun Gothic",sans-serif;background:#fff}
#wrap{display:flex;flex-direction:column;height:100%}
#pw{position:relative;width:100%;padding-top:56.25%;background:#000;border-radius:12px;overflow:hidden;flex:none}
#player,#err{position:absolute;inset:0;width:100%;height:100%}
#err{display:none;align-items:center;justify-content:center;color:#fff;text-align:center;font-size:14px;line-height:1.6;padding:16px;box-sizing:border-box}
#err a{color:#ffb199}
#tl{flex:1;overflow:auto;margin-top:12px;border-left:2px solid #ffd2c5;padding-left:0}
.it{position:relative;padding:8px 10px 10px 18px;border-radius:0 10px 10px 0;cursor:default}
.it:before{content:"";position:absolute;left:-6px;top:14px;width:10px;height:10px;border-radius:50%;background:#ff5a3c;border:2px solid #fff}
.it.go{cursor:pointer}.it.go:hover{background:#fff7f4}
.it.on{background:#fff1ec}.it.on:before{box-shadow:0 0 0 4px #ffd2c5}
.t{display:inline-block;font-size:12.5px;font-weight:700;color:#fff;background:#ff5a3c;padding:2px 9px;border-radius:999px}
.t.none{background:#ddd;color:#888;font-weight:500}
.n{font-size:15px;font-weight:700;color:#222;margin:5px 0 2px}
.s{font-size:11px;color:#a66;background:#fff3ef;padding:1px 7px;border-radius:999px;margin-left:6px;font-weight:500}
.d{font-size:13.5px;color:#555;line-height:1.55}
</style></head><body><div id="wrap"><div id="pw"><div id="player"></div><div id="err"></div></div><div id="tl"></div></div><script>
var VID=__VID__, URL_=__URL__, ITEMS=__ITEMS__, player=null, nodes=[];
function fmt(s){var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return (h?h+":"+String(m).padStart(2,"0"):String(m).padStart(2,"0"))+":"+String(x).padStart(2,"0")}
function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
var tl=document.getElementById("tl");
ITEMS.forEach(function(it,i){
  var row=el("div","it"+(it.sec!=null?" go":""));
  row.appendChild(el("span","t"+(it.sec!=null?"":" none"),it.sec!=null?"\u25b6 "+fmt(it.sec):"시각 정보 없음"));
  var n=el("div","n",it.name); if(it.stage)n.appendChild(el("span","s",it.stage)); row.appendChild(n);
  if(it.desc)row.appendChild(el("div","d",it.desc));
  if(it.sec!=null)row.onclick=function(){seek(it.sec)};
  tl.appendChild(row); nodes.push(row);
});
function seek(sec){ if(player&&player.seekTo){player.seekTo(sec,true);player.playVideo();window.scrollTo(0,0)} }
function fail(){var e=document.getElementById("err");e.style.display="flex";e.innerHTML="";e.appendChild(document.createTextNode("이 영상은 앱 안에서 재생할 수 없어요. "));var a=el("a",null,"YouTube에서 보기");a.href=URL_;a.target="_blank";e.appendChild(a)}
function tick(){ if(!player||!player.getCurrentTime)return; var t=player.getCurrentTime(),cur=-1;
  ITEMS.forEach(function(it,i){if(it.sec!=null&&it.sec<=t)cur=i}); nodes.forEach(function(n,i){n.classList.toggle("on",i===cur)}) }
window.onYouTubeIframeAPIReady=function(){
  player=new YT.Player("player",{videoId:VID,width:"100%",height:"100%",playerVars:{rel:0,modestbranding:1,playsinline:1},events:{onError:fail}});
  setInterval(tick,500)};
var s=document.createElement("script");s.src="https://www.youtube.com/iframe_api";s.onerror=fail;document.head.appendChild(s);
</script></body></html>"""


def player_html(video_id: str, url: str, menus: list) -> str:
    """앱 안에서 영상을 재생하는 HTML(유튜브 IFrame API). 오른쪽 타임라인의 메뉴를 누르면 그 시각으로 이동해 재생하고,
    재생 중에는 현재 시각에 해당하는 메뉴를 강조한다. 텍스트는 JS에서 textContent로 넣어 별도 이스케이프가 필요 없다."""
    import json
    foods = [m for m in menus if m["item_type"] == "FOOD"]
    foods.sort(key=lambda m: (m["mentioned_sec"] is None, m["mentioned_sec"] or 0, m["menu_order"] or 0))
    items = [{"sec": m["mentioned_sec"], "name": m["name"], "stage": m.get("course_stage"), "desc": m.get("description")} for m in foods]
    dump = lambda o: json.dumps(o, ensure_ascii=False).replace("</", r"<\/")   # </script> 방지
    return PLAYER_TEMPLATE.replace("__VID__", dump(video_id)).replace("__URL__", dump(url)).replace("__ITEMS__", dump(items))


DETAIL_CSS = """<style>
.sec{font-size:16px;font-weight:800;color:#222;margin:28px 0 10px;padding-top:2px}
.card{background:#faf7f4;border-radius:14px;padding:14px 16px;margin-bottom:10px}
.card.white{background:#fff;border:1px solid #eee}
.kv{display:grid;grid-template-columns:62px 1fr;gap:9px 12px;font-size:14px;line-height:1.6}
.kv .k{color:#9a8f86;font-size:12.5px;font-weight:600;padding-top:2px}
.kv .v{color:#2b2b2b;word-break:break-word}
.vid{font-size:13px;color:#888;margin-bottom:10px}
.vid a{color:#666;text-decoration:none;border-bottom:1px solid #ddd}
.vid b{color:#ff5a3c;font-weight:700;margin-right:6px}
.quote{border-left:3px solid #ff5a3c;padding:2px 0 2px 14px;font-size:14.5px;color:#333;line-height:1.65;margin:14px 0 4px}
.quote small{display:block;color:#aaa;font-size:11.5px;margin-top:3px}
.note{font-size:12px;color:#a09890;line-height:1.55;margin-top:10px}
.btns{display:flex;gap:8px;margin-top:12px}
.btns a{font-size:13px;font-weight:600;padding:7px 14px;border-radius:999px;text-decoration:none;border:1px solid #ffd2c5;color:#e0481f;background:#fff}
.hrs2{display:grid;grid-template-columns:62px 1fr;gap:8px 12px;font-size:14px;line-height:1.5}
.hrs2 .d{font-weight:700;color:#333;font-size:13.5px}
.hrs2 .t{color:#2b2b2b}
.hrs2 .t.off{color:#bbb}
.kp{margin:12px 0 0}
.card .dr:last-child{border-bottom:0}
</style>""".replace("\n", "")


def hours_groups(hours: list):
    """요일별 영업시간을 같은 시간이 이어지는 요일끼리 묶는다. [('월–금', '12:00 - 15:00, 18:00 - 22:30'), ('토', ...)]. 휴무는 '휴무'."""
    by_day = {}
    for h in hours:
        by_day.setdefault(h["day_of_week"], []).append("휴무" if h["open_time"] is None else f"{h['open_time']:%H:%M} - {h['close_time']:%H:%M}")
    groups = []
    for d in DAY_ORDER:
        text = ", ".join(by_day[d]) if d in by_day else None
        if groups and groups[-1][2] == text:
            groups[-1][1].append(d)
        else:
            groups.append([None, [d], text])
    out = []
    for _, days, text in groups:
        label = DAY_NAMES[days[0]] if len(days) == 1 else f"{DAY_NAMES[days[0]]}–{DAY_NAMES[days[-1]]}"
        out.append((label, text or "정보 없음"))
    return out


def section(icon: str, title: str) -> str:
    return f'<div class="sec">{icon} {esc(title)}</div>'


def intro_html(n: dict) -> str:
    """영상 한 편의 식당 소개: 영상 제목, 컨셉·분위기(라벨/값), 키포인트, 총평 인용."""
    course = f' · 코스: {esc(n["course_name"])}' if n.get("course_name") and n["course_name"] != "코스" else (" · 코스 요리" if n.get("is_course") else "")
    parts = [f'<div class="vid"><b>▶</b><a href="{esc(n["url"])}" target="_blank">{esc(n["title"])}</a>{course}</div>']
    rows = [(k, n.get(f)) for k, f in (("컨셉", "concept"), ("분위기", "atmosphere")) if n.get(f)]
    if rows:
        parts.append('<div class="kv">' + "".join(f'<div class="k">{k}</div><div class="v">{esc(v)}</div>' for k, v in rows) + "</div>")
    if n.get("key_points"):
        parts.append('<div class="kp">' + "".join(f"<span>{esc(k)}</span>" for k in n["key_points"]) + "</div>")
    if n.get("final_review"):
        parts.append(f'<div class="quote">{esc(n["final_review"])}<small>영상 속 총평</small></div>')
    return '<div class="card">' + "".join(parts) + "</div>"


def info_html(d: dict):
    """기본 정보 카드 두 장: (주소·전화·가격·링크, 영업시간)."""
    rows = [("주소", d.get("formatted_address")), ("전화", d.get("phone"))]
    if d.get("price_min"):
        cur = d.get("price_currency") or ""
        rng = f'{d["price_min"]:,}~' + (f'{d["price_max"]:,}' if d.get("price_max") else "")
        rows.append(("가격", f"{cur} {rng} (1인 기준)"))
    elif d.get("price_level"):
        lv = int(d["price_level"])
        rows.append(("가격대", f'{"●" * lv}{"○" * (4 - lv)} (Google 기준 단계)'))
    else:
        rows.append(("가격대", "정보 없음"))
    links = []
    if d.get("website"):
        links.append(f'<a href="{esc(d["website"])}" target="_blank">웹사이트</a>')
    if d.get("google_maps_url"):
        links.append(f'<a href="{esc(d["google_maps_url"])}" target="_blank">구글 지도</a>')
    left = ('<div class="card white"><div class="kv">' + "".join(f'<div class="k">{k}</div><div class="v">{esc(v)}</div>' for k, v in rows if v) + "</div>"
            + (f'<div class="btns">{"".join(links)}</div>' if links else "") + "</div>")
    if d["hours"]:
        right = ('<div class="card white"><div class="hrs2">' + "".join(
            f'<div class="d">{esc(lab)}</div><div class="t{" off" if t == "휴무" else ""}">{esc(t)}</div>' for lab, t in hours_groups(d["hours"])) + "</div></div>")
    else:
        right = '<div class="card white"><div class="note" style="margin:0">영업시간 정보가 없습니다.</div></div>'
    return left, right
