"""추천 식당을 지도에 찍는 HTML(Leaflet + OpenStreetMap 타일, API 키 불필요).

카드와 같은 순서의 번호 마커를 찍고, 마커를 누르면 이름·평점·미슐랭·영상/지도 링크가 담긴 말풍선이 뜬다.
지도는 마커가 모두 보이도록 범위를 자동으로 맞춘다. 텍스트는 JS에서 DOM으로 만들어 넣으므로 별도 이스케이프가 필요 없다.
주의: 지도(iframe)는 Streamlit에 클릭을 돌려줄 수 없어서 마커를 눌러 상세 팝업을 열 수는 없다(상세 보기는 카드 버튼).
"""
import json

from .cards import first_video

TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body,#map{margin:0;height:100%;width:100%;font-family:-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
#map{border-radius:14px;background:#eee}
.pin{width:30px;height:30px;border-radius:50%;background:#ff5a3c;color:#fff;font-weight:800;font-size:14px;display:flex;align-items:center;justify-content:center;border:2px solid #fff;box-shadow:0 2px 6px rgba(0,0,0,.35)}
.pin.one{background:#e0481f}
.pp{min-width:170px;line-height:1.5}.pp b{font-size:14px;color:#222}.pp .s{font-size:12px;color:#777}
.pp .m{font-size:12px;color:#d42a1b;font-weight:700}.pp .r{font-size:12.5px;color:#ff5a3c;font-weight:700}
.pp a{display:inline-block;margin:6px 6px 0 0;font-size:12px;font-weight:600;color:#e0481f;text-decoration:none;border:1px solid #ffd2c5;border-radius:999px;padding:3px 10px}
.msg{position:absolute;inset:0;display:none;align-items:center;justify-content:center;color:#888;font-size:13px;background:#f5f2ef;border-radius:14px}
</style></head><body><div id="map"></div><div class="msg" id="msg">지도를 불러오지 못했어요.</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" onerror="document.getElementById('msg').style.display='flex'"></script>
<script>
var PINS=__PINS__;
function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
if(window.L){
  var map=L.map("map",{scrollWheelZoom:false});
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:"&copy; OpenStreetMap"}).addTo(map);
  var pts=[];
  PINS.forEach(function(p){
    var icon=L.divIcon({className:"",html:'<div class="pin'+(PINS.length===1?" one":"")+'">'+(PINS.length===1?"●":p.n)+'</div>',iconSize:[30,30],iconAnchor:[15,15],popupAnchor:[0,-14]});
    var box=el("div","pp");box.appendChild(el("b",null,(PINS.length>1?p.n+". ":"")+p.name));
    if(p.sub)box.appendChild(el("div","s",p.sub));
    if(p.michelin)box.appendChild(el("div","m",p.michelin));
    if(p.rating)box.appendChild(el("div","r","★ "+p.rating));
    if(p.video){var a=el("a",null,"영상 보기");a.href=p.video;a.target="_blank";box.appendChild(a)}
    if(p.maps){var b=el("a",null,"지도");b.href=p.maps;b.target="_blank";box.appendChild(b)}
    L.marker([p.lat,p.lng],{icon:icon}).addTo(map).bindPopup(box);
    pts.push([p.lat,p.lng]);
  });
  if(pts.length===1){map.setView(pts[0],15)}else{map.fitBounds(pts,{padding:[36,36],maxZoom:15})}
}
</script></body></html>"""


def pins_of(restaurants: list) -> list:
    """좌표가 있는 식당만 지도 핀 데이터로 바꾼다. 번호(n)는 카드 번호와 같은 순서(좌표가 없는 식당도 번호는 건너뛰지 않는다)."""
    pins = []
    for i, r in enumerate(restaurants, 1):
        if r.get("latitude") is None or r.get("longitude") is None:
            continue
        parts = [p for p in (r.get("region") or "").split(" > ") if p]
        cat = (r.get("category_detail") or r.get("category_broad") or "").strip()
        michelin = r.get("michelin") or ""
        pins.append({
            "n": i, "name": r.get("name") or r.get("name_official"), "lat": float(r["latitude"]), "lng": float(r["longitude"]),
            "sub": " · ".join(x for x in (" ".join(parts[-2:]), cat) if x),
            "michelin": "" if michelin.startswith("미슐랭 아님") else michelin.replace("미슐랭 ", "★ "),
            "rating": float(r["rating"]) if r.get("rating") else None,
            "video": first_video(r.get("videos"))[0], "maps": r.get("google_maps_url"),
        })
    return pins


def map_html(restaurants: list) -> str:
    """식당 목록의 지도 HTML. 찍을 좌표가 없으면 빈 문자열."""
    pins = pins_of(restaurants)
    if not pins:
        return ""
    dump = json.dumps(pins, ensure_ascii=False).replace("</", r"<\/")
    return TEMPLATE.replace("__PINS__", dump)
