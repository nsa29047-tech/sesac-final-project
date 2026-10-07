"""셰프 추출 결과(data/chef_notes/{TAG}/*.json) -> restaurants.chef_name / chef_info 적재. 재실행해도 결과가 같다.

선행: migrate_chef_columns.sql 적용, extract_chef_info.py 실행.
  - 한 식당이 여러 영상·가게 항목에 나오면 이름(공백 제거 기준)으로 합친다. 같은 셰프는 한 번만 넣고, 근거가 더 강한 항목을 우선한다.
  - chef_name : 셰프 이름을 쉼표로 연결(오너·총괄 셰프 먼저, 확신도 높은 순). 확신도가 --min-confidence 보다 낮은 이름은 넣지 않는다.
  - chef_info : "이름: 경력" 을 " / " 로 연결. 경력은 영상에서 한 말이라 검증된 사실이 아니다.
  - 모델 결과를 그대로 믿지 않고 규칙으로 한 번 더 거른다(filter_note 참고): 직함만 있는 이름("사장님", "대표님"), 채널 운영자, 식당 이름과
    같은 이름, 역할이 other 인 항목, 같은 영상의 여러 가게에 똑같이 붙은 이름(어느 가게의 셰프인지 알 수 없음)은 적재하지 않는다.
  - 값이 계산된 식당만 갱신한다(다른 식당의 기존 값은 지우지 않는다). 셰프가 없는 식당은 NULL 그대로다.

사용: uv run python src/db/load_chefs.py [--dir gpt-4o-mini-chef-v1] [--min-confidence medium] [--dry-run]
      POSTGRES_URI 환경변수(.env)를 사용한다.
"""
import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHEF_DIR = ROOT / "data" / "chef_notes"
RANK = {"high": 3, "medium": 2, "low": 1}
ROLE_ORDER = {"owner_chef": 0, "head_chef": 1, "chef": 2, "pastry_chef": 3, "other": 4}


def norm(name: str) -> str:
    return re.sub(r"[\s·.,'\"()\[\]]", "", name).replace("셰프님", "").replace("셰프", "")


# 규칙으로 걸러지지 않아 사람이 결과를 보고 직접 제외한 항목: (식당 korean_name, 셰프 이름) -> 사유
MANUAL_EXCLUDE = {
    ("프릳츠 장충점", "강민구"): "밍글스의 셰프가 방문한 영상. 이 식당의 셰프가 아님",
    ("칼 펩", "Cal Pep"): "식당 이름(Cal Pep)을 영문으로 적은 것. 사람 이름이 아님",
    ("멘야 사이미", "샤이미"): "식당 이름이 자동 자막에서 깨진 것으로 보임. 사람 이름이 아님",
}

GENERIC = {"사장님", "사장", "대표님", "대표", "오너", "주방장", "선생님", "프렌치", "프렌치셰프", "요리사", "쉐프", "세프", "비밀이야", "비밀아", "비밀리아"}
GENERIC_SUFFIX = ("사장님", "대표님", "셰프님", "방탈")


def reject_reason(chef, restaurant_name, shared_in_video):
    """셰프 항목을 버려야 하면 사유를, 쓸 수 있으면 None 을 반환한다."""
    key = norm(chef["name"])
    rest = norm(restaurant_name)
    if chef["role"] == "other":
        return "역할 other"
    if key in GENERIC or key.endswith(GENERIC_SUFFIX):
        return "직함·채널명"
    if rest and (key in rest or rest in key):
        return "식당 이름과 같음"
    if key in shared_in_video:
        return "같은 영상의 여러 가게에 붙음"
    return None


def merge_restaurant(entries, min_rank):
    """한 식당의 셰프 항목들을 이름 기준으로 합쳐 (chef_name, chef_info) 를 만든다. 쓸 수 있는 셰프가 없으면 (None, None)."""
    by_name = {}
    for chef in entries:
        if RANK[chef["confidence"]] < min_rank:
            continue
        key = norm(chef["name"])
        cur = by_name.get(key)
        if cur is None:
            by_name[key] = dict(chef)
            continue
        if RANK[chef["confidence"]] > RANK[cur["confidence"]]:
            cur.update(name=chef["name"], confidence=chef["confidence"])
        if ROLE_ORDER[chef["role"]] < ROLE_ORDER[cur["role"]]:
            cur["role"] = chef["role"]
        if chef.get("background") and len(chef["background"]) > len(cur.get("background") or ""):
            cur["background"] = chef["background"]
    chefs = sorted(by_name.values(), key=lambda c: (ROLE_ORDER[c["role"]], -RANK[c["confidence"]]))
    if not chefs:
        return None, None
    name = ", ".join(c["name"] for c in chefs)
    info = " / ".join(f"{c['name']}: {c['background']}" for c in chefs if c.get("background")) or None
    return name, info


def main(dir_name, min_confidence, dry_run):
    by_cid = defaultdict(list)
    files = sorted((CHEF_DIR / dir_name).glob("*.json"))
    notes = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    stores_of = defaultdict(lambda: defaultdict(set))  # 영상 -> 이름 -> 그 이름이 붙은 가게(cid)들
    for note in notes:
        for chef in note["chefs"]:
            if RANK[chef["confidence"]] >= RANK[min_confidence]:
                stores_of[note["video_id"]][norm(chef["name"])].add(note["google_cid"])
    dropped = defaultdict(list)
    passed = []  # (note, chef) 규칙을 통과한 항목
    for note in notes:
        shared = {k for k, cids in stores_of[note["video_id"]].items() if len(cids) > 1}
        for chef in note["chefs"]:
            if chef.get("background") and chef["background"].strip().lower() in ("null", "none", "없음"):
                chef["background"] = None
            why = reject_reason(chef, note["korean_name"], shared)
            if not why and (note["korean_name"], chef["name"]) in MANUAL_EXCLUDE:
                why = "사람이 확인해 제외"
            if why:
                dropped[why].append(f"{note['korean_name']}: {chef['name']}")
            else:
                passed.append((note, chef))
    # 같은 이름이 서로 다른 식당에 붙으면 방문한 셰프일 수 있다. 근거 문장이나 경력에 그 식당 이름이 나오는 항목만 남긴다.
    restaurants_of = defaultdict(set)
    for note, chef in passed:
        restaurants_of[norm(chef["name"])].add(note["google_cid"])
    for note, chef in passed:
        key = norm(chef["name"])
        text = norm(chef["evidence"] + (chef.get("background") or ""))
        if len(restaurants_of[key]) > 1 and norm(note["korean_name"]) not in text:
            dropped["같은 이름이 여러 식당에 붙고 근거에 식당 이름이 없음"].append(f"{note['korean_name']}: {chef['name']}")
        else:
            by_cid[note["google_cid"]].append(chef)
    for why, items in dropped.items():
        print(f"[규칙으로 제외 - {why}] {len(items)}건: {', '.join(items[:12])}{' ...' if len(items) > 12 else ''}")
    plans = {}
    for cid, entries in by_cid.items():
        name, info = merge_restaurant(entries, RANK[min_confidence])
        if name:
            plans[cid] = (name, info)
    print(f"추출 결과 {len(files)}건, 셰프를 적재할 식당 {len(plans)}곳 (최소 확신도 {min_confidence})")
    names = {n["google_cid"]: n["korean_name"] for n in notes}
    multi = defaultdict(list)  # 같은 셰프 이름이 서로 다른 식당에 붙은 경우는 사람이 확인한다(방문한 셰프일 수 있음)
    for cid, (name, _) in plans.items():
        for n in name.split(", "):
            multi[norm(n)].append(names[cid])
    shared_names = {k: v for k, v in multi.items() if len(v) > 1}
    if shared_names:
        print("[확인 필요] 같은 이름이 여러 식당에 붙음:", "; ".join(f"{k} -> {', '.join(v)}" for k, v in shared_names.items()))
    if dry_run:
        for cid, (name, info) in plans.items():
            print(f"  {names[cid][:16]:<16} | {name} | {(info or '-')[:70]}")
        return

    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    updated = missing = 0
    for cid, (name, info) in plans.items():
        cur.execute("UPDATE restaurants SET chef_name = %s, chef_info = %s, updated_at = now() WHERE google_cid = %s",
                    (name[:300], info, cid))
        updated += cur.rowcount
        missing += cur.rowcount == 0
    conn.commit()
    print(f"적재 {updated}곳, DB에 식당이 없어 건너뜀 {missing}곳")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="셰프 추출 결과를 restaurants 에 적재한다.")
    ap.add_argument("--dir", default="gpt-4o-mini-chef-v1", help="data/chef_notes 아래 결과 폴더")
    ap.add_argument("--min-confidence", choices=["high", "medium", "low"], default="medium", help="이 확신도 이상만 적재 (기본 medium)")
    ap.add_argument("--dry-run", action="store_true", help="DB 접속 없이 적재 대상만 출력")
    args = ap.parse_args()
    main(args.dir, args.min_confidence, args.dry_run)
