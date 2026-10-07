"""extract_script_course.py 결과를 사람이 짚어 준 오류 목록(점검표)으로 확인한다. LLM 호출 없이 JSON 만 읽는다.

점검표는 직접 검수한 영상(밍글스, 원조신촌식당, 르 퀸시, 전통식당 담양본점, 바르셀로나 영상)에서 지적된 누락·오분류를 그대로 옮긴 것이다.
항목마다 결과 JSON에 해당 내용이 있는지(키워드)로 판정하므로 표현이 달라도 키워드만 있으면 통과한다. 통과가 곧 내용이 정확하다는 뜻은 아니다.

사용: uv run python src/pipeline/check_course_gold.py [--dir data/video_notes/gpt-4o-mini-script-course]
"""
import argparse
import glob
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "data" / "video_notes" / "gpt-4o-mini-script-course"


def sq(text) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def menu_text(m: dict) -> str:
    return sq(" ".join(str(m.get(k) or "") for k in ("name", "alt_name", "cooking_features", "taste_review", "tips", "price")))


def has_menu(j: dict, *words) -> bool:
    return any(all(sq(w) in sq(m["name"]) for w in words) for m in j["menus"])


def in_menus(j: dict, *words) -> bool:
    return any(all(sq(w) in menu_text(m) for w in words) for m in j["menus"])


def drink(j: dict, name: str):
    return next((d for d in j["drinks"] if sq(name) in sq(d["name"])), None)


def everything(j: dict) -> str:
    return sq(json.dumps({k: j.get(k) for k in ("menus", "drinks", "key_points", "atmosphere", "final_review")}, ensure_ascii=False))


# (영상 ID, 가게 이름에 들어 있는 글자, 항목 설명, 판정 함수)
CHECKS = [
    # 밍글스(BCgIOkje9oQ)
    ("BCgIOkje9oQ", "밍글스", "당근 수프가 별도 메뉴", lambda j: has_menu(j, "당근", "수프")),
    ("BCgIOkje9oQ", "밍글스", "전갱이 토스트 설명에 당근 수프가 섞이지 않음",
     lambda j: not any("전갱이" in m["name"] and "당근" in menu_text(m) for m in j["menus"])),
    ("BCgIOkje9oQ", "밍글스", "배추 밀푀유 메뉴", lambda j: has_menu(j, "밀푀유")),
    ("BCgIOkje9oQ", "밍글스", "밍글링 팟에 불도장·신선로 언급", lambda j: any("밍글링" in m["name"] and re.search("불도장|신선로", menu_text(m)) for m in j["menus"])),
    ("BCgIOkje9oQ", "밍글스", "코쉬듀리 설명(향 변화)",
     lambda j: (lambda d: bool(d) and "향" in (d.get("review") or "") and bool(re.search("변|시간|끝까지", d.get("review") or "")))(drink(j, "Coche") or drink(j, "코쉬") or drink(j, "코슈"))),
    ("BCgIOkje9oQ", "밍글스", "옥돔구이에 미음 느낌", lambda j: any("옥돔" in m["name"] and "미음" in menu_text(m) for m in j["menus"])),
    ("BCgIOkje9oQ", "밍글스", "찹쌀밥+새우 메뉴", lambda j: has_menu(j, "찹쌀밥") or has_menu(j, "새우") or has_menu(j, "대하")),
    ("BCgIOkje9oQ", "밍글스", "90,000원 추가 시 블루 랍스터", lambda j: bool(re.search("90,?000|9만", everything(j))) and "블루랍스터" in everything(j)),
    ("BCgIOkje9oQ", "밍글스", "랍스터 군만두", lambda j: has_menu(j, "군만두")),
    ("BCgIOkje9oQ", "밍글스", "무 만두", lambda j: has_menu(j, "무", "만두")),
    ("BCgIOkje9oQ", "밍글스", "치킨 룰라드", lambda j: has_menu(j, "룰라드")),
    ("BCgIOkje9oQ", "밍글스", "모렐 치킨 설기", lambda j: has_menu(j, "설기")),
    ("BCgIOkje9oQ", "밍글스", "딸기 셀러리 장아찌", lambda j: has_menu(j, "딸기") or has_menu(j, "셀러리")),
    ("BCgIOkje9oQ", "밍글스", "한우 안심·우족편", lambda j: in_menus(j, "우족편")),
    ("BCgIOkje9oQ", "밍글스", "알배추·감귤·대저토마토 겉절이", lambda j: has_menu(j, "겉절이")),
    ("BCgIOkje9oQ", "밍글스", "샤또 디켐(디저트 와인)", lambda j: bool(drink(j, "디켐"))),
    ("BCgIOkje9oQ", "밍글스", "대분류 한식 + 세부 분류 있음", lambda j: j.get("category_broad") == "한식" and bool(j.get("category_detail")) and sq(j["category_detail"]).lower() != "null"),
    # 원조신촌식당(YeZJduJmo0Y)
    ("YeZJduJmo0Y", "원조신촌", "닭날개 메뉴", lambda j: has_menu(j, "날개")),
    ("YeZJduJmo0Y", "원조신촌", "닭불고기 설명(가슴살·한 마리 구성)", lambda j: any("불고기" in m["name"] and re.search("가슴살|한마리", menu_text(m)) for m in j["menus"])),
    # 르 퀸시(3vYwR8V8IaU)
    ("3vYwR8V8IaU", "퀸시", "대분류 양식 + 세부 프랑스", lambda j: j.get("category_broad") == "양식" and "프랑스" in (j.get("category_detail") or "")),
    ("3vYwR8V8IaU", "퀸시", "chefs 에 대표님이 없음", lambda j: not any(c["name"] in ("대표님", "사장님") for c in j.get("chefs", []))),
    ("3vYwR8V8IaU", "퀸시", "키포인트: 생선보다 고기", lambda j: any("고기" in k and "생선" in k for k in j["key_points"])),
    ("3vYwR8V8IaU", "퀸시", "메뉴 이름 푸아그라 드 카나드", lambda j: has_menu(j, "푸아그라", "드", "카나드")),
    ("3vYwR8V8IaU", "퀸시", "메뉴 이름 에스카르고", lambda j: has_menu(j, "에스카르고")),
    ("3vYwR8V8IaU", "퀸시", "메뉴 이름 테트 드 보", lambda j: has_menu(j, "테트", "보")),
    ("3vYwR8V8IaU", "퀸시", "풀이(오리 푸아그라)가 설명에 있음", lambda j: any("카나드" in m["name"] and "오리푸아그라" in menu_text(m) for m in j["menus"])),
    ("3vYwR8V8IaU", "퀸시", "풀이(달팽이)가 설명에 있음", lambda j: any("에스카르고" in m["name"] and "달팽이" in menu_text(m) for m in j["menus"])),
    # 전통식당 담양본점(-y3kWfSZlyg)
    ("-y3kWfSZlyg", "담양", "메뉴에 '고기'(범주 이름)가 없음", lambda j: not has_menu(j, "고기") or all(sq(m["name"]) != "고기" for m in j["menus"])),
    # 바르셀로나(z5sRczuZcQ8)
    ("z5sRczuZcQ8", "카탈라나", "투리아 매르첸에 발렌시아 로컬 맥주", lambda j: any("투리아" in d["name"] and "발렌시아" in (d.get("review") or "") for d in j["drinks"])),
    ("z5sRczuZcQ8", "카탈라나", "미니 봄바(품절)가 메뉴에 없음", lambda j: not has_menu(j, "봄바")),
    ("z5sRczuZcQ8", "카탈라나", "튀긴 고추·미니 핫도그", lambda j: has_menu(j, "핫도그") or has_menu(j, "고추")),
    ("z5sRczuZcQ8", "카탈라나", "그릴에 구운 새우", lambda j: any("새우" in m["name"] and "감바스" not in m["name"] for m in j["menus"]) or in_menus(j, "구운", "새우")),
    ("z5sRczuZcQ8", "카탈라나", "칼 펩의 예약 내용이 섞이지 않음", lambda j: "예약" not in sq(" ".join(j["key_points"])) and "예약" not in sq(j.get("atmosphere"))),
    ("z5sRczuZcQ8", "칼 펩", "테이블 예약 필수 내용", lambda j: "예약" in (sq(" ".join(j["key_points"])) + sq(j.get("atmosphere")))),
    ("z5sRczuZcQ8", "칼 펩", "끌로 모가도르 음료", lambda j: bool(drink(j, "모가도르"))),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(DEFAULT_DIR))
    ap.add_argument("-q", action="store_true", help="통과한 항목은 숨긴다")
    args = ap.parse_args()
    ok = total = 0
    for vid, store, label, test in CHECKS:
        files = [p for p in glob.glob(str(Path(args.dir) / f"{vid}__*.json")) if store in json.load(open(p, encoding="utf-8"))["korean_name"]]
        if not files:
            print(f"?  {vid} {store}: {label}  (결과 파일 없음)")
            continue
        j = json.load(open(files[0], encoding="utf-8"))
        try:
            passed = bool(test(j))
        except Exception as e:  # 필드가 없는 오래된 결과도 읽을 수 있게 한다
            passed = False
        total += 1
        ok += passed
        if not (args.q and passed):
            print(f"{'O' if passed else 'X'}  {j['korean_name']}: {label}")
    print(f"\n통과 {ok}/{total}")


if __name__ == "__main__":
    main()
