"""세부 분류(category_detail)를 식당당 대표 하나로 정하고 나머지 후보는 태그로 보낸다. 미슐랭 문구는 어디에도 넣지 않는다.

배경: 영상 추출값은 "고기 전문점, 삼겹살, 목살, 된장찌개"처럼 여러 개이거나 표기가 제각각("프랑스 요리"/"프렌치 레스토랑"/"고급 프랑스 요리")이라
SQL로 조회하기 어렵다. 후보 항목·태그·컨셉·메뉴를 보고 LLM이 대표 하나를 고르고(표기는 아래 예시 목록으로 통일) 나머지는 태그로 옮긴다.
대분류(category_broad)는 바꾸지 않고, 명백히 틀렸거나 어디에도 맞지 않아 보이는 것만 제안으로 출력한다(적용하지 않는다).

흐름: 결과는 건별로 data/category_pick/{google_cid}.json 에 저장한다(중단 후 재실행하면 이어서 처리).
  uv run python src/db/pick_detail.py --limit 8            # 샘플 8곳만 호출해 결과를 출력(DB·JSON 변경 없음)
  uv run python src/db/pick_detail.py                      # 전체 호출(이미 저장된 곳은 건너뜀). 120곳, 약 $0.02
  uv run python src/db/pick_detail.py --apply --dry-run    # 저장된 결과로 바뀔 내용만 출력
  uv run python src/db/pick_detail.py --apply              # DB(restaurants.category_detail, restaurant_tags)와 노트 JSON에 반영
JSON의 category_detail 은 원본을 category_detail_raw 에 남기고 바꾼다. 그래야 load_notes.py 를 다시 돌려도 같은 값이 적재된다.
"""
import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Literal, Optional

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from category_rules import BROAD, MANUAL_CATEGORY, MICHELIN_TAG, VOCAB, canonical_detail  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NOTE_DIRS = ["gpt-4o-mini-script-split-v2", "gpt-4o-mini-script-split-v2-2025", "gpt-4o-mini-script-split-v2-2024"]
OUT_DIR = ROOT / "data" / "category_pick"
MODEL = "gpt-4o-mini"
load_dotenv(ROOT / ".env")

PROMPT = f"""너는 식당의 세부 분류를 정리하는 편집자야. 후보 항목, 태그, 컨셉, 메뉴가 주어진다.
- detail: 이 식당을 대표하는 세부 분류를 딱 하나만 짧은 명사구로 쓴다. 쉼표나 여러 개를 쓰지 않는다.
  표기를 통일해야 검색이 되므로 아래 예시 목록에 맞는 말이 있으면 그 표기를 그대로 쓰고(프랑스 요리·프렌치 레스토랑·고급 프랑스 요리는 모두 '프렌치', 이탈리아 요리는 '이탈리안'),
  맞는 게 없을 때만 짧은 일반 명사 하나로 쓴다. 타파스를 주로 내는 바는 '스페인 요리'가 아니라 '타파스 바'로 한다. 음식의 나라·종류가 보이면 그것을 대표로 하고, 형태(파인다이닝, 코스 요리, 레스토랑)는 음식 종류를 알 수 없을 때만 쓴다.
  '시그니처 요리', '창의적인 애피타이저'처럼 분류가 아닌 말은 쓰지 않는다.
- extra_tags: 대표로 고르지 않은 다른 후보와 대표 음식·형태를 최대 5개(예: 삼겹살, 코스 요리, 와인 페어링). detail과 같은 말은 넣지 않는다.
- 미슐랭·미쉐린·별 개수 같은 말은 detail에도 extra_tags에도 절대 쓰지 않는다(미슐랭은 따로 관리한다).
- suggested_broad: 현재 대분류가 명백히 틀렸거나 어떤 큰 분류(한식·일식·중식·양식·동남아식·인도식·중동식)에도 맞지 않을 때만 적는다. 무리하게 끼워 맞추지 말고 애매하면 '기타'. 문제없으면 null.

세부 분류 예시(대분류별):
{VOCAB}"""


class Pick(BaseModel):
    detail: str = Field(description="대표 세부 분류 하나")
    extra_tags: List[str] = Field(default_factory=list, description="나머지 후보와 대표 음식 키워드")
    suggested_broad: Optional[Literal["한식", "일식", "중식", "양식", "동남아식", "인도식", "중동식", "기타"]] = Field(None, description="대분류를 바꿔야 할 때만")
    reason: Optional[str] = Field(None, description="대분류를 바꾸자고 한 이유 한 줄")


def load_targets():
    """google_cid 하나당 노트 하나(여러 영상이면 첫 번째). (cid, note, [JSON 파일들])"""
    by_cid, files = {}, {}
    for d in NOTE_DIRS:
        for f in sorted(glob.glob(str(ROOT / "data" / "video_notes" / d / "*.json"))):
            note = json.loads(Path(f).read_text(encoding="utf-8"))
            cid = note.get("google_cid")
            if not cid or not note.get("category_broad"):
                continue
            by_cid.setdefault(cid, note)
            files.setdefault(cid, []).append(f)
    return [(cid, by_cid[cid], files[cid]) for cid in by_cid]


def describe(note):
    foods = [m.get("name") for m in note.get("menus", []) if m.get("name")][:10]
    return (f"[식당] {note.get('korean_name')} / 국가 {note.get('country_code', '')}\n[현재 대분류] {note.get('category_broad')}\n"
            f"[후보 항목] {note.get('category_detail_raw') or note.get('category_detail')}\n[태그] {', '.join(note.get('cuisine_tags', []))}\n"
            f"[컨셉] {note.get('concept') or ''}\n[메뉴] {', '.join(foods)}")


def pick_all(limit):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    done = 0
    for cid, note, _ in load_targets():
        out = OUT_DIR / f"{cid}.json"
        if out.exists():
            continue
        if limit and done >= limit:
            break
        r = client.beta.chat.completions.parse(model=MODEL, temperature=0, seed=42, response_format=Pick,
                                               messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": describe(note)}]).choices[0].message.parsed
        # 안전장치: 모델이 미슐랭 문구를 썼으면 제거한다
        data = {"google_cid": cid, "name": note.get("korean_name"), "broad": note["category_broad"], "before": note.get("category_detail"),
                "detail": None if MICHELIN_TAG.search(r.detail) else r.detail.strip()[:50],
                "extra_tags": [t for t in dict.fromkeys(x.strip() for x in r.extra_tags) if t and t != r.detail and not MICHELIN_TAG.search(t)],
                "suggested_broad": r.suggested_broad if r.suggested_broad != note["category_broad"] else None, "reason": r.reason}
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        done += 1
        sug = f"  | 대분류 제안 {data['suggested_broad']}: {data['reason']}" if data["suggested_broad"] else ""
        print(f"{data['name']}: [{data['broad']}] {data['before']}  ->  {data['detail']}  | 태그 {data['extra_tags']}{sug}")
    print(f"\n{done}곳 호출")


def apply(dry_run):
    results = {json.loads(p.read_text(encoding="utf-8"))["google_cid"]: json.loads(p.read_text(encoding="utf-8")) for p in OUT_DIR.glob("*.json")}
    conn = psycopg2.connect(os.environ["POSTGRES_URI"])
    cur = conn.cursor()
    n = 0
    for cid, r in results.items():
        cur.execute("SELECT restaurant_id, category_detail FROM restaurants WHERE google_cid = %s", (cid,))
        row = cur.fetchone()
        if not row or not r["detail"]:
            continue
        rid, current = row
        # 수동 분류(MANUAL_CATEGORY)는 normalize_categories.py 가 반영한다
        if cid in MANUAL_CATEGORY:
            continue
        r["detail"] = canonical_detail(r["detail"])
        if current != r["detail"]:
            n += 1
            print(f"{r['name']}: {current} -> {r['detail']}  | 태그 +{r['extra_tags']}")
        if dry_run:
            continue
        cur.execute("UPDATE restaurants SET category_detail = %s, updated_at = now() WHERE restaurant_id = %s", (r["detail"], rid))
        for t in r["extra_tags"]:
            cur.execute("INSERT INTO restaurant_tags (restaurant_id, tag) VALUES (%s,%s) ON CONFLICT DO NOTHING", (rid, t[:100]))
    # 미슐랭 태그는 michelin_status 와 중복이라 모두 지운다
    cur.execute("SELECT count(*) FROM restaurant_tags WHERE tag ~* %s", (MICHELIN_TAG.pattern,))
    m = cur.fetchone()[0]
    print(f"\n세부 분류 변경 {n}곳, 미슐랭 태그 삭제 {m}개" + (" (dry-run)" if dry_run else ""))
    if dry_run:
        conn.rollback()
        return
    cur.execute("DELETE FROM restaurant_tags WHERE tag ~* %s", (MICHELIN_TAG.pattern,))
    conn.commit()
    conn.close()
    # 노트 JSON 갱신: 원본은 category_detail_raw 로 남기고, 재적재해도 같은 값이 들어가게 한다
    for cid, note, files in load_targets():
        r = results.get(cid)
        if not r or not r["detail"] or cid in MANUAL_CATEGORY:
            continue
        for f in files:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            d.setdefault("category_detail_raw", d.get("category_detail"))
            d["category_detail"] = canonical_detail(r["detail"])
            d["cuisine_tags"] = [t for t in dict.fromkeys([*d.get("cuisine_tags", []), *r["extra_tags"]]) if not MICHELIN_TAG.search(t)]
            Path(f).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="이 수만큼만 호출(샘플)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    apply(a.dry_run) if a.apply else pick_all(a.limit)
